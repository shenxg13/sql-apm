"""Canonical bytes and deep structure handling must not depend on recursion limits."""
import json
import random
import sys
import unittest

from sql_apm.sql.structure import dumps, loads, iterencode, _loads_stack
from sql_apm.sql.pg_ast import pg_clean


class StructureTests(unittest.TestCase):
    def test_canonical_compatibility(self):
        randomizer = random.Random(9)
        values = [None, True, False, 0, -12, 1.25, '换行\n"\\\t', [], {}, (1, 2)]
        for _ in range(80):
            values.append({'b': randomizer.choice(values), 'a': [randomizer.choice(values), None]})
        for value in values:
            expected = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
            self.assertEqual(dumps(value), expected)
            self.assertEqual(''.join(iterencode(value)), expected)
            self.assertEqual(_loads_stack(expected), json.loads(expected))

    def test_deep_round_trip_and_cleaning(self):
        before = sys.getrecursionlimit()
        raw = '{"child":' * 2000 + '[0,false,"中文",null]' + ',"location":7}' * 2000
        original = loads(raw)
        cleaned = pg_clean(original)
        encoded = dumps(cleaned)
        self.assertEqual(encoded, '{"child":' * 2000 + '[0,false,"\\u4e2d\\u6587",null]' + '}' * 2000)
        cursor = loads(encoded)
        for _ in range(2000):
            self.assertEqual(list(cursor), ['child'])
            cursor = cursor['child']
        self.assertEqual(cursor, [0, False, '中文', None])
        self.assertEqual(original['location'], 7)
        self.assertEqual(sys.getrecursionlimit(), before)

    def test_bad_json_is_not_repaired(self):
        for text in ('', '[1,]', '{"a":1,}', '{a:1}', '{"a" 1}', '[1 2]', '{}[]', '{', '[', 'true false'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                _loads_stack(text)
        with self.assertRaises(ValueError):
            loads('[' * 2000 + '0,' + ']' * 2000)

    def test_cycles_are_rejected_by_encoder(self):
        value = []
        value.append(value)
        with self.assertRaises(ValueError):
            ''.join(iterencode(value))

    def test_clean_retains_order_values_and_input(self):
        value = {'location': 1, 'items': [0, False, None, {'x': 4, 'stmt_len': 9}], 'label': 'location'}
        self.assertEqual(pg_clean(value), {'items': [0, False, None, {'x': 4}], 'label': 'location'})
        self.assertEqual(value['items'][-1]['stmt_len'], 9)
