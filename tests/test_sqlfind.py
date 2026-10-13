"""Input, terminal formatting and reversible login configuration contracts."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

from sql_apm.cli.sqlfind_output import Output, cells, clip, plain, stamp

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = all(importlib.util.find_spec(name) for name in ('psycopg2', 'pglast'))
if RUNTIME:
    from sql_apm.cli.sqlfind import arguments, connection, input_source, InputError, main, validate


class FormattingTests(unittest.TestCase):
    def test_width_and_only_stable_alignment_characters(self):
        self.assertEqual(cells('abc中文'), 7)
        self.assertEqual(clip('中文名字名字', 9), '中文名...')
        self.assertEqual(plain('x\t\x1b\u00b7\U0001f600'), 'x\\t\\x1b\\xb7\\U0001f600')
        for width in (40, 80, 200):
            out = io.StringIO()
            printer = Output(width, out)
            printer.table(['数据库', '完整身份', '计数'], [('业务库', '很长的完整身份' * 30, 12345678901234)])
            result = out.getvalue()
            self.assertTrue(all(cells(line) <= width for line in result.splitlines()))
            self.assertIn('12345678901234', result)
            self.assertEqual(result.replace('\n', '').count('很长'), 30)

    def test_time_and_encoding(self):
        self.assertEqual(stamp('2026-07-01T00:00:00+00:00'), '2026-07-01 08:00:00.000')
        storage = io.BytesIO()
        stream = io.TextIOWrapper(storage, encoding='gbk')
        printer = Output(80, stream)
        printer.write('中文\U0001f600\n')
        printer.footer()
        self.assertIn('中文?\n提示:', storage.getvalue().decode('gbk'))


@unittest.skipUnless(RUNTIME, 'requires pinned product runtime')
class InputTests(unittest.TestCase):
    def test_options_and_limits(self):
        for argv in (['--full-text'], ['--limit', '2'], ['--show', '1', '--limit', '1001'],
                     ['--mode', 'guess'], ['--start', '2026-07-02', '--end', '2026-07-01']):
            with self.assertRaises(InputError):
                arguments(argv)
        args = arguments(['--start', '2026-07-01', '--end', '2026-07-02T00:00:00Z'])
        self.assertEqual(args.mode, 'words')
        self.assertEqual(args.start.utcoffset().total_seconds(), 28800)
        for mode, size in [('words', 256 * 1024), ('passage', 256 * 1024), ('exact', 512 * 1024)]:
            validate(b'x' * size, mode)
            with self.assertRaises(InputError):
                validate(b'x' * (size + 1), mode)
        for raw in (b'', b' \t\n\r', b'\xff', b'x\0'):
            with self.assertRaises(InputError):
                validate(raw, 'words')

    def test_all_sources_preserve_bytes_and_conflicts_fail(self):
        raw = ' \r\nSELECT "中文", \'x\\n\'\rFROM orders\t '.encode()
        stream = io.TextIOWrapper(io.BytesIO(raw))
        self.assertEqual(input_source(arguments([]), stream), raw)
        terminal = io.StringIO()
        terminal.isatty = lambda: True
        self.assertEqual(input_source(arguments([raw.decode()]), terminal), raw)
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'sql'
            file.write_bytes(raw)
            self.assertEqual(input_source(arguments(['--file', str(file)]), terminal), raw)
            for args in ([raw.decode()], ['--file', str(file)], [raw.decode(), '--file', str(file)]):
                with self.assertRaises(InputError):
                    input_source(arguments(args), io.TextIOWrapper(io.BytesIO(raw)))

    def test_connection_priority_and_no_secret_in_errors(self):
        with tempfile.TemporaryDirectory() as tmp, patch('pathlib.Path.home', return_value=Path(tmp)), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(InputError, '请先'):
                connection(arguments([]))
            with patch.dict(os.environ, {'SQL_APM_DSN': 'host=/absent dbname=x password=SECRET'}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), patch('sys.stdin', io.TextIOWrapper(io.BytesIO(b'orders'))):
                    self.assertEqual(main([]), 1)
                self.assertNotIn('SECRET', out.getvalue())
                self.assertEqual(len(out.getvalue().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
