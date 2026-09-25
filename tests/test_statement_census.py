"""Synthetic regressions for evidence boundaries; no production log dependency."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sql_apm.diagnostics.statement_census import diagnose, replay, scan


class StatementCensusTests(unittest.TestCase):
    def test_strings_comments_bodies_and_identifiers_do_not_create_statements(self):
        sql = '''/* outer /* ; COMMIT */ */ SET x = 1;
SELECT ';VACUUM', $$;BEGIN$$, "ALTER;TABLE", E'a\\\';COMMIT'; -- ;END
/* tail */'''
        self.assertEqual(diagnose(sql), (('SET', 'SELECT'), ()))

    def test_category_boundaries_and_opaque_wrappers(self):
        cases = {
            'CREATE UNIQUE INDEX i ON t (a)': 'CREATE INDEX',
            'CREATE TEMP TABLE t AS SELECT 1': 'CREATE TABLE',
            'ALTER TABLE ONLY t ADD COLUMN a integer': 'ALTER TABLE',
            'ALTER INDEX i RENAME TO j': 'ALTER INDEX',
            "COMMIT PREPARED 'transaction'": 'COMMIT PREPARED',
            'COMMIT WORK': 'COMMIT',
            'COMMIT TRANSACTION': 'COMMIT',
            'TRUNCATE TABLE t': 'TRUNCATE',
            'CREATE WRITABLE EXTERNAL TABLE t(a int)': 'CREATE EXTERNAL',
            'SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY': 'SET TRANSACTION',
            'START TRANSACTION': 'START TRANSACTION',
            'ANALYSE t': 'ANALYSE',
            'SET SESSION AUTHORIZATION DEFAULT': 'SET AUTHORIZATION',
            'SET LOCAL search_path = public': 'SET',
            'SET CONSTRAINTS ALL IMMEDIATE': 'SET CONSTRAINTS',
            'EXPLAIN ANALYZE SELECT 1': 'EXPLAIN',
            'WITH t AS (SELECT 1) SELECT * FROM t': 'WITH',
            "DO $$BEGIN RAISE NOTICE 'x'; END$$": 'DO',
            'selectx secret': 'UNKNOWN',
            '"SET" x': 'UNKNOWN',
            'SELECT foo$bar FROM t': 'SELECT',
        }
        for sql, category in cases.items():
            with self.subTest(sql=sql):
                self.assertEqual(diagnose(sql), ((category,), ()))

    def test_uncertain_text_never_yields_partial_category_sequence(self):
        for sql in ('SET x=1; SELECT (', "SET x=1; SELECT 'cut", 'SET x=1; /*',
                    'SET x=1; SELECT $$cut', 'SET x=1; SELECT "cut',
                    "SET x=1; SELECT 'a\\b'", 'SET x=1; SELECT \udc80'):
            with self.subTest(sql=sql):
                seq, reasons = diagnose(sql)
                self.assertFalse(seq)
                self.assertTrue(reasons)

    def test_empty_segments_are_not_commands(self):
        self.assertEqual(diagnose('; /* comment */ ; -- tail\n'), ((), ()))
        self.assertEqual(diagnose('; SET x=1;; COMMIT;'), (('SET', 'COMMIT'), ()))

    def test_csv_full_scan_provenance_and_no_raw_sql_output(self):
        secret = 'private_table_87264'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.csv'
            rows = []
            for sql in ('SET x=1;\nSELECT * FROM '+secret, "SELECT 'cut"):
                row = ['']*30
                row[18], row[24] = 'statement: '+sql, sql
                row[27], row[28] = 'postgres.c', '1685'
                rows.append(row)
            rows[1][21] = 'SELECT 2'
            rows.append(['unexpected'])
            with path.open('w', newline='') as stream:
                csv.writer(stream).writerows(rows)
            raw = path.read_bytes()
            result = scan(path)
            self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(result['bytes'], len(raw))
            self.assertEqual(result['counts']['records'], 3)
            self.assertEqual(result['counts']['unexpected_columns'], 1)
            self.assertEqual(result['counts']['inline_equals_sql'], 2)
            group = result['groups']['sql/postgres.c:1685']
            self.assertEqual(group['categories'], {'SET': 1, 'SELECT': 1})
            self.assertEqual(group['issues'], {'unclosed_string': 1})
            self.assertEqual(group['shapes'], {'batch': 1})
            self.assertIn('internal/postgres.c:1685', result['groups'])
            self.assertNotIn(secret, json.dumps(result))
            self.assertEqual(path.read_bytes(), raw)
            targets = [dict(e, cluster='.', file=path.name) for e in result['examples'].values()]
            for target in targets:
                target['categories'] = list(target['categories'])
                target['issues'] = list(target['issues'])
            evidence = {'replay_targets': targets}
            self.assertEqual(len(replay(Path(directory), evidence)), len(targets))
            targets[0]['sql_sha256'] = '0'*64
            with self.assertRaises(ValueError):
                replay(Path(directory), evidence)

    def test_malformed_csv_fails_instead_of_claiming_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.csv'
            path.write_text('"unterminated')
            with self.assertRaises(csv.Error):
                scan(path)


if __name__ == '__main__':
    unittest.main()
