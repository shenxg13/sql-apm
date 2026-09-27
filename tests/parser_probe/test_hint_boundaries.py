"""R1 Hint ownership collisions and approximate protection regressions."""
import unittest

from sql_apm.sql.mpp_parser import parse
from sql_apm.sql.normalization import Normalizer


class HintBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Normalizer()

    def reliable(self, sql):
        result = self.engine.normalize(sql)
        self.assertEqual(result['fingerprint']['state'], 'reliable')
        return result['fingerprint']['value']

    def test_equal_ast_and_global_gap_cannot_merge_different_hint_owners(self):
        for hint in ('/*+ H */', '--+ H\n'):
            for left, right in (
                ('SELECT (a) FROM t {h}; SELECT b FROM u',
                 'SELECT a FROM t; SELECT {h} b FROM u'),
                ('SELECT (a) {h} FROM t WHERE x=1',
                 'SELECT a FROM t {h} WHERE x=1'),
                # Both statements have the same token count: a statement index
                # and a local gap alone still lose where the parentheses occur.
                ('SELECT (a) {h}, b FROM t',
                 'SELECT a, ({h} b) FROM t'),
            ):
                a, b = left.format(h=hint), right.format(h=hint)
                with self.subTest(a=a, b=b):
                    self.assertEqual(parse(a)['statements'], parse(b)['statements'])
                    self.assertEqual(parse(a)['hints'][0]['gap'], parse(b)['hints'][0]['gap'])
                    self.assertNotEqual(self.reliable(a), self.reliable(b))

    def test_bounded_sweep_of_parentheses_and_all_hint_gaps(self):
        # 9 equivalent AST spellings x every lexical gap x both Hint styles.
        # The independently constructed spellings retain every bracket/slot.
        baseline = parse('SELECT a, b FROM t')['statements']
        fingerprints = set()
        count = 0
        for a_depth in range(3):
            for b_depth in range(3):
                tokens = (['SELECT'] + ['('] * a_depth + ['a'] + [')'] * a_depth + [',']
                          + ['('] * b_depth + ['b'] + [')'] * b_depth + ['FROM', 't'])
                for gap in range(len(tokens) + 1):
                    for hint in ('/*+ H */', '--+ H\n'):
                        sql = ' '.join(tokens[:gap] + [hint] + tokens[gap:])
                        with self.subTest(depths=(a_depth, b_depth), gap=gap, hint=hint):
                            tree = parse(sql)
                            self.assertEqual(tree['statements'], baseline)
                            self.assertEqual(tree['hints'][0]['anchor']['token_gap'], gap)
                            value = self.reliable(sql)
                            self.assertNotIn(value, fingerprints)
                            fingerprints.add(value)
                            count += 1
        self.assertEqual(count, 198)

    def test_mpp_hint_ownership_and_nested_grouping(self):
        for left, right in (
            ('CREATE TABLE t AS SELECT (a) /*+ H */ FROM u DISTRIBUTED RANDOMLY',
             'CREATE TABLE t AS SELECT a FROM u /*+ H */ DISTRIBUTED RANDOMLY'),
            ('SELECT * FROM t WHERE (a) /*+ H */ = 1',
             'SELECT * FROM t WHERE a = 1 /*+ H */'),
            ('SELECT (SELECT (a) /*+ H */ FROM t)',
             'SELECT (SELECT a FROM t /*+ H */)'),
        ):
            with self.subTest(left=left):
                self.assertEqual(parse(left)['statements'], parse(right)['statements'])
                self.assertNotEqual(self.reliable(left), self.reliable(right))

    def test_hints_keep_format_and_business_value_equivalence(self):
        left = 'SELECT /*+ H */ a FROM t WHERE id=1; SELECT b --+ J\n FROM u'
        right = 'select /* ordinary */ /*+ H */ a\nfrom t where id=$2; select b --+ J\n /* gap */ from u'
        self.assertEqual(self.reliable(left), self.reliable(right))
        self.assertEqual(self.reliable(left), self.reliable(left.replace('id=1', "id='other'")))
        for changed in (left.replace('/*+ H */', '/*+ K */'), left.replace('/*+ H */', ''),
                        left.replace('/*+ H */ a', 'a /*+ H */')):
            self.assertNotEqual(self.reliable(left), self.reliable(changed))

    def test_statement_and_empty_batch_boundary_anchors(self):
        for sql, kinds, indices in (
            ('/*+ A */ SELECT a; /*+ B */ SELECT b /*+ C */',
             ['statement'] * 3, [0, 1, 1]),
            ('SELECT a; /*+ A */; /*+ B */ SELECT b; /*+ C */',
             ['batch_boundary', 'statement', 'batch_boundary'], [1, 1, 2]),
        ):
            with self.subTest(sql=sql):
                hints = parse(sql)['hints']
                self.assertEqual([h['anchor']['kind'] for h in hints], kinds)
                self.assertEqual([h['anchor']['statement_index'] for h in hints], indices)
                self.assertEqual(parse(sql), parse('/* plain */\n' + sql.replace(';', '; /* plain */\n')))
        self.assertNotEqual(self.reliable('SELECT a /*+ H */;'), self.reliable('SELECT a; /*+ H */'))

    def test_fallback_keeps_hint_protected_values_out_of_reliable_results(self):
        for hint in ('/*+ H */', '--+ H\n', '/*+ H */ /* ordinary */ --+ J\n'):
            for template in (
                'SELECT * FROM t WHERE custom {h} ((SELECT x FROM u WHERE id = 10)) AND (',
                'SELECT * FROM t WHERE (id = 10) {h} ::boolean AND (',
            ):
                sql = template.format(h=hint)
                with self.subTest(sql=sql):
                    a, b = [self.engine.normalize(s) for s in (sql, sql.replace('10', '20'))]
                    for result in (a, b):
                        self.assertEqual(result['fingerprint']['state'], 'unsupported_syntax')
                        self.assertIsNone(result['fingerprint']['value'])
                        self.assertEqual(result['fingerprint']['reason'], 'lexical_unbalanced_bracket')
                        self.assertTrue(result['approximate']['observation_only'])
                        self.assertEqual(result['approximate']['replacements'], 0)
                    self.assertNotEqual(a['approximate']['value'], b['approximate']['value'])
