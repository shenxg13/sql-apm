"""PTY and login checks used by verify_sqlfind; no real account configuration."""
import errno
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import fcntl
import unicodedata

ROOT = Path(__file__).resolve().parents[2]


class Terminal:
    def __init__(self, dsn, args=(), width=80, env=None, entry=None):
        pid, self.fd = os.forkpty()
        if pid == 0:
            os.chdir('/tmp')
            command = entry or [str(ROOT / 'sqlfind'), *args]
            os.execve(command[0], command,
                      dict(os.environ, SQL_APM_DSN=dsn, PYTHONPATH=str(ROOT), **(env or {})))
        self.pid, self.output, self.offset = pid, b'', 0
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, width, 0, 0))

    def send(self, value):
        os.write(self.fd, value.encode() if isinstance(value, str) else value)

    def until(self, text, timeout=20):
        wanted = text.encode()
        end = time.monotonic() + timeout
        while wanted not in self.output[self.offset:]:
            if time.monotonic() >= end:
                raise AssertionError('PTY did not reach ' + text + ': ' + repr(self.output[-1200:]))
            if select.select([self.fd], [], [], 0.1)[0]:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError as error:
                    raise AssertionError('PTY exited before ' + text + ': ' + repr(self.output[-1200:])) from error
                if not chunk:
                    raise AssertionError('PTY closed')
                self.output += chunk
        start = self.offset
        self.offset = self.output.index(wanted, start) + len(wanted)
        return self.output[start:self.offset].replace(b'\r\n', b'\n').decode('utf-8')

    def finish(self, timeout=5):
        end = time.monotonic() + timeout
        while True:
            if time.monotonic() >= end:
                raise AssertionError('PTY did not exit')
            if not select.select([self.fd], [], [], 0.1)[0]:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            self.output += chunk
        _, status = os.waitpid(self.pid, 0)
        self.pid = None
        assert os.waitstatus_to_exitcode(status) == 0, status
        start, self.offset = self.offset, len(self.output)
        return self.output[start:].replace(b'\r\n', b'\n').decode('utf-8')

    def close(self):
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            os.waitpid(self.pid, 0)
        os.close(self.fd)


def visible_screen(raw, width):
    """Interpret the ordinary VT cursor/erase operations used by input echo."""
    lines, row, column = {}, 0, 0
    for token in re.findall(r'\x1b\[[0-9;]*[A-Za-z]|[^\x1b]', raw.decode()):
        if token.startswith('\x1b['):
            count = int(token[2:-1] or '1')
            if token[-1] == 'A':
                row = max(0, row - count)
            elif token[-1] == 'C':
                column = min(width - 1, column + count)
            elif token == '\x1b[J':
                for key in list(lines):
                    if key[0] > row or key[0] == row and key[1] >= column:
                        del lines[key]
            else:
                raise AssertionError('Unexpected terminal control: ' + repr(token))
        elif token == '\r':
            column = 0
        elif token == '\n':
            row += 1
        else:
            size = 2 if unicodedata.east_asian_width(token) in ('W', 'F') else 1
            if column + size > width:
                row, column = row + 1, 0
            lines[row, column] = token
            if size == 2:
                lines[row, column + 1] = ''
            column += size
    return '\n'.join(''.join(lines.get((r, c), ' ') for c in range(width)).rstrip()
                     for r in range(row + 1)).rstrip()


