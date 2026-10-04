"""Candidate delivery identity must not weaken the immutable business baseline."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'scripts/deployment'
SPEC = importlib.util.spec_from_file_location('rehearsal_package_test', TOOLS / 'rehearsal.py')
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PackageIdentityTests(unittest.TestCase):
    def test_expected_commit_and_unchanged_package_are_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = 'a' * 40
            for name, value in {'VERSION': 'v0.1.0\n', 'PROGRAM_COMMIT': commit + '\n',
                                'synthetic.py': 'value = 1\n'}.items():
                (root / name).write_text(value)
            files = {p.name: digest(p) for p in root.iterdir()}
            (root / 'RELEASE.json').write_text(json.dumps(dict(
                files=files, version='v0.1.0', kind='candidate', commit=commit)))
            (root / 'SHA256SUMS').write_text(''.join(
                digest(root / name) + '  ' + name + '\n'
                for name in sorted(set(files) | {'RELEASE.json'})))
            with patch.object(rehearsal, 'ROOT', root), patch.object(sys, 'path', [str(TOOLS)] + sys.path):
                self.assertTrue(rehearsal.verify_product(commit)['passed'])
                with self.assertRaisesRegex(ValueError, 'unexpected_program_commit'):
                    rehearsal.verify_product('b' * 40)
                (root / 'synthetic.py').write_text('value = 2\n')
                with self.assertRaisesRegex(ValueError, 'package checksum mismatch'):
                    rehearsal.verify_product(commit)

    def test_legacy_path_still_requires_original_product_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'synthetic.py'
            source.write_text('value = 1\n')
            baseline = root / 'baseline.json'
            baseline.write_text(json.dumps(dict(provenance=dict(
                product_code_sha256={'synthetic.py': digest(source)}))))
            with patch.object(rehearsal, 'ROOT', root), patch.object(rehearsal, 'BASELINE', baseline):
                self.assertEqual({'baseline_product_equal': True}, rehearsal.verify_product())
                source.write_text('value = 2\n')
                with self.assertRaisesRegex(ValueError, 'product_code_changed'):
                    rehearsal.verify_product()


if __name__ == '__main__':
    unittest.main()
