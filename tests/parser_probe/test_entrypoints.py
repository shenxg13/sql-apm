"""Package boundaries and standalone entrypoints for optional parser tools."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sql_apm.diagnostics import mpp_adapter_probe, parser_fidelity
from sql_apm.sql.mpp_parser import parse

ROOT = Path(__file__).resolve().parents[2]


class EntrypointTests(unittest.TestCase):
    def test_core_imports_without_diagnostics_or_scripts(self):
        code = '''
import importlib.abc
import sys
class NoTools(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'scripts' or fullname.startswith(('scripts.', 'sql_apm.diagnostics')):
            raise AssertionError('Core imported a tool: ' + fullname)
sys.meta_path.insert(0, NoTools())
from sql_apm.sql.mpp_parser import parse
result = parse('CREATE TABLE t(id int) DISTRIBUTED BY(id); SELECT 1')
assert len(result['statements']) == 2
assert result['statements'][0]['extensions'][0]['policy']['kind'] == 'hash'
'''
        run = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_imports_do_not_change_sys_path_or_run_a_command(self):
        code = '''
import contextlib
import importlib
import io
import sys
before = list(sys.path)
output = io.StringIO()
with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
    for name in ('parser_fidelity', 'mpp_adapter_probe', 'mpp_expansion_probe',
                 'mpp_expansion_matrix', 'mpp_broad_matrix', 'mpp_broad_replay', 'mpp_full_scan',
                 'mpp_full_audit', 'mpp_full_repair'):
        importlib.import_module('sql_apm.diagnostics.' + name)
assert list(sys.path) == before
assert output.getvalue() == ''
'''
        run = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        for path in (ROOT / 'sql_apm').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                         else [n.name for n in node.names] if isinstance(node, ast.Import) else [])
                self.assertFalse(any(n == 'scripts' or n.startswith('scripts.') for n in names), str(path))

    def test_workers_resolve_the_package_from_another_working_directory(self):
        sql = 'CREATE TABLE t(id int) DISTRIBUTED RANDOMLY'
        # Worker cwd is the repository; PYTHONPATH is intentionally absolute
        # here so optional dependencies do not depend on the caller's cwd.
        env_path = os.environ.get('PYTHONPATH', '')
        entries = [str(Path(p).resolve()) for p in env_path.split(os.pathsep) if p]
        previous = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ['PYTHONPATH'] = os.pathsep.join(entries)
                os.chdir(directory)
                result = mpp_adapter_probe.probe(sql, synthetic=True)
                self.assertEqual(result['tree'], parse(sql))
                for engine in ('pglast', 'sqlglot'):
                    self.assertEqual(parser_fidelity.probe(engine, 'SELECT 1')['state'], 'parsed')
        finally:
            os.chdir(previous)
            if env_path:
                os.environ['PYTHONPATH'] = env_path
            else:
                os.environ.pop('PYTHONPATH', None)

    def test_module_worker_matches_probe_from_other_directory(self):
        env = dict(os.environ)
        paths = [str(ROOT)] + [str(Path(p).resolve()) for p in env.get('PYTHONPATH', '').split(os.pathsep) if p]
        env['PYTHONPATH'] = os.pathsep.join(paths)
        with tempfile.TemporaryDirectory() as directory:
            for name, request in (
                    ('mpp_adapter_probe', {'sql': 'SELECT 1; CREATE TABLE t(id int) DISTRIBUTED BY(id)', 'synthetic': True}),
                    ('parser_fidelity', {'parser': 'pglast', 'sql': 'SELECT /*+ H */ 1', 'synthetic': True})):
                command = [sys.executable, '-m', 'sql_apm.diagnostics.' + name, '--worker']
                run = subprocess.run(command, input=json.dumps(request), cwd=directory, env=env,
                                     capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stderr)
                actual = json.loads(run.stdout)
                expected = (mpp_adapter_probe.probe(request['sql'], synthetic=True)
                            if name == 'mpp_adapter_probe' else
                            parser_fidelity.probe(request['parser'], request['sql'], synthetic=True))
                self.assertEqual(actual, expected)
                self.assertIn('tree', actual)


if __name__ == '__main__':
    unittest.main()
