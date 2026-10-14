"""PTY and login checks used by verify_sqlfind; no real account configuration."""
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

    def close(self):
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
            window = (time_value('2026-06-26'), time_value('2026-06-27'))
            custom = finder.detail(found, '1', args, limit=100, window=window)
            rendered = term.until(prompt)
            compare(rendered, custom)
            assert '\n当前基线\n' not in rendered and '原文开始' not in rendered

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
                term.send('f\n')
                compare(term.until(prompt), custom)

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
            empty_window = (time_value('2030-01-01'), time_value('2030-01-02'))
            empty = finder.detail(exact, '1', exact_args, timing='parse', limit=1, window=empty_window)
            compare(term.until(prompt), empty)
            term.send('h\n')
            for end in ('可选 1-1000：', '指定起止时间 [2]：', ']：', ']：'):
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
            time.sleep(0.3)
            term.send('\x03')
            cancelled = term.until(prompt, timeout=5)
            assert '保留原设置' in cancelled
            owner.rollback()
            term.send('f\n')
            compare(term.until(prompt), restored)
        finally:
            owner.rollback()
            term.close()
    print('PASS: history settings, persistence, empty windows, reset and atomic input/query cancellation at 80/200 columns', flush=True)


def verify(dsn, directory, owner):
    from sql_apm.cli.sqlfind_output import cells
    from verify_sqlfind import command
    verify_input()
    verify_history(dsn, owner)
    for width in (80, 200):
        term = Terminal(dsn, ['--mode', 'words'], width)
        try:
            first = term.until('之后 q 退出。')
            assert '方式: 1' not in first
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
