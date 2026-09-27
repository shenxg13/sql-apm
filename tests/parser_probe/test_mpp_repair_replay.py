"""Repair comparison distinguishes preserved structure, improvements and regressions."""
import hashlib
import json
import sqlite3
import unittest

from sql_apm.diagnostics.mpp_adapter_probe import worker
from sql_apm.diagnostics.mpp_full_scan import initialize, set_meta
from sql_apm.diagnostics.mpp_full_repair import outcome, replay
from sql_apm.sql.mpp_parser import VERSION


class RepairReplayTests(unittest.TestCase):
    def test_version_comparison_does_not_change_current_tree(self):
        result = worker({'sql': 'SELECT 1', 'comparison_version': 'previous', 'synthetic': True})
        self.assertEqual(result['tree']['version'], VERSION)
        self.assertNotEqual(result['comparison_digest'], result['comparison_digest_at_version'])
        old = {'state': 'prototype_parsed', 'comparison_digest': result['comparison_digest_at_version']}
        self.assertEqual(outcome(old, result), 'same_structure')
        self.assertEqual(outcome(old, dict(result, comparison_digest_at_version='different')), 'changed_structure')
        self.assertEqual(outcome(old, {'state': 'unsupported', 'reason': 'example'}), 'regressed_state')

    def test_small_replay_keeps_baseline_and_occurrences(self):
        with sqlite3.connect(':memory:') as source, sqlite3.connect(':memory:') as destination:
            initialize(source)
            set_meta(source, 'context', {'prototype_version': 'previous'})
            normal = worker({'sql': 'SELECT 1', 'comparison_version': 'previous'})
            old_success = {'state': 'prototype_parsed', 'comparison_digest': normal['comparison_digest_at_version']}
            rows = [('SELECT 1', old_success),
                    ('ALTER TABLE private_table TRUNCATE PARTITION p', {'state': 'unsupported', 'reason': 'base_parser_rejected'}),
                    ('SELECT (', {'state': 'unsupported', 'reason': 'lexical_unbalanced_bracket'})]
            for uid, (sql, result) in enumerate(rows, 1):
                raw = sql.encode()
                source.execute('INSERT INTO inputs VALUES (?,?,?,?,?)',
                               (uid, hashlib.sha256(raw).hexdigest(), raw, '{}', json.dumps(result)))
                source.execute('INSERT INTO occurrences VALUES (?,?,?,?)', (uid, '119/synthetic.csv', 'sql', 7))
            before = source.execute('SELECT id,result FROM inputs').fetchall()
            result = replay(source, destination, workers=2)
            self.assertEqual(result['comparisons'], {'same_structure': 1, 'repaired': 1, 'same_failure': 1})
            self.assertEqual(result['changes'][0]['occurrences'], 7)
            self.assertEqual(source.execute('SELECT id,result FROM inputs').fetchall(), before)
            self.assertNotIn('private_table', json.dumps(result))
            self.assertEqual(destination.execute('SELECT count(*) FROM results').fetchone()[0], 3)
