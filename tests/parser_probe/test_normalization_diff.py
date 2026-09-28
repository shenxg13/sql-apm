"""Partition comparison, complete capture, independent audit and privacy."""
import contextlib
import io
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sql_apm.diagnostics import normalization_diff as diff
from sql_apm.diagnostics.mpp_full_scan import initialize
from sql_apm.sql.normalization import Normalizer


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def source(self):
        path = self.root / 'source.sqlite'
        with sqlite3.connect(str(path)) as db:
            initialize(db)
            for uid, raw in enumerate((
                b"SELECT /*+ private_hint_482 */ * FROM private_table_482 WHERE x IN (1,2)",
                b"SELECT /*+ private_hint_482 */ * FROM private_table_482 WHERE x IN (3,4)",
                b'SELECT * FROM private_table_482 WHERE x IN (1,2,3)',
                b'SELECT * FROM private_table_482 WHERE x IN (', b'SELECT \xff'), 1):
                db.execute('INSERT INTO inputs VALUES (?,?,?,?,NULL)',
                           (uid, hashlib.sha256(raw).hexdigest(), raw, '{}'))
                db.execute('INSERT INTO occurrences VALUES (?,?,?,?)', (uid, 'synthetic', 'sql', uid))
        return path

    def snapshot(self, name, groups, states=None, projected=None, structures=None, old=False):
        path = self.root / name
        with sqlite3.connect(str(path)) as db:
            db.executescript('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT); '
                'CREATE TABLE records (input_id INTEGER PRIMARY KEY, sql_sha256 TEXT, state TEXT, '
                'reason TEXT, fingerprint TEXT, structure_sha256 TEXT, v4_projection_sha256 TEXT, '
                'in_lists INTEGER, occurrences INTEGER);')
            context = Normalizer().context
            if old:
                context.update(algorithm_version='sql-normalization/3', parser_version='mpp-adapter-probe/8')
            for key, value in dict(format=diff.FORMAT, complete=True, source_sha256='a'*64,
                    context=context, project_v4=old, records=len(groups)).items():
                diff.put_meta(db, key, value)
            for uid, group in enumerate(groups, 1):
                state = states[uid-1] if states else 'reliable'
                db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)',
                    (uid, str(uid)*64, state, None if state == 'reliable' else 'base_parser_rejected',
                     group, structures[uid-1] if structures else ('b'*64 if group else None),
                     projected[uid-1] if projected else None, 0, uid))
        return path

    def test_partition_comparison_not_hash_string_comparison(self):
        left = self.snapshot('left', ['old-a', 'old-a', 'old-b', 'old-c'])
        right = self.snapshot('right', ['new-a', 'new-a', 'new-b', 'new-c'])
        result = diff.compare(left, right)
        self.assertEqual(result['merged_groups'], 0)
        self.assertEqual(result['split_groups'], 0)
        self.assertEqual(result['groups']['delta'], 0)

    def test_merges_splits_and_status_changes_are_separate(self):
        left = self.snapshot('left', ['a', 'b', 'c', 'c', 'd', None],
                             ['reliable']*5 + ['unsupported_syntax'])
        right = self.snapshot('right', ['x', 'x', 'y', 'z', None, 'w'],
                              ['reliable']*4 + ['unsupported_syntax', 'reliable'])
        r = diff.compare(left, right)
        self.assertEqual((r['merged_groups'], r['split_groups'], r['merged_inputs']), (1, 1, 2))
        self.assertEqual(r['merged_input_occurrences'], 3)
        self.assertEqual((r['status_changes'], r['reason_changes']), (2, 2))
        self.assertEqual(r['merge_examples'], [[1, 2]])
        self.assertEqual(r['split_examples'], [[3, 4]])

    def test_different_contexts_marked_and_every_v4_change_audited(self):
        left = self.snapshot('left', ['a', 'b'], projected=['c'*64]*2, old=True)
        right = self.snapshot('right', ['x', 'x'], structures=['c'*64]*2)
        r = diff.compare(left, right, require_v4=True)
        self.assertTrue(r['context_changed'])
        self.assertTrue(r['v4_acceptance_passed'])
        self.assertEqual(r['v4_audit']['checked'], 2)
        with sqlite3.connect(str(right)) as db:
            db.execute('UPDATE records SET structure_sha256=? WHERE input_id=2', ('d'*64,))
        r = diff.compare(left, right, require_v4=True)
        self.assertFalse(r['v4_acceptance_passed'])
        self.assertEqual(r['v4_audit']['mismatch_examples'], [2])

    def test_incomplete_changed_selection_and_source_are_rejected(self):
        left = self.snapshot('left', ['a', 'b'])
        right = self.snapshot('right', ['a'])
        with self.assertRaisesRegex(diff.EvidenceError, 'selected_inputs_mismatch'):
            diff.compare(left, right)
        with sqlite3.connect(str(right)) as db:
            diff.put_meta(db, 'complete', False)
        with self.assertRaisesRegex(diff.EvidenceError, 'incomplete_or_unknown_snapshot'):
            diff.compare(left, right)
        with sqlite3.connect(str(right)) as db:
            diff.put_meta(db, 'complete', True)
            diff.put_meta(db, 'source_sha256', 'different')
        with self.assertRaisesRegex(diff.EvidenceError, 'source_index_mismatch'):
            diff.compare(left, right)
        with self.assertRaisesRegex(diff.EvidenceError, 'v4_audit_context_mismatch'):
            diff.compare(left, left, require_v4=True)

    def test_projection_changes_only_bare_in_values_and_global_gap(self):
        value = {'SQLAPMBusinessValue': {}}
        tree = {'hints': [{'raw': '/*+ synthetic */', 'gap': 8, 'anchor': {'token_gap': 3}}],
                'statements': [{'A_Expr': {'kind': 'AEXPR_IN', 'rexpr': {'List': {'items': [value]*10}}}},
                    {'A_Expr': {'kind': 'AEXPR_IN', 'rexpr': {'List': {'items': [value, {'ColumnRef': {}}]}}}},
                    {'Other': {'rexpr': {'List': {'items': [value]*10}}}}]}
        projected, changes = diff.project_v4(tree)
        self.assertEqual(projected['statements'][0]['A_Expr']['rexpr']['List']['items'], {'SQLAPMInBucket': '2-10'})
        self.assertEqual(projected['statements'][1:], tree['statements'][1:])
        self.assertEqual(projected['hints'], [{'raw': '/*+ synthetic */', 'anchor': {'token_gap': 3}}])
        self.assertEqual(changes, {'global_gaps_removed': 1, 'in_lists_bucketed': 1})
        self.assertEqual(len(tree['statements'][0]['A_Expr']['rexpr']['List']['items']), 10)
        for length, label in ((1,'1'), (2,'2-10'), (11,'11-100'), (100,'11-100'), (101,'>100')):
            tree['statements'][0]['A_Expr']['rexpr']['List']['items'] = [value]*length
            result, _ = diff.project_v4(tree)
            self.assertEqual(result['statements'][0]['A_Expr']['rexpr']['List']['items'], {'SQLAPMInBucket': label})

    def test_actual_frozen_v3_trees_project_to_actual_v4_results(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/normalization-v3-preserved.json').read_text())
        engine = Normalizer()
        for case in fixture['projection_cases']:
            with self.subTest(sql=case['sql']):
                projected, _ = diff.project_v4(case['normalized'])
                actual = engine.normalize(case['sql'])
                self.assertEqual(actual['fingerprint']['state'], 'reliable')
                self.assertEqual(projected, actual['normalized'])

    def test_capture_selection_reproducibility_and_privacy(self):
        source = self.source()
        a, b = self.root/'a.sqlite', self.root/'b.sqlite'
        result = diff.capture(source, a, workers=1, markers=(b' in ',), ignore_case=True)
        self.assertEqual(result['records'], 4)
        diff.capture(source, b, workers=2, ids={1,2,3,4})
        comparison = diff.compare(a, b)
        self.assertEqual(comparison['status_changes'], 0)
        self.assertEqual(comparison['split_groups'], 0)
        with diff.readonly(a) as db:
            text = '\n'.join(db.iterdump()) + json.dumps(comparison)
            self.assertTrue(diff.meta(db)['complete'])
        for secret in ('private_hint_482', 'private_table_482', 'bytes_base64'):
            self.assertNotIn(secret, text)
        with self.assertRaises(FileExistsError):
            diff.capture(source, a, workers=1)

    def test_missing_id_or_corrupt_input_never_completes_snapshot(self):
        source = self.source()
        path = self.root/'missing.sqlite'
        with self.assertRaisesRegex(diff.EvidenceError, 'selected_ids_missing'):
            diff.capture(source, path, workers=1, ids={999})
        with diff.readonly(path) as db:
            self.assertFalse(diff.meta(db)['complete'])
        with sqlite3.connect(str(source)) as db:
            db.execute('UPDATE inputs SET sha256=? WHERE id=1', ('bad',))
        with self.assertRaisesRegex(diff.EvidenceError, 'input_digest_mismatch'):
            diff.capture(source, self.root/'corrupt.sqlite', workers=1)

    def test_all_selection_and_cli_json_text_outputs(self):
        source = self.source()
        snapshot = self.root/'all.sqlite'
        result = diff.capture(source, snapshot, workers=1)
        self.assertEqual(result['records'], 5)
        self.assertEqual(result['states']['unsupported_syntax'], 2)
        for flags in ([], ['--text']):
            stream = io.StringIO()
            with patch('sys.argv', ['normalization_diff', 'compare', str(snapshot), str(snapshot)] + flags):
                with contextlib.redirect_stdout(stream):
                    self.assertEqual(diff.main(), 0)
            for secret in ('private_hint_482', 'private_table_482', 'bytes_base64'):
                self.assertNotIn(secret, stream.getvalue())
        output = self.root/'report.json'
        with patch('sys.argv', ['normalization_diff', 'compare', str(snapshot), str(snapshot), '--output', str(output)]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(diff.main(), 0)
                self.assertEqual(diff.main(), 1)  # Existing evidence is preserved.
        self.assertEqual(json.loads(output.read_text())['inputs'], 5)

    def test_worker_exception_text_is_never_exported(self):
        with patch.object(diff, '_ENGINE') as engine:
            engine.normalize.side_effect = RuntimeError('private_sql_and_hint')
            safe = diff.capture_worker({'sql': 'select 1'})
        self.assertEqual(safe, {'state': 'capture_failed', 'reason': 'capture_failed'})


if __name__ == '__main__':
    unittest.main()
