"""Counterfactual boundaries, true date thresholds, isolation and privacy."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sql_apm.diagnostics import threshold_coverage as coverage
from sql_apm.diagnostics.threshold_encoding_audit import audit
from sql_apm.diagnostics.mpp_full_scan import initialize, set_meta
from sql_apm.sql.normalization import Normalizer


class ThresholdCoverageTests(unittest.TestCase):
    def compare(self, left, right, scheme):
        a = coverage.coverage_worker(dict(sql=left, schemes=[scheme]))['schemes'][scheme]
        b = coverage.coverage_worker(dict(sql=right, schemes=[scheme]))['schemes'][scheme]
        self.assertIsNotNone(a['fingerprint'])
        self.assertIsNotNone(b['fingerprint'])
        return a['fingerprint'] == b['fingerprint']

    def test_v4_is_exact_product_result_and_candidates_do_not_mutate_it(self):
        sql = "SELECT 1 FROM secret_table_583 WHERE a=9"
        before = Normalizer().normalize(sql)
        result = coverage.coverage_worker(dict(sql=sql, schemes=coverage.SCHEMES))
        self.assertEqual(result['schemes']['v4']['fingerprint'], before['fingerprint']['value'])
        self.assertEqual(Normalizer().normalize(sql), before)
        self.assertNotIn('secret_table_583', json.dumps(result))
        self.assertEqual(set(result['schemes']), set(coverage.SCHEMES))

    def test_positions_join_select_and_case_are_isolated_from_v4(self):
        pairs = [
            ('SELECT 1 FROM t', 'SELECT 2 FROM t'),
            ('SELECT a.x FROM a JOIN b ON a.x=b.x AND b.y=1',
             'SELECT a.x FROM a JOIN b ON a.x=b.x AND b.y=2'),
            ('SELECT CASE WHEN x=1 THEN 2 ELSE 3 END FROM t',
             'SELECT CASE WHEN x=4 THEN 5 ELSE 6 END FROM t'),
        ]
        for a, b in pairs:
            with self.subTest(a=a):
                self.assertFalse(self.compare(a, b, 'v4'))
                self.assertTrue(self.compare(a, b, 'positions'))
                self.assertTrue(self.compare(a, b, 'positions_functions'))

    def test_unknown_function_controls_are_explicit_counterfactual_only(self):
        a, b = "SELECT custom_func('one')", "SELECT custom_func('two')"
        self.assertFalse(self.compare(a, b, 'v4'))
        self.assertFalse(self.compare(a, b, 'positions'))
        self.assertTrue(self.compare(a, b, 'unqualified_functions'))
        self.assertFalse(self.compare(a.replace('custom_func', 'app.custom_func'),
                                     b.replace('custom_func', 'app.custom_func'), 'unqualified_functions'))
        self.assertFalse(self.compare("SELECT custom_func('t'::regclass)",
                                     "SELECT custom_func('u'::regclass)", 'unqualified_functions'))

    def test_structural_candidates_preserve_controls_and_identity(self):
        for scheme in coverage.SCHEMES[:-1]:
            for a, b in [('SET work_mem=1; SELECT 1', 'SET work_mem=2; SELECT 1'),
                         ('SELECT x FROM t LIMIT 1', 'SELECT x FROM t LIMIT 2'),
                         ('SELECT x FROM t', 'SELECT x FROM u'),
                         ('SELECT /*+ first */ 1', 'SELECT /*+ second */ 1'),
                         ('SELECT true', 'SELECT false')]:
                with self.subTest(scheme=scheme, a=a):
                    self.assertFalse(self.compare(a, b, scheme))

    def test_lexical_control_in_lists_comments_and_uncertainty(self):
        self.assertTrue(self.compare('SELECT * FROM t WHERE a IN (1)',
                                     'select /* ordinary */ * from T where A in (2,3)', 'tidb_lexical'))
        self.assertTrue(self.compare('SELECT 1 LIMIT 5', 'SELECT 2 LIMIT 10', 'tidb_lexical'))
        self.assertFalse(self.compare('SELECT "A" FROM t', 'SELECT "a" FROM t', 'tidb_lexical'))
        for sql in ("SELECT 'private_unclosed", 'SELECT (', 'SELECT \udc80', ';'):
            result = coverage.lexical_digest(sql)
            self.assertEqual(result['state'], 'lexical_refused')
            self.assertIsNone(result['fingerprint'])
            self.assertNotIn('private_unclosed', json.dumps(result))

    def test_invalid_encoding_and_nul_are_refused_in_every_lexical_context(self):
        contexts = (
            'SELECT $${}$$', 'SELECT $body${}$body$', "SELECT '{}'",
            "SELECT E'{}'", "SELECT B'{}'", "SELECT X'{}'", "SELECT N'{}'",
            "SELECT U&'{}'", 'SELECT "{}"', 'SELECT U&"{}"', 'SELECT a{}b',
            'SELECT 1 -- {}\n', 'SELECT /* {} */ 1', 'SELECT /*+ {} */ 1',
            'SELECT /* outer /* {} */ outer */ 1', "SELECT 'unclosed{}",
        )
        for raw in (b'\x80', b'\xc0\xaf', b'\xed\xa0\x80', b'\xe2\x82', b'\x00'):
            bad = raw.decode('utf-8', 'surrogateescape')
            for context in contexts:
                with self.subTest(raw=raw.hex(), context=context):
                    result = coverage.lexical_digest(context.format(bad))
                    self.assertEqual(result, dict(state='lexical_refused',
                        reason='invalid_encoding_or_nul', fingerprint=None))

    def test_legal_dollar_literals_still_fold_and_preserve_other_tokens(self):
        expected = coverage.lexical_digest('SELECT $$ok$$')
        self.assertEqual(expected['state'], 'lexical')
        self.assertIsNotNone(expected['fingerprint'])
        for tag in ('$$', '$body$'):
            for value in ('', '中文😀', "quote' -- /* */ ;", r'\x00'):
                with self.subTest(tag=tag, value=value):
                    self.assertEqual(coverage.lexical_digest('SELECT ' + tag + value + tag), expected)
        self.assertNotEqual(coverage.lexical_digest('SELECT $$ok$$ FROM t')['fingerprint'],
                            coverage.lexical_digest('SELECT $$ok$$ FROM u')['fingerprint'])

    def test_invalid_dollar_inputs_cannot_contribute_occurrences_or_active_days(self):
        for tag in (b'$$', b'$body$'):
            for bad in (b'\x80', b'\x00'):
                for mixed in (False, True):
                    with self.subTest(tag=tag, bad=bad.hex(), mixed=mixed), tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        key = '120/gpdb-2026-09-01_000000.csv'
                        with sqlite3.connect(str(root/'source')) as db:
                            initialize(db)
                            set_meta(db, 'collection_complete', True)
                            set_meta(db, 'record_dates', True)
                            db.execute('INSERT INTO files VALUES (?,?)', (key, '{}'))
                            raws = [b'SELECT ' + tag + bad + tag]
                            if mixed:
                                raws.append(b'SELECT ' + tag + b'ok' + tag)
                            for uid, raw in enumerate(raws, 1):
                                db.execute('INSERT INTO inputs VALUES (?,?,?,?,?)',
                                    (uid, hashlib.sha256(raw).hexdigest(), raw, '{}', '{"state":"synthetic"}'))
                                days = [7] if mixed and uid == 1 else range(1, 7 if mixed else 8)
                                db.execute('INSERT INTO occurrences VALUES (?,?,?,?)', (uid, key, 'sql', 5*len(days)))
                                for day in days:
                                    db.execute('INSERT INTO occurrence_dates VALUES (?,?,?,?,?)',
                                        (uid, key, '2026-09-%02d' % day, 'sql', 5))
                        before = coverage.file_sha(root/'source')
                        with contextlib.redirect_stdout(io.StringIO()):
                            result = coverage.capture(root/'source', root/'out', root/'cache', workers=1)
                        self.assertEqual(coverage.file_sha(root/'source'), before)
                        cluster = result['clusters']['120']
                        self.assertEqual(cluster['input_occurrences'], 35)
                        for scheme, item in cluster['schemes'].items():
                            self.assertEqual(item['fingerprinted_occurrences'], 30 if mixed else 0)
                            self.assertTrue(all(t['groups'] == t['input_occurrences'] ==
                                t['fraction_all_input_occurrences'] == t['fraction_fingerprinted_occurrences'] == 0
                                for t in item['thresholds'].values()))
                        with sqlite3.connect(str(root/'cache')) as db:
                            self.assertEqual(db.execute('SELECT state,reason,fingerprint FROM records '
                                "WHERE input_id=1 AND scheme='tidb_lexical'").fetchone(),
                                ('lexical_refused', 'invalid_encoding_or_nul', None))

    def source(self, path):
        with sqlite3.connect(str(path)) as db:
            initialize(db)
            set_meta(db, 'collection_complete', True)
            set_meta(db, 'record_dates', True)
            for uid, raw in enumerate((b'SELECT 1', b'SELECT 2', b"SELECT 'private_cut"), 1):
                db.execute('INSERT INTO inputs VALUES (?,?,?,?,?)',
                    (uid, hashlib.sha256(raw).hexdigest(), raw, '{}', '{"state":"synthetic"}'))
            # Same raw input in both clusters must not borrow days or counts.
            # All 7 dates deliberately live in one filename; do not infer dates.
            for cluster, number in (('119', 7), ('120', 6)):
                key = cluster + '/gpdb-2026-09-01_000000.csv'
                db.execute('INSERT INTO files VALUES (?,?)', (key, '{}'))
                for uid, count in ((1, 5), (2, 200), (3, 2)):
                    db.execute('INSERT INTO occurrences VALUES (?,?,?,?)', (uid, key, 'sql', number*count))
                    for day in range(1, number + 1):
                        db.execute('INSERT INTO occurrence_dates VALUES (?,?,?,?,?)',
                            (uid, key, '2026-09-%02d' % day, 'sql', count))

    def test_isolated_capture_switching_denominators_and_no_sql_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, cache = root/'source', root/'out.json', root/'cache'
            self.source(source)
            before = coverage.file_sha(source)
            with contextlib.redirect_stdout(io.StringIO()):
                result = coverage.capture(source, output, cache, ('v4', 'positions'), workers=2)
            self.assertEqual(before, coverage.file_sha(source))
            a, b = result['clusters']['119'], result['clusters']['120']
            self.assertEqual(a['input_occurrences'], 1449)
            v4 = a['schemes']['v4']['thresholds']
            self.assertEqual([v4[str(n)]['groups'] for n in coverage.THRESHOLDS], [2, 1, 1])
            merged = a['schemes']['positions']['thresholds']
            self.assertEqual([merged[str(n)]['groups'] for n in coverage.THRESHOLDS], [1, 1, 1])
            self.assertAlmostEqual(merged['1000']['fraction_all_input_occurrences'], 1435/1449)
            self.assertEqual(merged['1000']['fraction_fingerprinted_occurrences'], 1)
            self.assertTrue(all(v['groups'] == 0 for v in b['schemes']['positions']['thresholds'].values()))
            with sqlite3.connect(str(cache)) as db:
                text = '\n'.join(db.iterdump()) + output.read_text()
            for secret in ('SELECT', 'private_cut', 'bytes_base64'):
                self.assertNotIn(secret, text)
            self.assertEqual(set(a['schemes']), {'v4', 'positions'})
            with self.assertRaisesRegex(ValueError, 'output_exists'):
                coverage.capture(source, output, cache, workers=1)

    def test_encoding_audit_detects_old_success_and_rejects_unrelated_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source(root/'source')
            raw = b'SELECT $$\x80$$'
            with sqlite3.connect(str(root/'source')) as db:
                db.execute('UPDATE inputs SET sha256=?,sql=? WHERE id=1',
                           (hashlib.sha256(raw).hexdigest(), raw))
            with contextlib.redirect_stdout(io.StringIO()):
                coverage.capture(root/'source', root/'baseline.json', root/'baseline', workers=1)
            with sqlite3.connect(str(root/'baseline')) as db:
                db.execute("UPDATE records SET state='lexical',reason=NULL,fingerprint=? "
                           "WHERE scheme='tidb_lexical' AND input_id=1",
                           (coverage.lexical_digest('SELECT $$ok$$')['fingerprint'],))
            with contextlib.redirect_stdout(io.StringIO()):
                result = audit(root/'source', root/'baseline', root/'baseline.json', root/'audit.json', root/'work')
            self.assertEqual(result['invalid_inputs'], 1)
            self.assertEqual(result['previously_fingerprinted_invalid_inputs'], 1)
            self.assertFalse(result['threshold_metrics_unchanged_by_guard'])
            self.assertEqual(result['invalid_occurrences'], {'119': 35, '120': 30})
            self.assertEqual(result['replay']['scheme_comparisons'], 15)
            self.assertEqual(result['replay']['reason_changes'], {'None -> invalid_encoding_or_nul': 1})
            self.assertNotIn('SELECT', (root/'audit.json').read_text())
            self.assertNotIn('private_cut', (root/'audit.json').read_text())
            with sqlite3.connect(str(root/'baseline')) as db:
                db.execute("UPDATE records SET fingerprint='unexpected' WHERE scheme='v4' AND input_id=2")
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'unexpected_replay_change'):
                audit(root/'source', root/'baseline', root/'baseline.json', root/'failed.json', root/'retry')
            self.assertFalse((root/'failed.json').exists())

    def test_exact_thresholds_distinct_dates_and_multiple_fields(self):
        with sqlite3.connect(':memory:') as source, sqlite3.connect(':memory:') as cache:
            initialize(source)
            set_meta(source, 'record_dates', True)
            key = '119/gpdb-2026-09-01_000000.csv'
            source.execute('INSERT INTO files VALUES (?,?)', (key, '{}'))
            cache.execute('CREATE TABLE records(input_id,scheme,state,reason,fingerprint)')
            for uid, total in enumerate((29, 30, 199, 200, 999, 1000), 1):
                cache.execute('INSERT INTO records VALUES (?,?,?,?,?)', (uid, 'v4', 'reliable', None, str(uid)))
                source.execute('INSERT INTO occurrences VALUES (?,?,?,?)', (uid, key, 'sql', total))
                for day in range(1, 8):
                    source.execute('INSERT INTO occurrence_dates VALUES (?,?,?,?,?)',
                        (uid, key, '2026-09-%02d' % day, 'sql', total-6 if day == 1 else 1))
            # Two fields on one extra file/date must not create a seventh active day.
            for uid, day in ((7, '2026-09-01'), (7, '2026-09-02')):
                for field in ('sql', 'internal'):
                    source.execute('INSERT INTO occurrence_dates VALUES (?,?,?,?,?)', (uid, key, day, field, 1000))
            source.execute('INSERT INTO occurrences VALUES (?,?,?,?)', (7, key, 'sql', 4000))
            cache.execute('INSERT INTO records VALUES (7,\'v4\',\'reliable\',NULL,\'7\')')
            result = coverage.summarize(source, cache, ('v4',))['119']['schemes']['v4']['thresholds']
            self.assertEqual([result[str(n)]['groups'] for n in coverage.THRESHOLDS], [5, 3, 1])

    def test_worker_errors_do_not_export_exception_text_or_fallback(self):
        for error, expected in ((ValueError('private_sql_672'), 'coverage_failed'),
                                (MemoryError('private_sql_672'), 'probe_memory_limit')):
            with patch.object(coverage.Normalizer, 'normalize', side_effect=error):
                result = coverage.coverage_worker(dict(sql='SELECT private_sql_672', schemes=coverage.SCHEMES))
            self.assertEqual(result, dict(state=expected))
            self.assertNotIn('private_sql_672', json.dumps(result))
            self.assertNotIn('schemes', result)

    def test_invalid_and_incomplete_inputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source(root/'source')
            for schemes in ((), ('unknown',), ('v4', 'v4')):
                with self.assertRaisesRegex(ValueError, 'invalid_schemes'):
                    coverage.capture(root/'source', root/'out', root/'cache', schemes)
            with sqlite3.connect(str(root/'source')) as db:
                set_meta(db, 'collection_complete', False)
            with self.assertRaisesRegex(ValueError, 'incomplete_source'):
                coverage.capture(root/'source', root/'out', root/'cache')
            self.assertFalse((root/'out').exists())


if __name__ == '__main__':
    unittest.main()
