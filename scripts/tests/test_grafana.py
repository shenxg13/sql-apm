#!/usr/bin/env python3
"""Development checks of the Grafana tooling; no Grafana, database or network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/grafana' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Dashboards(unittest.TestCase):
    def test_committed_dashboards_are_what_the_generator_writes(self):
        for name, document in load('build_dashboards').build().items():
            expected = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
            self.assertEqual((ROOT / 'grafana/dashboards' / name).read_text(), expected, name)

    def test_panel_identifiers_are_unique(self):
        for name, board in load('build_dashboards').build().items():
            ids = [panel['id'] for outer in board['panels'] for panel in [outer] + outer.get('panels', [])]
            self.assertEqual(len(ids), len(set(ids)), name)


class Installer(unittest.TestCase):
    def run_files(self, directory, files, mode=0o600):
        secret = directory / 'secret'
        secret.write_text('a-long-enough-password\n')
        os.chmod(secret, mode)
        return subprocess.run([sys.executable, str(ROOT / 'scripts/grafana/install.py'), 'files', '--home', str(directory / 'home'),
                               '--files', str(files), '--db-password-file', str(secret), '--admin-password-file', str(secret),
                               '--pg-port', '5432'], capture_output=True, text=True)

    def test_missing_or_altered_archives_are_refused_before_anything_is_unpacked(self):
        manifest = json.loads((ROOT / 'grafana/components.json').read_text())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            files = directory / 'files'
            files.mkdir()
            missing = self.run_files(directory, files)
            self.assertEqual(missing.returncode, 1)
            self.assertIn('missing archive', missing.stderr)
            for item in [manifest['grafana']] + manifest['plugins']:
                (files / item['file']).write_bytes(b'not the pinned file')
            altered = self.run_files(directory, files)
            self.assertEqual(altered.returncode, 1)
            self.assertIn('digest mismatch, refusing to install', altered.stderr)
            self.assertFalse((directory / 'home').exists())

    def test_fetch_reports_a_file_that_does_not_match(self):
        fetch = load('fetch')
        manifest = json.loads((ROOT / 'grafana/components.json').read_text())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            names = [item['file'] for item in [manifest['grafana']] + manifest['plugins']]
            self.assertEqual(sorted(fetch.verify(directory)), sorted(names))
            (directory / names[0]).write_bytes(b'x')
            self.assertEqual(sorted(fetch.verify(directory)), sorted(names))

    def test_templates_have_no_placeholder_the_installer_does_not_fill(self):
        install = load('install')
        names = ('HOME', 'GRAFANA', 'LISTEN', 'PORT', 'ADMIN_PASSWORD_FILE', 'DB_PASSWORD_FILE', 'PG_PORT', 'DATABASE', 'SCHEMA',
                 'READONLY_ROLE', 'SERVICE_PORT', 'APP_ROOT', 'PYTHON', 'PGPASSFILE', 'USER')
        templates = sorted((ROOT / 'grafana').rglob('*.template'))
        self.assertEqual(len(templates), 5)
        for template in templates:
            self.assertNotRegex(install.render(template, {name: 'value' for name in names}), '@[A-Z_]+@')
        with self.assertRaises(SystemExit):
            install.render(ROOT / 'grafana/grafana.ini.template', dict(HOME='value'))


if __name__ == '__main__':
    unittest.main()