def verify_input():
    # Check both bytes delivered to the caller and what an operator sees, before
    # involving a database. Split writes exercise escape/UTF-8 read boundaries.
    cases = [
        ([b't exwec', b'\x7f\x7f\x7fecute_first', b'\x1b', b'[A\x1b[', b'B\r'],
         b't execute_first', 't execute_first', False),
        ([b'\x08\x7ft requesx\x08t\x1bOA\x1bOB\x1b[1;5D\r'],
         b't request', 't request', False),
        (['中文错'.encode(), b'\x7f', '词'.encode(), b'\r'], '中文词'.encode(), '中文词', False),
        ([b'a\xe4', b'\x7f', '中'.encode()[:1], '中'.encode()[1:], b'\r'], 'a中'.encode(), 'a中', False),
        ([b'a\t', b'\x08', b'b\r'], b'ab', 'ab', False),
        ([b'x' * 79 + '中'.encode(), b'\x7f\x08Z\r'], b'x' * 78 + b'Z', 'x' * 78 + 'Z', False),
        ([b'x' * 80, b'\x7fZ\r'], b'x' * 79 + b'Z', 'x' * 79 + 'Z\n', False),
        (['SELECT 中文\n\tFROM wrong'.encode(), b'\x08' * 5, b'orders\n\x04'],
         'SELECT 中文\n\tFROM orders'.encode(), 'SELECT 中文\n        FROM orders\n', True),
        ([b'first\n', b'\x7f', b' next\n\x04'], b'first next', 'first next\n', True),
        ([b'SELECT 1\r\nFROM example\r\n\x04'], b'SELECT 1\nFROM example',
         'SELECT 1\nFROM example\n', True),
    ]
    for width in (80, 200):
        for parts, expected, display, multiline in cases:
            probe = (
                'import hashlib; from sql_apm.cli.sqlfind import terminal_input; '
                'from sql_apm.cli.sqlfind_output import Output; '
                f'raw = terminal_input(Output(), "READY", multiline={multiline}); '
                'print("RESULT=" + hashlib.sha256(raw).hexdigest())'
            )
            term = Terminal('', width=width, entry=[sys.executable, '-B', '-c', probe])
            try:
                term.until('READY')
                for part in parts:
                    term.send(part)
                    time.sleep(0.03)
                result = 'RESULT=' + hashlib.sha256(expected).hexdigest()
                term.until(result)
                if width == 200 and display == 'x' * 79 + 'Z\n':
                    display = display.rstrip('\n')
                assert visible_screen(term.output, width) == 'READY\n' + display + '\n' + result, repr(term.output)
            finally:
                term.close()
    print('PASS: input erase display and bytes, BS/DEL, UTF-8, tabs, wraps and split arrow sequences', flush=True)


def check_operations(term):
    rendered = term.output[:term.offset].replace(b'\r\n', b'\n').decode('utf-8')
    active, number, content = None, 0, False
    for line in rendered.splitlines():
        start = re.fullmatch(r'========== 操作 (\d+)：(.*) ==========', line)
        end = re.fullmatch(r'========== 操作 (\d+) 输出结束 ==========', line)
        if start:
            assert active is None and int(start[1]) == number + 1, line
            number = active = int(start[1])
            content = False
        elif end:
            assert active == int(end[1]), line
            assert content, 'Empty operation: ' + line
            active = None
        elif line in ('请选择检索方式：', '检索结果列表操作：', 'SQL 详情操作：'):
            assert active is None, 'Menu inside command output: ' + line
        elif active is not None and line.strip():
            content = True
    assert number > 0 and active is None, 'Missing operation boundary'


def check_full_text(term, detail, width):
    from sql_apm.cli.sqlfind_output import Output
    term.send('f\n')
    rendered = term.until('Ctrl-C 返回列表。', timeout=5)
    stream = io.StringIO()
    Output(width, stream).original_text(detail)
    assert stream.getvalue().strip() in rendered
    assert '\n当前基线\n' not in rendered and '\n执行历史\n' not in rendered
    assert '身份 (* 表示当前所选)' not in rendered
    check_operations(term)
    return rendered


def check_help(term, prompt, width):
    from sql_apm.cli.sqlfind_output import cells
    term.send('?\n')
    rendered = term.until('交互帮助结束。', timeout=5)
    assert 'sqlfind 交互帮助' in rendered
    for line in ('  o  调整检索结果排序', '  l  重新显示当前结果列表',
                 '  i 序号  切换身份表', '  t 类别  切换计时类别',
                 '  v 编号  选择一份原文', '  h  设置历史条数'):
        assert '\n' + line in rendered
    assert all(cells(line) <= width for line in rendered.splitlines())
    assert '\x1b' not in rendered
    term.until(prompt)
    check_operations(term)


