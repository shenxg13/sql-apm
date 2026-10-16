#!/usr/bin/env python3
"""Issue #65: synthetic master -> private sshd as sfmon -> intake -> real daily CLI.

Reuses the #54 private SSH/PG and user-systemd fixtures. No production connections,
system clock changes, or SSH configuration changes. --timer installs and removes
uniquely named user units. Platform su/hostname/date are test stand-ins only.
"""
import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts/daily'), str(ROOT / 'scripts/db'), str(ROOT / 'tests'), str(ROOT)]
from verify_fetch import Sandbox
from verify import instance, Verification
from verify_daily import Site, lines, many, one, write_csv
from verify_timer import Units, upcoming, wait_until, wait_for, systemctl
from sql_apm.storage.ingestion import connect


def run(words, **kwargs):
    return subprocess.run([str(w) for w in words], capture_output=True, text=True, timeout=120, **kwargs)


def snapshot(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        info = path.lstat()
        result[str(path.relative_to(directory))] = [info.st_size, info.st_mtime_ns, info.st_mode,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None]
    return result


class PushBox(Sandbox):
    def __init__(self, sshd, directory):
        super().__init__(sshd, directory)
        assert self.user == 'sfmon', 'U12 requires the private sshd login to be sfmon'
        self.landing = directory / 'landing'
        self.landing.mkdir()
        self.number = 0
        with (directory / 'ssh_config_client').open('a') as stream:
            stream.write('Port %d\n' % self.port)
        scripts = {
            'su': '#!/bin/bash\nshift 3\nexec bash -c "$1"\n',
            'hostname': '#!/bin/bash\nprintf "%s\\n" "$PUSH_TEST_HOST"\n',
            'date': '#!/bin/bash\nif [[ $1 == +%F ]]; then printf "%s\\n" "$PUSH_TEST_TODAY"; else exec /usr/bin/date "$@"; fi\n',
        }
        for name in ('ssh', 'scp'):
            scripts[name] = '#!/bin/bash\nprintf "%s\\n" "$*" >> "$PUSH_TEST_CALLS"\n'
            if name == 'ssh':
                scripts[name] += 'if [[ ${PUSH_TEST_MARKER_FAIL:-} == yes && ${*: -1} == "cat > "* ]]; then exit 23; fi\n'
            scripts[name] += 'exec %s -F "$FETCH_TEST_SSH_CONFIG" "$@"\n' % shutil.which(name)
        for name, content in scripts.items():
            path = directory / 'bin' / name
            path.write_text(content)
            path.chmod(0o755)
        self.calls = directory / 'calls'

    def push(self, today=None, days='', host='master-a', source=None, **extra):
        self.number += 1
        self.calls.write_text('')
        destination = 'sub/unit/' + host + '/Full/task-%d' % self.number
        env = dict(os.environ, PATH=str(self.directory / 'bin') + ':' + os.environ['PATH'],
                   FETCH_TEST_SSH_CONFIG=str(self.directory / 'ssh_config_client'), PUSH_TEST_CALLS=str(self.calls),
                   PUSH_TEST_HOST=host, PUSH_TEST_TODAY=str(today or date.today()),
                   RCLONE_DEST_PATH=destination, RCLONE_BUCKET_NAME='bucket', PLAN_ID='synthetic-plan',
                   backHomePath=str(self.landing), backupFilePath=str(source or self.directory / 'source'),
                   appUser=self.user, uploadServerIp='127.0.0.1', pushDays=str(days), **extra)
        done = run(['bash', ROOT / 'daily/push-logs.sh'], env=env)
        return done, self.landing / 'bucket' / destination

    def intake_config(self, sources=None):
        path = self.directory / 'intake.conf'
        sources = sources or {'master-a': ('S1', self.directory / 'inbox')}
        path.write_text('landing %s\n' % self.landing + ''.join(
            'source %s %s %s\n' % (name, host, inbox) for host, (name, inbox) in sources.items()))
        return path

    def intake(self, conf=None):
        return run([ROOT / 'daily/intake-logs.sh', '--config', conf or self.intake_config()],
                   env=dict(os.environ, SQL_APM_PYTHON=sys.executable))


def push_checks(box, ok):
    today = date.today()
    source = box.directory / 'source'
    for n in range(26):
        box.put(today - timedelta(days=n))
    rotated = box.put(today - timedelta(days=1), '_000000.csv.3', 2000)
    (source / 'notes.txt').write_text('not a log')
    (source / ('gpdb-' + str(today - timedelta(days=2)) + '_010101.csv')).mkdir()
    before = snapshot(source)
    # Three accepted windows, complete metadata, flattening and local immutability.
    for days in ('', '3', '24'):
        done, destination = box.push(days=days)
        assert done.returncode == 0, (done.stdout, done.stderr)
        expected = {p.name for p in source.iterdir() if p.is_file() and p.name.startswith('gpdb-') and
                    str(today - timedelta(days=int(days or 7))) <= p.name[5:15] < str(today)}
        assert {p.name for p in destination.iterdir()} == expected | {'push.complete'}
        text = (destination / 'push.complete').read_text()
        assert text.startswith('SQL_APM_PUSH\t1\nhost\tmaster-a\ntoday\t%s\ndays\t%s\n' % (today, days or 7))
        for name in expected:
            assert 'file\t%s\t%d\n' % (name, (source / name).stat().st_size) in text
        assert 'cat > ' in box.calls.read_text() and '/push.complete.tmp ' in box.calls.read_text()
        assert ' && mv -f ' in box.calls.read_text()
        assert snapshot(source) == before
    ok('U2-U5 default 7 / 3 / 24 days, rotation, exclusions, manifest and atomic remote rename; master snapshot unchanged')

    for field in ('RCLONE_DEST_PATH', 'RCLONE_BUCKET_NAME', 'backHomePath'):
        # Direct overrides cannot be duplicate keyword arguments in dict().
        original = (ROOT / 'daily/push-logs.sh').read_text()
        env = dict(os.environ, PATH=str(box.directory / 'bin') + ':' + os.environ['PATH'],
                   PUSH_TEST_CALLS=str(box.calls), RCLONE_DEST_PATH='task', RCLONE_BUCKET_NAME='bucket',
                   backHomePath=str(box.landing), pushDays='7')
        env[field] = ''
        box.calls.write_text('')
        done = run(['bash'], input=original, env=env)
        assert done.returncode != 0 and box.calls.read_text() == '', (field, done.stdout, done.stderr)
    for days in ('0', '-1', '1.5', 'abc', '367', '1000', ' '):
        done, _ = box.push(days=days)
        assert done.returncode != 0 and box.calls.read_text() == '', (days, done.stdout)
    duplicate = box.directory / 'duplicate'
    duplicate.mkdir()
    shutil.copy2(rotated, duplicate / rotated.name)
    done, _ = box.push(source=str(source) + ' ' + str(duplicate))
    assert done.returncode != 0 and box.calls.read_text() == ''
    ok('U3-U4 duplicate names, empty destination fields and invalid days refuse before any SSH connection')

    rotated.chmod(0)
    done, destination = box.push()
    rotated.chmod(before[rotated.name][2])
    assert done.returncode != 0 and not (destination / 'push.complete').exists(), (done.stdout, done.stderr)
    done, destination = box.push(PUSH_TEST_MARKER_FAIL='yes')
    assert done.returncode == 23 and not (destination / 'push.complete').exists()
    empty = box.directory / 'empty-source'
    empty.mkdir()
    done, destination = box.push(source=empty)
    assert done.returncode == 0 and (destination / 'push.complete').read_text().count('\n') == 4
    missing = source / ('gpdb-' + str(today - timedelta(days=3)) + '_000000.csv')
    saved = missing.read_bytes()
    missing.unlink()
    done, _ = box.push(days=3)
    assert done.returncode == 0
    missing.write_bytes(saved)
    os.utime(missing, ns=(before[missing.name][1], before[missing.name][1]))
    assert snapshot(source) == before
    ok('U2/U3/U5 real SCP failure, marker failure exit 23, empty manifest and missing day; master still unchanged')


def chain_checks(box, pg_bin, timer, ok):
    with instance(pg_bin) as (directory, env):
        Verification(pg_bin, directory, env).init()
        dsn = 'host=%s port=55473 dbname=sql_apm user=sql_apm' % (directory / 'socket')
        site = Site(directory, dsn, dict(S1='C1', S2='C2', M1='R1', M2='R2'))
        site.settings.update(build=dict(interval_days='off'), cleanup=dict(enabled=False), raw_files=dict(retention_days='off'))
        site.write()
        conf = box.intake_config({'master-a': ('S1', site.inbox('S1')), 'master-b': ('S2', site.inbox('S2'))})
        today = date.today()
        source = box.directory / 'source'
        for n in range(1, 25):
            write_csv(source / ('gpdb-%s_000000.csv' % (today - timedelta(days=n))), lines(today - timedelta(days=n), base=n * 10))
        # First copy 24 days, then repeat the routine 7-day window: inode unchanged.
        done, _ = box.push(days=24)
        assert done.returncode == 0, done.stderr
        done = box.intake(conf)
        assert done.returncode == 0, done.stdout
        assert len(list(site.inbox('S1').glob('*.complete'))) == 24
        before = {p.name: p.stat().st_ino for p in site.inbox('S1').iterdir()}
        done, _ = box.push()
        assert done.returncode == 0
        done = box.intake(conf)
        assert done.returncode == 0 and before == {p.name: p.stat().st_ino for p in site.inbox('S1').iterdir()}
        ok('U13 first 24-day push has all 24 markers; default 7-day push changes no received inode')

        # A second source follows five simulated daily pushes. All data dates are
        # before the real local today, so the unmodified daily CLI can consume them.
        other_source = box.directory / 'master-b'
        other_source.mkdir()
        start = today - timedelta(days=5)
        expected = []
        for index in range(5):
            day = start + timedelta(days=index) - timedelta(days=1)
            path = other_source / ('gpdb-%s_000000.csv' % day)
            write_csv(path, lines(day, base=1000 + index * 10))
            expected.append(path)
            if index == 3:  # intake before the late push
                assert box.intake(conf).returncode in (0, 1)
                done = site.cli('daily', 'run', '--config', site.path)
                assert done.returncode == 0, (done.stdout, done.stderr)
            if index == 1:
                path.chmod(0)  # real SCP failure
            pushed, _ = box.push(today=day + timedelta(days=1), host='master-b', source=other_source)
            path.chmod(0o644)
            assert (pushed.returncode != 0) == (index == 1), (pushed.stdout, pushed.stderr)
            if index not in (2, 3):  # missed intake, then a late push; next day catches both
                received = box.intake(conf)
                assert received.returncode in (0, 1), received.stdout
            done = site.cli('daily', 'run', '--config', site.path)
            assert done.returncode == 0, (done.stdout, done.stderr)
        for original, manual in ((source, 'M1'), (other_source, 'M2')):
            for path in original.iterdir():
                shutil.copy2(path, site.inbox(manual) / path.name)
                (site.inbox(manual) / (path.name[5:15] + '.complete')).touch()
        done = site.cli('daily', 'run', '--config', site.path)
        assert done.returncode == 0, (done.stdout, done.stderr)
        db = connect(dsn, 'sql_apm')
        try:
            def totals(source_id):
                return (
                    many(db, '''SELECT batch_id,state FROM import_batch WHERE batch_id LIKE %s ORDER BY batch_id''', ('daily:' + source_id + ':%',)),
                    many(db, 'SELECT checksum_value,byte_count FROM source_file WHERE source_id=%s ORDER BY checksum_value', (source_id,)),
                    one(db, 'SELECT count(*) FROM evidence_record r JOIN source_file f USING(file_id) WHERE f.source_id=%s', (source_id,)))
            for actual, manual, days in (('S1', 'M1', 24), ('S2', 'M2', 5)):
                a, b = totals(actual), totals(manual)
                assert [(name.replace(actual, 'source'), state) for name, state in a[0]] == [
                    (name.replace(manual, 'source'), state) for name, state in b[0]]
                assert len(a[0]) == days and all(state == 'complete' for _, state in a[0])
                assert a[1:] == b[1:] and len(a[1]) == days and a[2] == days * 5, (a, b)
            assert one(db, 'SELECT count(*) FROM mpp_daily_problems()') == 0
            done = site.cli('daily', 'status')
            assert done.returncode == 0 and 'files_without_marker' not in done.stdout
            ok('U10/U12 real daily CLI: two masters, failed push, missed intake, late push; 29 unique day batches / 29 files / 145 records equal manual input; no problems')
            if timer:
                timer_check(box, site, db, conf, ok)
        finally:
            db.close()


def timer_check(box, site, db, conf, ok):
    assert systemctl('is-system-running', ok=False) in ('running', 'degraded'), 'no systemd user manager'
    # One invalid push makes intake return 1; a separately sealed new date must
    # still reach daily run. A fetch config with no candidate source days exercises
    # the second pre-command through real SSH without altering the intake result.
    invalid = box.landing / 'incomplete'
    invalid.mkdir()
    (invalid / 'gpdb-2000-01-01_000000.csv').write_text('incomplete')
    fresh = date.today() - timedelta(days=26)
    staging = box.directory / 'timer-source'
    staging.mkdir()
    write_csv(staging / ('gpdb-%s_000000.csv' % fresh), lines(fresh))
    pushed, _ = box.push(days=30, source=staging)
    assert pushed.returncode == 0
    fetch = box.directory / 'timer-fetch.conf'
    fetch.write_text('T sfmon@127.0.0.1 %s %s %d\n' % (box.directory / 'empty', site.inbox('S1'), box.port))
    units = Units('sql-apm-push-verify-%d' % os.getpid(), site)
    try:
        (moment,) = upcoming(1, lead=3)
        units.install([moment], intake=conf, fetch=fetch, environment={
            'PATH': str(box.directory / 'bin') + ':' + os.environ['PATH'],
            'FETCH_TEST_SSH_CONFIG': str(box.directory / 'ssh_config_client'), 'PUSH_TEST_CALLS': str(box.calls)})
        service = units.files[0].read_text()
        assert service.index('intake-logs.sh') < service.index('fetch-logs.sh') < service.index('ExecStart=')
        assert service.count('ExecStartPre=-') == 2
        wait_until(moment)
        wait_for(lambda: one(db, "SELECT count(*) FROM import_batch WHERE batch_id=%s AND state='complete'", ('daily:S1:' + str(fresh),)) == 1,
                 30, 'timer consumed the received day')
        wait_for(lambda: units.show('ActiveState')['ActiveState'] == 'inactive', 30, 'timer finished')
        shown = units.show('Result', 'ExecMainStatus')
        assert shown == dict(Result='success', ExecMainStatus='0'), shown
        journal = systemctl('show', units.name + '.service', '-p', 'ExecStartPre')
        assert 'status=1' in journal and 'intake-logs.sh' in journal and 'fetch-logs.sh' in journal, journal
        ok('U14 user systemd timer: intake exit 1 ignored, then real fetch, then daily run imports the received day')
    finally:
        units.remove()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sshd-root', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--timer', action='store_true')
    args = parser.parse_args()
    started = time.monotonic()
    def ok(label):
        print('PASS: ' + label, flush=True)
    # Separate fixtures keep intentional invalid pushes out of the end-to-end run.
    parent = ROOT / 'var/issue65'
    parent.mkdir(parents=True, exist_ok=True)
    for function in ('push', 'chain'):
        with tempfile.TemporaryDirectory(prefix='sql-apm-push-', dir=parent) as temporary, PushBox(args.sshd_root.resolve(), Path(temporary)) as box:
            if function == 'push':
                push_checks(box, ok)
            else:
                chain_checks(box, args.pg_bin, args.timer, ok)
    print(json.dumps(dict(result='passed', seconds=round(time.monotonic() - started, 3), timer=args.timer)))


if __name__ == '__main__':
    main()
