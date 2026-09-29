"""R2: value spelling must not alter Hint ownership or protected structure."""
from itertools import product
import unittest

from sql_apm.sql.mpp_parser import parse, Unsupported
from sql_apm.sql.normalization import Normalizer


class SignedHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Normalizer()

    def value(self, sql):
        r = self.engine.normalize(sql)['fingerprint']
        self.assertEqual(r['state'], 'reliable', r['reason'])
        return r['value']

    def test_r2_72_signed_business_value_pairs(self):
        templates = [
            'SELECT * FROM t WHERE x = {v}', 'SELECT * FROM t WHERE x > {v} AND y = 2',
            'SELECT * FROM t WHERE x IN ({v}, 2)', 'SELECT * FROM t WHERE x BETWEEN {v} AND 9',
            'INSERT INTO t (a) VALUES ({v})', 'UPDATE t SET a = {v} WHERE id = 3',
            'DELETE FROM t WHERE id = {v}', 'SELECT * FROM t WHERE x = {v}; SELECT 1',
        ]
        for template, (a, b), hint in product(templates, [('1', '-1'), ('2.5', '-2.5'), ('7', '- 7')],
                                             ['/*+ H */ {s}', '{s} /*+ H */', '--+ H\n{s}']):
            left, right = template.format(v=a), template.format(v=b)
            with self.subTest(template=template, values=(a, b), hint=hint):
                self.assertEqual(self.value(left), self.value(right))
                self.assertEqual(self.value(hint.format(s=left)), self.value(hint.format(s=right)))

    def test_signed_variants_across_every_value_boundary(self):
        for hint in ('/*+ H */', '--+ H\n'):
            for template in (
                'SELECT {h} * FROM t WHERE x = {v}',
                'SELECT * FROM t WHERE x = {h} {v}',
                'SELECT * FROM t WHERE x = {v} {h}',
                'SELECT * FROM t WHERE x = {v}; {h} SELECT a FROM u',
                'SELECT * FROM t WHERE x = {v}; {h}; SELECT a FROM u; {h}',
                'UPDATE t SET (a,b)=({v},2) {h} WHERE id={v}',
                'WITH c AS (SELECT a FROM u WHERE x={v}) SELECT {h} * FROM c',
                'COPY (SELECT a FROM t WHERE x={v}) TO STDOUT {h} ON SEGMENT',
                'CREATE TABLE t WITH (orientation=row) AS SELECT {h} a FROM u WHERE x={v} DISTRIBUTED RANDOMLY',
                'CREATE TABLE t WITHOUT OIDS AS SELECT a FROM u WHERE x={v} {h} DISTRIBUTED RANDOMLY',
                'SELECT "中文" FROM "表" WHERE x={v} {h}',
            ):
                baseline = self.value(template.format(h=hint, v='1'))
                for value in ('-1', '- 2.5', '-0', '-1e3', '- -1', '- /* ordinary */ - 7', '$2', "'one'"):
                    with self.subTest(template=template, hint=hint, value=value):
                        self.assertEqual(baseline, self.value(template.format(h=hint, v=value)))

    def test_parenthesized_constants_and_binary_operators_keep_ownership(self):
        for hint in ('/*+ H */', '--+ H\n'):
            for template, a, b in (
                ('SELECT * FROM t WHERE x={v} {h}', '(1)', '-(2)'),
                ('SELECT * FROM t WHERE x={v} {h}', '(1)', '(-2)'),
                ('SELECT * FROM t WHERE x={v} {h}', '((1))', '-(-(-2))'),
                ('SELECT * FROM t WHERE x=y - {v} {h}', '1', '-2'),
                ('SELECT * FROM t WHERE x=y {h} - {v}', '1', '-2'),
                ('SELECT * FROM t WHERE x={v} - y {h}', '1', '-2'),
                ('SELECT * FROM t WHERE x={v} {h}', '(-1)::int', '(2)::int'),
            ):
                with self.subTest(template=template, a=a, b=b):
                    self.assertEqual(self.value(template.format(v=a, h=hint)),
                                     self.value(template.format(v=b, h=hint)))
            for left, right in (
                ('SELECT * FROM t WHERE x=y {h} - 1', 'SELECT * FROM t WHERE x=y - {h} 1'),
                ('SELECT * FROM t WHERE x=y - {h} 1', 'SELECT * FROM t WHERE x=y - 1 {h}'),
                ('SELECT * FROM t WHERE x=1 {h}', 'SELECT * FROM t WHERE x=-1::int {h}'),
                ('SELECT * FROM t WHERE x=1 {h}', 'SELECT * FROM t WHERE x=+1 {h}'),
                ('SELECT * FROM t WHERE x=y - 1 {h}', 'SELECT * FROM t WHERE x=y + 1 {h}'),
            ):
                with self.subTest(left=left, right=right):
                    self.assertNotEqual(self.value(left.format(h=hint)), self.value(right.format(h=hint)))

    def test_v5_select_values_keep_signed_hint_identity(self):
        self.assertEqual(self.value('SELECT 1 /*+ H */'), self.value('SELECT -1 /*+ H */'))

    def test_protected_values_remain_different(self):
        for template in (
            'SELECT * FROM t LIMIT {v} /*+ H */',
            'SELECT * FROM t WHERE custom({v}) /*+ H */',
            'SELECT * FROM t WHERE x=({v})::boolean /*+ H */',
            'CREATE TABLE t(a int DEFAULT {v}) /*+ H */ DISTRIBUTED RANDOMLY',
        ):
            with self.subTest(template=template):
                self.assertNotEqual(self.value(template.format(v='1')), self.value(template.format(v='-1')))

    def test_hint_inside_folded_sign_is_explicitly_rejected(self):
        for sql in ('SELECT * FROM t WHERE x=- /*+ H */ 1',
                    'SELECT * FROM t WHERE x=- --+ H\n1',
                    'SELECT * FROM t WHERE x=- /*+ H */ (1)',
                    'SELECT * FROM t WHERE x=- - /*+ H */ 1'):
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(Unsupported, '^hint_inside_folded_sign$'):
                    parse(sql)
                r = self.engine.normalize(sql)
                self.assertEqual(r['fingerprint']['state'], 'unsupported_syntax')
                self.assertIsNone(r['fingerprint']['value'])
                self.assertTrue(r['approximate']['observation_only'])
        # A binary '-' was not folded by PG and remains an independent gap.
        self.value('SELECT * FROM t WHERE x=y - /*+ H */ 1')

    def test_r2_728_position_sweep(self):
        bases = [
            ['SELECT', '{p}a{q}', 'FROM', 't', 'WHERE', '{p}x{q}', '=', '1', ';', 'SELECT', '{p}b{q}', 'FROM', 'u'],
            ['WITH', 'c', 'AS', '(', 'SELECT', '{p}a{q}', 'FROM', 't', ')', 'SELECT', '{p}a{q}', 'FROM', 'c'],
            ['INSERT', 'INTO', 't', 'SELECT', '{p}a{q}', ',', '{p}b{q}', 'FROM', 'u', 'WHERE', 'id', '=', '2'],
            ['UPDATE', 't', 'SET', 'a', '=', '{p}b{q}', 'FROM', 'u', 'WHERE', 't.id', '=', '{p}u.id{q}'],
            ['CREATE', 'TABLE', 'x', 'AS', 'SELECT', '{p}a{q}', 'FROM', 'u', 'DISTRIBUTED', 'BY', '(', 'a', ')'],
            ['SELECT', '(', 'SELECT', '{p}a{q}', 'FROM', 't', ')', ',', '{p}b{q}', 'FROM', 'u', ';', 'SELECT', '1'],
        ]
        seen, count = {}, 0
        for base in bases:
            for depths in product(range(2), repeat=sum(t.count('{p}') for t in base)):
                it, tokens = iter(depths), []
                for token in base:
                    if '{p}' in token:
                        depth = next(it)
                        token = token.replace('{p}', '(' * depth).replace('{q}', ')' * depth)
                    tokens.append(token)
                for gap, hint in product(range(len(tokens) + 1), ('/*+ H */', '--+ H\n')):
                    sql = ' '.join(tokens[:gap] + [hint] + tokens[gap:])
                    begin, index = 0, 0
                    for end in [i for i, t in enumerate(tokens) if t == ';'] + [len(tokens)]:
                        if gap <= end:
                            oracle = (index, tuple(tokens[begin:end]), gap - begin, hint)
                            break
                        begin, index = end + 1, index + 1
                    value = self.value(sql)
                    self.assertEqual(seen.setdefault(value, oracle), oracle)
                    count += 1
        self.assertEqual(count, 728)

    def test_adjudication_e1_57_synthesized_constant_cases(self):
        forms = ('x::char', 'CAST(x AS character)', 'x::bit', 'substring(x for 2)',
                 "interval(3) '1 day'", 'x::interval(2)')
        tails = ('WHERE y = {v}', 'WHERE y = {v} FETCH FIRST ROWS ONLY',
                 'WHERE y = z - 1 AND w = {v}')
        hints = ('/*+ H */ {s}', '{s} /*+ H */', '--+ H\n{s}')
        count = 0
        for form, tail, hint in product(forms, tails, hints):
            pair = ['SELECT {} FROM t {}'.format(form, tail.format(v=v)) for v in ('1', '-1')]
            with self.subTest(form=form, tail=tail, hint=hint):
                self.assertEqual(self.value(pair[0]), self.value(pair[1]))
                self.assertEqual(self.value(hint.format(s=pair[0])), self.value(hint.format(s=pair[1])))
            count += 1
        for sql in (
            'CREATE TABLE t (a char, b int DEFAULT -1) DISTRIBUTED RANDOMLY',
            "UPDATE t SET a = b - 1 WHERE c::char = 'x'",
            'INSERT INTO t (a) SELECT substring(x for 2) FROM u WHERE id = -3',
        ):
            with self.subTest(sql=sql):
                self.value(sql)
                self.value('/*+ H */ ' + sql)
            count += 1
        self.assertEqual(count, 57)

    def test_adjudication_e2_30_targeted_forms(self):
        forms = ('x::char', 'CAST(x AS character)', 'x::bit', 'x::character(1)',
                 'substring(x for 2)', "interval(3) '1 day'", 'x::interval(2)',
                 "overlay(x placing 'a' from 1)", 'x::char(1)', 'x::varchar')
        for form, tail in product(forms, ('WHERE y = -1', 'WHERE y = z - 1', 'WHERE y = 1')):
            with self.subTest(form=form, tail=tail):
                self.value('/*+ H */ SELECT {} FROM t {}'.format(form, tail))

    def test_adjudication_e3_180_nested_sign_and_adapter_cases(self):
        values = ('-1', '- 1', '(-1)', '-(1)', '-((1))', '-(-1)', '-(-(-1))', '- -1',
                  '(-1)::int', '-(1)::int', '(-1::int)', 'CAST(-1 AS integer)',
                  '-(1::int)', 'CAST(-(-1) AS numeric)', '- /* ordinary */ 1')
        contexts = (
            'SELECT x::char FROM t WHERE y={v}',
            'WITH c AS (SELECT substring(x for 2) FROM t WHERE y={v}) SELECT * FROM c',
            'INSERT INTO t(a) SELECT x::bit FROM u WHERE y={v}',
            'CREATE TABLE t AS SELECT x::char FROM u WHERE y={v} DISTRIBUTED BY (x)',
            'CREATE TABLE t WITH (orientation=row) AS SELECT x::char FROM u WHERE y={v} DISTRIBUTED RANDOMLY',
            'CREATE TABLE t WITHOUT OIDS AS SELECT x::char FROM u WHERE y={v} DISTRIBUTED RANDOMLY',
        )
        count = 0
        for value, context, hint in product(values, contexts, ('/*+ H */ {s}', '{s} /*+ H */')):
            sql = context.format(v=value)
            with self.subTest(value=value, context=context, hint=hint):
                self.value(sql)
                self.value(hint.format(s=sql))
            count += 1
        self.assertEqual(count, 180)

    def test_source_less_constants_leave_real_numeric_signs_mapped(self):
        # PG supplies implicit typmods, FETCH counts and substring starts without
        # a source token. A real signed constant in the same AST still maps.
        from sql_apm.sql.mpp_parser import _constant_sign_offsets
        for location in (None, -1, -2, '7', 7.0, True):
            synthesized = {'ival': {'ival': 1}}
            if location is not None:
                synthesized['location'] = location
            tree = [{'A_Const': synthesized}, {'A_Const': {'ival': {'ival': -1}, 'location': 7}}]
            with self.subTest(location=location):
                self.assertEqual(_constant_sign_offsets('SELECT -1', tree), {7})

    def test_unmapped_location_is_an_internal_ast_source_invariant(self):
        from sql_apm.sql.mpp_parser import _constant_sign_offsets
        # Deliberately inconsistent AST: positive source location outside SQL.
        tree = {'A_Const': {'ival': {'ival': -1}, 'location': 99}}
        with self.assertRaisesRegex(Unsupported, '^hint_constant_location_unmapped$'):
            _constant_sign_offsets('SELECT -1', tree)

    def test_unmapped_span_is_an_internal_ast_source_invariant(self):
        from sql_apm.sql.mpp_parser import _constant_sign_offsets
        # PG cannot produce a numeric A_Const for a unary operator on a column.
        tree = {'A_Const': {'ival': {'ival': -1}, 'location': 7}}
        with self.assertRaisesRegex(Unsupported, '^hint_constant_span_unmapped$'):
            _constant_sign_offsets('SELECT -x', tree)
        self.assertEqual(_constant_sign_offsets('SELECT -(1)', tree), {7})

    def test_sign_inside_replacement_is_an_internal_adapter_invariant(self):
        from sql_apm.sql.mpp_parser import _restore_offsets
        # Replacement-generated text is not an original numeric source token.
        replacements = [(2, 6, 'X')]
        with self.assertRaisesRegex(Unsupported, '^hint_sign_inside_adapter_replacement$'):
            _restore_offsets({2}, replacements)
        self.assertEqual(_restore_offsets({1, 3, 5}, replacements), {1, 6, 8})