def verify_help(dsn):
    from verify_sqlfind import command
    for width in (80, 200):
        # The outer help requires no database connection, including repeat use.
        term = Terminal('host=/nonexistent-sqlfind-help-socket connect_timeout=1', width=width)
        try:
            menu = term.until('回车选择 1。')
            assert '\n  ?  查看交互帮助\n' in menu
            for _ in range(2):
                check_help(term, '回车选择 1。', width)
            for mode in ('', '1', '2', '3', 'words', 'passage', 'exact'):
                term.send(mode + '\n')
                assert '========== 操作' not in term.until('之后 q 退出。')
                term.send('\x03')
                term.until('回车选择 1。')
                check_operations(term)
        finally:
            term.close()
        term = Terminal(dsn, width=width)
        try:
            for mode, raw in (('1', '?'), ('2', "SELECT '?'\nFROM orders"),
                              ('3', "SELECT '?'\nFROM orders")):
                term.until('回车选择 1。')
                term.send(mode + '\n')
                term.until('之后 q 退出。')
                term.send(raw + ('\n' if mode == '1' else '\n\x04'))
                result = term.until('Ctrl-C 返回输入。')
                assert 'sqlfind 交互帮助' not in result
                if mode == '3':
                    # Exact mode reports a fingerprint, not the raw input digest.
                    expected = command(dsn, ['--mode', 'exact', '--width', str(width), raw])
                    assert expected.returncode == 0 and expected.stdout.decode().strip() in result
                else:
                    assert hashlib.sha256(raw.encode()).hexdigest() in result.replace('\n', '')
                term.send('n\n')
                assert '========== 操作' not in term.until('请选择检索方式：')
        finally:
            term.close()
    print('PASS: outer help without database, repeat help, 80/200 columns and literal question marks in all input modes', flush=True)


def verify_operations(dsn, owner):
    from sql_apm.cli.sqlfind import arguments, search
    from sql_apm.storage.sqlfind import Finder
    from database import dashboard_data as data
    finder, args = Finder(dsn, 'sql_apm'), arguments([])
    for width in (80, 200):
        term = Terminal(dsn, width=width)
        try:
            term.until('回车选择 1。')
            term.send('1\n')
            assert '========== 操作' not in term.until('之后 q 退出。')
            term.send('big_list\n')
            assert '操作 1：按词检索' in term.until('Ctrl-C 返回输入。')
            check_operations(term)
            term.send('1\n')
            term.until('Ctrl-C 返回列表。')
            found = search(finder, b'big_list', args)
            detail = finder.detail(found, '1', args)
            assert detail['text']['sql_text'] == data.LONG and len(data.LONG) > 4000
            with owner.cursor() as cur:
                cur.execute('LOCK sql_apm.mpp_sql_text IN ACCESS EXCLUSIVE MODE')
            rendered = check_full_text(term, detail, width)
            assert '原文开始\n' + data.LONG + '\n原文结束' in rendered
            owner.rollback()
            term.send('n\n')
            assert '========== 操作' not in term.until('回车选择 1。')
            term.send('9\n')
            assert '请选择 1、2 或 3' in term.until('回车选择 1。')
            check_operations(term)
            term.send('3\n')
            assert '========== 操作' not in term.until('之后 q 退出。')
            term.send('SELECT absent_column_a; SELECT absent_column_b;\n\x04')
            term.until('Ctrl-C 返回输入。')
            term.send('s1\n')
            assert '没有可查看的执行记录和身份' in term.until('Ctrl-C 返回列表。')
            assert '当前没有可查看的原文' in check_full_text(term, {}, width)
            term.send('invalid\n')
            assert '操作无效' in term.until('Ctrl-C 返回列表。')
            check_operations(term)
            term.send('b\n')
            rendered = term.until('Ctrl-C 返回输入。')
            assert '：b 返回列表' in rendered and '这次检索' in rendered
            check_operations(term)
            term.send('s1\n')
            term.until('Ctrl-C 返回列表。')
            term.send('\x03')
            term.until('Ctrl-C 返回输入。')
            check_operations(term)
            term.send('q\n')
            assert term.finish().strip() == 'q'
            check_operations(term)
        finally:
            owner.rollback()
            term.close()
        for screen, command in (('mode', 'q'), ('list', ''), ('detail', 'q'), ('detail', '')):
            term = Terminal(dsn, width=width)
            try:
                term.until('回车选择 1。')
                if screen != 'mode':
                    term.send('\n')
                    assert '========== 操作' not in term.until('之后 q 退出。')
                    term.send('big_list\n')
                    assert '操作 1：按词检索' in term.until('Ctrl-C 返回输入。')
                    if screen == 'detail':
                        term.send('1\n')
                        term.until('Ctrl-C 返回列表。')
                term.send(command + '\n')
                assert term.finish().strip() == command
                if screen != 'mode':
                    check_operations(term)
                else:
                    assert b'==========' not in term.output
            finally:
                term.close()
    print('PASS: nonempty sequential operation boundaries, silent transitions/exits, errors/cancel, text-only long/sample/empty output and no f query', flush=True)


