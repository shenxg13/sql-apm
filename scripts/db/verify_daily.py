#!/usr/bin/env python3
"""Synthetic acceptance of the daily run: markers, day batches, build interval, isolation,
busy clusters, cleanup, raw-file deletion, connection liveness, abort and recovery, records.

Runs on a private disposable PostgreSQL 17 instance with synthetic logs only.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

RESOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', str(RESOURCE_ROOT))).resolve()
sys.path[:0] = [str(ROOT), str(RESOURCE_ROOT / 'tests')]
from verify import instance, Verification
from database.retention import clone_build, contents, digest
from ingestion.test_reader import row, write_csv
from sql_apm.daily import inbox
from sql_apm.daily.config import DailyError, load_config
from sql_apm.daily.run import DailyRun
from sql_apm.ingestion.config import IngestionError
from sql_apm.storage.daily import status
from sql_apm.storage.ingestion import connect
from sql_apm.storage.publication import version_status
from sql_apm.storage.tasks import Task

TODAY = date(2026, 9, 20)  # the "today" given to in-process runs; command-line runs use the real date
SOURCE = dict(build='HashData Warehouse 3.13.13', timezone='UTC+08:00', declaration='synthetic')
PRIVATE = ('synthetic_user', 'synthetic_db', 'SELECT', 'COMMIT')


def lines(day, count=5, base=0, text=None):
    stamp = day.isoformat()
    return [row(text=text or 'SELECT %d FROM daily_t' % (base + i), message='duration: %d ms' % (base + i + 1),
                **{'0': '%s 10:%02d:%02d.000001 CST' % (stamp, i // 60, i % 60), '7': stamp + ' 09:00:00 CST',
                   '9': 'con%d' % (base + 17), '10': 'cmd%d' % (i + 1)}) for i in range(count)]


class Site:
    """Receiving directories, the three configuration files and ways to run the command."""
    def __init__(self, directory, dsn, sources):
        self.directory, self.dsn, self.sources = directory, dsn, sources
        self.settings = dict(workers=1)
        self.training = dict(version=1, clusters=sorted(set(sources.values())), window=dict(cutoff_date='2026-01-01'))
        for source in sources:
            (directory / 'inbox' / source).mkdir(parents=True)
        self.write()

    def inbox(self, source):
        return self.directory / 'inbox' / source

    def write(self):
        clusters = list(dict.fromkeys(self.sources.values()))
        (self.directory / 'import.json').write_text(json.dumps(dict(version=1, clusters=clusters, batches={},
            sources={s: dict(SOURCE, cluster=c) for s, c in self.sources.items()})))
        (self.directory / 'training.json').write_text(json.dumps(self.training))
        document = dict(version=1, import_config='import.json', training_config='training.json',
                        sources={s: dict(directory='inbox/' + s) for s in self.sources})
        document.update(self.settings)
        self.path = self.directory / 'daily.json'
        self.path.write_text(json.dumps(document))

    def put(self, source, day, count=5, base=0, marker=True, suffix='_000000.csv', text=None):
        path = self.inbox(source) / ('gpdb-' + day.isoformat() + suffix)
        write_csv(path, lines(day, count, base, text))
        if marker:
            self.mark(source, day)
        return path

    def mark(self, source, day):
        (self.inbox(source) / inbox.marker_name(day)).touch()

    def run(self, today=TODAY, fault=None):
        events = []
        code = DailyRun(self.dsn, 'sql_apm', load_config(self.path), 'manual', lambda **row: events.append(row),
                        today, fault).execute()
        return code, {e['cluster']: e for e in events if e.get('phase') == 'cluster_finished'}, events

    def cli(self, *words, env=None, timeout=120):
        return subprocess.run([sys.executable, '-m', 'sql_apm'] + [str(w) for w in words], cwd=ROOT,
                              env=dict(os.environ, SQL_APM_DSN=self.dsn, **(env or {})),
                              capture_output=True, text=True, timeout=timeout)

    def start(self, *words, env=None):
        return subprocess.Popen([sys.executable, '-m', 'sql_apm'] + [str(w) for w in words], cwd=ROOT,
                                env=dict(os.environ, SQL_APM_DSN=self.dsn, **(env or {})),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def one(db, sql, params=()):
    with db, db.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def many(db, sql, params=()):
    with db, db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def tree(directory):
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


def problems(db, kind=None):
    rows = many(db, 'SELECT kind,scope_id,source_id,log_date,result_month,reason FROM mpp_daily_problems()')
    return [r for r in rows if kind is None or r[0] == kind]


def clear(db):
    """True when nothing is open; says what is otherwise."""
    left = problems(db)
    assert not left, left
    return True


def cutoff(db, cluster):
    current = version_status(db, cluster)['current']
    return current and current['cutoff_date']


def verify_configuration(v, site, db):
    base = json.loads(site.path.read_text())
    before = (one(db, 'SELECT count(*) FROM mpp_daily_run'), one(db, 'SELECT count(*) FROM task'))
    bad = [(dict(base, extra=1), 'unknown_config_key'), (dict(base, version=2), 'daily_config_version'),
           (dict(base, workers=0), 'invalid_workers'), (dict(base, workers=True), 'invalid_workers'),
           (dict(base, build=dict(interval_days=0)), 'invalid_build_interval'),
           (dict(base, build=dict(interval_days='never')), 'invalid_build_interval'),
           (dict(base, build=dict(interval_days=1.5)), 'invalid_build_interval'),
           (dict(base, build=dict(every=1)), 'invalid_build'),
           (dict(base, build=dict(clusters={'NOPE': 7})), 'unknown_build_cluster'),
           (dict(base, cleanup=dict(enabled='yes')), 'invalid_cleanup'),
           (dict(base, raw_files=dict(retention_days=-1)), 'invalid_raw_retention'),
           (dict(base, raw_files=dict(days=3)), 'invalid_raw_files'),
           (dict(base, stale_after_hours=0), 'invalid_stale_after_hours'),
           (dict(base, sources={}), 'daily_sources_required'),
           (dict(base, sources=dict(base['sources'], S9=dict(directory='inbox/S1'))), 'unregistered_source'),
           (dict(base, sources={'S1': dict(directory='inbox/missing')}), 'receiving_directory_missing'),
           (dict(base, sources={'S1': dict(directory='inbox/S1', keep=1)}), 'invalid_daily_source'),
           (dict(base, sources={'S1': dict(directory='inbox/S1'), 'S2': dict(directory='inbox/S1')}), 'duplicate_receiving_directory'),
           (dict(base, training_config='absent.json'), 'invalid_training_config'),
           (dict(base, import_config='absent.json'), 'invalid_config_or_unregistered_source'),
           ([], 'invalid_daily_config')]
    path = site.directory / 'bad.json'
    for document, reason in bad:
        path.write_text(json.dumps(document))
        out = site.cli('daily', 'run', '--config', path)
        assert out.returncode == 1 and json.loads(out.stdout)['reason'] == reason, (reason, out.stdout, out.stderr)
    path.write_text('{"version":1,"version":1}')
    assert json.loads(site.cli('daily', 'run', '--config', path).stdout)['reason'] == 'duplicate_config_key'
    assert site.cli('daily', 'run').returncode == 2 and site.cli('daily', 'run', '--config', path, '--trigger', 'cron').returncode == 2
    assert before == (one(db, 'SELECT count(*) FROM mpp_daily_run'), one(db, 'SELECT count(*) FROM task'))
    loaded = load_config(site.path)
    assert loaded['clusters'] == ['C1', 'C2', 'C3'] and loaded['intervals'] == dict(C1=1, C2=1, C3=1)
    assert loaded['cleanup'] is True and loaded['raw_days'] == 45 and loaded['stale_after_hours'] == 48
    path.write_text(json.dumps(dict(base, build=dict(interval_days='off', clusters={'C2': 7}), raw_files=dict(retention_days='off'),
                                    cleanup=dict(enabled=False))))
    loaded = load_config(path)
    assert loaded['intervals'] == dict(C1=None, C2=7, C3=None) and loaded['raw_days'] is None and loaded['cleanup'] is False
    v.require(True, 'D1 configuration: defaults, overrides and switches load; unknown keys and invalid values are rejected with a fixed code, exit 1, and nothing is written')


def verify_markers(v, site, db):
    d1, d2, d3 = (TODAY - timedelta(days=n) for n in (5, 4, 3))
    site.put('S1', d1)
    unmarked = site.put('S1', d2, marker=False)
    site.mark('S1', d3)                                   # marker, no files
    site.mark('S1', TODAY)                                # marker dated today, with a file
    site.put('S1', TODAY, marker=False, base=40)
    site.mark('S1', TODAY + timedelta(days=2))            # marker in the future, no file
    (site.inbox('S1') / 'notes.txt').write_text('x')
    (site.inbox('S1') / 'gpdb-2026-13-45_000000.csv').write_text('x')
    (site.inbox('S1') / 'sub').mkdir()
    (site.inbox('S1') / 'sub' / ('gpdb-' + d1.isoformat() + '_000000.csv')).write_text('x')
    outside = site.directory / 'outside.csv'
    outside.write_text('x')
    (site.inbox('S1') / ('gpdb-' + d1.isoformat() + '_111111.csv')).symlink_to(outside)
    before = tree(site.inbox('S1'))
    code, clusters, _ = site.run()
    assert code == 0 and clusters['C1']['build_state'] == 'published' and clusters['C1']['cutoff_date'] == d1.isoformat(), clusters
    assert tree(site.inbox('S1')) == before and unmarked.exists()
    assert many(db, 'SELECT batch_id,state FROM import_batch ORDER BY 1') == [(inbox.batch_id('S1', d1), 'complete')]
    assert one(db, 'SELECT count(*) FROM source_file') == 1
    seen = {(r[0], r[3]) for r in problems(db)}
    assert seen == {('files_without_marker', d2), ('marker_without_files', d3), ('marker_not_before_today', TODAY),
                    ('marker_not_before_today', TODAY + timedelta(days=2))}, seen
    other = many(db, "SELECT file_name FROM mpp_daily_problem WHERE kind='nonconforming_file' ORDER BY 1")
    assert [r[0] for r in other] == ['gpdb-' + d1.isoformat() + '_111111.csv', 'gpdb-2026-13-45_000000.csv', 'notes.txt'], other
    v.require(True, 'D2 only marked days are processed; unmarked files stay byte-identical and unregistered; marker without files, files without marker, '
                    'markers dated today and later, and non-conforming names (a symbolic link among them) are recorded; a subdirectory is not looked into')
    # The day after: the marker that was too early is now an ordinary one.
    code, clusters, _ = site.run(today=TODAY + timedelta(days=1))
    assert code == 0 and one(db, 'SELECT state FROM import_batch WHERE batch_id=%s', (inbox.batch_id('S1', TODAY),)) == 'complete'
    assert ('marker_not_before_today', TODAY) not in {(r[0], r[3]) for r in problems(db)}
    assert ('marker_not_before_today', TODAY + timedelta(days=2)) in {(r[0], r[3]) for r in problems(db)}
    v.require(True, 'D2 a marker dated today is neither processed nor registered; once that day has passed it is processed like any other')
    # After the freeze: an added file and a changed file are reported, never merged.
    records = one(db, 'SELECT count(*) FROM evidence_record')
    added = site.put('S1', d1, base=20, marker=False, suffix='_120000.csv')
    code, clusters, _ = site.run()
    assert code == 1 and ('day_failed', 'C1', 'S1', d1, None, 'files_changed_after_import') in problems(db)
    assert one(db, 'SELECT count(*) FROM evidence_record') == records and one(db, 'SELECT count(*) FROM source_file') == 2
    added.unlink()
    assert site.run()[0] == 0
    first = site.inbox('S1') / ('gpdb-' + d1.isoformat() + '_000000.csv')
    original = first.read_bytes()
    first.write_bytes(original + original)
    code, _, _ = site.run()
    assert code == 1 and ('day_failed', 'C1', 'S1', d1, None, 'files_changed_after_import') in problems(db)
    assert one(db, 'SELECT count(*) FROM evidence_record') == records
    first.write_bytes(original)
    assert site.run()[0] == 0 and not problems(db, 'day_failed')
    v.require(True, 'D2 a file added or changed after the day was imported is reported as a conflict of that day and is not merged; removing it clears the report')
    for name in ('notes.txt', 'gpdb-2026-13-45_000000.csv', 'gpdb-' + d1.isoformat() + '_111111.csv'):
        (site.inbox('S1') / name).unlink()
    unmarked.unlink()
    (site.inbox('S1') / ('gpdb-' + TODAY.isoformat() + '_000000.csv')).unlink()
    for day in (d3, TODAY, TODAY + timedelta(days=2)):
        (site.inbox('S1') / inbox.marker_name(day)).unlink()
    assert site.run()[0] == 0 and clear(db)
    return d1


def verify_days_and_builds(v, site, db):
    # C2: three days at once -> three day batches oldest first, one version at the newest day.
    days = [TODAY - timedelta(days=n) for n in (9, 8, 6)]
    for n, day in enumerate(days):
        site.put('S2', day, base=10 * n)
    builds = one(db, "SELECT count(*) FROM build WHERE scope_id='C2'")
    code, clusters, events = site.run()
    order = [e['date'] for e in events if e.get('phase') == 'day_finished' and e['cluster'] == 'C2']
    assert code == 0 and order == [d.isoformat() for d in days], order
    assert [r[0] for r in many(db, "SELECT batch_id FROM import_batch WHERE scope_id='C2' AND state='complete' ORDER BY 1")] == \
        [inbox.batch_id('S2', d) for d in days]
    assert one(db, "SELECT count(*) FROM build WHERE scope_id='C2'") == builds + 1 and cutoff(db, 'C2') == days[-1]
    assert one(db, '''SELECT count(*) FROM input_batch i JOIN build b USING(input_id) JOIN current_version v USING(build_id)
        WHERE v.scope_id='C2' ''') == 3
    v.require(True, 'D3/D5 several pending days become one day batch each, oldest first, and give one version whose cutoff is the newest day')
    # Nothing new: nothing is imported or built again, nothing is counted twice.
    records = one(db, 'SELECT count(*) FROM evidence_record')
    code, clusters, events = site.run()
    assert code == 0 and clusters['C2']['build_state'] == 'not_due' and not [e for e in events if e.get('phase') == 'day_finished']
    assert one(db, 'SELECT count(*) FROM evidence_record') == records
    # Back-filling an earlier day does not move the window back.
    site.put('S2', TODAY - timedelta(days=7), base=50)
    code, clusters, _ = site.run()
    assert code == 0 and clusters['C2']['build_state'] == 'not_due' and cutoff(db, 'C2') == days[-1]
    v.require(True, 'D3/D5 a second run imports nothing twice; a back-filled earlier day is imported and the current cutoff does not move back')
    # Interval 7 for C2, off for C3, default 1 for C1.
    site.settings['build'] = dict(interval_days=1, clusters={'C2': 7, 'C3': 'off'})
    site.write()
    site.put('S2', TODAY - timedelta(days=2), base=60)   # 4 days after the cutoff: not due
    site.put('S3', TODAY - timedelta(days=2), base=70)
    code, clusters, _ = site.run()
    assert code == 0 and clusters['C2']['build_state'] == 'not_due' and clusters['C2']['newest_imported'] == (TODAY - timedelta(days=2)).isoformat()
    assert clusters['C3']['build_state'] == 'disabled' and cutoff(db, 'C3') is None
    site.put('S2', TODAY + timedelta(days=1), base=80)   # 7 days after the cutoff: due
    code, clusters, _ = site.run(today=TODAY + timedelta(days=2))
    assert clusters['C2']['build_state'] == 'published' and cutoff(db, 'C2') == TODAY + timedelta(days=1), clusters['C2']
    # The earlier back-filled day is inside the build that followed it.
    assert one(db, '''SELECT count(*) FROM input_batch i JOIN build b USING(input_id) JOIN current_version v USING(build_id)
        WHERE v.scope_id='C2' AND i.batch_id=%s''', (inbox.batch_id('S2', TODAY - timedelta(days=7)),)) == 1
    site.settings['build'] = dict(interval_days=1)
    site.write()
    code, clusters, _ = site.run()
    assert clusters['C3']['build_state'] == 'published' and cutoff(db, 'C3') == TODAY - timedelta(days=2)
    v.require(True, 'D4 build interval 1, 7 and off decide building as specified, per cluster; a cluster without a version builds right after its first import')


def verify_failures(v, site, db):
    # C1: a day that cannot be read fails alone; later days import and the build goes ahead.
    bad_day, good_day, next_day = (TODAY + timedelta(days=n) for n in (3, 4, 5))
    now = TODAY + timedelta(days=9)
    bad = site.put('S1', bad_day, base=100)
    site.put('S1', good_day, base=110)
    bad.chmod(0)
    code, clusters, _ = site.run(today=now)
    failed = many(db, "SELECT state,reason FROM mpp_daily_day WHERE source_id='S1' AND log_date=%s ORDER BY run_id", (bad_day,))
    assert code == 1 and clusters['C1']['build_state'] == 'published' and cutoff(db, 'C1') == good_day, clusters['C1']
    assert failed[-1] == ('failed', 'file_unreadable') and ('day_failed', 'C1', 'S1', bad_day, None, 'file_unreadable') in problems(db)
    code, _, _ = site.run(today=now)                      # still failing, still reported
    assert code == 1 and problems(db, 'day_failed')
    bad.chmod(0o644)
    code, clusters, _ = site.run(today=now)               # fixed: imported by itself, same batch
    assert code == 0 and not problems(db, 'day_failed') and clusters['C1']['build_state'] == 'not_due'
    assert one(db, 'SELECT state FROM import_batch WHERE batch_id=%s', (inbox.batch_id('S1', bad_day),)) == 'complete'
    assert one(db, 'SELECT count(*) FROM import_batch WHERE batch_id LIKE %s', ('daily:S1:' + bad_day.isoformat() + '%',)) == 1
    site.put('S1', next_day, base=120)
    code, clusters, _ = site.run(today=now)
    assert code == 0 and cutoff(db, 'C1') == next_day
    assert one(db, '''SELECT count(*) FROM input_batch i JOIN build b USING(input_id) JOIN current_version v USING(build_id)
        WHERE v.scope_id='C1' AND i.batch_id=%s''', (inbox.batch_id('S1', bad_day),)) == 1
    v.require(True, 'D3/D7 a failed day blocks neither later days nor the build; it is retried under the same batch, imported once fixed, and is in the build that follows')
    # A failed build keeps the current version; the next run builds.
    last_day = TODAY + timedelta(days=6)
    site.put('S1', last_day, base=130)
    good = (site.directory / 'training.json').read_text()
    def spoil(point, scope=None):
        if point == 'before_build' and scope == 'C1':
            (site.directory / 'training.json').write_text(json.dumps(dict(site.training, thresholds=dict(nope={}))))
    code, clusters, _ = site.run(today=now, fault=spoil)
    (site.directory / 'training.json').write_text(good)
    assert code == 1 and clusters['C1']['build_state'] == 'failed' and clusters['C1']['build_reason'] == 'invalid_thresholds'
    assert cutoff(db, 'C1') == next_day and problems(db, 'build_not_succeeded')
    code, clusters, _ = site.run(today=now)
    assert code == 0 and clusters['C1']['build_state'] == 'published' and cutoff(db, 'C1') == last_day and clear(db)
    v.require(True, 'D7 a failed build leaves the current version, is listed as open, and the next run builds and publishes')
    # A whole window without a valid sample: no new version, and not a failure.
    site.sources['S4'] = 'C4'
    (site.directory / 'inbox' / 'S4').mkdir()
    site.training['clusters'].append('C4')
    site.write()
    site.put('S4', TODAY - timedelta(days=2), text='COMMIT')
    code, clusters, _ = site.run(today=now)
    assert code == 0 and clusters['C4']['build_state'] == 'no_samples' and cutoff(db, 'C4') is None and clear(db)
    v.require(True, 'D7 a window without valid samples publishes nothing and is not a failure')


def verify_busy_and_single(v, site, db):
    day = TODAY + timedelta(days=7)
    now = TODAY + timedelta(days=9)
    site.put('S1', day, base=140)
    site.put('S2', day, base=150)
    holder = connect(site.dsn, 'sql_apm')
    with Task(holder, 'C1', 'snapshot'):
        code, clusters, _ = site.run(today=now)
    holder.close()
    assert code == 1 and clusters['C1']['state'] == 'skipped' and clusters['C1']['reason'] == 'cluster_busy'
    assert clusters['C2']['state'] == 'done' and cutoff(db, 'C2') == day and cutoff(db, 'C1') != day
    assert [r[:2] for r in problems(db, 'cluster_skipped')] == [('cluster_skipped', 'C1')]
    code, clusters, _ = site.run(today=now)
    assert code == 0 and cutoff(db, 'C1') == day and clear(db)
    v.require(True, 'D8 a busy cluster is skipped and recorded without waiting while the others finish; the next run makes it up')
    # A second daily run while one is active: exits at once, changes nothing.
    seen = {}
    def second(point, scope=None):
        if point == 'run_started':
            before = contents(db)
            out = site.cli('daily', 'run', '--config', site.path)
            seen.update(code=out.returncode, line=json.loads(out.stdout), same=before == contents(db))
    code, _, _ = site.run(today=now, fault=second)
    assert code == 0 and seen == dict(code=1, line=dict(reason='daily_run_active', state='rejected'), same=True), seen
    v.require(True, 'D8 a second daily run exits at once with daily_run_active and leaves every table unchanged')


def verify_cleanup(v, site, db):
    source = one(db, "SELECT build_id FROM current_version WHERE scope_id='C1'")
    now = TODAY + timedelta(days=9)
    def old(build, month):
        return clone_build(db, source, build, month, True)
    # Switched off: nothing is cleaned.
    pid = old('daily-old-1', '2025-01-01')
    site.settings['cleanup'] = dict(enabled=False)
    site.write()
    code, clusters, _ = site.run(today=now)
    assert code == 0 and clusters['C1']['cleanup_state'] == 'disabled'
    assert one(db, 'SELECT cleaned_at IS NULL FROM mpp_result_partition WHERE partition_id=%s', (pid,))
    del site.settings['cleanup']
    site.write()
    # A reader holds the statistics table: the month stays, the run does not fail, the next run cleans.
    holder = connect(site.dsn, 'sql_apm')
    with holder.cursor() as cur:
        cur.execute('SELECT 1 FROM mpp_statistic LIMIT 1')
    kept = digest(db, 'mpp_statistic', where='WHERE partition_id=%s', params=(pid,))
    code, clusters, _ = site.run(today=now)
    holder.rollback()
    holder.close()
    assert code == 0 and clusters['C1']['cleanup_state'] == 'pending' and clusters['C1']['months_pending'] == 1, clusters['C1']
    assert digest(db, 'mpp_statistic', where='WHERE partition_id=%s', params=(pid,)) == kept and kept['rows'] > 0
    assert [(r[1], r[4], r[5]) for r in problems(db, 'cleanup_pending')] == [('C1', date(2025, 1, 1), 'cleanup_lock_timeout')]
    code, clusters, _ = site.run(today=now)
    assert code == 0 and clusters['C1']['cleanup_state'] == 'cleaned' and clusters['C1']['months_cleaned'] == 1 and clear(db)
    assert one(db, 'SELECT cleaned_at IS NOT NULL AND groups_cleaned_at IS NOT NULL FROM mpp_result_partition WHERE partition_id=%s', (pid,))
    assert one(db, "SELECT to_regclass('mpp_statistic_p'||%s) IS NULL", (pid,))
    v.require(True, 'D9 cleanup switched off cleans nothing; a lock that cannot be had leaves the month as it was, does not fail the run and is retried; then the month is cleaned')
    # Same outcome as the manual command on an identical month.
    a, b = old('daily-old-2', '2025-02-01'), old('daily-old-3', '2025-03-01')
    manual = site.cli('cleanup', '--cluster', 'C1', '--training-config', site.directory / 'training.json', '--execute')
    assert manual.returncode == 0, manual.stdout
    c = old('daily-old-4', '2025-04-01')
    code, clusters, _ = site.run(today=now)
    shape = '''SELECT m.state,m.reason,m.published_builds,m.unpublished_builds,m.after_bytes,m.released_bytes>0,
        p.cleaned_at IS NOT NULL,p.groups_cleaned_at IS NOT NULL FROM mpp_cleanup_month m JOIN mpp_result_partition p USING(partition_id)
        WHERE m.partition_id=%s AND m.state='succeeded' '''
    assert code == 0 and len(many(db, shape, (c,))) == 1 and many(db, shape, (a,)) == many(db, shape, (b,)) == many(db, shape, (c,))
    assert one(db, "SELECT mode FROM task WHERE scope_id='C1' ORDER BY started_at DESC LIMIT 1") == 'cleanup'
    # Nothing expired: no cleanup task is opened at all.
    tasks = one(db, "SELECT count(*) FROM task WHERE scope_id='C1'")
    code, clusters, _ = site.run(today=now)
    assert code == 0 and clusters['C1']['cleanup_state'] == 'nothing' and one(db, "SELECT count(*) FROM task WHERE scope_id='C1'") == tasks
    v.require(True, 'D9 an expired month is cleaned by the daily run with the same result as cleanup --execute; with nothing expired no task is opened')


def verify_raw_files(v, site, db):
    now = TODAY + timedelta(days=9)
    present = {p.name for p in site.inbox('S2').iterdir()}
    oldest = TODAY - timedelta(days=9)
    assert 'gpdb-' + oldest.isoformat() + '_000000.csv' in present
    # A failed day, an unmarked file, an extra file of an imported day and a file outside stay.
    stuck_day = TODAY - timedelta(days=20)
    stuck = site.put('S2', stuck_day, base=200)
    stuck.chmod(0)
    loose = site.put('S2', TODAY - timedelta(days=21), base=210, marker=False)
    extra_day = TODAY - timedelta(days=8)
    extra = site.put('S2', extra_day, base=220, marker=False, suffix='_230000.csv')
    outside = site.directory / 'outside.csv'
    site.settings['raw_files'] = dict(retention_days='off')
    site.write()
    code, clusters, _ = site.run(today=now)
    assert clusters['C2']['raw_state'] == 'disabled' and {p.name for p in site.inbox('S2').iterdir()} >= present
    site.settings['raw_files'] = dict(retention_days=15)   # days before TODAY-6 are due
    site.write()
    sizes = {p.name: p.stat().st_size for p in site.inbox('S2').iterdir()}
    code, clusters, _ = site.run(today=now)
    left = {p.name for p in site.inbox('S2').iterdir()}
    gone = set(sizes) - left
    expected = set()
    for day in (TODAY - timedelta(days=9), TODAY - timedelta(days=7)):
        expected |= {'gpdb-' + day.isoformat() + '_000000.csv', inbox.marker_name(day)}
    assert gone == expected, (gone, expected)
    logs = [n for n in gone if n.endswith('.csv')]
    assert (clusters['C2']['raw_state'], clusters['C2']['raw_files'], clusters['C2']['raw_bytes']) == \
        ('deleted', len(logs), sum(sizes[n] for n in logs)), clusters['C2']
    assert many(db, "SELECT raw_days,raw_files,raw_bytes FROM mpp_daily_cluster WHERE scope_id='C2' AND raw_state='deleted'") == \
        [(2, len(logs), sum(sizes[n] for n in logs))]
    assert stuck.exists() and loose.exists() and extra.exists() and outside.exists()
    assert (site.inbox('S2') / inbox.marker_name(extra_day)).exists()
    v.require(True, 'D10 only days imported whole and past the keeping time lose their files and marker, and the record holds the counts; '
                    'a failed day, an unmarked file, a day with an extra file and a file outside the directory stay; switched off deletes nothing')
    # A deleted day that is put back and marked again is not counted twice, and goes again.
    records = one(db, 'SELECT count(*) FROM evidence_record')
    site.put('S2', oldest)
    code, clusters, _ = site.run(today=now)
    assert one(db, 'SELECT count(*) FROM evidence_record') == records and clusters['C2']['raw_files'] == 1
    assert not (site.inbox('S2') / ('gpdb-' + oldest.isoformat() + '_000000.csv')).exists()
    stuck.chmod(0o644)
    stuck.unlink()
    (site.inbox('S2') / inbox.marker_name(stuck_day)).unlink()
    loose.unlink()
    extra.unlink()
    site.settings['raw_files'] = dict(retention_days=45)
    site.write()
    assert site.run(today=now)[0] == 0 and clear(db)
    v.require(True, 'D10 a deleted day put back and marked again adds no record and is deleted again')


def blocked_kill(site, db, words, env, cluster='C1'):
    """Kill a command while its statement waits behind our table lock; seconds until its cluster is free."""
    blocker = connect(site.dsn, 'sql_apm')
    try:
        with blocker.cursor() as cur:
            cur.execute('LOCK TABLE task IN ACCESS EXCLUSIVE MODE')
        child = site.start(*words, env=env)
        try:
            probe = connect(site.dsn, 'sql_apm')
            probe.autocommit = True
            deadline = time.monotonic() + 30
            with probe.cursor() as cur:
                while True:      # the command owns the cluster and waits inside a statement
                    cur.execute("""SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a USING(pid)
                        WHERE l.locktype='advisory' AND l.granted AND a.wait_event_type='Lock'
                          AND l.objid::bigint=(hashtextextended(%s,1835101) & 4294967295)""", (cluster,))
                    if cur.fetchone()[0]:
                        break
                    assert time.monotonic() < deadline and child.poll() is None, (words, child.poll())
                    time.sleep(0.05)
                child.send_signal(signal.SIGKILL)
                child.wait(timeout=10)
                killed = time.monotonic()
                while True:
                    cur.execute("""SELECT count(*) FROM pg_locks l WHERE l.locktype='advisory' AND l.granted
                        AND l.objid::bigint=(hashtextextended(%s,1835101) & 4294967295)""", (cluster,))
                    if not cur.fetchone()[0] or time.monotonic() - killed > 12:
                        break
                    time.sleep(0.05)
                cur.execute("""SELECT count(*) FROM pg_locks l WHERE l.locktype='advisory' AND l.granted
                    AND l.objid::bigint=(hashtextextended(%s,1835101) & 4294967295)""", (cluster,))
                still = cur.fetchone()[0]
            probe.close()
            return time.monotonic() - killed, bool(still)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()
    finally:
        blocker.rollback()
        blocker.close()


def verify_liveness(v, site, db):
    now_day = date.today() - timedelta(days=1)
    frozen = version_status(db, 'C1')
    build = one(db, "SELECT build_id FROM current_version WHERE scope_id='C1'")
    ids = many(db, 'SELECT input_id,config_id FROM build WHERE build_id=%s', (build,))[0]
    manual = site.directory / 'manual.csv'
    write_csv(manual, lines(TODAY - timedelta(days=30), base=300))
    document = json.loads((site.directory / 'import.json').read_text())
    document['batches'] = dict(M1=dict(source='S1', files_confirmed_complete=True, dates=[(TODAY - timedelta(days=30)).isoformat()],
                                       files=[dict(path=str(manual), closed_and_copied=True)]))
    manual_config = site.directory / 'manual.json'
    manual_config.write_text(json.dumps(document))
    training = site.directory / 'training.json'
    clone_build(db, build, 'daily-live', '2025-06-01', True)
    commands = {
        'daily run': ['daily', 'run', '--config', site.path],
        'full': ['full', '--config', manual_config, '--source', 'S1', '--batch', 'M1', '--training-config', training, '--workers', '1'],
        'import': ['import', '--config', manual_config, '--source', 'S1', '--batch', 'M1', '--workers', '1'],
        'rebuild': ['rebuild', '--cluster', 'C1', '--training-config', training, '--cutoff-date', frozen['current']['cutoff_date'].isoformat()],
        'training snapshot': ['training', 'snapshot', '--cluster', 'C1', '--config', training, '--batch', inbox.batch_id('S1', TODAY - timedelta(days=5))],
        'statistics': ['statistics', '--cluster', 'C1', '--input', ids[0], '--config-id', ids[1]],
        'cleanup --execute': ['cleanup', '--cluster', 'C1', '--training-config', training, '--execute'],
    }
    site.put('S1', now_day, base=310)   # a real-date day so the command-line daily run has work
    measured = {}
    for name, words in commands.items():
        seconds, still = blocked_kill(site, db, words, dict(SQL_APM_CONNECTION_CHECK_SECONDS='2'))
        assert not still and seconds <= 2 + 2, (name, seconds)
        measured[name] = round(seconds, 2)
    v.require(True, 'D11 killed inside a waiting statement, each writing command frees its cluster within the check interval (2 s here) plus margin; seconds=' + json.dumps(measured))
    seconds, still = blocked_kill(site, db, commands['rebuild'], dict(SQL_APM_CONNECTION_CHECK_SECONDS='0'))
    assert still and seconds >= 12, (seconds, still)
    v.require(True, 'D11 with the check switched off (0) the killed command still holds its cluster 12 s later, until its statement ends: the interval is what frees it')
    out = site.cli('rebuild', '--cluster', 'C1', '--training-config', training, '--cutoff-date', '2026-09-01',
                   env=dict(SQL_APM_CONNECTION_CHECK_SECONDS='soon'))
    assert out.returncode == 1 and json.loads(out.stdout)['reason'] == 'invalid_connection_check_seconds'
    probe = connect(site.dsn, 'sql_apm')
    assert one(probe, 'SHOW client_connection_check_interval') == '10s'
    probe.close()
    v.require(True, 'D11 the interval defaults to 10 s on every product connection, is configurable, and an invalid value is rejected')
    # Leftovers of the kills are closed by the next admission; the run that follows is clean.
    out = site.cli('daily', 'run', '--config', site.path)
    assert out.returncode == 0, out.stdout[-2000:]
    assert one(db, "SELECT count(*) FROM task WHERE state='running'") == 0
    assert one(db, "SELECT count(*) FROM mpp_daily_run WHERE state='unfinished' AND reason='owner_exited'") == 1
    return now_day


def verify_abort_and_recovery(v, site, db, reference_dsn):
    """Interrupt real command-line runs; the final version must equal an undisturbed run's."""
    days = [date.today() - timedelta(days=n) for n in (4, 3)]
    def fill(target):
        for n, day in enumerate(days):
            path = target.inbox('S5') / ('gpdb-' + day.isoformat() + '_000000.csv')
            write_csv(path, lines(day, 6000, 1000 * n))
            target.mark('S5', day)
    for target in (site,):
        target.sources['S5'] = 'C5'
        (target.directory / 'inbox' / 'S5').mkdir()
        target.training['clusters'].append('C5')
        target.write()
    fill(site)
    def interrupt(sig):
        child = site.start('daily', 'run', '--config', site.path)
        try:
            for line in child.stdout:           # wait until the first file of C5 is being read
                event = json.loads(line)
                if event.get('phase') == 'cluster_started' and event['cluster'] == 'C5':
                    time.sleep(0.4)
                    child.send_signal(sig)
                    break
            rest = child.stdout.read()
            return child.wait(timeout=60), rest
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()
    code, rest = interrupt(signal.SIGTERM)
    assert code == 130 and json.loads(rest.splitlines()[-1]) == dict(reason='operator_interrupt', state='interrupted'), (code, rest[-500:])
    assert many(db, 'SELECT state,reason FROM mpp_daily_runs() ORDER BY started_at DESC LIMIT 1') == [('aborted', 'operator_interrupt')]
    assert many(db, "SELECT c.state FROM mpp_daily_cluster c JOIN mpp_daily_run r USING(run_id) WHERE c.scope_id='C5' ORDER BY r.started_at DESC LIMIT 1") == [('aborted',)]
    assert [r[5] for r in problems(db, 'run_not_finished')] == ['aborted']
    v.require(True, 'D12 a stop signal ends the run with exit 130; the run and its cluster are recorded as aborted and listed as open')
    code, _ = interrupt(signal.SIGKILL)
    assert code == -signal.SIGKILL
    deadline = time.monotonic() + 15   # the dead owner's sessions leave within the check interval
    while one(db, "SELECT state FROM mpp_daily_runs() ORDER BY started_at DESC LIMIT 1") != 'unfinished':
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert one(db, 'SELECT state FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1') == 'running'
    assert [r[5] for r in problems(db, 'run_not_finished')] == ['unfinished']
    while one(db, "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND objid::bigint=(hashtextextended('C5',1835101) & 4294967295)"):
        assert time.monotonic() < deadline
        time.sleep(0.2)
    out = site.cli('daily', 'run', '--config', site.path, timeout=300)
    assert out.returncode == 0, out.stdout[-2000:]
    assert one(db, 'SELECT state FROM mpp_daily_run ORDER BY started_at DESC OFFSET 1 LIMIT 1') == 'unfinished'
    assert not problems(db) and cutoff(db, 'C5') == days[-1]
    v.require(True, 'D12 a killed run shows as unfinished at once and is recorded so by the next run, which picks up where it stopped')
    # Reference: the same days in a database that was never interrupted.
    reference = connect(reference_dsn, 'sql_apm')
    twin = Site(site.directory / 'reference', reference_dsn, dict(S5='C5'))
    fill(twin)
    out = twin.cli('daily', 'run', '--config', twin.path, timeout=300)
    assert out.returncode == 0, out.stdout[-2000:]
    def version(connection):
        build = one(connection, "SELECT build_id FROM current_version WHERE scope_id='C5'")
        with connection, connection.cursor() as cur:
            # Group identifiers are derived from the group's content, so they are equal across databases.
            cur.execute('''SELECT (to_jsonb(s)-'build_id'-'partition_id')::text FROM mpp_statistic s
                WHERE s.build_id=%s ORDER BY 1''', (build,))
            return cur.fetchall()
    ours, theirs = version(db), version(reference)
    reference.close()
    assert ours and ours == theirs, (len(ours), len(theirs))
    assert one(db, "SELECT count(*) FROM evidence_record r JOIN source_file f USING(file_id) WHERE f.source_id='S5'") == 12000
    v.require(True, 'D12 after an abort and a kill the final version has the same statistics, row for row, as a run that was never interrupted; rows=' + str(len(ours)))


