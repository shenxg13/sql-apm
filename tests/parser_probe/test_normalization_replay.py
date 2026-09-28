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
