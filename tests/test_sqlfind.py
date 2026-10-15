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
    from sql_apm.cli.sqlfind import arguments, connection, history_settings, input_source, InputError, main, search_order, time_value, validate


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

    def test_original_controls_keep_text_identity_and_normal_layout(self):
        ordinary = "SELECT '中文\t原文\r\n下一行';\n"
        unsafe = '\x00\x07\x1b[31m\r\x7f\x80\x85\x9b\x9f'
        visible = r'\x00\x07\x1b[31m\r\x7f\x80\x85\x9b\x9f'
        for raw, expected, count in ((ordinary, ordinary, 0),
                                     (ordinary + unsafe, ordinary + visible, 9)):
            stream = io.StringIO()
            detail = dict(text=dict(sql_id='same-id', sql_text=raw, selected=True),
                          originals=[dict(number=7, sql_id='same-id')])
            Output(80, stream).original_text(detail)
            rendered = stream.getvalue()
            self.assertEqual(rendered.split('原文开始\n', 1)[1].split('\n原文结束', 1)[0], expected)
            self.assertIn(f'原文总字符数: {len(raw)}\n', rendered)
            self.assertIn('编号 7', rendered)
            self.assertEqual(detail['text']['sql_text'], raw)
            self.assertEqual('控制字符' in rendered, bool(count))
            if count:
                self.assertIn(f'{count} 个控制字符', rendered)
        # A CR at the display limit must not escape as a terminal action when
        # its following LF is outside the displayed portion.
        detail['text']['sql_text'] = 'x' * 3999 + '\r\n末尾'
        stream = io.StringIO()
        Output(80, stream).original_text(detail, False)
        self.assertIn('x\\r\n原文结束', stream.getvalue())
        self.assertIn('1 个控制字符', stream.getvalue())


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
        for option, value in (('--history-start', '2026-07-01'), ('--history-end', '2026-07-02'),
                              ('--history-order', 'slowest'), ('--original', 'all'), ('--view', 'text')):
            with self.assertRaises(InputError):
                arguments([option, value])
        for options in (['--history-start', '2026-07-01'], ['--history-end', '2026-07-02'],
                        ['--history-start', '2026-07-02', '--history-end', '2026-07-01'],
                        ['--history-start', 'wrong', '--history-end', '2026-07-02'],
                        ['--history-order', 'count'], ['--original', '0'], ['--original', '-1'],
                        ['--original', 'x'], ['--original', '1.5'], ['--view', 'unknown'],
                        ['--view', 'originals', '--full-text'], ['--view', 'history', '--full-text']):
            with self.assertRaises(InputError):
                arguments(['--show', '1', *options])
        args = arguments(['--show', '1', '--start', '2026-07-20', '--end', '2026-07-21',
                          '--history-start', '2026-07-01', '--history-end', '2026-07-02T00:00:00Z',
                          '--original', '3', '--history-order', 'slowest', '--view', 'text', '--full-text'])
        self.assertEqual(args.original, 3)
        self.assertEqual(args.history_order, 'slowest')
        self.assertEqual(args.view, 'text')
        self.assertLess(args.history_end, args.start)
        self.assertEqual(args.history_start.utcoffset().total_seconds(), 28800)
        self.assertEqual(args.history_end.utcoffset().total_seconds(), 0)
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

    def test_history_settings_validation_and_defaults(self):
        original = dict(limit=20, window=None, order='latest')
        detail = dict(start=time_value('2026-07-16'), end=time_value('2026-07-23'))
        stream = io.StringIO()
        entries = ['0', '1001', 'wrong', '100', '9', '2', 'bad-date', '2026-09-19',
                   '2026-09-18', '2026-09-19', '2026-09-19T16:00:00Z', '9', '2']
        with patch('sql_apm.cli.sqlfind.terminal_input', side_effect=[s.encode() for s in entries]):
            chosen = history_settings(Output(80, stream), original, detail)
        self.assertEqual(chosen, dict(limit=100, window=(time_value('2026-09-19'), time_value('2026-09-20')), order='slowest'))
        self.assertEqual(original, dict(limit=20, window=None, order='latest'))
        self.assertIn('结束时间必须晚于开始时间', stream.getvalue())
        for size in (1, 1000):
            with patch('sql_apm.cli.sqlfind.terminal_input', side_effect=[str(size).encode(), b'1', b'1']):
                self.assertEqual(history_settings(Output(stream=io.StringIO()), chosen, detail), dict(limit=size, window=None, order='latest'))
        with patch('sql_apm.cli.sqlfind.terminal_input', side_effect=[b'', b'', b'', b'', b'']):
            self.assertEqual(history_settings(Output(stream=io.StringIO()), chosen, detail), chosen)

    def test_history_cancel_and_reset_do_not_partially_apply(self):
        original = dict(limit=100, window=(time_value('2026-07-01'), time_value('2026-07-02')), order='slowest')
        before = dict(original)
        detail = dict(start=original['window'][0], end=original['window'][1])
        for prefix in ([], [b'200'], [b'200', b'2'], [b'200', b'2', b'2026-07-03'], [b'200', b'1']):
            for cancellation in (b'q', KeyboardInterrupt):
                with patch('sql_apm.cli.sqlfind.terminal_input', side_effect=prefix + [cancellation]):
                    self.assertIsNone(history_settings(Output(stream=io.StringIO()), original, detail))
                self.assertEqual(original, before)
            with patch('sql_apm.cli.sqlfind.terminal_input', side_effect=prefix + [b'r']):
                self.assertEqual(history_settings(Output(stream=io.StringIO()), original, detail), dict(limit=20, window=None, order='latest'))
            self.assertEqual(original, before)

    def test_search_sort_choice_and_cancel(self):
        for entries, expected in [([b'9', b'2'], 'recent'), ([b'1'], 'count'),
                                  ([b''], 'recent'), ([b'q'], None), ([KeyboardInterrupt], None)]:
            with patch('sql_apm.cli.sqlfind.terminal_input', side_effect=entries):
                self.assertEqual(search_order(Output(stream=io.StringIO()), 'recent'), expected)

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
