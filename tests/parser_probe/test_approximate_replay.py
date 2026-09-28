import json
import sqlite3
import unittest

from sql_apm.diagnostics.mpp_approximate_replay import sanitized_worker, comparison, replay
from sql_apm.diagnostics.mpp_full_scan import initialize, ExactIndex
from sql_apm.diagnostics.mpp_adapter_probe import worker


class ApproximateReplayTests(unittest.TestCase):
    def test_private_text_not_exported(self):
        sql = "SELECT private_table.column FROM private_table WHERE a = 42 AND b = 'secret"
        result = sanitized_worker({'sql': sql})
        encoded = json.dumps(result)
        self.assertNotIn('private_table', encoded)
        self.assertNotIn('secret', encoded)
        self.assertTrue(result['source_preserved'])
        self.assertIn('unclosed_string', result['approximate_diagnostics'])
        self.assertEqual(result['state'], 'approximate_available')
        self.assertEqual(comparison(worker({'sql': sql}), result), 'original_refusal_preserved')

    def test_regression_and_failure_are_detected(self):
        old = {'state': 'unsupported', 'reason': 'lexical_unbalanced_bracket'}
        actual = sanitized_worker({'sql': 'SELECT ('})
        self.assertEqual(comparison(old, actual), 'original_refusal_preserved')
        for field, value in [('parser_reason', 'other'), ('structure_fingerprint_absent', False),
                             ('source_preserved', False), ('fingerprint', None), ('observation_only', False)]:
            self.assertEqual(comparison(old, dict(actual, **{field: value})), 'regression')
        self.assertEqual(comparison(old, {'state': 'probe_timeout'}), 'regression')

    def test_real_worker_replays_refusal_and_recovered_control(self):
        with sqlite3.connect(':memory:') as source, sqlite3.connect(':memory:') as old, sqlite3.connect(':memory:') as dest:
            initialize(source)
            old.execute('CREATE TABLE results(input_id INTEGER PRIMARY KEY,result TEXT)')
            index = ExactIndex(source)
            for sql in ['SELECT (', ';', 'ANALYZE ROOTPARTITION t']:
                uid = index.observe(sql.encode(), {'record': 1})
                source.execute('UPDATE inputs SET result=? WHERE id=?', (json.dumps({'state':'unsupported'}), uid))
                source.execute('INSERT INTO occurrences VALUES (?,?,?,?)', (uid, 'synthetic', 'sql', 2))
                old.execute('INSERT INTO results VALUES (?,?)', (uid, json.dumps(worker({'sql': sql}))))
            result = replay(source, old, dest, workers=1)
            self.assertEqual(result['completed'], 3)
            self.assertEqual(result['comparisons']['original_refusal_preserved'], 2)
            self.assertEqual(result['comparisons']['recovered_structure_unchanged'], 1)
            self.assertEqual(result['states']['approximate_available'], 1)
            self.assertEqual(result['states']['approximate_unavailable'], 1)
            self.assertEqual(result['changes'], [])


if __name__ == '__main__':
    unittest.main()
