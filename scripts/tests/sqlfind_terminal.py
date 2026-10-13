"""PTY and login checks used by verify_sqlfind; no real account configuration."""
import hashlib
import importlib.util
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import termios
import time
import fcntl

ROOT = Path(__file__).resolve().parents[2]


class Terminal:
    def __init__(self, dsn, args=(), width=80, env=None):
        pid, self.fd = os.forkpty()
        if pid == 0:
            os.chdir('/tmp')
            os.execve(str(ROOT / 'sqlfind'), ['sqlfind', *args],
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


def verify(dsn, directory, owner):
    from sql_apm.cli.sqlfind_output import cells
    from verify_sqlfind import command
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
            term.send('t request\n')
            term.until('Ctrl-C 返回列表。')
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
