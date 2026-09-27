"""Approximate grouping remains separate from reliable SQL identity."""
import base64
import json
import subprocess
import sys
import unittest

from sql_apm.sql.approximate import fingerprint, MAX_BYTES, RULES_DIGEST


def fp(sql, reason='lexical_unbalanced_bracket'):
    return fingerprint(sql, structural_reason=reason)


class ApproximateTests(unittest.TestCase):
    def test_simple_business_values_merge_without_repair(self):
        a = fp('SELECT * FROM t WHERE id IN (1001, 1002,')
        b = fp(' select /* ordinary */ * from T where ID in (8, $3,')
        self.assertEqual(a['value'], b['value'])
        self.assertEqual(a['replacements'], 2)
        self.assertEqual(a['normalized']['open_brackets'], ['('])
        self.assertEqual(a['kind'], 'approximate')
        self.assertTrue(a['observation_only'])
        self.assertEqual(a['normalized']['completeness'], 'unverified')
        self.assertEqual(a['structural_reason'], 'lexical_unbalanced_bracket')

    def test_predicate_and_assignment_values(self):
        for left, right in [
            ("SELECT * FROM t WHERE id = 1 AND x = '", "SELECT * FROM t WHERE id = 900 AND x = '"),
            ('UPDATE t SET a = 1, b = (', 'UPDATE t SET a = $8, b = ('),
            ("UPDATE t SET a = 'one', b = (", "UPDATE t SET a = 'two', b = ("),
            ('SELECT * FROM t WHERE (s.id >= 1 AND b = (', 'SELECT * FROM t WHERE (s.id >= 2 AND b = ('),
        ]:
            with self.subTest(sql=left):
                self.assertEqual(fp(left)['value'], fp(right)['value'])
                self.assertGreater(fp(left)['replacements'], 0)

    def test_unknown_positions_and_control_values_stay_distinct(self):
        samples = [
            'SET work_mem = 10; SELECT (', 'SELECT 10, (',
            'SELECT * FROM t LIMIT 10; SELECT (', 'SELECT * FROM t OFFSET 10; SELECT (',
            'SELECT * FROM t WHERE custom(10) = 0 AND (',
            'SELECT * FROM t WHERE custom(x = 10) AND (',
            'SELECT * FROM t WHERE custom((SELECT x FROM u WHERE id = 10)) AND (',
            'SELECT * FROM t WHERE (id = 10)::boolean AND (',
            'SELECT * FROM t WHERE id IN (10, 2)::boolean AND (',
            'SELECT * FROM t WHERE x::integer = 10 AND (',
            'SELECT * FROM t WHERE x = 10::oid AND (',
            'SELECT * FROM t WHERE x = 10 + 2 AND (',
            'INSERT INTO t VALUES (10,', 'CREATE TABLE t(a int DEFAULT 10,',
            'SELECT CASE WHEN x = 10 THEN 3 END, (',
            'SELECT * FROM t WHERE a IN (10::oid,',
        ]
        for sql in samples:
            with self.subTest(sql=sql):
                self.assertNotEqual(fp(sql)['value'], fp(sql.replace('10', '20'))['value'])
                self.assertEqual(fp(sql)['replacements'], 0)

    def test_preserve_unknown_and_unclosed_literal_tails(self):
        samples = ["SELECT * FROM t WHERE x = 'tail 10", 'SELECT * FROM t /* tail 10',
                   'SELECT * FROM "tail 10', 'SELECT $body$tail 10',
                   r"SELECT * FROM t WHERE x = 'a\b' AND id = 10 AND (",
                   "SELECT U&'tail 10' WHERE id = 10 AND (",
                   "SELECT E'tail 10'; SELECT (", "SELECT $body$tail 10$body$; SELECT ("]
        for sql in samples:
            with self.subTest(sql=sql):
                self.assertNotEqual(fp(sql)['value'], fp(sql.replace('10', '20'))['value'])
                self.assertEqual(fp(sql)['replacements'], 0)
        self.assertIn('opaque', [t[0] for t in fp("SELECT 'tail")['normalized']['tokens']])

    def test_invalid_bytes_never_disappear(self):
        for raw in [b'SELECT x WHERE id = \'\xff\'; SELECT (', b'SELECT /*\xff*/ (',
                    b'SELECT --\xff\n(', b'SELECT na\xffme (', b'SELECT \x00 (']:
            with self.subTest(raw=raw):
                result = fp(raw, 'invalid_encoding')
                self.assertEqual(base64.b64decode(result['source']['bytes_base64']), raw)
                self.assertIn('invalid_encoding_or_nul', result['diagnostics'])
                self.assertEqual(result['replacements'], 0)
                changed = raw.replace(b'\xff', b'\xfe').replace(b'\x00', b'\xfe')
                self.assertNotEqual(result['value'], fp(changed, 'invalid_encoding')['value'])

    def test_batch_order_objects_and_hints_are_preserved(self):
        sql = 'SELECT /*+ hint_a */ * FROM "T" WHERE a = 1; UPDATE t SET b = ('
        for altered in [sql.replace('hint_a', 'hint_b'), sql.replace('"T"', '"t"'),
                        sql.replace('a =', 'b ='), 'UPDATE t SET b = (; SELECT * FROM "T" WHERE a = 1',
                        sql.replace('/*+ hint_a */ *', '* /*+ hint_a */')]:
            self.assertNotEqual(fp(sql)['value'], fp(altered)['value'])
        self.assertNotEqual(fp('SELECT 1; SELECT (')['value'], fp('SELECT (')['value'])

    def test_empty_comments_and_garbage_have_no_fingerprint(self):
        for sql in ['', ' \n', '; /* note */ ;', '/* unfinished', '\xff', '123', 'nonsense']:
            with self.subTest(sql=sql):
                result = fp(sql)
                self.assertIsNone(result['value'])
                self.assertEqual(result['state'], 'unavailable')

    def test_eof_numeric_and_casts_not_assumed_complete(self):
        for sql in ['SELECT * FROM t WHERE id = 10', 'SELECT * FROM t WHERE id IN (10',
                    'SELECT * FROM t WHERE id = $10', 'SELECT * FROM t WHERE id = 10e',
                    'SELECT * FROM t WHERE id = 0x10', "SELECT * FROM t WHERE id = '10'::text AND ("]:
            self.assertNotEqual(fp(sql)['value'], fp(sql.replace('10', '20'))['value'])
            self.assertEqual(fp(sql)['replacements'], 0)

    def test_string_concatenation_and_protected_literals(self):
        for sql in ["SELECT * FROM t WHERE id = '10'\n'20' AND (",
                    "SELECT * FROM t WHERE id = E'10' AND (", "SELECT * FROM t WHERE id = B'10' AND (",
                    "SELECT * FROM t WHERE id = N'10' AND (", "SELECT * FROM t WHERE id = $$10$$ AND ("]:
            self.assertEqual(fp(sql)['replacements'], 0)

    def test_resource_limit_and_deep_stack(self):
        self.assertEqual(fp(b'SELECT '+b'x'*MAX_BYTES)['reason'], 'input_size_limit')
        limit = sys.getrecursionlimit()
        result = fp('SELECT '+ '(' * 3000)
        self.assertEqual(len(result['normalized']['open_brackets']), 3000)
        self.assertEqual(sys.getrecursionlimit(), limit)

    def test_rules_cannot_be_mutated_via_result(self):
        result = fp('SELECT (')
        result['rules']['business_values'].clear()
        again = fp('SELECT (')
        self.assertTrue(again['rules']['business_values'])
        self.assertEqual(again['rules_digest'], RULES_DIGEST)
        self.assertEqual(result['value'], again['value'])

    def test_cross_process_determinism_and_validation(self):
        code = "from sql_apm.sql.approximate import fingerprint; print(fingerprint('SELECT * FROM t WHERE id IN (1,', structural_reason='lexical_unbalanced_bracket')['value'])"
        actual = subprocess.check_output([sys.executable, '-c', code], text=True).strip()
        self.assertEqual(actual, fp('SELECT * FROM t WHERE id IN (1,')['value'])
        with self.assertRaises(ValueError):
            fingerprint('SELECT (', structural_reason='private SQL text')
        with self.assertRaises(ValueError):
            fingerprint('SELECT (', structural_reason='failed', profile='other')


if __name__ == '__main__':
    unittest.main()