def verify_history(dsn, owner):
    from sql_apm.cli.sqlfind import arguments, search, time_value
    from sql_apm.cli.sqlfind_output import Output
    from sql_apm.storage.sqlfind import Finder
    from database import dashboard_data as data
    finder, args = Finder(dsn, 'sql_apm'), arguments([])
    for width in (80, 200):
        term = Terminal(dsn, ['--mode', 'words'], width)
        try:
            prompt = 'Ctrl-C 返回列表。'
            found = search(finder, b'orders', args)
            term.until('之后 q 退出。')
            term.send('orders\n')
            term.until('Ctrl-C 返回输入。')
            term.send('1\n')
            term.until(prompt)

            def compare(rendered, expected):
                stream = io.StringIO()
                Output(width, stream).history(expected)
                assert stream.getvalue().strip() in rendered, rendered[-1500:]

            def full_and_keep(expected):
                check_full_text(term, expected, width)
                # f no longer prints history; blank settings verify it was retained.
                term.send('h\n')
                custom = expected['custom_window']
                markers = [f"显示条数 [{expected['limit']}]，可选 1-1000：",
                           f'指定起止时间 [{2 if custom else 1}]：']
                if custom:
                    markers += [']：', ']：']
                markers += ['最慢在前 [1]：']
                for marker in markers:
                    term.until(marker)
                    term.send('\n')
                compare(term.until(prompt), expected)

            term.send('h\n')
            term.until('可选 1-1000：')
            term.send('0\n')
            term.until('显示条数必须是')
            term.until('可选 1-1000：')
            term.send('100\n')
            term.until('指定起止时间 [1]：')
            term.send('2\n')
            term.until(']：')
            term.send('2026-06-26\n')
            term.until(']：')
            term.send('2026-06-27\n')
            term.until('最慢在前 [1]：')
            term.send('1\n')
            window = (time_value('2026-06-26'), time_value('2026-06-27'))
            custom = finder.detail(found, '1', args, limit=100, window=window)
            rendered = term.until(prompt)
            compare(rendered, custom)
            assert '\n当前基线\n' not in rendered and '原文开始' not in rendered
            check_help(term, prompt, width)
            full_and_keep(custom)

            # Cancelling after entering a different limit must retain all settings.
            for cancel in ('q\n', '\x03'):
                term.send('h\n')
                term.until('可选 1-1000：')
                term.send('3\n')
                term.until('指定起止时间 [2]：')
                term.send('2\n')
                term.until(']：')
                term.send(cancel)
                cancelled = term.until(prompt)
                assert '已取消历史设置，保留原设置。' in cancelled
                full_and_keep(custom)

            term.send('i 2\n')
            compare(term.until(prompt), finder.detail(found, '1', args, identity=2, limit=100, window=window))
            term.send('t request\n')
            compare(term.until(prompt), finder.detail(found, '1', args, identity=2, timing='request', limit=100, window=window))
            term.send('b\n')
            term.until('Ctrl-C 返回输入。')
            term.send('2\n')
            compare(term.until(prompt), finder.detail(found, '2', args, limit=100, window=window))

            # A new SQL with real alternate timing categories inherits the session options.
            term.send('n\n')
            term.until('q 退出')
            term.send('3\n')
            term.until('之后 q 退出。')
            term.send(data.EXTENDED + '\n\x04')
            term.until('Ctrl-C 返回输入。')
            term.send('1\n')
            exact_args = arguments(['--mode', 'exact'])
            exact = search(finder, data.EXTENDED.encode(), exact_args)
            compare(term.until(prompt), finder.detail(exact, '1', exact_args, limit=100, window=window))
            term.send('t parse\n')
            compare(term.until(prompt), finder.detail(exact, '1', exact_args, timing='parse', limit=100, window=window))
            check_help(term, prompt, width)
            full_and_keep(finder.detail(exact, '1', exact_args, timing='parse', limit=100, window=window))

            # Empty ranges are valid, and Enter retains all four settings.
            term.send('h\n')
            term.until('可选 1-1000：')
            term.send('1\n')
            term.until('指定起止时间 [2]：')
            term.send('2\n')
            term.until(']：')
            term.send('2030-01-01\n')
            term.until(']：')
            term.send('2030-01-02\n')
            term.until('最慢在前 [1]：')
            term.send('1\n')
            empty_window = (time_value('2030-01-01'), time_value('2030-01-02'))
            empty = finder.detail(exact, '1', exact_args, timing='parse', limit=1, window=empty_window)
            compare(term.until(prompt), empty)
            term.send('h\n')
            for end in ('可选 1-1000：', '指定起止时间 [2]：', ']：', ']：', '最慢在前 [1]：'):
                term.until(end)
                term.send('\n')
            compare(term.until(prompt), empty)

            term.send('h\n')
            term.until('可选 1-1000：')
            term.send('r\n')
            restored = finder.detail(exact, '1', exact_args, timing='parse')
            compare(term.until(prompt), restored)
            # Cancel a real blocked history query, after completing the settings wizard.
            with owner.cursor() as cur:
                cur.execute('LOCK sql_apm.mpp_sql_text IN ACCESS EXCLUSIVE MODE')
            term.send('h\n')
            term.until('可选 1-1000：')
            term.send('2\n')
            term.until('指定起止时间 [1]：')
            term.send('1\n')
            term.until('最慢在前 [1]：')
            term.send('1\n')
            time.sleep(0.3)
            term.send('\x03')
            cancelled = term.until(prompt, timeout=5)
            assert '保留原设置' in cancelled
            owner.rollback()
            full_and_keep(restored)
        finally:
            owner.rollback()
            term.close()
    print('PASS: history settings, persistence, empty windows, reset and atomic input/query cancellation at 80/200 columns', flush=True)


