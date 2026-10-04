"""Differential scanner boundaries against the pinned native implementation."""
import unittest
from unittest.mock import patch

from pglast import parser

from sql_apm.sql import mpp_parser, scanning
from sql_apm.sql.normalization import Normalizer


class ScanningTests(unittest.TestCase):
    def test_token_fields_and_original_contents(self):
        for sql in [
                'SELECT uni中ue, se中uence, inde中, e中cept, 中ml, 中文, é, 😀 FROM 表',
                "SELECT 中'abc', 中'01', 中&'text'",
                'SELECT ' + '测试' * 40 + ', "' + '😀' * 40 + '" FROM 表',
                '/* 中文 😀 */ SELECT /*+ 测试(-1) */ -2 AS 中文 -- 注释\n;',
                "SELECT '中文é😀', E'中\\n文', U&'中文', B'01', X'0f'",
                "SELECT '中' /* 中文 */\n '文'",
                "SELECT U&'中' UESCAPE 'é', U&\"中文\"",
                'SELECT 中文 NOT IN (1), 中文 NULLS FIRST, inde中, index',
                'SELECT $$中文; $x$; $$, $tag$中文$tag$',
                'SELECT $中文$one$中文$, $中中$two$文文$three$中中$',
                'SELECT $中$abc$x$def$中$, $中$abc$文$def$中$',
                'SELECT 1; /* outer 中 /* nested 文 */ done */ SELECT 2',
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(parser.scan(sql), scanning.scan(sql))

    def test_masked_keywords_are_identifiers(self):
        # Exercise every keyword containing q, including all keyword kinds.
        from pglast import keywords
        for group in ('RESERVED_KEYWORDS', 'UNRESERVED_KEYWORDS',
                      'COL_NAME_KEYWORDS', 'TYPE_FUNC_NAME_KEYWORDS'):
            for word in getattr(keywords, group):
                if 'q' in word:
                    sql = 'SELECT ' + word.replace('q', '中')
                    self.assertEqual(parser.scan(sql), scanning.scan(sql))

    def test_dollar_tag_falls_back(self):
        sql = 'SELECT $中$abc$文$def$中$'
        with patch.object(scanning.parser, 'scan', wraps=parser.scan) as native:
            scanning.scan(sql)
            native.assert_called_once_with(sql)
        self.assertEqual('non_ascii_dollar_tag', scanning.fallback_reason(sql))

    def test_original_error_type_message_and_character_position(self):
        for sql in ["SELECT 中文, 'unterminated", 'SELECT 中文 /* open',
                    'SELECT 中文, 1中', "SELECT 中文, E'\\uZZZZ'",
                    'SELECT 中文, ""', 'SELECT \ud800', 'SELECT 0中12']:
            with self.subTest(sql=sql):
                with self.assertRaises((parser.ParseError, UnicodeError)) as original:
                    parser.scan(sql)
                with self.assertRaises(type(original.exception)) as optimized:
                    scanning.scan(sql)
                self.assertEqual(original.exception.args, optimized.exception.args)

    def test_all_three_adapter_sites_and_normalization(self):
        sql = 'SELECT /*+ 中文 */ -2 AS 中文'
        engine = Normalizer()
        with patch.object(mpp_parser, 'scan', wraps=scanning.scan) as optimized:
            result = engine.normalize(sql.encode())
            self.assertEqual('reliable', result['fingerprint']['state'])
            self.assertEqual(3, optimized.call_count)
        with patch.object(mpp_parser, 'scan', parser.scan):
            self.assertEqual(engine.normalize(sql.encode()), result)


if __name__ == '__main__':
    unittest.main()
