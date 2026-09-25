"""R1 regressions: independently specified preservation and type expectations."""
import copy
from pathlib import Path
import unittest

from sql_apm.sql.function_dictionary import FunctionDictionary
from sql_apm.sql.type_policy import signature_compatibility

ROOT = Path(__file__).resolve().parents[1]


def literal(value):
    return {'kind': 'literal', 'value': value}


def cast(node, typename):
    return {'kind': 'cast', 'type': typename, 'expr': node}


def call(name, arg, types=None):
    node = {'kind': 'call', 'name': name, 'args': [arg]}
    if types is not None:
        node['types'] = types
    return node


class TypeBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = FunctionDictionary.load(ROOT/'rules/functions/v1.0.1.json')

    def test_protected_cast_values_and_nested_casts(self):
        cases = [('regclass', 'orders', 'customers'), ('regtype', 'int4', 'text'),
                 ('regproc', 'f', 'g'), ('regprocedure', 'f(int4)', 'g(int4)'),
                 ('regoper', '+', '-'), ('regoperator', '+(int4,int4)', '-(int4,int4)'),
                 ('regconfig', 'english', 'simple'), ('regdictionary', 'english_stem', 'simple'),
                 ('oid', 10, 20), ('json', '{"a":1}', '{"b":2}'),
                 ('jsonb', '{"a":1}', '{"b":2}'), ('_int4', '{1}', '{1,2}'),
                 ('bool', 'true', 'false'), ('custom_domain', 'a', 'b')]
        for typ, left, right in cases:
            for outer in (False, True):
                for explicit_types in (False, True):
                    def tree(value):
                        node = cast(literal(value), typ)
                        if outer:
                            node = cast(node, 'text')
                        name = 'ascii' if outer else 'quote_nullable'
                        return call(name, node, ['text' if outer else typ] if explicit_types else None)
                    with self.subTest(type=typ, outer=outer, types=explicit_types):
                        a, b = tree(left), tree(right)
                        self.assertNotEqual(self.dictionary.preview(a), self.dictionary.preview(b))
                        self.assertEqual(self.dictionary.preview(a), a)

    def test_polymorphic_rules_do_not_prove_scalar_inputs(self):
        for name in ('quote_literal', 'quote_nullable'):
            for typ in ('anyelement', 'regclass', 'regtype', 'json', '_int4', 'bool', 'numeric'):
                with self.subTest(name=name, type=typ):
                    choice = self.dictionary.select(name, 1, types=[typ])
                    self.assertEqual(choice['actions'], ['preserve'])
        for rule in self.dictionary._data['rules']:
            if any(t in ('any', 'anyelement', 'anyarray', 'anyrange', 'anyenum', 'anynonarray')
                   for t in rule['types']):
                self.assertNotEqual(rule['decision'], 'normalize', rule['id'])

    def test_protected_cast_stops_parameters_and_nested_calls(self):
        for inner in ({'kind': 'parameter', 'index': 1},
                      call('lower', literal('orders'), ['text'])):
            tree = call('length', cast(cast(inner, 'regclass'), 'text'), ['text'])
            self.assertEqual(self.dictionary.preview(tree), tree)

    def test_scalar_casts_continue_to_normalize(self):
        for name, typ, left, right in [('ascii', 'text', 'abc', 'def'),
                                        ('abs', 'numeric', 10, 20)]:
            for types in (None, [typ]):
                a, b = [call(name, cast(literal(v), typ), types) for v in (left, right)]
                original = copy.deepcopy(a)
                self.assertEqual(self.dictionary.preview(a), self.dictionary.preview(b))
                self.assertEqual(a, original)
                self.assertEqual(self.dictionary.preview(a)['args'][0]['type'], typ)

    def test_polymorphic_category_and_relationship_mismatches(self):
        cases = [('lower', ['int4']), ('upper', ['json']),
                 ('array_length', ['int4', 'int4']), ('enum_first', ['text']),
                 ('array_append', ['_int4', 'text']), ('array_prepend', ['text', '_int4']),
                 ('array_cat', ['_int4', '_text']), ('array_cat', ['_int4', 'int4']),
                 ('lag', ['int4', 'int4', 'text'])]
        for name, types in cases:
            with self.subTest(name=name, types=types):
                result = self.dictionary.select(name, len(types), types=types,
                                                kind='window' if name == 'lag' else 'function')
                self.assertEqual(result['reason'], 'no_matching_rule')
                self.assertEqual(result['actions'], ['preserve'] * len(types))

    def test_supported_polymorphic_and_exact_signatures(self):
        for name, types in [('array_append', ['_int4', 'int4']),
                            ('array_prepend', ['text', '_text']),
                            ('array_cat', ['_int4', '_int4']),
                            ('array_length', ['_text', 'int4']),
                            ('lower', ['int4range']), ('upper', ['numrange']),
                            ('quote_nullable', ['regclass'])]:
            with self.subTest(name=name, types=types):
                result = self.dictionary.select(name, len(types), types=types)
                self.assertEqual(result['reason'], 'matched')
                self.assertEqual(result['actions'], ['preserve'] * len(types))
        self.assertEqual(self.dictionary.select('lower', 1, types=['text'])['actions'], ['normalize'])
        self.assertEqual(self.dictionary.select('quote_nullable', 1, types=['text'])['actions'], ['normalize'])
        self.assertEqual(self.dictionary.select('to_char', 2)['actions'], ['normalize', 'preserve'])
        self.assertEqual(self.dictionary.select('lower', 1)['reason'], 'ambiguous_overload')

    def test_linked_type_constraints_across_all_families(self):
        # Synthetic declarations exercise relationships absent from this
        # dictionary's chapter-9 signatures without claiming catalog entries.
        cases = [
            (['int4', 'text'], ['anyelement', 'anyelement'], False),
            (['int4', 'int4'], ['anyelement', 'anyelement'], True),
            (['_int4'], ['anynonarray'], False),
            (['text'], ['anynonarray'], True),
            (['int4range', 'int4'], ['anyrange', 'anyelement'], True),
            (['int4range', 'text'], ['anyrange', 'anyelement'], False),
            (['int4range', 'numrange'], ['anyrange', 'anyrange'], False),
            (['_int4', 'int4range'], ['anyarray', 'anyrange'], True),
            (['_text', 'int4range'], ['anyarray', 'anyrange'], False),
            (['text', 'text'], ['anyelement', 'anyenum'], False),
            (['custom_enum', 'custom_enum'], ['anyelement', 'anyenum'], None),
            (['int4', 'text'], ['any', 'any'], True),
            (['text'], ['anything'], False),
        ]
        for actual, expected, result in cases:
            with self.subTest(actual=actual, expected=expected):
                self.assertIs(signature_compatibility(actual, expected), result)

    def test_unknown_and_partial_types_remain_conservative(self):
        for name, types in [('enum_first', ['custom_enum']), ('lower', ['custom_range']),
                            ('array_length', ['_custom', 'int4']),
                            ('quote_nullable', ['custom_domain']),
                            ('array_append', ['_int4', 'custom_domain']),
                            ('array_append', ['anyarray', 'int4'])]:
            with self.subTest(name=name, types=types):
                result = self.dictionary.select(name, len(types), types=types)
                self.assertEqual(result['reason'], 'polymorphic_requires_resolver')
                self.assertEqual(result['actions'], ['preserve'] * len(types))
        self.assertEqual(self.dictionary.select('array_append', 2, types=['int4', None])['reason'],
                         'no_matching_rule')
        self.assertEqual(self.dictionary.select('array_append', 2, types=['_int4', None])['actions'],
                         ['preserve', 'preserve'])


if __name__ == '__main__':
    unittest.main()
