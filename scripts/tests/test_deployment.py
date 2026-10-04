#!/usr/bin/env python3
"""Bounded release regressions; run with the locked build Python environment."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/deployment'))
from build_release import archive, select
from render_manual import inspect, render
from verify_package import digest, verify
import rehearsal


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sql-apm-package-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / 'app'
        self.app.mkdir()
        (self.app / 'VERSION').write_text('v0.1.0\n')
        (self.app / 'PROGRAM_COMMIT').write_text('abc123\n')
        (self.app / 'schema.sql').write_text('SELECT 1;\n')
        doc = dict(version='v0.1.0', commit='abc123', kind='candidate',
                   files={p.name: digest(p) for p in self.app.iterdir()})
        (self.app / 'RELEASE.json').write_text(json.dumps(doc))
        (self.app / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.name + '\n'
                                                  for p in sorted(self.app.iterdir())))

    def test_valid_tree(self):
        self.assertTrue(verify(self.app)['passed'])

    def test_rehearsal_requires_expected_commit_and_unchanged_package(self):
        with patch.object(rehearsal, 'ROOT', self.app):
            self.assertTrue(rehearsal.verify_product('abc123')['passed'])
            with self.assertRaisesRegex(ValueError, 'unexpected_program_commit'):
                rehearsal.verify_product('other-commit')
            (self.app / 'schema.sql').write_text('SELECT 2;\n')
            with self.assertRaisesRegex(ValueError, 'package checksum mismatch'):
                rehearsal.verify_product('abc123')

    def test_rehearsal_legacy_path_rejects_original_product_drift(self):
        baseline = self.root / 'baseline.json'
        baseline.write_text(json.dumps(dict(provenance=dict(
            product_code_sha256={'schema.sql': digest(self.app / 'schema.sql')}))))
        with patch.object(rehearsal, 'ROOT', self.app), patch.object(rehearsal, 'BASELINE', baseline):
            self.assertEqual({'baseline_product_equal': True}, rehearsal.verify_product())
            (self.app / 'schema.sql').write_text('SELECT 2;\n')
            with self.assertRaisesRegex(ValueError, 'product_code_changed'):
                rehearsal.verify_product()

    def test_missing_sql(self):
        (self.app / 'schema.sql').unlink()
        with self.assertRaisesRegex(ValueError, 'file set mismatch'):
            verify(self.app)

    def test_unlisted_file(self):
        (self.app / 'private.conf').write_text('synthetic')
        with self.assertRaisesRegex(ValueError, 'file set mismatch'):
            verify(self.app)

    def test_corrupted_file(self):
        (self.app / 'schema.sql').write_text('SELECT 2;\n')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            verify(self.app)

    def test_corrupted_checksum_list(self):
        (self.app / 'SHA256SUMS').write_text('')
        with self.assertRaisesRegex(ValueError, 'SHA256SUMS'):
            verify(self.app)

    def test_symbolic_link(self):
        (self.app / 'schema.sql').unlink()
        (self.app / 'schema.sql').symlink_to(self.app / 'VERSION')
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            verify(self.app)

    def test_installed_generated_files_only(self):
        (self.app / '.venv').mkdir()
        (self.app / '.venv/pyvenv.cfg').write_text('synthetic')
        (self.app / '__pycache__').mkdir()
        (self.app / '__pycache__/sample.pyc').write_bytes(b'generated')
        self.assertTrue(verify(self.app, installed=True)['passed'])
        with self.assertRaisesRegex(ValueError, 'file set mismatch'):
            verify(self.app)

    def test_archive_reproducibility(self):
        first, second = self.root / 'a.tar.gz', self.root / 'b.tar.gz'
        archive(self.app, first)
        (self.app / 'schema.sql').touch()
        archive(self.app, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_directory_rule_and_missing_pattern(self):
        source = self.root / 'source'
        (source / 'runtime/nested').mkdir(parents=True)
        (source / 'runtime/nested/resource.sql').write_text('SELECT 1;')
        (source / 'secret').write_text('synthetic')
        target = self.root / 'selected'
        self.assertEqual(select(source, target, ['runtime/*.sql']), {'runtime/nested/resource.sql'})
        self.assertFalse((target / 'secret').exists())
        with self.assertRaisesRegex(ValueError, 'matched no files'):
            select(source, target, ['missing/*.sql'])

    def test_verification_rejects_mismatched_program_commit(self):
        kit = self.root / 'verification'
        (kit / 'scripts/deployment').mkdir(parents=True)
        runner = kit / 'scripts/deployment/run_verification.py'
        shutil.copy2(ROOT / 'scripts/deployment/run_verification.py', runner)
        (kit / 'PROGRAM_COMMIT').write_text('different-commit\n')
        for name in ('sql_apm/__init__.py', 'scripts/db/initialize.sh', 'rules/functions/v1.0.1.json'):
            path = self.app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('')
        result = subprocess.run([sys.executable, str(runner), '--app-root', str(self.app), 'unit'],
                                capture_output=True, text=True, cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('different commits', result.stderr)


class HtmlTests(unittest.TestCase):
    def test_current_manual(self):
        _, checks = render(ROOT, 'a' * 40, 'v0.1.0')
        self.assertGreater(checks['code_blocks'], 40)

    def test_external_resources_rejected(self):
        for fragment in ('<script src="https://example.com/a.js"></script>',
                         '<img src="https://example.com/a.png">',
                         '<style>@import "https://example.com/a.css";</style>',
                         '<style>body{background:url(https://example.com/a)}</style>',
                         '<meta http-equiv="refresh" content="0;url=https://example.com">'):
            with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                inspect(fragment)

    def test_missing_or_duplicate_anchor(self):
        for fragment in ('<a href="#absent">x</a>', '<h1 id="a">a</h1><h2 id="a">b</h2>'):
            with self.assertRaises(ValueError):
                inspect(fragment)

    def test_commands_preserve_literal_characters(self):
        document = '<pre><code>echo &quot;$APM_APP&quot; &amp;&amp; test 1 &lt; 2\n</code></pre>'
        command = 'echo "$APM_APP" && test 1 < 2\n'
        self.assertEqual(inspect(document, [command])['code_blocks'], 1)
        with self.assertRaisesRegex(ValueError, 'differ'):
            inspect(document, [command.rstrip()])


if __name__ == '__main__':
    unittest.main(verbosity=2)