def verify_records(v, site, db):
    run = many(db, '''SELECT run_id,started_by,state,failed,started_at<=finished_at,local_date,stale_after_hours
        FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1''')[0]
    assert run[1:5] == ('manual', 'finished', False, True) and run[5] == date.today() and run[6] == 48
    out = site.cli('daily', 'status', '--limit', '200')
    shown = json.loads(out.stdout)
    assert out.returncode == 0 and set(shown) == {'clusters', 'problems', 'runs', 'nonconforming_files'}
    assert shown == status(db, 200)
    assert len(shown['runs']) == one(db, 'SELECT count(*) FROM mpp_daily_run') and shown['runs'][0]['run_id'] == run[0]
    newest = shown['runs'][0]['clusters']
    assert [c['scope_id'] for c in newest] == ['C1', 'C2', 'C3', 'C4', 'C5'] and all(set(c['stage_seconds']) >= {'import'} for c in newest)
    with_build = one(db, "SELECT count(*) FROM mpp_daily_cluster WHERE build_state='published' AND build_id IS NOT NULL AND publication_id IS NOT NULL AND stage_seconds ? 'rebuild_stages'")
    assert with_build >= 5 and one(db, "SELECT count(*) FROM mpp_daily_day WHERE state='complete' AND jsonb_array_length(files)=file_count") >= 10
    assert one(db, "SELECT count(*) FROM mpp_daily_run WHERE started_by='timer'") == 0
    assert site.cli('daily', 'run', '--config', site.path, '--trigger', 'timer').returncode == 0
    assert one(db, 'SELECT started_by FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1') == 'timer'
    # Status is read-only and needs no cluster: it answers while one is held, and writes nothing.
    holder = connect(site.dsn, 'sql_apm')
    with Task(holder, 'C1', 'snapshot'):
        before = contents(db)
        assert site.cli('daily', 'status').returncode == 0 and contents(db) == before
    holder.close()
    reader = connect(site.dsn.replace('user=sql_apm', 'user=sql_apm_ro'), 'sql_apm')
    assert one(reader, 'SELECT count(*) FROM mpp_view_daily_recent(5)') >= 5 and one(reader, 'SELECT count(*) FROM mpp_view_daily_clusters()') == 5
    try:
        one(reader, "UPDATE mpp_daily_run SET state='finished'")
    except Exception:
        reader.rollback()
    else:
        raise AssertionError('read-only account wrote a run record')
    reader.close()
    # Nothing private in the records or in any output.
    everything = out.stdout + site.cli('daily', 'run', '--config', site.path).stdout
    for table in ('mpp_daily_run', 'mpp_daily_cluster', 'mpp_daily_day', 'mpp_daily_problem'):
        everything += json.dumps(many(db, 'SELECT to_jsonb(t) FROM ' + table + ' t'), default=str)
    assert not [word for word in PRIVATE if word in everything]
    v.require(True, 'D16 a run record holds times, trigger, result, per-cluster days, build, cleanup, deleted files and stage seconds; status is read-only, '
                    'needs no cluster and equals the database; the read-only account can read but not write; no SQL text, database or user name anywhere')
    # Too long without a normally finished run.
    with db, db.cursor() as cur:
        cur.execute("UPDATE mpp_daily_run SET started_at=started_at-interval '50 hours',finished_at=finished_at-interval '50 hours'")
    assert [r[0] for r in problems(db)] == ['no_recent_success']
    with db, db.cursor() as cur:
        cur.execute("UPDATE mpp_daily_run SET started_at=started_at+interval '50 hours',finished_at=finished_at+interval '50 hours'")
    assert clear(db)
    labels = many(db, 'SELECT problem,hint FROM mpp_view_daily_problems()')
    assert labels == []
    v.require(True, 'D16 no normally finished run for longer than the configured hours is listed as open, and clears when one finishes')


def verify(pg_bin):
    with instance(pg_bin) as (directory, env), instance(pg_bin) as (other, _):
        v = Verification(pg_bin, directory, env)
        v.init()
        Verification(pg_bin, other, env).init()
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        db = connect(dsn, 'sql_apm')
        site = Site(directory, dsn, dict(S1='C1', S2='C2', S3='C3'))
        verify_configuration(v, site, db)
        verify_markers(v, site, db)
        verify_days_and_builds(v, site, db)
        verify_failures(v, site, db)
        verify_busy_and_single(v, site, db)
        verify_cleanup(v, site, db)
        verify_raw_files(v, site, db)
        verify_liveness(v, site, db)
        verify_abort_and_recovery(v, site, db, 'host=' + str(other / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm')
        verify_records(v, site, db)
        v.init('check')
        db.close()
        print('DAILY CHECKS:', v.completed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
