"""Full traversal boundaries using synthetic CSV only."""
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from sql_apm.diagnostics import mpp_full_scan as scan


def row(sql='', message='', internal=''):
    fields = [''] * 30
    fields[24], fields[18], fields[21] = sql, message, internal
    return fields


class FullScanTests(unittest.TestCase):
    def test_fields_do_not_split_or_normalize(self):
        batch = 'SELECT 1;\nSELECT 2;'
        self.assertEqual(list(scan.candidates(row(batch, 'duration: 1 ms execute p: ' + batch, batch))),
                         [('sql', batch)])
        self.assertEqual(list(scan.candidates(row('SELECT 1', 'statement: SELECT 2', 'SELECT 3'))),
                         [('sql', 'SELECT 1'), ('inline_distinct', 'SELECT 2'), ('internal', 'SELECT 3')])
        self.assertEqual(list(scan.candidates(row('  ', 'duration: 2 ms statement: '))), [])

    def test_exact_dedup_eviction_and_digest_collision(self):
        with sqlite3.connect(':memory:') as db:
            scan.initialize(db)
            index = scan.ExactIndex(db, cache_bytes=270)
            first = index.observe(b'SELECT 1', {})
            self.assertNotEqual(first, index.observe(b'select 1', {}))
            self.assertEqual(first, index.observe(b'SELECT 1', {}))
            with patch.object(scan.hashlib, 'sha256') as digest:
                # Compute outside the patch to avoid mocking our expected value.
                digest.return_value.hexdigest.return_value = db.execute('SELECT sha256 FROM inputs WHERE id=?', (first,)).fetchone()[0]
                with self.assertRaisesRegex(ValueError, 'collision'):
                    index.observe(b'DIFFERENT', {})

    def fixture(self, directory, malformed=False):
        root = Path(directory)
        (root / '119').mkdir()
        text = io.StringIO(newline='')
        writer = csv.writer(text)
        for item in [row('SELECT 1;\nSELECT 2;'), row('SELECT 1;\nSELECT 2;'),
                     row('', 'statement: SELECT 3'), row('select 3'), row('', '', 'SELECT 4')]:
            writer.writerow(item)
        raw = text.getvalue().encode() + (b'"unfinished' if malformed else b'')
        source = root / '119/source.csv'
        source.write_bytes(raw)
        evidence = root / 'manifest.json'
        evidence.write_text(json.dumps({'clusters': {'119': {'files': [dict(file=source.name,
            bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), counts={'records': 5})]}}}))
        return root, evidence

    def test_full_eof_provenance_frequencies_and_no_sql_export(self):
        with tempfile.TemporaryDirectory() as directory, sqlite3.connect(':memory:') as db:
            root, evidence = self.fixture(directory)
            scan.initialize(db)
            scan.collect(root, evidence, db)
            self.assertEqual(db.execute('SELECT count(*) FROM inputs').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT sum(count) FROM occurrences').fetchone()[0], 5)
            scan.parse_all(db, workers=2)
            result = scan.report(db)
            self.assertEqual(result['unique_states'], {'prototype_parsed': 4})
            self.assertEqual(result['occurrence_states'], {'prototype_parsed': 5})
            self.assertEqual(result['totals']['parsed_unique_batches'], 1)
            self.assertNotIn('SELECT 1', json.dumps(result))
            locator = json.loads(db.execute('SELECT locator FROM inputs WHERE id=1').fetchone()[0])
            self.assertEqual((locator['record'], locator['line_start'], locator['line_end']), (1, 1, 2))

    def test_cross_file_dedup_preserves_each_file_frequency(self):
        with tempfile.TemporaryDirectory() as directory, sqlite3.connect(':memory:') as db:
            root, evidence = self.fixture(directory)
            original = root / '119/source.csv'
            (root / '119/second.csv').write_bytes(original.read_bytes())
            manifest = json.loads(evidence.read_text())
            manifest['clusters']['119']['files'].append(dict(
                manifest['clusters']['119']['files'][0], file='second.csv'))
            evidence.write_text(json.dumps(manifest))
            scan.initialize(db)
            scan.collect(root, evidence, db)
            self.assertEqual(db.execute('SELECT count(*) FROM inputs').fetchone()[0], 4)
            counts = dict(db.execute('SELECT file_key,sum(count) FROM occurrences GROUP BY file_key'))
            self.assertEqual(counts, {'119/source.csv': 5, '119/second.csv': 5})

    def test_malformed_full_tail_cannot_count_as_complete(self):
        with tempfile.TemporaryDirectory() as directory, sqlite3.connect(':memory:') as db:
            root, evidence = self.fixture(directory, malformed=True)
            scan.initialize(db)
            with self.assertRaises(csv.Error):
                scan.collect(root, evidence, db)
            self.assertFalse(scan.get_meta(db, 'collection_complete'))

    def test_source_hash_mismatch_blocks_completion(self):
        with tempfile.TemporaryDirectory() as directory, sqlite3.connect(':memory:') as db:
            root, evidence = self.fixture(directory)
            source = root / '119/source.csv'
            source.write_bytes(source.read_bytes().replace(b'SELECT 4', b'SELECT 5'))
            scan.initialize(db)
            with self.assertRaisesRegex(ValueError, 'evidence mismatch'):
                scan.collect(root, evidence, db)
            self.assertFalse(scan.get_meta(db, 'collection_complete'))

    def test_worker_crash_restarts_and_batch_failure_is_whole(self):
        process = scan.ParserProcess()
        try:
            process.submit(1, b'SELECT 1; ANALYZE ROOTPARTITION;')
            while (result := process.receive()) is None:
                time.sleep(.01)
            self.assertEqual(result[1]['state'], 'unsupported')
            self.assertNotIn('comparison_digest', result[1])
            process.submit(2, b'SELECT 2')
            process.process.kill()
            process.process.join()
            # Discard a response if it won the race; then simulate timed-out pending work.
            if process.connection.poll():
                try:
                    process.connection.recv()
                except (EOFError, OSError):
                    pass
            result = process.receive()
            self.assertEqual(result[1]['state'], 'worker_failed')
            process.submit(3, b'SELECT 3')
            while (result := process.receive()) is None:
                time.sleep(.01)
            self.assertEqual(result[1]['state'], 'prototype_parsed')
        finally:
            process.close()

    def test_timeout_restarts_worker(self):
        import os
        import signal
        process = scan.ParserProcess(timeout=.02)
        try:
            process.start()
            os.kill(process.process.pid, signal.SIGSTOP)
            process.submit(1, b'SELECT 1')
            time.sleep(.03)
            result = process.receive()
            self.assertEqual(result[1]['state'], 'probe_timeout')
            process.timeout = 5
            process.submit(2, b'SELECT 2')
            while (result := process.receive()) is None:
                time.sleep(.01)
            self.assertEqual(result[1]['state'], 'prototype_parsed')
        finally:
            process.close()

    def test_size_limit_records_explicit_result(self):
        with sqlite3.connect(':memory:') as db:
            scan.initialize(db)
            db.execute('INSERT INTO inputs VALUES (1,?,?,?,NULL)', ('fake', b'x' * (scan.MAX_BYTES + 1), '{}'))
            scan.parse_all(db, workers=1)
            result = json.loads(db.execute('SELECT result FROM inputs').fetchone()[0])
            self.assertEqual(result['state'], 'probe_size_limit')


if __name__ == '__main__':
    unittest.main()
