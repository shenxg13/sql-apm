#!/usr/bin/env python3
"""Check daily/fetch-logs.sh against a real SSH server on the loopback address.

A private sshd (given by --sshd-root, an unpacked openssh-server package) runs as the
current user on a free high port with its own host key, client key and known-hosts
file below a temporary directory; nothing of the user's or the system's SSH setup is
read or changed, and production is never contacted. The "source" is a directory of
synthetic files served by that sshd.
"""
import argparse
from datetime import date, timedelta
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', Path(__file__).resolve().parents[2])).resolve()
SCRIPT = ROOT / 'daily/fetch-logs.sh'


def run(words, **options):
    return subprocess.run([str(w) for w in words], capture_output=True, text=True, **options)


class Sandbox:
    def __init__(self, sshd_root, directory):
        self.directory = directory
        self.sshd = sshd_root / 'usr/sbin/sshd'
        self.session = sshd_root / 'usr/libexec/openssh/sshd-session'
        for name in ('source', 'inbox', 'keys', 'bin', 'empty'):
            (directory / name).mkdir()
        for name in ('host', 'client', 'stranger'):
            done = run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', directory / 'keys' / name])
            assert done.returncode == 0, done.stderr
        (directory / 'keys/authorized').write_text((directory / 'keys/client.pub').read_text())
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        # OpenSSH 10 split the session helper; Kylin's older packaged sshd has no such option.
        session_option = ['SshdSessionPath ' + str(self.session)] if self.session.is_file() else []
        (directory / 'sshd_config').write_text('\n'.join([
            'Port ' + str(self.port), 'ListenAddress 127.0.0.1', 'HostKey ' + str(directory / 'keys/host'),
            'PidFile ' + str(directory / 'sshd.pid'), 'AuthorizedKeysFile ' + str(directory / 'keys/authorized'),
            *session_option, 'Subsystem sftp internal-sftp', 'UsePAM no', 'StrictModes no',
            'PasswordAuthentication no', 'KbdInteractiveAuthentication no', 'PubkeyAuthentication yes',
            'LogLevel VERBOSE', '']))
        self.user = os.environ.get('USER') or run(['id', '-un']).stdout.strip()
        self.write_client('client', known=True)
        # The script calls plain ssh and scp; these stand in front of them only to pick the
        # private client configuration. The programs and the protocol are the real ones.
        for name in ('ssh', 'scp'):
            real = shutil.which(name)
            wrapper = directory / 'bin' / name
            wrapper.write_text('#!/bin/sh\nexec ' + real + ' -F "$FETCH_TEST_SSH_CONFIG" "$@"\n')
            wrapper.chmod(0o755)
        self.process = None

    def write_client(self, key, known):
        known_hosts = self.directory / ('known_hosts' if known else 'empty/known_hosts')
        if known:
            public = (self.directory / 'keys/host.pub').read_text().split()
            known_hosts.write_text('[127.0.0.1]:%d %s %s\n' % (self.port, public[0], public[1]))
        path = self.directory / ('ssh_config_' + key + ('' if known else '_unknown'))
        path.write_text('\n'.join(['IdentityFile ' + str(self.directory / 'keys' / key), 'IdentitiesOnly yes',
                                   'UserKnownHostsFile ' + str(known_hosts), 'GlobalKnownHostsFile /dev/null',
                                   'PreferredAuthentications publickey', '']))
        return path

    def __enter__(self):
        self.log = self.directory / 'sshd.log'
        self.process = subprocess.Popen([str(self.sshd), '-D', '-e', '-f', str(self.directory / 'sshd_config')],
                                        stderr=self.log.open('w'), stdout=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while True:
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', self.port)) == 0:
                    break
            assert self.process.poll() is None and time.monotonic() < deadline, self.log.read_text()
            time.sleep(0.1)
        return self

    def __exit__(self, *_):
        self.process.send_signal(signal.SIGTERM)
        self.process.wait(timeout=10)

    def config(self, lines=None):
        path = self.directory / 'fetch.conf'
        path.write_text('# test sources\n\n' + '\n'.join(lines or [
            'S1 %s@127.0.0.1 %s %s %d' % (self.user, self.directory / 'source', self.directory / 'inbox', self.port)]) + '\n')
        return path

    def fetch(self, *words, client='ssh_config_client', path=None, timeout=120):
        env = dict(os.environ, PATH=(path or str(self.directory / 'bin')) + ':' + os.environ['PATH'],
                   FETCH_TEST_SSH_CONFIG=str(self.directory / client))
        started = time.monotonic()
        done = run(['bash', SCRIPT, '--config', self.config()] + list(words), env=env, timeout=timeout,
                   stdin=subprocess.DEVNULL)
        return done, time.monotonic() - started

    def put(self, day, suffix='_000000.csv', size=1000, where='source'):
        path = self.directory / where / ('gpdb-' + day.isoformat() + suffix)
        path.write_bytes((day.isoformat() + suffix).encode() * (size // len(day.isoformat() + suffix) + 1))
        return path

    def inbox(self):
        return sorted(p.name for p in (self.directory / 'inbox').iterdir())

    def clear(self):
        for path in (self.directory / 'inbox').iterdir():
            path.unlink()


def snapshot(directory):
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in sorted(directory.iterdir())}


def names(day, *suffixes):
    return ['gpdb-' + day.isoformat() + s for s in suffixes]


def verify(sshd_root):
    passed = []
    def ok(label):
        passed.append(label)
        print('PASS: ' + label, flush=True)
    today = date.today()
    back = lambda n: today - timedelta(days=n)
    with tempfile.TemporaryDirectory(prefix='sql-apm-fetch-') as temporary, Sandbox(sshd_root, Path(temporary)) as box:
        for n in range(1, 10):
            box.put(back(n))
        box.put(back(1), '_000000.csv.1', 2500)
        box.put(back(1), '_000000.csv.2', 10)
        box.put(back(3), '_161546.csv', 700)
        growing = box.put(today)
        (box.directory / 'source' / 'gpdb-notes.txt').write_text('not a log')
        (box.directory / 'source' / ('gpdb-' + back(2).isoformat() + '_000000.csv.gz')).write_text('not fetched')
        before = snapshot(box.directory / 'source')

        done, _ = box.fetch()
        expected = []
        for n in range(1, 8):
            expected += names(back(n), '_000000.csv') + [back(n).isoformat() + '.complete']
        expected += names(back(1), '_000000.csv.1', '_000000.csv.2') + names(back(3), '_161546.csv')
        assert done.returncode == 0 and box.inbox() == sorted(expected), (done.stdout, done.stderr, box.inbox())
        for name in expected:
            if name.endswith('.complete'):
                assert (box.directory / 'inbox' / name).stat().st_size == 0
            else:
                assert (box.directory / 'inbox' / name).read_bytes() == (box.directory / 'source' / name).read_bytes()
        total = sum((box.directory / 'source' / name).stat().st_size for name in names(back(1), '_000000.csv', '_000000.csv.1', '_000000.csv.2'))
        assert 'date=%s state=complete files=3 bytes=%d' % (back(1).isoformat(), total) in done.stdout, done.stdout
        ok('D14 without a date: yesterday and the last 7 days without a marker are fetched whole, each then gets its marker; '
           'today\'s growing file, older days and names that do not fit are not fetched')

        done, _ = box.fetch()
        assert done.returncode == 0 and done.stdout == '' and box.inbox() == sorted(expected)
        ok('D14 a second run finds every recent day marked and fetches nothing')

        done, _ = box.fetch('--date', back(9).isoformat())
        assert done.returncode == 0 and names(back(9), '_000000.csv')[0] in box.inbox() and back(9).isoformat() + '.complete' in box.inbox()
        assert back(8).isoformat() + '.complete' not in box.inbox()
        box.clear()
        done, _ = box.fetch('--from', back(9).isoformat(), '--to', back(8).isoformat())
        assert done.returncode == 0 and box.inbox() == sorted(names(back(9), '_000000.csv') + names(back(8), '_000000.csv') +
                                                              [back(9).isoformat() + '.complete', back(8).isoformat() + '.complete'])
        ok('D14 --date fetches that day only; --from/--to fetches the range')

        for words in (['--date', today.isoformat()], ['--from', back(1).isoformat(), '--to', today.isoformat()],
                      ['--date', (today + timedelta(days=1)).isoformat()], ['--date', '2026-02-30'], ['--from', back(2).isoformat()],
                      ['--date', back(2).isoformat(), '--from', back(3).isoformat(), '--to', back(2).isoformat()],
                      ['--backfill-days', 'x'], ['--nope']):
            state = box.inbox()
            done, _ = box.fetch(*words)
            assert done.returncode == 2 and box.inbox() == state, (words, done.stdout, done.stderr)
        ok('D14 today, a later day, an impossible date and wrong options are refused (exit 2) and nothing is fetched')

        # A day without files on the source: no marker, not a failure.
        box.clear()
        missing = today - timedelta(days=30)
        done, _ = box.fetch('--date', missing.isoformat())
        assert done.returncode == 0 and box.inbox() == [] and 'state=no_files' in done.stdout
        ok('D14 a day the source has no files for gets no marker and is not a failure')

        # A file that cannot be copied: no file under its final name, no marker; the next run completes the day.
        blocked = box.directory / 'source' / names(back(1), '_000000.csv.1')[0]
        blocked.chmod(0)
        done, _ = box.fetch('--date', back(1).isoformat())
        assert done.returncode == 1 and 'reason=copy_failed' in done.stdout, (done.stdout, done.stderr)
        assert box.inbox() == names(back(1), '_000000.csv'), box.inbox()
        blocked.chmod(0o644)
        os.utime(blocked, ns=(before[blocked.name][1], before[blocked.name][1]))
        done, _ = box.fetch('--date', back(1).isoformat())
        assert done.returncode == 0 and back(1).isoformat() + '.complete' in box.inbox()
        ok('D14 when a copy fails, that file never appears under its final name and the day gets no marker; a later run completes it')

        # The source file changes between the listing and the copy: the size no longer matches.
        box.clear()
        changing = box.put(back(8), '_010101.csv', 400)
        racing = box.directory / 'racing'
        racing.mkdir()
        (racing / 'ssh').write_text((box.directory / 'bin/ssh').read_text())
        (racing / 'scp').write_text('#!/bin/sh\nprintf more >> %s\n%s' % (changing, (box.directory / 'bin/scp').read_text().split('\n', 1)[1]))
        for name in ('ssh', 'scp'):
            (racing / name).chmod(0o755)
        done, _ = box.fetch('--date', back(8).isoformat(), path=str(racing))
        assert done.returncode == 1 and 'reason=size_mismatch file=' + changing.name in done.stdout, (done.stdout, done.stderr)
        assert changing.name not in box.inbox() and back(8).isoformat() + '.complete' not in box.inbox()
        assert not [name for name in box.inbox() if name.endswith('.part')], box.inbox()
        changing.unlink()
        ok('D14 a size that differs from the source leaves neither the file, a temporary file nor a marker')

        # Killed in the middle of a large copy.
        box.clear()
        large = box.put(back(8), '_020202.csv', 300 * 1024 * 1024)
        env = dict(os.environ, PATH=str(box.directory / 'bin') + ':' + os.environ['PATH'],
                   FETCH_TEST_SSH_CONFIG=str(box.directory / 'ssh_config_client'))
        child = subprocess.Popen(['setsid', 'bash', str(SCRIPT), '--config', str(box.config()), '--date', back(8).isoformat()],
                                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        part = box.directory / 'inbox' / ('.' + large.name + '.part')
        deadline = time.monotonic() + 30
        while not (part.exists() and part.stat().st_size > 0):
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        child.stdout.close()
        child.stderr.close()
        left = box.inbox()
        assert large.name not in left and back(8).isoformat() + '.complete' not in left, left
        done, _ = box.fetch('--date', back(8).isoformat())
        assert done.returncode == 0 and large.name in box.inbox() and back(8).isoformat() + '.complete' in box.inbox()
        assert not part.exists() and (box.directory / 'inbox' / large.name).stat().st_size == large.stat().st_size
        large.unlink()
        ok('D14 killed in the middle of a copy: no file under its final name and no marker; the next run copies it whole and removes the leftover')

        # A local file the source does not have.
        box.clear()
        box.put(back(2), '_999999.csv', where='inbox')
        done, _ = box.fetch('--date', back(2).isoformat())
        assert done.returncode == 1 and 'reason=local_file_not_on_source' in done.stdout and back(2).isoformat() + '.complete' not in box.inbox()
        ok('D14 a local file of that day which the source does not have keeps the marker away')

        # A marked day is sealed. However it is asked for and whatever the source shows by now,
        # nothing of it is fetched again, replaced or removed, and its marker stays with the files it sealed.
        box.clear()
        sealed_day = back(12)
        first, second = box.put(sealed_day, size=900), box.put(sealed_day, '_000000.csv.1', 1200)
        done, _ = box.fetch('--date', sealed_day.isoformat())
        assert done.returncode == 0 and 'state=complete files=2' in done.stdout, done.stdout
        def exact(directory):
            return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                    for p in sorted(directory.iterdir())}
        kept = exact(box.directory / 'inbox')
        calls = box.directory / 'scp-calls'
        counting = box.directory / 'counting'
        counting.mkdir()
        (counting / 'ssh').write_text((box.directory / 'bin/ssh').read_text())
        (counting / 'scp').write_text('#!/bin/sh\necho "$@" >> %s\nexit 1\n' % calls)   # any copy would be seen, and would fail
        for name in ('ssh', 'scp'):
            (counting / name).chmod(0o755)
        asked = (['--date', sealed_day.isoformat()], ['--from', back(13).isoformat(), '--to', back(11).isoformat()])
        def untouched(label, code, said):
            for words in asked:
                done, _ = box.fetch(*words, path=str(counting))
                line = 'date=' + sealed_day.isoformat() + ' ' + said
                assert done.returncode == code and line in done.stdout, (label, words, done.returncode, done.stdout, done.stderr)
                assert exact(box.directory / 'inbox') == kept and not calls.exists(), (label, words)
            # The daily way: other days of the period are tried (and fail here); the marked day is not even looked at.
            done, _ = box.fetch('--backfill-days', '15', path=str(counting))
            assert sealed_day.isoformat() not in done.stdout + calls.read_text() and back(1).isoformat() in calls.read_text(), label
            assert exact(box.directory / 'inbox') == kept, label
            calls.unlink()
        untouched('as sealed', 0, 'state=already_complete')
        original = first.read_bytes()
        with first.open('ab') as stream:                       # a file of that day grew on the source
            stream.write(b'more')
        untouched('source file grew', 1, 'state=failed reason=marked_day_differs_from_source')
        first.write_bytes(original)
        slice_ = box.put(sealed_day, '_120000.csv', 300)       # a new slice of that day appeared on the source
        untouched('new slice', 1, 'state=failed reason=marked_day_differs_from_source')
        slice_.unlink()
        second.chmod(0)                                        # a file that could not be copied if anything tried
        untouched('source file unreadable', 0, 'state=already_complete')
        second.chmod(0o644)
        # The daily run has begun to delete the day (a file is gone, the marker is still there): it is not brought back.
        gone = box.directory / 'inbox' / first.name
        gone.unlink()
        kept.pop(first.name)
        untouched('deletion under way', 1, 'state=failed reason=marked_day_differs_from_source')
        for path in (first, second):
            path.unlink()
        ok('F002 a marked day is never fetched again, replaced or removed: asked for with --date or inside --from/--to, with the source '
           'as sealed, grown, with a new slice, unreadable, or with a local file already deleted, every local file keeps its content, inode '
           'and times, no copy is started, and a difference from the source is reported with exit 1; without a date it is not looked at')

        # The list of the day changes on the source while its files are being copied: no marker; the next run completes the day.
        box.clear()
        moving_day = back(13)
        steady = box.put(moving_day, size=800)
        late = box.directory / 'source' / names(moving_day, '_130000.csv')[0]
        racing_list = box.directory / 'racing-list'
        racing_list.mkdir()
        (racing_list / 'ssh').write_text((box.directory / 'bin/ssh').read_text())
        (racing_list / 'scp').write_text('#!/bin/sh\n[ -e %s ] || printf late > %s\n%s' % (
            late, late, (box.directory / 'bin/scp').read_text().split('\n', 1)[1]))
        for name in ('ssh', 'scp'):
            (racing_list / name).chmod(0o755)
        done, _ = box.fetch('--date', moving_day.isoformat(), path=str(racing_list))
        assert done.returncode == 1 and 'reason=source_changed_during_copy' in done.stdout, (done.stdout, done.stderr)
        assert box.inbox() == [steady.name], box.inbox()
        done, _ = box.fetch('--date', moving_day.isoformat())
        assert done.returncode == 0 and box.inbox() == sorted([steady.name, late.name, moving_day.isoformat() + '.complete'])
        done, _ = box.fetch('--date', moving_day.isoformat())      # and again: sealed now
        assert done.returncode == 0 and 'state=already_complete' in done.stdout
        steady.unlink()
        late.unlink()
        ok('F002 when the list of the day changes on the source during the copy, no marker is placed; the next run fetches what is missing '
           'and places it; asking once more changes nothing')

        # No key login, and an unknown host key: fail at once, never wait for input.
        box.clear()
        for client, label in [(box.write_client('stranger', known=True).name, 'a key the source does not accept'),
                              (box.write_client('client', known=False).name, 'an unknown host key')]:
            done, seconds = box.fetch(client=client)
            assert done.returncode == 1 and seconds < 10 and box.inbox() == [], (label, done.returncode, seconds, done.stdout, done.stderr)
            assert done.stdout.count('reason=source_unreachable') == 1, done.stdout   # stops after the first day of the source
            ok('D14 with ' + label + ' the script fails in %.1f s without asking for anything and fetches nothing' % seconds)

        # A second instance.
        holder = subprocess.Popen(['flock', str(box.config()), 'sleep', '5'])
        time.sleep(0.5)
        done, _ = box.fetch()
        holder.wait()
        assert done.returncode == 1 and 'another fetch-logs is running' in done.stderr
        ok('D14 a second instance exits at once')

        blocked.chmod(before[blocked.name][2])
        assert snapshot(box.directory / 'source') == dict(before, **{growing.name: before[growing.name]})
        ok('D14 the source directory is unchanged after all of the above: same names, contents, modes and modification times')

        # One connection at a time: every accepted connection is closed before the next is accepted.
        opened, peak, accepted = set(), 0, 0
        for line in box.log.read_text().splitlines():
            login = re.search(r'Accepted publickey for \S+ from 127\.0\.0\.1 port (\d+)', line)
            # A connection whose client was killed ends in one of several ways, with or without the user's name.
            gone = re.search(r'(?:(?:Disconnected from|Connection reset by|Connection closed by)(?: user \S+)?|Read error from remote host'
                             r'|Closing connection to) 127\.0\.0\.1 port (\d+)', line)
            if login:
                opened.add(login.group(1))
                accepted += 1
                peak = max(peak, len(opened))
            elif gone:
                opened.discard(gone.group(1))
        assert accepted > 20 and peak == 1 and not opened, (accepted, peak, opened, [
            re.sub(r'for \S+ from|user \S+', 'USER', line) for line in box.log.read_text().splitlines()
            if any('port ' + port in line for port in opened)][:12])
        ok('D14 the server log shows %d accepted logins and never more than one open at a time' % accepted)
    print('FETCH CHECKS:', len(passed))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sshd-root', type=Path, required=True,
                        help='root holding usr/sbin/sshd (and sshd-session for OpenSSH 10); use / for the installed system sshd')
    verify(parser.parse_args().sshd_root.resolve())
