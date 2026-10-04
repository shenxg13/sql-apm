"""Deep result audit must compare complete payloads without Python recursion."""
import unittest

from sql_apm.diagnostics.scanning_equivalence import equal_results
from sql_apm.sql.structure import loads, dumps


class EquivalenceTests(unittest.TestCase):
    def test_deep_payload_equality_and_difference(self):
        left = dict(normalized=loads('{"child":' * 2000 + '[1,"中文"]' + '}' * 2000),
                    fingerprint=dict(state='reliable', reason=None, value='synthetic'),
                    approximate=None)
        right = loads(dumps(left))
        with self.assertRaises(RecursionError):
            _ = left == right
        self.assertTrue(equal_results(left, right))
        bottom = right['normalized']
        for _ in range(2000):
            bottom = bottom['child']
        bottom[0] = 2
        self.assertFalse(equal_results(left, right))
        bottom[0] = 1
        right['fingerprint']['reason'] = 'different_reason'
        self.assertFalse(equal_results(left, right))

    def test_approximate_and_scalar_types_are_not_ignored(self):
        self.assertFalse(equal_results(dict(approximate=dict(value='a')),
                                       dict(approximate=dict(value='b'))))
        self.assertFalse(equal_results(dict(value=True), dict(value=1)))