def verify_drilldown(dsn, owner):
    from sql_apm.cli.sqlfind import arguments, search
    from sql_apm.cli.sqlfind_output import Output
    from sql_apm.storage.sqlfind import Finder
    from database import dashboard_data as data
    finder = Finder(dsn, 'sql_apm')
    for width in (80, 200):
        term = Terminal(dsn, ['--mode', 'words'], width)
        listing, prompt = 'Ctrl-C 返回输入。', 'Ctrl-C 返回列表。'
        try:
            args = arguments([])
            term.until('之后 q 退出。')
            term.send('wide_\n')
            term.until(listing)
            term.send('o\n')
            term.until('取消。')
            term.send('9\n')
            term.until('请选择 1 或 2。')
            term.until('取消。')
            term.send('2\n')
            rendered = term.until(listing)
            args.order = 'recent'
            expected = search(finder, b'wide_', args)
            stream = io.StringIO()
            Output(width, stream).candidates(expected, args)
            assert stream.getvalue().strip() in rendered
            assert len(expected['candidates']) == 50 and 'wide_59' in expected['candidates'][0]['example']
            for cancellation in ('q\n', '\x03', '\n'):
                term.send('o\n')
                term.until('取消。')
                term.send(cancellation)
                assert '保留原排序和列表' in term.until(listing)
            with owner.cursor() as cur:
                cur.execute('LOCK sql_apm.mpp_sql_text IN ACCESS EXCLUSIVE MODE')
            check_help(term, listing, width)
            term.send('o\n')
            term.until('取消。')
            term.send('1\n')
            time.sleep(0.3)
            term.send('\x03')
            assert '保留原排序和列表' in term.until(listing)
            owner.rollback()
            term.send('l\n')
            assert stream.getvalue().strip() in term.until(listing)
            term.send('n\n')
            term.until('q 退出')
            term.send('1\n')
            term.until('之后 q 退出。')
            term.send('orders 2026-06-26\n')
            assert '排序: 最近执行的在前' in term.until(listing)
            found = search(finder, b'orders 2026-06-26', args)
            term.send('1\n')
            term.until(prompt)
            detail = finder.detail(found, '1', args)

            def compare(text, expected, method='history'):
                stream = io.StringIO()
                if method == 'originals':
                    expected = finder.originals(expected)
                getattr(Output(width, stream), method)(expected)
                assert stream.getvalue().strip() in text, text[-2000:]

            term.send('v\n')
            compare(term.until(prompt), detail, 'originals')
            term.send('h\n')
            for marker, value in [('可选 1-1000：', '1000'), ('指定起止时间 [1]：', '1'),
                                  ('最慢在前 [1]：', '9'), ('最慢在前 [1]：', '2')]:
                term.until(marker)
                term.send(value + '\n')
            slow = finder.detail(found, '1', args, limit=1000, order='slowest')
            compare(term.until(prompt), slow)
            number = slow['executions'][0]['original_number']
            original = slow['executions'][0]['sql_id']
            frozen = (slow['start'], slow['end'])
            term.send(f'v {number}\n')
            single = finder.detail(found, '1', args, original=original, limit=1000, order='slowest', window=frozen)
            single['window_kept'] = True
            compare(term.until(prompt), single)
            with owner.cursor() as cur:
                cur.execute('LOCK sql_apm.mpp_sql_text IN ACCESS EXCLUSIVE MODE')
            check_help(term, prompt, width)
            check_help(term, prompt, width)
            rendered = check_full_text(term, single, width)
            owner.rollback()
            assert single['text']['sql_text'] in rendered
            term.send('v 999999\n')
            assert '原文编号不存在' in term.until(prompt)
            term.send('v\n')
            compare(term.until(prompt), single, 'originals')
            term.send('b\n')
            term.until(listing)
            term.send('1\n')
            compare(term.until(prompt), single)
            identity = next(n for n, r in enumerate(slow['identities'], 1) if r['scope_id'] == 'C2')
            term.send(f'i {identity}\n')
            changed = finder.detail(found, '1', args, identity=identity, original=original, limit=1000, order='slowest')
            compare(term.until(prompt), changed)
            term.send('t request\n')
            compare(term.until(prompt), changed)
            check_help(term, prompt, width)
            check_full_text(term, changed, width)
            for scope in ('all', 'matched'):
                term.send('v ' + scope + '\n')
                changed = finder.detail(found, '1', args, identity=identity, original=scope,
                    limit=1000, order='slowest', window=(changed['start'], changed['end']))
                changed['window_kept'] = True
                compare(term.until(prompt), changed)
                term.send('v\n')
                compare(term.until(prompt), changed, 'originals')
            with owner.cursor() as cur:
                cur.execute('LOCK sql_apm.mpp_sql_text IN ACCESS EXCLUSIVE MODE')
            term.send('v all\n')
            time.sleep(0.3)
            term.send('\x03')
            assert '保留原选择' in term.until(prompt)
            owner.rollback()
            term.send('v\n')
            compare(term.until(prompt), changed, 'originals')
            with owner.cursor() as cur:
                cur.execute('LOCK sql_apm.mpp_sql_text IN ACCESS EXCLUSIVE MODE')
            term.send('v\n')
            time.sleep(0.3)
            term.send('\x03')
            cancelled = term.until(prompt)
            assert '取消' in cancelled and '保留原选择' in cancelled, repr(cancelled)
            owner.rollback()
            check_full_text(term, changed, width)
            term.send('n\n')
            term.until('q 退出')
            term.send('3\n')
            term.until('之后 q 退出。')
            term.send(data.EXTENDED + '\n\x04')
            term.until(listing)
            term.send('1\n')
            exact_args = arguments(['--mode', 'exact'])
            exact = search(finder, data.EXTENDED.encode(), exact_args)
            compare(term.until(prompt), finder.detail(exact, '1', exact_args, limit=1000, order='slowest'))
            term.send('h\n')
            term.until('可选 1-1000：')
            term.send('r\n')
            compare(term.until(prompt), finder.detail(exact, '1', exact_args))
        finally:
            owner.rollback()
            term.close()
    print('PASS: interactive search sort/requery/cancel, original list/selection/trace, history sort and persistence at 80/200 columns', flush=True)


