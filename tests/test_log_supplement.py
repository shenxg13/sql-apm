"""Immutable manifests and SQL-free evidence using tiny synthetic logs."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from sql_apm.diagnostics import log_supplement as supplement


class LogSupplementTests(unittest.TestCase):
    def fixture(self, root):
        cluster = root / '120'
        cluster.mkdir()
        source = cluster / 'gpdb-2026-09-13_000000.csv'
        rows = []
        for day in ('2026-09-13', '2026-09-14'):
            row = [''] * 30
            row[0], row[24] = day + ' 00:00:00 CST', 'SELECT private_marker_672'
            rows.append(row)
        with source.open('w', newline='') as stream:
            csv.writer(stream).writerows(rows)
        entry = dict(file=source.name, bytes=source.stat().st_size,
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            counts=dict(records=2, duration_records=0, inline_equals_sql=0))
        previous = root / 'old.json'
        previous.write_text(json.dumps(dict(clusters={'120': dict(files=[entry])})))
        return source, previous, entry

    def test_preserves_old_entry_and_checks_real_dates_full_tail_and_privacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, previous, entry = self.fixture(root)
            (source.parent / 'gpdb-2026-09-14_000000.csv').write_bytes(source.read_bytes())
            before = previous.read_bytes(), source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                result = supplement.manifest(root, previous)
            self.assertEqual(result['clusters']['120']['files'][0], entry)
            self.assertEqual(len(result['clusters']['120']['files']), 2)
            self.assertEqual(result['clusters']['120']['files'][1]['counts']['records'], 2)
            check = result['validation']['120'][0]
            self.assertEqual(check['records_by_date'], {'2026-09-13': 1, '2026-09-14': 1})
            self.assertFalse(check['filename_date_matches_records'])
            self.assertTrue(check['read_to_eof'])
            self.assertNotIn('private_marker_672', json.dumps(result))
            self.assertEqual(before, (previous.read_bytes(), source.read_bytes()))

    def test_changed_missing_and_malformed_input_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, previous, _ = self.fixture(root)
            original = source.read_bytes()
            source.write_bytes(original.replace(b'672', b'673'))
            with self.assertRaisesRegex(ValueError, 'historical_evidence_mismatch'):
                supplement.manifest(root, previous)
            source.write_bytes(original + b'"unfinished')
            with self.assertRaises(csv.Error):
                supplement.manifest(root, previous)
            source.unlink()
            with self.assertRaisesRegex(ValueError, 'historical_file_missing'):
                supplement.manifest(root, previous)

    def test_write_never_overwrites_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report.json'
            supplement.write_json(output, dict(count=1))
            with self.assertRaises(FileExistsError):
                supplement.write_json(output, dict(count=2))
            self.assertEqual(json.loads(output.read_text()), dict(count=1))


if __name__ == '__main__':
    unittest.main()
