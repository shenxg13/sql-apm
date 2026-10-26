#!/usr/bin/env python3
"""Upgrade the installed v1.1.0 in place; run using its Python 3.13 interpreter.

check is read-only. apply journals under ROOT/upgrades only after preflight and
explicit no-rollback acknowledgement. resume finishes a committed migration;
it never downgrades the database or guesses whether a transaction committed.
"""
import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

from runtime_archive import verify_installed
from verify_package import digest, verify

RUNTIME_SHA = 'd271d6a1471c41723724b48afad59f9675e1b324747fbb7dfae283f97340314c'
RUNTIME_MANIFEST_SHA = '470e93f741a5d5f3cb82118baf632ac9468036fe6ffa5ccb698de430c4f92d0c'
SERVICES = ('sql-apm-daily.timer', 'sql-apm-daily.service',
            'sql-apm-grafana.service', 'sql-apm-fingerprint.service')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    with directory_fd(path.parent) as fd:
        os.fsync(fd)


class directory_fd:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)

    def __enter__(self):
        return self.fd

    def __exit__(self, *unused):
        os.close(self.fd)


def verify_checksums(directory):
    expected = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split('  ', 1)
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and name not in expected,
                'invalid verification manifest')
        expected[name] = checksum
    files = set()
    for path in directory.rglob('*'):
        require(not path.is_symlink(), 'verification contains a symbolic link')
        if path.is_file() and '__pycache__' not in path.parts:
            files.add(str(path.relative_to(directory)))
    require(files == set(expected) | {'SHA256SUMS'}, 'verification file set differs')
    for name, checksum in expected.items():
        require(digest(directory / name) == checksum, 'verification checksum differs: ' + name)


def space_required(overall_rows, package_bytes):
    # #72: 221 bytes/index entry; allow three times that for sort/WAL plus
    # the permanent index. Counts include all retained builds, not just current.
    return max(6_000_000_000, math.ceil(overall_rows * 221 * 4)) + package_bytes * 2


