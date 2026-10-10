#!/usr/bin/env python3
"""Replay local sample logs through the daily run and compare with the manual commands.

Three ways, each in a private PostgreSQL 17 instance below --work:

  manual  one hand-written batch per cluster with the import command, then rebuild
  all     every day marked at once, one daily run per cluster
  daily   one day marked, one daily run, and so on in date order

``compare`` then requires, per cluster, the statistics of the final version to be the
same in all three, by the rule of the release comparison (every field exact; the two
logarithm metrics within the stated limit, and here reported when not bit-identical).

The sample directories are never used as receiving directories: each replay works on
its own copy of the files below --work, so a deletion by the run removes a copy and
the replays cannot disturb one another (a second name for the same file would not
do: making or removing it changes the file's status time, which an import that is
reading it takes for a change of the file). The samples' names, sizes and
modification times are compared before and after.
Reports hold counts, dates, sizes and durations; no file content and no SQL text.
"""
import argparse
from contextlib import contextmanager
from datetime import date
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts/deployment')]
from sql_apm.daily import inbox
from sql_apm.storage.ingestion import connect

SOURCE = dict(build='HashData Warehouse 3.13.13', timezone='UTC+08:00', declaration='local sample replay')
PORTS = dict(manual=55601, all=55602, daily=55603)
SETTINGS = """
listen_addresses = ''
shared_buffers = '512MB'
work_mem = '16MB'
maintenance_work_mem = '128MB'
max_connections = 30
max_wal_size = '2GB'
min_wal_size = '256MB'
timezone = 'Asia/Shanghai'
log_statement = 'none'
log_min_error_statement = 'panic'
"""


def run(words, **options):
    done = subprocess.run([str(w) for w in words], capture_output=True, text=True, **options)
    if done.returncode:
        raise SystemExit('ERROR: %s failed\n%s' % (words[0], (done.stderr or done.stdout)[-2000:]))
    return done


def listing(directory):
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(directory.iterdir()) if p.is_file()}


class Sampler(threading.Thread):
    """Peak memory while a step runs: of the whole machine, and of this replay's processes (PSS)."""
    def __init__(self, postmaster):
        super().__init__(daemon=True)
        self.postmaster, self.stop, self.host, self.own = postmaster, threading.Event(), 0, 0

    @staticmethod
    def used():
        values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        return (int(values['MemTotal'].split()[0]) - int(values['MemAvailable'].split()[0])) * 1024

    def tree(self):
        parents = {}
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    parents[int(entry.name)] = int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[1])
                except (OSError, IndexError, ValueError):
                    pass
        wanted, grew = {os.getpid(), self.postmaster}, True
        while grew:
            more = {pid for pid, parent in parents.items() if parent in wanted} - wanted
            grew = bool(more)
            wanted |= more
        return wanted

    def run(self):
        while not self.stop.wait(5):
            total = 0
            for pid in self.tree():
                try:
                    for line in Path('/proc/%d/smaps_rollup' % pid).read_text().splitlines():
                        if line.startswith('Pss:'):
                            total += int(line.split()[1]) * 1024
                except OSError:
                    pass
            self.host, self.own = max(self.host, self.used()), max(self.own, total)

    def result(self):
        self.stop.set()
        self.join()
        return dict(peak_host_used_bytes=self.host, peak_replay_pss_bytes=self.own)


