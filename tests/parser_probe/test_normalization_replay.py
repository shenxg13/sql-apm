"""The replay evidence must reject structural loss and omit supplied SQL values."""
import unittest

from sql_apm.diagnostics.normalization_replay import delta_audit, sanitized_worker
from sql_apm.sql.structure import dumps


class NormalizationReplayTests(unittest.TestCase):
    def test_conservation_rejects_loss_and_unexpected_replacement(self):
        marker = {'SQLAPMBusinessValue': {}}
        self.assertEqual(delta_audit({'A_Const': {'ival': {'ival': 2}}}, marker), {'': 1})
        for before, after in [({'node': 1}, {}), ([1, 2], [1]),
                              ({'ColumnRef': {}}, marker), ({'A_Const': {'boolval': {}}}, marker)]:
            with self.assertRaises(ValueError):
                delta_audit(before, after)

    def test_only_approved_in_buckets_and_global_hint_gap_can_change(self):
        literal = {'A_Const': {'ival': {'ival': 1}}}
        before = {'A_Expr': {'kind': 'AEXPR_IN', 'rexpr': {'List': {'items': [literal, literal]}}}}
        after = {'A_Expr': {'kind': 'AEXPR_IN', 'rexpr': {'List': {'items': {'SQLAPMInBucket': '2-10'}}}}}
        with self.assertRaisesRegex(ValueError, 'unexpected_in_bucket'):
            delta_audit(before, after)
        wrap = lambda node: {'SelectStmt': {'whereClause': node}}
        self.assertEqual(delta_audit(wrap(before), wrap(after)),
                         {'/SelectStmt/whereClause/A_Expr/rexpr/List/items/in_bucket': 1})
        for wrong in ('1', '11-100', '>100'):
            after['A_Expr']['rexpr']['List']['items']['SQLAPMInBucket'] = wrong
            with self.assertRaisesRegex(ValueError, 'incorrect_in_bucket'):
                delta_audit(wrap(before), wrap(after))
        after['A_Expr']['rexpr']['List']['items']['SQLAPMInBucket'] = '2-10'
        for field in ('targetList', 'havingClause', 'whereClause'):
            filtered = lambda node: {'SelectStmt': {field: {'FuncCall': {'agg_filter': node}}}}
            with self.assertRaisesRegex(ValueError, 'unexpected_in_bucket'):
                delta_audit(filtered(before), filtered(after))
        before['A_Expr']['rexpr']['List']['items'][0] = {'ColumnRef': {}}
        with self.assertRaisesRegex(ValueError, 'unexpected_in_bucket'):
            delta_audit(wrap(before), wrap(after))
        before = {'hints': [{'gap': 5, 'anchor': {'token_gap': 3}, 'raw': 'synthetic'}]}
        after = {'hints': [{'anchor': {'token_gap': 3}, 'raw': 'synthetic'}]}
        self.assertEqual(delta_audit(before, after), {'/hints/*/gap_removed': 1})
        after['hints'][0]['anchor']['token_gap'] = 2
        with self.assertRaisesRegex(ValueError, 'unexpected_value_change'):
            delta_audit(before, after)

    def test_filter_preservation_nested_where_and_conflict_update_are_audited(self):
        for sql, expected in (
            ('SELECT sum(y) FILTER (WHERE x IN (1,2)) FROM t', 0),
            ('SELECT sum(y) FILTER (WHERE EXISTS (SELECT 1 FROM u WHERE x IN (1,2))) FROM t', 1),
            ('INSERT INTO t VALUES (1) ON CONFLICT (x) DO UPDATE SET y=2 WHERE x IN (1,2)', 1),
            ('INSERT INTO t VALUES (1) ON CONFLICT (x) WHERE x IN (1,2) DO UPDATE SET y=2', 0),
        ):
            with self.subTest(sql=sql):
                result = sanitized_worker({'sql': sql})
                self.assertEqual(result['state'], 'reliable')
                self.assertEqual(sum(n for path, n in result['replacement_paths'].items()
                                     if path.endswith('/in_bucket')), expected)

    def test_export_contains_no_input_values(self):
        result = sanitized_worker({'sql': "select * from private_object where secret_column='sensitive_value'"})
        text = dumps(result)
        for value in ('private_object', 'secret_column', 'sensitive_value', 'bytes_base64'):
            self.assertNotIn(value, text)
        self.assertEqual(result['state'], 'reliable')
        self.assertTrue(result['repeat_stable'])
        self.assertTrue(result['format_stable'])
        self.assertTrue(result['source_preserved'])
        self.assertEqual(sum(result['replacement_paths'].values()), 1)

    def test_refusal_remains_separate(self):
        result = sanitized_worker({'sql': 'select * from t where x in(1,'})
        self.assertEqual(result['state'], 'unsupported_syntax')
        self.assertIsNone(result['value'])
        self.assertTrue(result['structural_absent'])
        self.assertEqual(result['approximate_state'], 'available')


if __name__ == '__main__':
    unittest.main()
