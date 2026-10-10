#!/usr/bin/env python3
"""Check the rendered timer and service on a real systemd (units of the current user).

Installs temporary units under ~/.config/systemd/user with a name of their own, runs
them against a private disposable PostgreSQL 17 instance and synthetic logs, and
removes them again. Needs no root. Times are shortened: the timer is set to the next
minutes and the run-time limit to seconds. With --sshd-root the pull step of the
service is exercised against a private sshd as well (see verify_fetch.py).

A run is made to last by holding a table lock it has to wait for; that is a test
device, the product is not changed for it.
"""
import argparse
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts/db'), str(ROOT / 'scripts/daily')]
from verify import instance, Verification
from verify_daily import Site, one, many, problems
from verify_fetch import Sandbox
from sql_apm.daily import inbox
from sql_apm.storage.ingestion import connect

UNITS = Path.home() / '.config/systemd/user'
LOCK = "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND objid::bigint=(hashtextextended('C1',1835101) & 4294967295)"


def systemctl(*words, ok=True):
    done = subprocess.run(['systemctl', '--user'] + list(words), capture_output=True, text=True)
    assert not ok or done.returncode == 0, (words, done.stdout, done.stderr)
    return done.stdout.strip()


def upcoming(count, lead=12):
    first = (datetime.now() + timedelta(seconds=lead)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    return [first + timedelta(minutes=n) for n in range(count)]


def wait_until(moment, extra=0.0):
    time.sleep(max(0.0, (moment - datetime.now()).total_seconds() + extra))


def wait_for(condition, seconds, label):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, label
        time.sleep(0.2)


class Units:
    def __init__(self, name, site):
        self.name, self.site = name, site
        self.files = [UNITS / (name + '.service'), UNITS / (name + '.timer')]
        self.drop_in = UNITS / (name + '.service.d')

    def install(self, times, max_hours=12.0, fetch=None, environment=None):
        self.remove()
        words = [sys.executable, ROOT / 'scripts/daily/install.py', '--user-unit', '--output', UNITS, '--name', self.name,
                 '--config', self.site.path, '--dsn', self.site.dsn, '--max-hours', max_hours, '--check-seconds', 2]
        for moment in times:
            words += ['--at', moment.strftime('%H:%M')]
        if fetch:
            words += ['--fetch-config', fetch]
        done = subprocess.run([str(w) for w in words], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        if environment:
            self.drop_in.mkdir()
            (self.drop_in / 'test.conf').write_text('[Service]\n' + ''.join('Environment="%s=%s"\n' % pair for pair in environment.items()))
        systemctl('daemon-reload')
        systemctl('start', self.name + '.timer')

    def remove(self):
        systemctl('stop', self.name + '.timer', ok=False)
        systemctl('stop', self.name + '.service', ok=False)
        for path in self.files:
            path.unlink(missing_ok=True)
        if self.drop_in.exists():
            (self.drop_in / 'test.conf').unlink(missing_ok=True)
            self.drop_in.rmdir()
        systemctl('daemon-reload')
        systemctl('reset-failed', self.name + '.service', ok=False)
        # The stamp of a persistent timer outlives the unit file.
        for stamp in (Path.home() / '.local/share/systemd/timers').glob('stamp-' + self.name + '.timer'):
            stamp.unlink()

    def show(self, *properties):
        text = systemctl('show', self.name + '.service', '-p', ','.join(properties))
        return dict(line.split('=', 1) for line in text.splitlines())


def verify(pg_bin, sshd_root):
    passed = []
    def ok(label):
        passed.append(label)
        print('PASS: ' + label, flush=True)
    assert systemctl('is-system-running', ok=False) in ('running', 'degraded'), 'no systemd user manager'
    UNITS.mkdir(parents=True, exist_ok=True)
    today = date.today()
    day = iter(today - timedelta(days=n) for n in range(40, 1, -1))
    with instance(pg_bin) as (directory, env):
        Verification(pg_bin, directory, env).init()
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        db = connect(dsn, 'sql_apm')
        db.autocommit = True
        site = Site(directory, dsn, dict(S1='C1'))
        units = Units('sql-apm-daily-verify-%d' % os.getpid(), site)
        runs = lambda: one(db, 'SELECT count(*) FROM mpp_daily_run')
        newest = lambda: many(db, 'SELECT started_by,state,failed,started_at FROM mpp_daily_runs() ORDER BY started_at DESC LIMIT 1')[0]
        def block():
            holder = connect(dsn, 'sql_apm')
            with holder.cursor() as cur:
                cur.execute('LOCK TABLE task IN ACCESS EXCLUSIVE MODE')
            return holder
        def release(holder):
            holder.rollback()
            holder.close()
        try:
            # The rendered system unit names the user; the user unit runs as the owner of its manager.
            with tempfile.TemporaryDirectory() as temporary:
                done = subprocess.run([sys.executable, str(ROOT / 'scripts/daily/install.py'), '--output', temporary, '--config', str(site.path),
                                       '--dsn', dsn, '--user', 'apm-user', '--at', '17:00', '--at', '05:30'], capture_output=True, text=True)
                assert done.returncode == 0, done.stderr
                service = (Path(temporary) / 'sql-apm-daily.service').read_text()
                timer = (Path(temporary) / 'sql-apm-daily.timer').read_text()
                assert '\nUser=apm-user\n' in service and '\nType=simple\n' in service and '\nRuntimeMaxSec=43200s\n' in service
                assert '--trigger timer' in service and 'ExecStartPre' not in service
                assert timer.count('OnCalendar=') == 2 and '\nOnCalendar=*-*-* 17:00:00\n' in timer and '\nPersistent=true\n' in timer
                for words in (['--at', '25:00'], ['--at', '17:00', '--at', '17:00'], ['--max-hours', '0'], ['--dsn', 'host=x password=y'],
                              ['--dsn', 'postgresql://sql_apm:y@db.example/sql_apm'], ['--dsn', 'postgresql://sql_apm@db.example/sql_apm?password=y'],
                              ['--name', 'Bad Name'], ['--check-seconds', '-1']):
                    refused = Path(temporary) / 'refused'
                    bad = subprocess.run([sys.executable, str(ROOT / 'scripts/daily/install.py'), '--output', str(refused), '--config', str(site.path),
                                          '--dsn', dsn, '--user', 'apm-user'] + words, capture_output=True, text=True)
                    assert bad.returncode == 1 and not refused.exists(), words
            ok('D13 the rendered system unit runs as the named user with the default 12-hour limit and daily times; wrong values, and a '
               'connection string with a password in keyword or URI form, are refused and no unit file is written')

            # A: starts at the set time, as the current user, and only runs the command.
            site.put('S1', next(day))
            (moment,) = upcoming(1)
            units.install([moment])
            before = runs()
            wait_until(moment)
            wait_for(lambda: runs() == before + 1 and newest()[1] == 'finished', 30, 'run at the set time')
            started_by, state, failed, started_at = newest()
            delay = (started_at.astimezone().replace(tzinfo=None) - moment).total_seconds()
            assert (started_by, state, failed) == ('timer', 'finished', False) and 0 <= delay < 10, (started_by, state, failed, delay)
            shown = units.show('Result', 'ExecMainStatus')
            assert shown == dict(Result='success', ExecMainStatus='0'), shown
            ok('D13/D15 the timer starts the run at the set time (%.1f s after the minute); without a pull configured only the command runs' % delay)

            # B: while a run is still going, the next time does not start a second one.
            site.put('S1', next(day))
            first, second = upcoming(2)
            units.install([first, second])
            holder = block()
            before = runs()
            wait_until(first)
            wait_for(lambda: one(db, LOCK) == 1, 30, 'run waiting behind the lock')
            pid = int(units.show('MainPID')['MainPID'])
            owner = [line.split()[1] for line in Path('/proc/%d/status' % pid).read_text().splitlines() if line.startswith('Uid:')]
            assert owner == [str(os.getuid())], owner
            wait_until(second, 5)
            commands = subprocess.run(['pgrep', '-f', '-c', 'sql_apm daily run --config ' + str(site.path)], capture_output=True, text=True).stdout.strip()
            assert runs() == before + 1 and commands == '1' and int(units.show('MainPID')['MainPID']) == pid, (runs() - before, commands)
            release(holder)
            wait_for(lambda: runs() == before + 2 and newest()[1] == 'finished', 60, 'blocked run finishing and the missed time made up')
            time.sleep(8)
            pair = many(db, 'SELECT started_at,finished_at FROM mpp_daily_run ORDER BY started_at DESC LIMIT 2')
            assert runs() == before + 2 and units.show('Result')['Result'] == 'success' and pair[0][0] >= pair[1][1], (runs() - before, pair)
            ok('D13 the unit runs as the current user; a time that comes while a run is still going starts no second run beside it; '
               'systemd makes that time up once, %.1f s after the first run has ended, and nothing more follows' % (pair[0][0] - pair[1][1]).total_seconds())

            # C: a time missed while the timer was not active is made up once when it comes back.
            site.put('S1', next(day))
            one_, two, three = upcoming(3)
            units.install([one_, two, three])
            before = runs()
            wait_until(one_)
            wait_for(lambda: runs() == before + 1 and newest()[1] == 'finished', 30, 'first run')
            systemctl('stop', units.name + '.timer')
            site.put('S1', next(day))
            wait_until(three, 5)
            assert runs() == before + 1
            systemctl('start', units.name + '.timer')
            wait_for(lambda: runs() == before + 2 and newest()[1] == 'finished', 30, 'catch-up run')
            time.sleep(8)
            assert runs() == before + 2 and newest()[0] == 'timer'
            ok('D13 two times missed while the timer was stopped are made up by exactly one run when the timer is started again')

            # D: a timer-started run that exceeds the limit is stopped although its statement is waiting, and says so itself.
            site.put('S1', next(day))
            (moment,) = upcoming(1)
            units.install([moment], max_hours=20 / 3600)
            holder = block()
            before = runs()
            wait_until(moment)
            wait_for(lambda: one(db, LOCK) == 1, 30, 'run waiting behind the lock')
            wait_for(lambda: units.show('ActiveState')['ActiveState'] == 'failed', 60, 'unit stopped by its time limit')
            shown = units.show('Result', 'ExecMainStatus', 'ExecMainStartTimestampMonotonic', 'ExecMainExitTimestampMonotonic')
            lasted = (int(shown['ExecMainExitTimestampMonotonic']) - int(shown['ExecMainStartTimestampMonotonic'])) / 1e6
            assert (shown['Result'], shown['ExecMainStatus']) == ('timeout', '130') and 20 <= lasted < 25, shown
            assert one(db, LOCK) == 0 and runs() == before + 1
            assert many(db, 'SELECT state,reason FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1') == [('aborted', 'operator_interrupt')]
            assert [r[0] for r in problems(db)] == ['run_not_finished']
            release(holder)
            ok('F008/D13 a timer-started run over its limit (20 s here) is stopped by systemd while its statement waits behind a lock: the '
               'process ends %.1f s after its start with exit 130, the run is recorded as aborted and its cluster is free at once' % lasted)

            # E: a run started by hand has no such limit, with the short-limit unit still installed.
            holder = block()
            child = site.start('daily', 'run', '--config', site.path)
            wait_for(lambda: one(db, LOCK) == 1, 30, 'manual run waiting behind the lock')
            time.sleep(30)
            assert child.poll() is None
            release(holder)
            code = child.wait(timeout=120)
            child.stdout.close()
            child.stderr.close()
            assert code == 0 and newest()[:3] == ('manual', 'finished', False) and not problems(db), (code, newest(), problems(db))
            ok('D13 a run started by hand outlasts that limit (30 s here) and finishes; it picks up the day the stopped run left')

            # F: the service is stopped by hand while its statement waits.
            site.put('S1', next(day))
            units.install([datetime.now() + timedelta(hours=6)])
            holder = block()
            before = runs()
            systemctl('start', '--no-block', units.name + '.service')
            wait_for(lambda: one(db, LOCK) == 1, 30, 'service run waiting behind the lock')
            began = time.monotonic()
            systemctl('stop', units.name + '.service')
            seconds = time.monotonic() - began
            shown = units.show('ActiveState', 'Result', 'ExecMainStatus')
            assert seconds < 10 and shown == dict(ActiveState='inactive', Result='success', ExecMainStatus='130'), (seconds, shown)
            assert one(db, LOCK) == 0 and runs() == before + 1
            assert many(db, 'SELECT started_by,state,reason FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1') == [('timer', 'aborted', 'operator_interrupt')]
            release(holder)
            done = site.cli('daily', 'run', '--config', site.path)
            assert done.returncode == 0 and newest()[:3] == ('manual', 'finished', False) and not problems(db), (done.stdout[-500:], problems(db))
            assert one(db, "SELECT count(*) FROM mpp_daily_run WHERE state='aborted'") == 2 and one(db, "SELECT count(*) FROM mpp_daily_run WHERE state='unfinished'") == 0
            ok('F008 systemctl stop while the statement of the run waits behind a lock returns after %.1f s: exit 130, the run recorded as '
               'aborted, the cluster free, the unit not failed; the next run carries on' % seconds)

            if sshd_root is None:
                print('SKIPPED: pull step of the service (no --sshd-root)', flush=True)
            else:
                with tempfile.TemporaryDirectory(prefix='sql-apm-timer-') as temporary, Sandbox(sshd_root, Path(temporary)) as box:
                    environment = dict(PATH=str(box.directory / 'bin') + ':' + os.environ['PATH'],
                                       FETCH_TEST_SSH_CONFIG=str(box.directory / 'ssh_config_client'))
                    fetch = box.directory / 'fetch.conf'
                    fetch.write_text('S1 %s@127.0.0.1 %s %s %d\n' % (box.user, box.directory / 'source', site.inbox('S1'), box.port))
                    # Pull, then run: yesterday exists only on the source when the service starts.
                    yesterday = today - timedelta(days=1)
                    site.put('S1', yesterday, marker=False).rename(box.directory / 'source' / ('gpdb-' + yesterday.isoformat() + '_000000.csv'))
                    for old in range(2, 8):      # keep the back-fill of the last week from pulling anything else
                        site.mark('S1', today - timedelta(days=old))
                    (moment,) = upcoming(1)
                    units.install([moment], fetch=fetch, environment=environment)
                    before = runs()
                    wait_until(moment)
                    wait_for(lambda: runs() == before + 1 and newest()[1] == 'finished', 60, 'pull and run')
                    assert one(db, 'SELECT state FROM import_batch WHERE batch_id=%s', (inbox.batch_id('S1', yesterday),)) == 'complete'
                    assert (site.inbox('S1') / inbox.marker_name(yesterday)).exists() and newest()[2] is False
                    ok('D15 with a pull configured the service pulls first and the run then imports the day that was pulled')
                    # A failed pull does not keep the run from a day already marked.
                    (site.inbox('S1') / inbox.marker_name(today - timedelta(days=2))).unlink()   # something the pull would want
                    environment['FETCH_TEST_SSH_CONFIG'] = str(box.write_client('stranger', known=True))
                    marked = next(day)
                    site.put('S1', marked)
                    (moment,) = upcoming(1)
                    units.install([moment], fetch=fetch, environment=environment)
                    before = runs()
                    wait_until(moment)
                    wait_for(lambda: runs() == before + 1 and newest()[1] == 'finished', 60, 'run after failed pull')
                    assert one(db, 'SELECT state FROM import_batch WHERE batch_id=%s', (inbox.batch_id('S1', marked),)) == 'complete'
                    # The pull's own exit status is kept by systemd; its failure is ignored for the run that follows.
                    pull = units.show('ExecStartPre')['ExecStartPre']
                    assert 'ignore_errors=yes' in pull and 'status=1' in pull and units.show('Result')['Result'] == 'success', pull
                    assert not (site.inbox('S1') / inbox.marker_name(today - timedelta(days=2))).exists()
                    ok('D15 when the pull fails the run still handles the day already marked')
        finally:
            units.remove()
            db.close()
    left = [path.name for path in UNITS.glob('sql-apm-daily-verify-*')]
    assert not left and 'sql-apm-daily-verify' not in systemctl('list-timers', '--all', ok=False), left
    ok('the temporary units are removed')
    print('TIMER CHECKS:', len(passed))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--sshd-root', type=Path, help='unpacked openssh-server package; also checks the pull step')
    arguments = parser.parse_args()
    verify(arguments.pg_bin, arguments.sshd_root and arguments.sshd_root.resolve())