class Upgrade:
    def __init__(self, root, candidate, verification, port=5432):
        self.root, self.candidate, self.verification = map(Path, (root, candidate, verification))
        self.port = port
        self.run = None
        self.state = {}
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith('PG') and k not in ('PYTHONPATH', 'PYTHONHOME', 'SQL_APM_DSN')}
        self.env.update(PGPASSFILE=str(self.root / 'private/pgpass'), PGCONNECT_TIMEOUT='10',
                        PYTHONDONTWRITEBYTECODE='1', PGAPPNAME='sql-apm-upgrade')
        self.env['SQL_APM_DSN'] = 'host=' + str(self.root / 'socket') + ' port=' + str(port) + ' dbname=sql_apm user=sql_apm'

    def command(self, name, args):
        started = time.monotonic()
        output = self.run / (str(len(self.state.setdefault('commands', []))) + '-' + name + '.log') if self.run else None
        if output:
            print('RUN: ' + name, flush=True)
            with output.open('wb') as stream:
                result = subprocess.run([str(a) for a in args], env=self.env,
                                        cwd=self.root / 'app' if (self.root / 'app').exists() else self.root,
                                        stdout=stream, stderr=subprocess.STDOUT)
            self.state['commands'].append(dict(name=name, exit_code=result.returncode,
                                              seconds=round(time.monotonic()-started, 6), log=output.name))
            self.persist()
            require(result.returncode == 0, name + ' failed; inspect ' + str(output))
            return output.read_text(errors='replace')
        result = subprocess.run([str(a) for a in args], env=self.env, capture_output=True, text=True)
        require(result.returncode == 0, name + ' failed (no installation changes made)')
        return result.stdout

    def query(self, sql):
        import psycopg2
        # Explicit connection identity; no environment-provided remote host or credentials.
        with psycopg2.connect(host=str(self.root / 'socket'), port=self.port, dbname='sql_apm',
                              user='sql_apm', passfile=str(self.root / 'private/pgpass'),
                              connect_timeout=10, application_name='sql-apm-upgrade') as db:
            db.set_session(readonly=True)
            with db.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='30s'")
                cur.execute(sql)
                return cur.fetchall()

    def schema(self):
        return self.query("SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")[0][0]

    def idle(self):
        require(not self.query("SELECT 1 FROM task WHERE state='running' LIMIT 1"), 'product task is running')
        require(not self.query("SELECT 1 FROM mpp_daily_run WHERE state='running' LIMIT 1"), 'daily run is running')
        require(not self.query("""SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
            AND usename=current_user AND pid<>pg_backend_pid() AND state IS DISTINCT FROM 'idle' LIMIT 1"""),
                'another product connection is busy')

    def services(self):
        states = {}
        for unit in SERVICES:
            raw = self.command('service-state', ['systemctl', 'show', unit,
                               '--property=LoadState,ActiveState,UnitFileState'])
            state = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
            require(state.get('LoadState') in ('loaded', 'not-found', 'masked'), 'unrecognized service: ' + unit)
            require(state.get('ActiveState') in ('active', 'inactive'), 'service is not settled: ' + unit)
            if unit.endswith('daily.service'):
                require(state['ActiveState'] == 'inactive', 'daily service is running')
            states[unit] = state
        return states

    def preflight(self):
        require(self.root.is_absolute() and self.root.resolve() == self.root, 'root must be a real absolute directory')
        for name in ('app', 'verification', 'pgdata', 'socket', 'private', 'grafana/dashboards/mpp'):
            path = self.root / name
            require(path.is_dir() and path.resolve() == path, 'missing or linked installation directory: ' + name)
        require((self.root / 'upgrades').resolve() == self.root / 'upgrades', 'upgrade records must not be linked')
        require(not self.candidate.is_relative_to(self.root) and not self.verification.is_relative_to(self.root),
                'unpack the new delivery outside the installation root')
        installed = verify(self.root / 'app', installed=True)
        require(installed['version'] == 'v1.1.0' and installed['kind'] == 'release', 'installed formal v1.1.0 required')
        target = verify(self.candidate, installed=True)
        require(target['version'] == 'v1.2.0', 'candidate v1.2.0 required')
        verify_checksums(self.verification)
        require((self.verification / 'PROGRAM_COMMIT').read_text().strip() == target['commit'], 'verification commit differs')
        require(digest(self.root / 'app/requirements.txt') == digest(self.candidate / 'requirements.txt'), 'dependencies changed')
        require(digest(self.root / 'runtime-manifest.json') == RUNTIME_MANIFEST_SHA, 'runtime manifest is not the fixed artifact')
        verify_installed(self.root)
        # Bind the local socket to this PGDATA, rather than trusting a connection string.
        pid = (self.root / 'pgdata/postmaster.pid').read_text().splitlines()[0]
        require((self.root / ('socket/.s.PGSQL.' + str(self.port) + '.lock')).read_text().splitlines()[0] == pid,
                'socket belongs to a different database instance')
        require(self.schema() == '1.12.0', 'schema 1.12.0 required')
        self.idle()
        services = self.services()
        if any(state['LoadState'] == 'loaded' for state in services.values()):
            self.command('sudo-access', ['sudo', '-n', 'true'])
        require(os.access(self.root, os.W_OK) and os.access(self.root / 'grafana/dashboards/mpp', os.W_OK),
                'installation and dashboard directories must be writable')
        # The released layout uses pg_default and local WAL, so no volume is silently omitted.
        require(not (self.root / 'pgdata/pg_wal').is_symlink(), 'external WAL needs a separate space assessment')
        require(not list((self.root / 'pgdata/pg_tblspc').iterdir()), 'external tablespaces need a separate space assessment')
        count = self.query("""SELECT coalesce(sum(c.row_count),0) FROM mpp_build_layer_count c
            JOIN build b USING(build_id) JOIN mpp_result_partition p USING(partition_id)
            WHERE c.kind='formal' AND c.layer='overall' AND p.cleaned_at IS NULL""")[0][0]
        package_bytes = sum(p.stat().st_size for tree in (self.candidate, self.verification) for p in tree.rglob('*') if p.is_file())
        required = space_required(int(count), package_bytes)
        free = min(shutil.disk_usage(self.root).free, shutil.disk_usage(self.root / 'pgdata').free)
        require(free >= required, 'insufficient free space: need ' + str(required) + ' bytes')
        # Reject unresolved earlier attempts; resume must reconcile those before a new run.
        for path in (self.root / 'upgrades').glob('*/state.json'):
            require(json.loads(path.read_text()).get('phase') in ('complete', 'rolled-back'),
                    'unfinished upgrade; use resume with ' + str(path.parent))
        return dict(installed=installed, target=target, services=services, runtime_sha256=RUNTIME_SHA,
                    overall_rows=int(count), required_free_bytes=required, available_bytes=free,
                    root=str(self.root), candidate=str(self.candidate), verification=str(self.verification), port=self.port)

    def persist(self):
        save(self.run / 'state.json', self.state)

    def phase(self, phase):
        self.state['phase'] = phase
        self.persist()

    def initialize(self, mode, app):
        self.command('schema-' + mode, ['bash', app / 'scripts/db/initialize.sh', mode,
                     '--host', self.root / 'socket', '--port', str(self.port),
                     '--pg-bin', self.root / 'postgresql-17.10/bin'])

    def stop(self):
        for unit, state in self.state['preflight']['services'].items():
            if state['LoadState'] == 'loaded':
                self.command('stop-' + unit, ['sudo', '-n', 'systemctl', 'stop', unit])
        self.idle()

    def restore(self):
        for unit in reversed(SERVICES):
            state = self.state['preflight']['services'][unit]
            if state['ActiveState'] == 'active':
                self.command('start-' + unit, ['sudo', '-n', 'systemctl', 'start', unit])
        current = self.services()
        require(current == self.state['preflight']['services'], 'service states differ; inspect upgrade logs')

    def activate(self):
        # Every rename is local to ROOT. Actual paths resolve interruptions between
        # operations; the journal is evidence, never a substitute for the DB receipt.
        old, staged, installed = self.run / 'previous-app', self.run / 'next-app', self.root / 'app'
        if staged.exists():
            require(verify(staged, installed=True)['commit'] == self.state['preflight']['target']['commit'],
                    'staged program differs from preflight')
        if (self.run / 'next-verification').exists():
            verify_checksums(self.run / 'next-verification')
        if not old.exists():
            installed.rename(old)
        if not installed.exists():
            if (old / '.venv').exists():
                (old / '.venv').rename(staged / '.venv')
            staged.rename(installed)
        require(verify(installed, installed=True)['commit'] == self.state['preflight']['target']['commit'],
                'activated program differs from preflight')
        previous = self.run / 'previous-verification'
        if not previous.exists():
            (self.root / 'verification').rename(previous)
        if not (self.root / 'verification').exists():
            (self.run / 'next-verification').rename(self.root / 'verification')
        for path in (installed / 'grafana/dashboards').glob('mpp-*.json'):
            target = self.root / 'grafana/dashboards/mpp' / path.name
            temporary = target.with_suffix('.new')
            require(not temporary.is_symlink() and not target.is_symlink(), 'dashboard target must not be linked')
            shutil.copyfile(path, temporary)
            temporary.replace(target)
        verify_installed(self.root)
        self.phase('activated')
        self.initialize('check', installed)
        self.command('environment', [installed / '.venv/bin/python', installed / 'scripts/deployment/check_environment.py'])
        self.command('daily-status', [installed / '.venv/bin/python', '-m', 'sql_apm', 'daily', 'status'])
        self.restore()
        self.phase('complete')
        print('OK: upgraded to v1.2.0; finish the checks in UPGRADE.html; record=' + str(self.run))

    def recover(self):
        self.idle()  # A detached psql may still be committing after its parent died.
        version = self.schema()
        if version == '1.12.0':
            require(not (self.run / 'previous-app').exists(), 'old database with replaced program; manual inspection required')
            self.initialize('check', self.root / 'app')
            self.restore()
            self.phase('rolled-back')
            print('OK: migration rolled back; v1.1.0 and original service states restored')
        elif version == '1.13.0':
            self.stop()
            self.phase('committed')
            self.activate()
        else:
            raise ValueError('unknown database receipt; keep writers stopped and inspect the record')

    def apply(self, preflight):
        parent = self.root / 'upgrades'
        parent.mkdir(mode=0o700, exist_ok=True)
        self.run = parent / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        self.run.mkdir(mode=0o700)
        self.state = dict(format='sql-apm-upgrade/1', phase='prepared', preflight=preflight,
                          accepted_no_rollback=True, commands=[])
        self.persist()
        try:
            shutil.copytree(self.candidate, self.run / 'next-app', ignore=shutil.ignore_patterns('__pycache__', '.venv'))
            shutil.copytree(self.verification, self.run / 'next-verification', ignore=shutil.ignore_patterns('__pycache__'))
            verify(self.run / 'next-app')
            verify_checksums(self.run / 'next-verification')
            self.stop()
            self.phase('migrating')
            self.initialize('upgrade', self.run / 'next-app')
            self.phase('committed')
            self.activate()
        except BaseException:
            # On an ordinary failure reconcile the DB before restoring anything.
            # SIGKILL/power loss requires explicit resume with the same journal.
            try:
                if self.schema() == '1.12.0':
                    self.recover()
            except BaseException:
                print('STOP: recovery incomplete; keep writers stopped and use resume', file=sys.stderr)
            print('Upgrade record: ' + str(self.run), file=sys.stderr)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'apply', 'resume'))
    parser.add_argument('--root', type=Path, default=Path('/data/sql-apm'))
    parser.add_argument('--candidate', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--verification', type=Path)
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--record', type=Path)
    parser.add_argument('--confirm-no-rollback', action='store_true',
                        help='确认不备份；迁移提交后不能回退，只能重装旧版并从原始日志重建')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirm_no_rollback, 'read UPGRADE.html and explicitly pass --confirm-no-rollback')
    root = args.root.absolute()
    require(root.resolve() == root, 'root must not contain symbolic links')
    with directory_fd(root) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another upgrade is running') from None
        upgrade = Upgrade(root, args.candidate.resolve(),
                          (args.verification or args.candidate.parent / 'verification').resolve(), args.port)
        if args.action == 'resume':
            require(args.record is not None and args.record.resolve().parent == root / 'upgrades', 'record must belong to this installation')
            upgrade.run = args.record.resolve()
            upgrade.state = json.loads((upgrade.run / 'state.json').read_text())
            require(upgrade.state['preflight']['root'] == str(root) and upgrade.state['preflight']['port'] == args.port,
                    'record connection identity differs')
            require(upgrade.state['phase'] not in ('complete', 'rolled-back'), 'record is already finished')
            # The base interpreter stays at a stable path even while app/.venv is
            # being moved. Load the unchanged installed dependencies from its
            # actual location, so the documented resume command always works.
            for tree in (root / 'app', upgrade.run / 'previous-app', upgrade.run / 'next-app'):
                dependencies = tree / '.venv/lib/python3.13/site-packages'
                if dependencies.is_dir():
                    sys.path.insert(0, str(dependencies))
                    break
            upgrade.recover()
        else:
            result = upgrade.preflight()
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            if args.action == 'apply':
                upgrade.apply(result)
            else:
                print('OK: preflight passed; no installation changes made')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, KeyboardInterrupt) as error:
        raise SystemExit('STOP: ' + str(error)) from None