class Replay:
    def __init__(self, args):
        self.mode, self.pg_bin = args.mode, args.pg_bin
        self.work, self.base = args.work.resolve(), args.work.resolve() / args.mode
        self.samples = {'119': args.samples_119.resolve(), '120': args.samples_120.resolve()}
        self.port, self.socket = PORTS[args.mode], self.base / 'socket'
        self.dsn = 'host=%s port=%d dbname=sql_apm user=sql_apm' % (self.socket, self.port)
        self.env = dict({k: v for k, v in os.environ.items() if not k.startswith('PG')}, SQL_APM_DSN=self.dsn)
        self.steps = []
        if ROOT not in self.work.parents or self.work.relative_to(ROOT).parts[0] != 'var':
            raise SystemExit('ERROR: --work must be below var/ of this repository (an ignored location)')
        for cluster, directory in self.samples.items():
            if self.work == directory or self.work in directory.parents or directory in self.work.parents:
                raise SystemExit('ERROR: --work and the sample directories must be apart')

    def prepare(self):
        if (self.base / 'report.json').exists():
            raise SystemExit('ERROR: this mode has already been replayed in ' + str(self.base))
        self.base.mkdir(parents=True)
        (self.base / 'logs').mkdir()
        self.socket.mkdir(mode=0o700)
        data = self.base / 'pgdata'
        run([self.pg_bin / 'initdb', '-D', data, '-U', 'apm_admin', '--auth-local=trust', '--auth-host=reject',
             '--encoding=UTF8', '--locale=C'], env=self.env)
        with (data / 'postgresql.conf').open('a') as stream:
            stream.write(SETTINGS + "unix_socket_directories = '%s'\nport = %d\n" % (self.socket, self.port))
        run([self.pg_bin / 'pg_ctl', '-D', data, '-l', self.base / 'postgres.log', '-w', 'start'], env=self.env)
        run([ROOT / 'scripts/db/initialize.sh', 'all', '--host', self.socket, '--port', self.port, '--pg-bin', self.pg_bin,
             '--admin-user', 'apm_admin', '--admin-database', 'postgres'], env=self.env)
        self.days = {}
        for cluster, directory in self.samples.items():
            target = self.base / 'inbox' / cluster
            target.mkdir(parents=True)
            for path in sorted(directory.iterdir()):
                day = inbox._day(inbox.LOG, path.name)
                if not path.is_file() or day is None:
                    raise SystemExit('ERROR: unexpected entry in the sample directory of ' + cluster)
                shutil.copy2(path, target / path.name)
                self.days.setdefault(cluster, {}).setdefault(day, []).append(path.name)
        clusters = sorted(self.samples)
        (self.base / 'training.json').write_text(json.dumps(dict(version=1, clusters=clusters, window=dict(cutoff_date='2026-01-01'))))
        batches = {'M' + c: dict(source='S' + c, files_confirmed_complete=True, dates=sorted(d.isoformat() for d in self.days[c]),
                                 files=[dict(path='inbox/%s/%s' % (c, name), closed_and_copied=True)
                                        for day in sorted(self.days[c]) for name in sorted(self.days[c][day])]) for c in clusters}
        (self.base / 'import.json').write_text(json.dumps(dict(version=1, clusters=clusters, batches=batches,
            sources={'S' + c: dict(SOURCE, cluster=c) for c in clusters})))
        (self.base / 'daily.json').write_text(json.dumps(dict(version=1, import_config='import.json', training_config='training.json',
            workers=4, sources={'S' + c: dict(directory='inbox/' + c) for c in clusters})))

    def size(self):
        db = connect(self.dsn, 'sql_apm')
        try:
            with db, db.cursor() as cur:
                cur.execute('SELECT pg_database_size(current_database())')
                return cur.fetchone()[0]
        finally:
            db.close()

    def command(self, label, words, **facts):
        """One product command; its output goes to a private log, its cost into the report."""
        postmaster = int((self.base / 'pgdata/postmaster.pid').read_text().split()[0])
        sampler, started = Sampler(postmaster), time.monotonic()
        sampler.start()
        log = self.base / 'logs' / ('%03d-%s.jsonl' % (len(self.steps) + 1, re.sub(r'[^a-z0-9]+', '-', label)))
        with log.open('w') as stream:
            code = subprocess.run([sys.executable, '-m', 'sql_apm'] + [str(w) for w in words], cwd=ROOT, env=self.env,
                                  stdout=stream, stderr=subprocess.STDOUT).returncode
        step = dict(label=label, exit=code, seconds=round(time.monotonic() - started, 1), database_bytes=self.size(),
                    **sampler.result(), **facts)
        self.steps.append(step)
        print('STEP: ' + json.dumps(step), flush=True)
        if code:
            raise SystemExit('ERROR: step failed, see ' + str(log))
        return log

    @contextmanager
    def heavy(self, cluster):
        """The large cluster of one replay at a time: two of its builds together would not fit the machine."""
        if cluster != '120':
            yield
            return
        with (self.work / 'lock-120').open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def mark(self, cluster, day):
        (self.base / 'inbox' / cluster / inbox.marker_name(day)).touch()

    def replay(self):
        for cluster in sorted(self.samples):
            days = sorted(self.days[cluster])
            with self.heavy(cluster):
                if self.mode == 'manual':
                    self.command('import ' + cluster, ['import', '--config', self.base / 'import.json', '--source', 'S' + cluster,
                                                       '--batch', 'M' + cluster, '--workers', 4], cluster=cluster, days=len(days))
                    self.command('rebuild ' + cluster, ['rebuild', '--cluster', cluster, '--training-config', self.base / 'training.json',
                                                        '--cutoff-date', days[-1].isoformat()], cluster=cluster)
                elif self.mode == 'all':
                    for day in days:
                        self.mark(cluster, day)
                    self.command('daily ' + cluster, ['daily', 'run', '--config', self.base / 'daily.json'], cluster=cluster, days=len(days))
                else:
                    for day in days:
                        self.mark(cluster, day)
                        self.command('daily %s %s' % (cluster, day.isoformat()), ['daily', 'run', '--config', self.base / 'daily.json'],
                                     cluster=cluster, date=day.isoformat())

    def finish(self):
        from statistic_comparison import export_statistics
        db = connect(self.dsn, 'sql_apm')
        report = dict(mode=self.mode, steps=self.steps, clusters={}, days={c: len(d) for c, d in self.days.items()},
                      files={c: sum(len(names) for names in d.values()) for c, d in self.days.items()})
        try:
            (self.base / 'export').mkdir()
            for cluster in sorted(self.samples):
                with db, db.cursor() as cur:
                    cur.execute('''SELECT v.build_id,s.cutoff_date,s.window_days FROM current_version v JOIN build b USING(build_id)
                        JOIN config_snapshot s USING(config_id) WHERE v.scope_id=%s''', (cluster,))
                    build, cutoff, window = cur.fetchone()
                    cur.execute("SELECT count(*) FROM import_batch WHERE scope_id=%s AND state='complete'", (cluster,))
                    batches = cur.fetchone()[0]
                    cur.execute('SELECT count(*) FROM evidence_record r JOIN source_file f USING(file_id) WHERE f.scope_id=%s', (cluster,))
                    records = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM publication WHERE scope_id=%s AND result='published'", (cluster,))
                    versions = cur.fetchone()[0]
                assert cutoff == max(self.days[cluster]), (cluster, cutoff)
                report['clusters'][cluster] = dict(cutoff_date=cutoff.isoformat(), window_days=window, complete_batches=batches,
                    log_records=records, published_versions=versions,
                    statistics=export_statistics(db, build, self.base / 'export', cluster))
            if self.mode != 'manual':
                with db, db.cursor() as cur:
                    cur.execute('''SELECT c.scope_id,r.started_at,c.newest_imported,c.build_state,c.cleanup_state,c.raw_state,c.raw_days,
                            c.raw_files,c.raw_bytes,c.stage_seconds,
                            (SELECT count(*) FROM mpp_daily_day d WHERE d.run_id=c.run_id AND d.scope_id=c.scope_id AND d.state='complete')
                        FROM mpp_daily_cluster c JOIN mpp_daily_run r USING(run_id) ORDER BY r.started_at,c.ordinal''')
                    report['runs'] = [dict(cluster=row[0], started_at=row[1].isoformat(), newest_imported=row[2] and row[2].isoformat(),
                                           build_state=row[3], cleanup_state=row[4], raw_state=row[5], raw_days=row[6], raw_files=row[7],
                                           raw_bytes=row[8], stage_seconds=row[9], imported_days=row[10]) for row in cur]
                    cur.execute("SELECT count(*),count(*) FILTER (WHERE state='finished' AND NOT failed) FROM mpp_daily_run")
                    report['daily_runs'], report['daily_runs_without_failure'] = cur.fetchone()
                    cur.execute('SELECT kind,count(*) FROM mpp_daily_problems() GROUP BY 1 ORDER BY 1')
                    report['open_problems'] = dict(cur.fetchall())
                report['left_in_receiving_directories'] = {c: len(list((self.base / 'inbox' / c).iterdir())) for c in sorted(self.samples)}
        finally:
            db.close()
        report['samples_unchanged'] = {c: listing(d) == self.before[c] for c, d in self.samples.items()}
        assert all(report['samples_unchanged'].values()), report['samples_unchanged']
        (self.base / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
        print('RESULT: %s replay finished; report in %s' % (self.mode, self.base / 'report.json'), flush=True)

    def main(self):
        self.before = {c: listing(d) for c, d in self.samples.items()}
        self.prepare()
        try:
            self.replay()
            self.finish()
        finally:
            run([self.pg_bin / 'pg_ctl', '-D', self.base / 'pgdata', '-m', 'fast', '-w', 'stop'], env=self.env)


def compare(work):
    from statistic_comparison import compare_statistics
    reports = {mode: json.loads((work / mode / 'report.json').read_text()) for mode in PORTS}
    outcome = dict(passed=True, clusters={})
    for cluster in sorted(reports['manual']['clusters']):
        reference = reports['manual']['clusters'][cluster]
        entry = dict(cutoff_date=reference['cutoff_date'], log_records=reference['log_records'], versions={}, comparisons={})
        for mode in PORTS:
            current = reports[mode]['clusters'][cluster]
            entry['versions'][mode] = dict(published_versions=current['published_versions'], complete_batches=current['complete_batches'],
                                           rows={table: current['statistics'][table]['rows'] for table in current['statistics']})
            assert (current['cutoff_date'], current['window_days'], current['log_records']) == \
                (reference['cutoff_date'], reference['window_days'], reference['log_records']), (mode, cluster)
            if mode == 'manual':
                continue
            result = compare_statistics(current['statistics'], work / mode / 'export', reference['statistics'], work / 'manual' / 'export')
            identical = all(table['exact_fields_equal'] and not table['different_rows'] for table in result['tables'].values())
            entry['comparisons'][mode + ' vs manual'] = dict(passed=result['passed'], bit_identical=identical,
                tables={name: dict(rows=table['actual_rows'], exact_fields_equal=table['exact_fields_equal'],
                                   key_mismatches=table['key_mismatches'], different_rows=table['different_rows'])
                        for name, table in result['tables'].items()})
            outcome['passed'] = outcome['passed'] and result['passed']
        outcome['clusters'][cluster] = entry
    (work / 'comparison.json').write_text(json.dumps(outcome, indent=1) + '\n')
    print(json.dumps(outcome, indent=1))
    return 0 if outcome['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest='action', required=True)
    replay = actions.add_parser('run', help='回放一种方式')
    replay.add_argument('--mode', choices=sorted(PORTS), required=True)
    replay.add_argument('--samples-119', type=Path, required=True)
    replay.add_argument('--samples-120', type=Path, required=True)
    replay.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    check = actions.add_parser('compare', help='比对三种方式的最终版本')
    for action in (replay, check):
        action.add_argument('--work', type=Path, required=True, help='回放目录，须在仓库的 var/ 下')
    args = parser.parse_args()
    if args.action == 'compare':
        return compare(args.work.resolve())
    Replay(args).main()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
