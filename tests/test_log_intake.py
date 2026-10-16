"""Destructive boundaries and interrupted receiving, using synthetic files only."""
from contextlib import redirect_stdout
from datetime import date, timedelta
import fcntl
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('intake', ROOT / 'daily/intake_logs.py')
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)
TODAY = date(2026, 10, 16)
DAY = '2026-10-15'
FILE = 'gpdb-' + DAY + '_000000.csv'


def marker(host='master-a', today=TODAY, files=None, days=7):
    return 'SQL_APM_PUSH\t1\nhost\t%s\ntoday\t%s\ndays\t%d\n' % (host, today, days) + ''.join(
        'file\t%s\t%d\n' % pair for pair in (files or {}).items())


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.landing = self.root / 'landing'
        self.inbox = self.root / 'inbox'
        self.other = self.root / 'other'
        for path in (self.landing, self.inbox, self.other):
            path.mkdir()
        self.conf = self.root / 'intake.conf'
        self.conf.write_text('landing %s\nsource S1 master-a %s\nsource S2 master-b %s\n' % (
            self.landing, self.inbox, self.other))
        self.fd = os.open(self.landing, intake.DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.sources = {'master-a': ('S1', self.inbox), 'master-b': ('S2', self.other)}

    def push(self, path='bucket/sub/unit/master/Full/task', host='master-a', today=TODAY, files=None):
        directory = self.landing / path
        directory.mkdir(parents=True)
        files = {FILE: b'one'} if files is None else files
        for name, content in files.items():
            (directory / name).write_bytes(content)
        (directory / intake.MARKER).write_text(marker(host, today, {name: len(value) for name, value in files.items()}))
        return directory

    def run_intake(self, age=0):
        output = io.StringIO()
        with redirect_stdout(output):
            code = intake.intake(self.fd, self.sources, 86400, TODAY, time.time() + age)
        return code, output.getvalue()

    def test_inodes_markers_existing_dates_and_shared_landing(self):
        pushed = self.push()
        inode = (pushed / FILE).stat().st_ino
        self.push('bucket/sub/unit/second/Full/task', 'master-b')
        code, output = self.run_intake()
        self.assertEqual(code, 0, output)
        self.assertEqual((self.inbox / FILE).stat().st_ino, inode)
        self.assertTrue((self.inbox / (DAY + '.complete')).is_file())
        self.assertEqual((self.other / FILE).read_bytes(), b'one')
        self.assertEqual(list(self.landing.iterdir()), [])
        before = (self.inbox / FILE).stat()
        self.push(files={FILE: b'different length'})
        code, output = self.run_intake()
        self.assertEqual(code, 0, output)
        self.assertIn('size_differences=1', output)
        self.assertEqual((self.inbox / FILE).stat(), before)

    def test_invalid_pushes_are_retained_then_expire(self):
        for reason in ('missing_marker', 'invalid_format', 'missing_file', 'file_size', 'extra_log'):
            with self.subTest(reason=reason):
                pushed = self.push(path=reason)
                if reason == 'missing_marker':
                    (pushed / intake.MARKER).unlink()
                elif reason == 'invalid_format':
                    (pushed / intake.MARKER).write_text('broken')
                elif reason == 'missing_file':
                    (pushed / FILE).unlink()
                elif reason == 'file_size':
                    (pushed / FILE).write_bytes(b'bad size')
                else:
                    (pushed / (FILE + '.1')).write_bytes(b'extra')
                code, output = self.run_intake()
                self.assertEqual(code, 1, output)
                self.assertIn(reason, output)
                self.assertTrue(pushed.exists())
                self.assertEqual(list(self.inbox.iterdir()), [])
                code, output = self.run_intake(90000)
                self.assertEqual(code, 1, output)
                self.assertFalse(pushed.exists(), output)

    def test_old_source_mtime_is_not_arrival_and_temp_is_not_complete(self):
        pushed = self.push()
        (pushed / intake.MARKER).rename(pushed / intake.TEMP)
        for path in pushed.iterdir():
            os.utime(path, (1, 1))
        self.assertEqual(self.run_intake()[0], 1)
        self.assertTrue(pushed.exists())
        self.assertEqual(list(self.inbox.iterdir()), [])
        self.run_intake(90000)
        self.assertFalse(pushed.exists())

    def test_unknown_host_including_malformed_marker_never_deleted(self):
        pushed = self.push(host='unknown')
        for malformed in (False, True):
            if malformed:
                with (pushed / intake.MARKER).open('a') as stream:
                    stream.write('broken\n')
            code, output = self.run_intake(90000)
            self.assertEqual(code, 1)
            self.assertIn('unknown_host', output)
            self.assertTrue(pushed.exists())

    def test_no_overwrite_resume_and_conflicting_unmarked_day(self):
        pushed = self.push(files={FILE: b'one', FILE + '.1': b'two'})
        os.rename(pushed / FILE, self.inbox / FILE)
        inode = (self.inbox / FILE).stat().st_ino
        self.assertEqual(self.run_intake()[0], 0)
        self.assertEqual((self.inbox / FILE).stat().st_ino, inode)
        (self.inbox / (DAY + '.complete')).unlink()
        (self.inbox / FILE).write_bytes(b'conflict')
        pushed = self.push()
        code, output = self.run_intake()
        self.assertEqual(code, 1)
        self.assertIn('local_day_conflict', output)
        self.assertEqual((self.inbox / FILE).read_bytes(), b'conflict')
        self.assertTrue((pushed / FILE).exists())
        (self.inbox / FILE).write_bytes(b'one')
        code, output = self.run_intake()
        self.assertEqual(code, 1)
        self.assertIn('local_day_conflict', output)  # extra .1 not in this push

    def test_kill_between_renames_then_resume_real_process(self):
        pushed = self.push(files={FILE: b'one', FILE + '.1': b'two'})
        inode = (pushed / FILE).stat().st_ino
        # Test instrumentation wraps the real rename, then suspends the process.
        code = '''import importlib.util, os, signal, sys
s = importlib.util.spec_from_file_location('intake', sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
original = m.rename_new
def stop(*args):
    original(*args)
    os.kill(os.getpid(), signal.SIGSTOP)
m.rename_new = stop
sys.argv = [sys.argv[1], '--config', sys.argv[2]]
raise SystemExit(m.main())
'''
        child = subprocess.Popen([sys.executable, '-c', code, ROOT / 'daily/intake_logs.py', self.conf],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not (self.inbox / FILE).exists():
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            self.assertFalse((self.inbox / (DAY + '.complete')).exists())
            child.kill()
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stderr.close()
        self.assertEqual(self.run_intake()[0], 0)
        self.assertEqual((self.inbox / FILE).stat().st_ino, inode)
        self.assertTrue((self.inbox / (DAY + '.complete')).exists())

    def test_unrelated_content_symlinks_and_outside_are_untouched(self):
        external = self.root / 'outside'
        external.mkdir()
        (external / 'keep').write_text('keep')
        (self.landing / 'link').symlink_to(external, target_is_directory=True)
        for kind in ('note', 'link', 'directory', 'fifo'):
            pushed = self.push(path=kind + '-push', files={})
            extra = pushed / 'extra'
            if kind == 'note':
                extra.write_text('keep')
            elif kind == 'link':
                extra.symlink_to(external)
            elif kind == 'directory':
                extra.mkdir()
            else:
                os.mkfifo(extra)
            code, output = self.run_intake(90000)
            self.assertEqual(code, 1)
            self.assertIn('cleanup_unexpected_content', output)
            self.assertTrue(pushed.exists())
        self.assertEqual((external / 'keep').read_text(), 'keep')
        self.assertTrue((self.landing / 'link').is_symlink())
        self.assertTrue(self.landing.exists())

    def test_symlink_log_and_marker_are_rejected_without_reading_target(self):
        for target in (FILE, intake.MARKER):
            pushed = self.push(path=target)
            (pushed / target).unlink()
            (pushed / target).symlink_to(self.conf)
            code, _ = self.run_intake(90000)
            self.assertEqual(code, 1)
            self.assertTrue((pushed / target).is_symlink())
            self.assertEqual(list(self.inbox.iterdir()), [])

    def test_oldest_push_wins_and_future_date_waits(self):
        self.push('new', files={FILE: b'new'})
        self.push('old', today=TODAY - timedelta(days=1), files={'gpdb-2026-10-14_000000.csv': b'old'})
        code, output = self.run_intake()
        self.assertEqual(code, 0)
        self.assertLess(output.index('push=old'), output.index('push=new'))
        tomorrow = self.push('future', today=TODAY + timedelta(days=1), files={'gpdb-2026-10-16_000000.csv': b'wait'})
        self.assertEqual(self.run_intake()[0], 1)
        self.assertTrue(tomorrow.exists())

    def test_configuration_overlap_and_lock(self):
        for inbox in (self.landing, self.root, self.landing / 'child'):
            inbox.mkdir(exist_ok=True)
            self.conf.write_text('landing %s\nsource S master-a %s\n' % (self.landing, inbox))
            done = subprocess.run([sys.executable, ROOT / 'daily/intake_logs.py', '--config', self.conf], capture_output=True)
            self.assertEqual(done.returncode, 2, done.stdout)
        self.conf.write_text('landing %s\nsource S master-a %s\n' % (self.landing, self.inbox))
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        done = subprocess.run([sys.executable, ROOT / 'daily/intake_logs.py', '--config', self.conf], capture_output=True, timeout=3)
        self.assertEqual(done.returncode, 1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn(b'another_intake_is_running', done.stdout)
        fcntl.flock(self.fd, fcntl.LOCK_UN)

    def test_rename_race_refuses_destination_without_overwrite(self):
        pushed = self.push()
        real = intake.rename_new
        def competing(src, dst, name):
            (self.inbox / name).write_bytes(b'competitor')
            real(src, dst, name)
        with patch.object(intake, 'rename_new', competing):
            code, _ = self.run_intake()
        self.assertEqual(code, 1)
        self.assertEqual((self.inbox / FILE).read_bytes(), b'competitor')
        self.assertEqual((pushed / FILE).read_bytes(), b'one')
        self.assertFalse((self.inbox / (DAY + '.complete')).exists())


class UnitOrderTests(unittest.TestCase):
    def test_optional_precommands_order_and_missing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conf = root / 'config'
            conf.write_text('test')
            for intake_enabled, fetch_enabled in ((False, False), (True, False), (False, True), (True, True)):
                words = [sys.executable, ROOT / 'scripts/daily/install.py', '--user-unit', '--config', conf,
                         '--output', root / 'units', '--dsn', 'dbname=sql_apm']
                if intake_enabled:
                    words += ['--intake-config', conf]
                if fetch_enabled:
                    words += ['--fetch-config', conf]
                done = subprocess.run([str(w) for w in words], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                service = (root / 'units/sql-apm-daily.service').read_text()
                expected = (['intake-logs.sh'] if intake_enabled else []) + (['fetch-logs.sh'] if fetch_enabled else [])
                lines = [line for line in service.splitlines() if line.startswith('ExecStartPre=')]
                self.assertEqual(len(lines), len(expected))
                for line, name in zip(lines, expected):
                    self.assertIn(name, line)
                    self.assertTrue(line.startswith('ExecStartPre=-'))
                    self.assertLess(service.index(line), service.index('ExecStart='))
            done = subprocess.run([str(w) for w in words + ['--intake-config', root / 'missing', '--output', root / 'refused']],
                                  capture_output=True)
            self.assertNotEqual(done.returncode, 0)
            self.assertFalse((root / 'refused').exists())


if __name__ == '__main__':
    unittest.main()
