"""Actual record dates survive rotation, raw dedup and multiple evidence fields."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sql_apm.diagnostics import mpp_full_scan as scan
from sql_apm.diagnostics.log_supplement import scan_summary


class RecordDateTests(unittest.TestCase):
    def test_cross_midnight_actual_dates_and_per_day_unique_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'120').mkdir()
            path = root/'120/gpdb-2026-09-19_000000.csv'
            rows = []
            for day in ('2026-09-19', '2026-09-19', '2026-09-20'):
                row = ['']*30
                row[0], row[24], row[21] = day+' 00:00:00 CST', 'SELECT 1', 'SELECT 2'
                rows.append(row)
            with path.open('w', newline='') as stream:
                csv.writer(stream).writerows(rows)
            evidence = root/'manifest.json'
            evidence.write_text(json.dumps(dict(clusters={'120': dict(files=[dict(file=path.name,
                bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                counts=dict(records=3))])})))
            database = root/'index.sqlite'
            with sqlite3.connect(str(database)) as db:
                scan.initialize(db)
                with contextlib.redirect_stdout(io.StringIO()):
                    scan.collect(root, evidence, db, record_dates=True)
                self.assertEqual(list(db.execute('SELECT day,sum(count) FROM occurrence_dates GROUP BY day')),
                                 [('2026-09-19', 4), ('2026-09-20', 2)])
                db.execute('UPDATE inputs SET result=?', ('{"state":"prototype_parsed"}',))
            result = scan_summary(database)['buckets']
            self.assertEqual(result['120']['unique_inputs'], 2)
            self.assertEqual(result['120']['records'], 3)
            self.assertEqual(result['120/2026-09-19']['unique_inputs'], 2)
            self.assertEqual(result['120/2026-09-20']['records'], 1)
            self.assertEqual(result['120/2026-09-20']['input_occurrences'], 2)
            self.assertNotIn('SELECT', json.dumps(result))

    def test_invalid_record_date_never_finishes_collection(self):
        with tempfile.TemporaryDirectory() as directory, sqlite3.connect(':memory:') as db:
            root = Path(directory)
            (root/'120').mkdir()
            row = ['']*30
            row[0], row[24] = 'invalid', 'SELECT 1'
            path = root/'120/source.csv'
            with path.open('w', newline='') as stream:
                csv.writer(stream).writerow(row)
            evidence = root/'manifest.json'
            evidence.write_text(json.dumps(dict(clusters={'120': dict(files=[dict(file=path.name,
                bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                counts=dict(records=1))])})))
            scan.initialize(db)
            with self.assertRaisesRegex(ValueError, 'invalid_record_date'):
                scan.collect(root, evidence, db, record_dates=True)
            self.assertFalse(scan.get_meta(db, 'collection_complete'))


if __name__ == '__main__':
    unittest.main()
