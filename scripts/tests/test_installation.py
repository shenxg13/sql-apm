#!/usr/bin/env python3
"""Bounded v1 installation regressions, separate from the ordinary application suite."""
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/deployment'))
import credentials
from install_postgres import render
from runtime_archive import FORMAT, PREFIX, TREES, digest, inventory, inspect_artifact, install_runtime, verify_installed
import runtime_check
import runtime_archive


class Credentials(unittest.TestCase):
    def test_prompt_rejects_only_mismatch_empty_or_linebreak(self):
        for value in ['x', ':', '\\', '  spaced  ', '密碼', 'admin']:
            with self.subTest(case=repr(value)), patch('credentials.getpass', side_effect=[value, value]):
                self.assertEqual(credentials.prompt_password('test'), value)
        for first, second in [('', ''), ('x', 'y'), ('x\ny', 'x\ny'), ('x\ry', 'x\ry')]:
            with patch('credentials.getpass', side_effect=[first, second]), self.assertRaises(ValueError):
                credentials.prompt_password('test')

    def test_read_file_does_not_strip_password_or_extra_newline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'secret'
            for value in ['x', ' x ', 'a:\\b']:
                credentials.write_private(path, value + '\n')
                self.assertEqual(credentials.read_password(path), value)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            credentials.write_private(path, 'x\n\n')
            with self.assertRaises(ValueError):
                credentials.read_password(path)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                credentials.write_private(path, 'replacement')

    def test_passfile_rotates_only_target_role_and_preserves_other_account(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pgpass'
            value = 'x:\\:$word'
            content = credentials.passfile_text(path, ['/socket', '127.0.0.1'], 5432, 'sql_apm', 'sql_apm', value)
            credentials.write_private(path, content)
            updated = credentials.passfile_text(path, ['/socket', '127.0.0.1'], 5432, 'sql_apm', 'sql_apm_ro', 'r:')
            credentials.write_private(path, updated)
            rotated = credentials.passfile_text(path, ['/socket', '127.0.0.1'], 5432, 'sql_apm', 'sql_apm', 'new\\:')
            rows = [credentials.pgpass_fields(line) for line in rotated.splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertEqual([r[-1] for r in rows if r[3] == 'sql_apm_ro'], ['r:', 'r:'])
            self.assertEqual([r[-1] for r in rows if r[3] == 'sql_apm'], ['new\\:', 'new\\:'])
            self.assertEqual(credentials.pgpass_fields(content.splitlines()[0])[-1], value)

    def test_symbolic_link_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'target'
            credentials.write_private(path, 'original')
            link = Path(folder) / 'link'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                credentials.write_private(link, 'new')
            self.assertEqual(path.read_text(), 'original')


class Services(unittest.TestCase):
    def test_database_waits_for_start_and_profiles_are_account_specific(self):
        files = render(PREFIX, 5432)
        unit = files['sql-apm-postgresql.service']
        for item in ['Type=forking', 'User=postgres', '-w -t 120 start', '-m fast -w -t 120 stop', 'WantedBy=multi-user.target']:
            self.assertIn(item, unit)
        for name in ('sfmon-psql.sh', 'postgres-psql.sh'):
            result = subprocess.run(['bash', '-n'], input=files[name], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PGUSER=sql_apm\n', files['sfmon-psql.sh'])
        self.assertIn('PGPASSFILE=/data/sql-apm/private/pgpass', files['sfmon-psql.sh'])
        self.assertIn('PGUSER=postgres\n', files['postgres-psql.sh'])
        self.assertIn('unset PGPASSFILE', files['postgres-psql.sh'])
        for path in (ROOT / 'grafana/systemd').glob('*.template'):
            self.assertIn('After=network-online.target sql-apm-postgresql.service', path.read_text())

    def test_unit_path_injection_is_rejected(self):
        for path in ['/tmp/x\nExecStart=bad', '/tmp/with space', '/tmp/percent%h']:
            with self.assertRaises(ValueError):
                render(path, 5432)


class RuntimeArtifact(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.payload = self.root / 'payload'
        for tree in TREES:
            (self.payload / tree).mkdir(parents=True)
        binary = self.payload / TREES[0] / 'python'
        binary.write_bytes(b'synthetic executable')
        binary.chmod(0o755)
        (self.payload / 'app/.venv/python').symlink_to(PREFIX + '/python-3.13.16/python')
        self.artifact = self.root / 'artifact'
        self.artifact.mkdir()
        self.write_artifact()

    def write_artifact(self, extra=None):
        rows = inventory(self.payload)
        with tarfile.open(self.artifact / 'runtime.tar.gz', 'w:gz', dereference=False) as tar:
            for name in rows:
                tar.add(self.payload / name, arcname=name, recursive=False)
            if extra:
                tar.addfile(extra, io.BytesIO(b'x') if extra.isfile() else None)
        document = dict(format=FORMAT, prefix=PREFIX, python='3.13.16', postgresql='17.10',
                        files=rows, evidence={})
        (self.artifact / 'runtime-manifest.json').write_text(json.dumps(document))
        self.checksums()

    def checksums(self):
        (self.artifact / 'SHA256SUMS').write_text(''.join(digest(path) + '  ' + path.name + '\n'
            for path in sorted(self.artifact.iterdir()) if path.name != 'SHA256SUMS'))

    def test_valid_artifact_and_installed_tree_including_links_modes(self):
        document = inspect_artifact(self.artifact)
        (self.payload / 'runtime-manifest.json').write_text(json.dumps(document))
        verify_installed(self.payload)
        (self.payload / TREES[0] / 'python').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'differs'):
            verify_installed(self.payload)

    def test_export_repeat_and_install_round_trip(self):
        # A small Kylin evidence fixture exercises the format, not target compatibility.
        prefix = self.payload.as_posix()
        link = self.payload / 'app/.venv/python'
        link.unlink()
        link.symlink_to(prefix + '/python-3.13.16/python')
        app = self.payload / 'app'
        (app / 'requirements.txt').write_text('fixture-lock')
        (app / 'VERSION').write_text('v1.0.0\n')
        (app / 'PROGRAM_COMMIT').write_text('fixture-commit\n')
        files = {p.name: digest(p) for p in app.iterdir() if p.is_file()}
        (app / 'RELEASE.json').write_text(json.dumps(dict(files=files, version='v1.0.0', kind='candidate', commit='fixture-commit')))
        (app / 'SHA256SUMS').write_text(''.join(digest(app / name) + '  ' + name + '\n'
            for name in sorted(set(files) | {'RELEASE.json'})))
        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'manifest.json').write_text(json.dumps(dict(program_commit='fixture-commit', files=[])))
        evidence = self.root / 'evidence'
        evidence.mkdir()
        for name in ('system-release.txt', 'architecture.txt', 'packages-before.tsv', 'packages-after.tsv',
                     'compile-runtime.sh', 'build.log', 'prefix-details.txt'):
            (evidence / name).write_text('Kylin' if name == 'system-release.txt' else 'x86_64')
        (evidence / 'selfcheck.json').write_text(json.dumps(dict(passed=True, program_commit='fixture-commit', checks=runtime_archive.CHECKS)))
        (evidence / 'checks').mkdir()
        first, second = self.root / 'export1', self.root / 'export2'
        with patch('runtime_archive.PREFIX', prefix):
            runtime_archive.export_runtime(self.payload, bundle, evidence, first)
            runtime_archive.export_runtime(self.payload, bundle, evidence, second)
            for name in ['runtime.tar.gz', 'runtime-manifest.json', 'SHA256SUMS']:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            for tree in TREES:
                shutil.rmtree(self.payload / tree)
            install_runtime(self.payload, first)
            verify_installed(self.payload)
            self.assertEqual((self.payload / 'python-3.13.16/python').read_bytes(), b'synthetic executable')
            self.assertEqual(os.readlink(self.payload / 'app/.venv/python'), prefix + '/python-3.13.16/python')
            with self.assertRaisesRegex(ValueError, 'target exists'):
                install_runtime(self.payload, first)

    def test_modified_archive_and_missing_or_extra_file_fail(self):
        with (self.artifact / 'runtime.tar.gz').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            inspect_artifact(self.artifact)
        self.write_artifact()
        (self.artifact / 'unexpected').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'file set'):
            inspect_artifact(self.artifact)

    def test_traversal_duplicate_and_external_link_fail(self):
        for name in ['../outside', '/absolute', 'python-3.13.16/python']:
            member = tarfile.TarInfo(name)
            member.size = 1
            self.write_artifact(member)
            with self.assertRaises(ValueError):
                inspect_artifact(self.artifact)
        (self.payload / 'app/.venv/python').unlink()
        (self.payload / 'app/.venv/python').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'leaves runtime'):
            inventory(self.payload)

    def test_wrong_prefix_fails_before_read_or_write(self):
        destination = self.root / 'wrong-prefix'
        with self.assertRaisesRegex(ValueError, 'only supports /data/sql-apm'):
            install_runtime(destination, self.artifact)
        self.assertFalse(destination.exists())

    def test_missing_library_stops_selfcheck_before_database_or_package(self):
        fake = self.payload / 'app/.venv/bin'
        fake.mkdir()
        python = fake / 'python'
        python.write_text('#!/bin/sh\necho "error while loading shared libraries: libfixture.so: cannot open shared object file" >&2\nexit 127\n')
        python.chmod(0o755)
        output = self.root / 'checks'
        self.assertFalse(runtime_check.check(self.payload, self.root / 'unused-kit', output))
        report = json.loads((output / 'selfcheck.json').read_text())
        self.assertEqual(report['failed_check'], 'python')
        self.assertEqual(report['checks'], [])
        self.assertIn('libfixture.so', (output / 'python.log').read_text())
        self.assertEqual(set(p.name for p in output.iterdir()), {'python.log', 'selfcheck.json'})


if __name__ == '__main__':
    unittest.main()