def verify(dsn, directory, owner):
    from sql_apm.cli.sqlfind_output import cells
    from verify_sqlfind import command
    verify_input()
    verify_help(dsn)
    verify_operations(dsn, owner)
    verify_history(dsn, owner)
    verify_drilldown(dsn, owner)
    for width in (80, 200):
        term = Terminal(dsn, ['--mode', 'words'], width)
        try:
            first = term.until('之后 q 退出。')
            assert '请选择检索方式' not in first
            term.send('\n')
            term.until('输入为空')
            term.until('之后 q 退出。')
            term.send('orders ' * 21 + '\n')
            term.until('超过 20 个')
            term.until('之后 q 退出。')
            # Pasted extra lines are drained; q below must not execute as a menu action.
            term.send('orders\nq\n')
            rendered = term.until('输入标记看详情')
            assert all(cells(line) <= width for line in rendered.split('时间均为北京时间', 1)[1].splitlines())
            if width == 200:
                assert '  原文:' not in rendered
            expected = command(dsn, ['--width', str(width), 'orders']).stdout.decode()
            assert expected.strip() in rendered
            term.until('Ctrl-C 返回输入。')
            term.send('1\n')
            detail = term.until('Ctrl-C 返回列表。')
            assert '当前基线' in detail and '最近执行' in detail
            expected = command(dsn, ['--width', str(width), 'orders', '--show', '1']).stdout.decode()
            assert expected.strip() in detail
            term.send('i 2\n')
            term.until('Ctrl-C 返回列表。')
            term.send('t requesx\x7ft\x1b[A\x1b[B\n')
            changed = term.until('Ctrl-C 返回列表。')
            assert '计时类别: 请求整体' in changed and '该身份没有所选计时类别' not in changed
            term.send('f\n')
            term.until('Ctrl-C 返回列表。')
            term.send('b\n')
            term.until('Ctrl-C 返回输入。')
            term.send('n\n')
            term.until('q 退出')
            term.send('2\n')
            term.until('之后 q 退出。')
            raw = "SELECT '中文', 'a\\b', \"引号\"\n\n\tFROM orders\nWHERE id=1"
            term.send(raw + '\n\x04')
            multiline = term.until('Ctrl-C 返回输入。')
            # The database's note reports the exact bytes received, including blank lines and tabs.
            assert hashlib.sha256(raw.encode()).hexdigest() in multiline.replace('\n', '')
            term.send('n\n')
            term.until('q 退出')
            term.send('3\n')
            term.until('之后 q 退出。')
            term.send('SELECT 1\n\x04')
            term.until('Ctrl-C 返回输入。')
            term.send('\x03')
            term.until('已取消')
            term.until('之后 q 退出。')
            check_operations(term)
        finally:
            term.close()
    # Lock the synthetic text table only, to make cancellation deterministic.
    term = Terminal(dsn, ['--mode', 'words'])
    try:
        term.until('之后 q 退出。')
        with owner.cursor() as cur:
            cur.execute('LOCK sql_apm.mpp_sql_text IN ACCESS EXCLUSIVE MODE')
        term.send('orders\n')
        time.sleep(0.3)
        term.send('\x03')
        cancelled = term.until('退出。', timeout=5)
        assert '取消' in cancelled
        owner.rollback()
    finally:
        owner.rollback()
        term.close()
    # Temporary account home: Bash login startup, no DSN, idempotence and exact restoration.
    spec = importlib.util.spec_from_file_location('sqlfind_configure', ROOT / 'scripts/sqlfind/configure.py')
    configure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configure)
    home = directory / 'login-home'
    home.mkdir()
    password = home / 'pgpass'
    password.write_text('# local synthetic test uses socket trust\n')
    password.chmod(0o600)
    profile = home / '.bash_profile'
    original = b'export PGUSER=existing_psql_user\nexport OTHER_SETTING=retained\n'
    profile.write_bytes(original)
    values = dict(host=str(directory / 'socket'), port=55473, dbname='sql_apm', user='sql_apm_ro',
                  passfile=str(password), schema='sql_apm')
    configure.install(home, ROOT, values)
    assert configure.install(home, ROOT, values) == []
    environment = {k: v for k, v in os.environ.items() if k != 'SQL_APM_DSN' and not k.startswith('PG')}
    environment['HOME'] = str(home)  # isolated login test, never a shell-wide override
    done = subprocess.run(['bash', '--login', '-c',
        'test "$PGUSER" = existing_psql_user && test "$OTHER_SETTING" = retained && sqlfind'],
        input=b'orders', stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd='/tmp', timeout=30)
    assert done.returncode == 0 and b'SQL_APM_DSN' not in done.stdout, done.stderr
    configure.uninstall(home)
    assert profile.read_bytes() == original
    print('PASS: PTY workflows, paste bytes, cancellation, 80/200 columns and isolated login configuration', flush=True)


if __name__ == '__main__':
    verify_input()
