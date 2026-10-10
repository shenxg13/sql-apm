#!/usr/bin/env python3
"""Synthetic acceptance of what the daily run must not lose or hide:

imported files that change afterwards, a deletion cut short, a busy cluster with nothing
else to do, problems that outlive a skipped or aborted run, the meaning of "no successful
run for too long", and a stop signal that arrives while a statement waits.

Runs on private disposable PostgreSQL 17 instances with synthetic logs only.
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
sys.path[:0] = [str(ROOT), str(RESOURCE_ROOT / 'tests'), str(RESOURCE_ROOT / 'scripts/db')]
from verify import instance, Verification
from verify_daily import TODAY, Site, clear, cutoff, many, one, problems
from database.retention import clone_build
import sql_apm.daily.run as daily_run
from sql_apm.daily import inbox
from sql_apm.storage.daily import status
from sql_apm.storage.ingestion import connect
from sql_apm.storage.tasks import Task

CHANGED = 'files_changed_after_import'
CLUSTER_LOCK = "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND objid::bigint=(hashtextextended(%s,1835101) & 4294967295)"


def day_problems(db):
    return sorted((r[1], r[3], r[5]) for r in problems(db, 'day_failed'))


def name_of(day, suffix='_000000.csv'):
    return 'gpdb-' + day.isoformat() + suffix


def same_size_rewrite(path):
    """Other content, same length; the times of the file move as with any write."""
    original = path.read_bytes()
    changed = original.replace(b'SELECT', b'select')
    assert changed != original and len(changed) == len(original)
    path.write_bytes(changed)
    return original


def verify_changed_files(v, site, db):
    """F001: only a file proven to be what was imported may be deleted."""
    days = [TODAY - timedelta(days=n) for n in (40, 39, 38, 37, 36, 35)]
    now = TODAY
    site.settings['raw_files'] = dict(retention_days='off')
    site.write()
    for n, day in enumerate(days):
        site.put('S1', day, base=10 * n)
    assert site.run(today=now)[0] == 0 and clear(db)
    paths = [site.inbox('S1') / name_of(day) for day in days]
    assert one(db, "SELECT count(*) FROM mpp_daily_file WHERE source_id='S1'") == 6
    # An untouched file is recognised by its stat values alone: nothing is read again.
    reads, original = [], daily_run.checksum
    daily_run.checksum = lambda path: (reads.append(path), original(path))[1]
    try:
        assert site.run(today=now)[0] == 0 and reads == []
        os.utime(paths[5])                       # only its times moved: read once, found the same, remembered
        assert site.run(today=now)[0] == 0 and clear(db) and reads == [paths[5]]
        assert site.run(today=now)[0] == 0 and reads == [paths[5]]
    finally:
        daily_run.checksum = original
    v.require(True, 'F001 a run reads no imported file again while device, inode, size and both times are as recorded; a file whose times '
                    'alone moved is read once, found to be the same content and recorded anew')
    site.settings['raw_files'] = dict(retention_days=10)     # every one of these days is now due
    site.write()
    same_size_rewrite(paths[0])
    replacement = site.directory / 'replacement.csv'         # another file moved onto the name
    replacement.write_bytes(paths[1].read_bytes().replace(b'daily_t', b'daily_u') + b'\n')
    assert replacement.stat().st_ino != paths[1].stat().st_ino
    os.replace(replacement, paths[1])
    hidden = same_size_rewrite(paths[2])                     # and its change made invisible to stat-based checks
    info = paths[2].stat()
    with db, db.cursor() as cur:
        cur.execute("UPDATE mpp_daily_file SET device=%s,inode=%s,mtime_ns=%s,ctime_ns=%s WHERE source_id='S1' AND log_date=%s",
                    (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, days[2]))
    records = one(db, 'SELECT count(*) FROM evidence_record')
    code, clusters, _ = site.run(today=now)
    assert code == 1 and day_problems(db) == [('C1', days[0], CHANGED), ('C1', days[1], CHANGED), ('C1', days[2], CHANGED)], day_problems(db)
    assert all(path.exists() for path in paths[:3]) and all((site.inbox('S1') / inbox.marker_name(d)).exists() for d in days[:3])
    assert not any(path.exists() for path in paths[3:]) and (clusters['C1']['raw_state'], clusters['C1']['raw_files']) == ('deleted', 3)
    assert one(db, 'SELECT count(*) FROM evidence_record') == records
    assert site.run(today=now)[0] == 1 and len(day_problems(db)) == 3 and all(path.exists() for path in paths[:3])
    v.require(True, 'F001 an imported file rewritten at the same length, one replaced under its name, and one whose change no stat value '
                    'shows are all kept and reported as a conflict of their day; the unchanged days beside them are deleted with the right count')
    paths[2].write_bytes(hidden)
    assert site.run(today=now)[0] == 1 and day_problems(db) == [('C1', days[0], CHANGED), ('C1', days[1], CHANGED)] and not paths[2].exists()
    for path, day in zip(paths[:2], days[:2]):
        path.unlink()
        (site.inbox('S1') / inbox.marker_name(day)).unlink()
    assert site.run(today=now)[0] == 0 and clear(db)
    # Killed between the import's commit and the run's own record, then the file grows.
    crash = TODAY - timedelta(days=30)
    path = site.put('S1', crash, base=200)
    killed_at(site, now, 'day_imported')
    assert one(db, 'SELECT state FROM import_batch WHERE batch_id=%s', (inbox.batch_id('S1', crash),)) == 'complete'
    assert one(db, 'SELECT count(*) FROM mpp_daily_day WHERE log_date=%s', (crash,)) == 0
    assert one(db, 'SELECT count(*) FROM mpp_daily_file WHERE log_date=%s', (crash,)) == 0
    with path.open('ab') as stream:
        stream.write(b'\n')
    code, clusters, _ = site.run(today=now)
    assert code == 1 and path.exists() and day_problems(db) == [('C1', crash, CHANGED)] and clusters['C1']['raw_files'] == 0
    path.write_bytes(path.read_bytes()[:-1])
    code, clusters, _ = site.run(today=now)
    assert code == 0 and not path.exists() and clusters['C1']['raw_files'] == 1 and clear(db)
    v.require(True, 'F001 after a kill between the import and the run\'s own record, the names and contents come from the import\'s rows: '
                    'a file that grew meanwhile is kept and reported; restored, it is proven and deleted')


def verify_busy_without_work(v, site, db):
    """F004: a cluster someone else holds is left alone even when no step would have opened a task."""
    now = TODAY
    day = TODAY - timedelta(days=29)
    site.settings['raw_files'] = dict(retention_days='off')
    site.write()
    path, other = site.put('S1', day, base=300), site.put('S2', day, base=310)
    assert site.run(today=now)[0] == 0 and clear(db)
    site.settings['raw_files'] = dict(retention_days=10)
    site.write()
    holder = connect(site.dsn, 'sql_apm')
    with Task(holder, 'C1', 'snapshot'):
        tasks = one(db, "SELECT count(*) FROM task WHERE scope_id='C1'")
        code, clusters, _ = site.run(today=now)
        assert code == 1 and (clusters['C1']['state'], clusters['C1']['reason'], clusters['C1']['raw_files']) == ('skipped', 'cluster_busy', 0)
        assert path.exists() and not other.exists() and clusters['C2']['raw_files'] == 1
        assert [r[:2] for r in problems(db)] == [('cluster_skipped', 'C1')]
        assert one(db, "SELECT count(*) FROM task WHERE scope_id='C1'") == tasks      # looked, did not try to take it
        site.settings.update(build=dict(interval_days='off'), cleanup=dict(enabled=False))
        site.write()
        code, clusters, _ = site.run(today=now)
        assert code == 1 and clusters['C1']['state'] == 'skipped' and path.exists()
    site.settings.pop('build')
    site.settings.pop('cleanup')
    site.write()
    # Taken after the first look, before the files would go: the deletion itself asks for the cluster.
    second = connect(site.dsn, 'sql_apm')
    def take(point, scope=None, name=None):
        if point == 'before_raw_files' and scope == 'C1':
            with second.cursor() as cur:
                cur.execute('SELECT pg_advisory_lock(hashtextextended(%s,1835101))', ('C1',))
            second.commit()
    code, clusters, _ = site.run(today=now, fault=take)
    assert code == 1 and clusters['C1']['state'] == 'skipped' and path.exists()
    second.close()
    holder.close()
    code, clusters, _ = site.run(today=now)
    assert code == 0 and not path.exists() and clusters['C1']['raw_files'] == 1 and clear(db)
    v.require(True, 'F004 a held cluster with nothing to import, build or clean is skipped and recorded, exit 1, its raw files kept, also with '
                    'building and cleanup switched off; the other cluster goes on; a cluster taken just before the deletion is skipped as well')


CHILD = '''import json, os, sys, time
from datetime import date
from sql_apm.daily.config import load_config
from sql_apm.daily.run import DailyRun
def fault(point, scope=None, name=None):
    if point == os.environ['POINT'] and (not os.environ.get('NAME') or name == os.environ['NAME']):
        print('READY', flush=True)
        time.sleep(300)
DailyRun(os.environ['SQL_APM_DSN'], 'sql_apm', load_config(os.environ['CONFIG']), 'manual', None,
         date.fromisoformat(os.environ['TODAY']), fault).execute()
'''


def killed_at(site, now, point, name=''):
    """Run a daily run in a child and kill it when it reaches the given point."""
    child = subprocess.Popen([sys.executable, '-c', CHILD], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env=dict(os.environ, SQL_APM_DSN=site.dsn, CONFIG=str(site.path), TODAY=now.isoformat(), POINT=point, NAME=name,
                                      SQL_APM_CONNECTION_CHECK_SECONDS='1'))
    try:
        line = child.stdout.readline()
        assert line.strip() == 'READY', (line, child.stderr.read()[-800:])
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdout.close()
        child.stderr.close()
    db = connect(site.dsn, 'sql_apm')
    try:
        deadline = time.monotonic() + 15       # the dead run's sessions leave within the check interval
        while one(db, "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted"):
            assert time.monotonic() < deadline
            time.sleep(0.1)
    finally:
        db.close()


def verify_interrupted_deletion(v, site, db):
    """F005: a deletion cut short is finished later and never mistaken for a change of the input."""
    now = TODAY
    site.settings['raw_files'] = dict(retention_days='off')
    site.write()
    days = [TODAY - timedelta(days=n) for n in (28, 27, 26, 25, 24, 23, 22)]
    sizes = {}
    for n, day in enumerate(days):
        for part, suffix in enumerate(('_000000.csv', '_000000.csv.1', '_000000.csv.2')):
            sizes[name_of(day, suffix)] = site.put('S1', day, base=400 + 30 * n + 10 * part, suffix=suffix).stat().st_size
    assert site.run(today=now)[0] == 0 and clear(db)
    site.settings['raw_files'] = dict(retention_days=27)       # only the first day is due
    site.write()
    first, second, third = (name_of(days[0], s) for s in ('_000000.csv', '_000000.csv.1', '_000000.csv.2'))
    def fail(point, scope=None, name=None):
        if point == 'before_unlink' and name == second:
            raise OSError('synthetic')
    code, clusters, _ = site.run(today=now, fault=fail)
    left = sorted(p.name for p in site.inbox('S1').iterdir() if days[0].isoformat() in p.name)
    assert code == 1 and (clusters['C1']['raw_state'], clusters['C1']['raw_files'], clusters['C1']['raw_bytes']) == ('failed', 1, sizes[first])
    assert left == [inbox.marker_name(days[0]), second, third], left
    code, clusters, _ = site.run(today=now)
    assert code == 0 and clear(db) and (clusters['C1']['raw_state'], clusters['C1']['raw_days'], clusters['C1']['raw_files'],
        clusters['C1']['raw_bytes']) == ('deleted', 1, 2, sizes[second] + sizes[third]), clusters['C1']
    assert not [p for p in site.inbox('S1').iterdir() if days[0].isoformat() in p.name]
    v.require(True, 'F005 a deletion that fails at the second file is finished by the next run without a false conflict; each run counts what it removed')

    def audit(day):
        return many(db, '''SELECT count(*),count(removed_at),coalesce(sum(byte_count) FILTER (WHERE removed_at IS NOT NULL),0)
            FROM mpp_daily_file WHERE source_id='S1' AND log_date=%s''', (day,))[0]
    def counted():
        return many(db, "SELECT sum(raw_days),sum(raw_files),sum(raw_bytes) FROM mpp_daily_cluster WHERE scope_id='C1'")[0]
    before, killed = counted(), one(db, "SELECT count(*) FROM mpp_daily_run WHERE state='unfinished'")
    # Killed between two files, right after a file went but before that was written down, and before the marker.
    for index, (day, point, name) in enumerate([(days[1], 'before_unlink', name_of(days[1], '_000000.csv.1')),
                                                (days[2], 'after_unlink', name_of(days[2], '_000000.csv')),
                                                (days[3], 'before_marker', '')]):
        site.settings['raw_files'] = dict(retention_days=26 - index)
        site.write()
        killed_at(site, now, point, name)
        present = sorted(p.name for p in site.inbox('S1').iterdir() if day.isoformat() in p.name)
        assert inbox.marker_name(day) in present and len(present) == (3, 3, 1)[index], (point, present)
        # What the killed run wrote down before it died is in its own record.
        assert many(db, '''SELECT c.raw_days,c.raw_files FROM mpp_daily_cluster c JOIN mpp_daily_runs() r USING(run_id)
            WHERE c.scope_id='C1' AND r.state='unfinished' ORDER BY r.started_at DESC LIMIT 1''') == [(0, (1, 0, 3)[index])], point
        code, clusters, _ = site.run(today=now)
        assert code == 0 and clear(db) and not [p for p in site.inbox('S1').iterdir() if day.isoformat() in p.name], (point, clusters['C1'])
        assert clusters['C1']['raw_state'] == 'deleted' and clusters['C1']['raw_files'] == (2, 3, 0)[index], (point, clusters['C1'])
        total = sum(sizes[name_of(day, s)] for s in ('_000000.csv', '_000000.csv.1', '_000000.csv.2'))
        assert audit(day) == (3, 3, total), (point, audit(day))
    after = counted()
    removed = many(db, "SELECT count(*),sum(byte_count) FROM mpp_daily_file WHERE source_id='S1' AND log_date=ANY(%s) AND removed_at IS NOT NULL",
                   (days[1:4],))[0]
    assert removed[0] == 9 and (after[0] - before[0], after[1] - before[1], after[2] - before[2]) == (3, 9, removed[1]), (before, after, removed)
    assert one(db, "SELECT count(*) FROM mpp_daily_run WHERE state='unfinished'") == killed + 3
    v.require(True, 'F005 killed between two files, right after a file went, and before the marker: the next run finishes each deletion and reports '
                    'no conflict; the killed runs\' records hold what they had removed, and the records of all runs add up to exactly the '
                    'days, files and bytes removed (3 days, %d files, %d bytes)' % removed)
    # A file added or changed after a deletion began is not deleted.
    day = days[4]
    site.settings['raw_files'] = dict(retention_days=23)
    site.write()
    def fail_last(point, scope=None, name=None):
        if point == 'before_unlink' and name == name_of(day, '_000000.csv.1'):
            raise OSError('synthetic')
    assert site.run(today=now, fault=fail_last)[0] == 1
    late = site.put('S1', day, base=900, marker=False, suffix='_230000.csv')
    kept = site.inbox('S1') / name_of(day, '_000000.csv.2')
    code, clusters, _ = site.run(today=now)
    assert code == 1 and day_problems(db) == [('C1', day, CHANGED)] and late.exists() and kept.exists() and clusters['C1']['raw_files'] == 0
    late.unlink()
    same_size_rewrite(kept)
    code, clusters, _ = site.run(today=now)
    assert code == 1 and day_problems(db) == [('C1', day, CHANGED)] and kept.exists() and clusters['C1']['raw_files'] == 0
    assert (site.inbox('S1') / name_of(day, '_000000.csv.1')).exists()
    for path in list(site.inbox('S1').iterdir()):
        if day.isoformat() in path.name:
            path.unlink()
    assert site.run(today=now)[0] == 0 and clear(db)
    v.require(True, 'F005 a file that appears, or a remaining file that changes, after a deletion began is kept and reported; nothing more of that day is deleted')
    # Stopped, not killed, between two files and before the marker: the run records itself as aborted with what it removed.
    for day, point, name, done in ((days[5], 'before_unlink', name_of(days[5], '_000000.csv.1'), 1), (days[6], 'before_marker', days[6].isoformat(), 3)):
        site.settings['raw_files'] = dict(retention_days=(TODAY - day).days - 1)
        site.write()
        def stop(at, scope=None, which=None, point=point, name=name):
            if at == point and which == name:
                raise KeyboardInterrupt
        try:
            site.run(today=now, fault=stop)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError('not stopped')
        assert many(db, '''SELECT r.state,c.state,c.raw_state,c.raw_files FROM mpp_daily_cluster c JOIN mpp_daily_run r USING(run_id)
            WHERE c.scope_id='C1' ORDER BY r.started_at DESC LIMIT 1''') == [('aborted', 'aborted', 'deleted', done)], point
        code, clusters, _ = site.run(today=now)
        total = sum(sizes[name_of(day, s)] for s in ('_000000.csv', '_000000.csv.1', '_000000.csv.2'))
        assert code == 0 and clear(db) and (clusters['C1']['raw_days'], clusters['C1']['raw_files']) == (1, 3 - done), (point, clusters['C1'])
        assert audit(day) == (3, 3, total) and not [p for p in site.inbox('S1').iterdir() if day.isoformat() in p.name], point
    site.settings['raw_files'] = dict(retention_days='off')
    site.write()
    assert site.run(today=now)[0] == 0 and clear(db)
    v.require(True, 'F005 stopped by a signal between two files, and between the last file and the marker: the run is recorded as aborted with the '
                    'files it had removed; the next run finishes the day without a conflict and the counts add up')


def verify_lasting_problems(v, site, db):
    """F007: a step that did not run says nothing; a problem goes only when a run has really dealt with it."""
    now = TODAY
    def kinds():
        return sorted((r[0], r[1]) for r in problems(db))
    def same_everywhere():
        shown = status(db, 5)['problems']
        board = many(db, 'SELECT problem,cluster FROM mpp_view_daily_problems()')
        assert sorted((p['kind'], p['scope_id']) for p in shown) == kinds() and len(board) == len(shown)
    site.put('S3', TODAY - timedelta(days=20), base=500)
    good = (site.directory / 'training.json').read_text()
    def spoil(point, scope=None, name=None):
        if point == 'before_build' and scope == 'C3':
            (site.directory / 'training.json').write_text(json.dumps(dict(site.training, thresholds=dict(nope={}))))
    assert site.run(today=now, fault=spoil)[0] == 1
    (site.directory / 'training.json').write_text(good)
    assert kinds() == [('build_not_succeeded', 'C3')] and cutoff(db, 'C3') is None
    site.put('S3', TODAY - timedelta(days=19), base=510)
    holder = connect(site.dsn, 'sql_apm')
    with Task(holder, 'C3', 'snapshot'):
        assert site.run(today=now)[0] == 1
        assert kinds() == [('build_not_succeeded', 'C3'), ('cluster_skipped', 'C3')], kinds()
        same_everywhere()
        assert site.run(today=now)[0] == 1 and kinds() == [('build_not_succeeded', 'C3'), ('cluster_skipped', 'C3')]
    holder.close()
    def first_cluster(point, scope=None, name=None):   # stopped before this cluster's turn: it is still the one that was skipped
        if point == 'cluster_started' and scope == 'C1':
            raise KeyboardInterrupt
    try:
        site.run(today=now, fault=first_cluster)
    except KeyboardInterrupt:
        pass
    assert kinds() == [('build_not_succeeded', 'C3'), ('cluster_skipped', 'C3'), ('run_not_finished', None)], kinds()
    same_everywhere()
    assert site.run(today=now)[0] == 0 and clear(db) and cutoff(db, 'C3') == TODAY - timedelta(days=19)
    same_everywhere()
    v.require(True, 'F007 a failed build stays listed through two skipped runs and a run stopped before the cluster\'s turn, and so does the '
                    'skip itself; both go when a run really builds: kept, kept, kept, gone; the command line and the dashboard functions agree')
    # A conflict of a day and a month waiting for cleanup, through a skipped run and through a run stopped early.
    bad_day, changed_day = TODAY - timedelta(days=18), TODAY - timedelta(days=19)
    bad = site.put('S3', bad_day, base=520)
    bad.chmod(0)
    changed = site.inbox('S3') / name_of(changed_day)          # imported above; now other content at the same length
    imported = same_size_rewrite(changed)
    pid = clone_build(db, one(db, "SELECT build_id FROM current_version WHERE scope_id='C3'"), 'recovery-old', '2025-01-01', True)
    reader = connect(site.dsn, 'sql_apm')
    def hold(point, scope=None, name=None):
        if point == 'before_cleanup' and scope == 'C3':
            with reader.cursor() as cur:
                cur.execute('SELECT 1 FROM mpp_statistic LIMIT 1')
    assert site.run(today=now, fault=hold)[0] == 1
    reader.rollback()
    expected = [('cleanup_pending', 'C3'), ('day_failed', 'C3'), ('day_failed', 'C3')]
    days_open = [('C3', changed_day, CHANGED), ('C3', bad_day, 'file_unreadable')]
    assert kinds() == expected and day_problems(db) == days_open, (kinds(), day_problems(db))
    holder = connect(site.dsn, 'sql_apm')
    with Task(holder, 'C3', 'snapshot'):
        assert site.run(today=now)[0] == 1 and kinds() == sorted(expected + [('cluster_skipped', 'C3')]) and day_problems(db) == days_open
    holder.close()
    def early(point, scope=None, name=None):          # stopped after the import step, before building and cleaning
        if point == 'before_cleanup' and scope == 'C3':
            raise KeyboardInterrupt
    try:
        site.run(today=now, fault=early)
    except KeyboardInterrupt:
        pass
    assert kinds() == sorted(expected + [('run_not_finished', None)], key=lambda k: (k[0], k[1] or '')), kinds()
    same_everywhere()
    def before_import(point, scope=None, name=None):  # stopped before anything of the cluster was looked at
        if point == 'cluster_started' and scope == 'C3':
            raise KeyboardInterrupt
    try:
        site.run(today=now, fault=before_import)
    except KeyboardInterrupt:
        pass
    assert day_problems(db) == days_open and ('cleanup_pending', 'C3') in kinds()
    bad.chmod(0o644)
    changed.write_bytes(imported)
    assert site.run(today=now)[0] == 0 and clear(db)
    assert one(db, 'SELECT cleaned_at IS NOT NULL FROM mpp_result_partition WHERE partition_id=%s', (pid,))
    reader.close()
    v.require(True, 'F007 a failed day, a conflict of an imported day and a month waiting for cleanup stay listed through a skipped run, a run '
                    'stopped before those steps and a run stopped before the cluster was looked at; they go when a later run imports the day, '
                    'finds the file to be the imported content again and cleans the month')


def verify_success_age(v, site, db):
    """F006: only a run that finished without any failure counts as a successful one."""
    def stale():
        return [(r[0], r[8]) for r in many(db, 'SELECT * FROM mpp_daily_problems()') if r[0] == 'no_recent_success']
    def shift(where, hours):
        with db, db.cursor() as cur:
            cur.execute("UPDATE mpp_daily_run SET started_at=started_at-make_interval(hours => %s),"
                        "finished_at=finished_at-make_interval(hours => %s) WHERE " + where, (hours, hours))
    assert site.run()[0] == 0 and clear(db)
    success = one(db, "SELECT max(finished_at) FROM mpp_daily_run WHERE state='finished' AND NOT failed")
    # Every later run failed or only skipped: the last success is what counts.
    bad = site.put('S1', TODAY - timedelta(days=15), base=600)
    bad.chmod(0)
    assert site.run()[0] == 1
    holders = [connect(site.dsn, 'sql_apm') for _ in range(3)]
    with Task(holders[0], 'C1', 'snapshot'), Task(holders[1], 'C2', 'snapshot'), Task(holders[2], 'C3', 'snapshot'):
        code, clusters, _ = site.run()
        assert code == 1 and {c['state'] for c in clusters.values()} == {'skipped'}
    for holder in holders:
        holder.close()
    assert stale() == []
    shift("state='finished' AND NOT failed", 72)
    found = stale()
    assert len(found) == 1 and abs((found[0][1] - success).total_seconds() + 72 * 3600) < 1, found
    assert one(db, 'SELECT last_success_at FROM mpp_view_daily_last()') == found[0][1]
    told = json.loads(site.cli('daily', 'status', '--limit', 1).stdout)['problems']
    shown = [p for p in status(db, 3)['problems'] if p['kind'] == 'no_recent_success']
    assert told == status(db, 1)['problems'] and [p['kind'] for p in told].count('no_recent_success') == 1, told
    assert len(shown) == 1 and many(db, "SELECT problem FROM mpp_view_daily_problems() WHERE problem='太久没有成功的运行'") == [('太久没有成功的运行',)]
    # The limit itself: 48 hours by default, taken from the newest run.
    shift("state='finished' AND NOT failed", -72 + 47)
    assert stale() == []
    shift("state='finished' AND NOT failed", 2)
    assert len(stale()) == 1
    with db, db.cursor() as cur:
        cur.execute('UPDATE mpp_daily_run SET stale_after_hours=72 WHERE run_id=(SELECT run_id FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1)')
    assert stale() == []
    with db, db.cursor() as cur:
        cur.execute('UPDATE mpp_daily_run SET stale_after_hours=48')
    assert len(stale()) == 1
    # Still open while a run is going on.
    runner = connect(site.dsn, 'sql_apm')
    with runner.cursor() as cur:
        cur.execute('SELECT pg_advisory_lock(mpp_daily_lock_key())')
        cur.execute("INSERT INTO mpp_daily_run (run_id,started_by,state,local_date,stale_after_hours) VALUES ('D:probe','manual','running',current_date,48)")
    runner.commit()
    assert one(db, "SELECT state FROM mpp_daily_runs() WHERE run_id='D:probe'") == 'running' and len(stale()) == 1
    with runner.cursor() as cur:
        cur.execute("DELETE FROM mpp_daily_run WHERE run_id='D:probe'")
    runner.commit()
    runner.close()
    # No success ever: counted from the first run.
    with db, db.cursor() as cur:
        cur.execute("UPDATE mpp_daily_run SET failed=true WHERE state='finished'")
    first = one(db, 'SELECT min(started_at) FROM mpp_daily_run')
    assert [since for _, since in stale()] == [first]
    with db, db.cursor() as cur:
        cur.execute("UPDATE mpp_daily_run SET started_at=clock_timestamp()-interval '10 hours',finished_at=clock_timestamp()-interval '9 hours' WHERE finished_at IS NOT NULL")
    assert stale() == []
    # A run that succeeds ends it.
    shift('true', 60)
    assert len(stale()) == 1
    bad.chmod(0o644)
    assert site.run()[0] == 0 and clear(db)
    v.require(True, 'F006 runs that failed or only skipped do not reset the time of the last success; the limit and its boundary hold; the problem '
                    'stays while a run is going on; without any success it is counted from the first run; a successful run ends it; '
                    'the command line and the dashboard functions show the same')


def stopped(site, db, signal_number, lock, mode, group=False, cluster='C1', waiting="a.wait_event_type='Lock'"):
    """Send a stop signal to a command-line run whose statement waits behind the lock we take with `lock`.

    With `group` the signal goes to the whole process group, as from a terminal or from systemd.
    """
    blocker = connect(site.dsn, 'sql_apm')
    others = """SELECT count(*) FROM pg_stat_activity a WHERE a.datname=current_database() AND a.backend_type='client backend'
        AND a.pid<>pg_backend_pid() AND a.pid<>ALL(%s)"""
    try:
        with blocker.cursor() as cur:
            if lock:
                cur.execute(*lock)
        probe = connect(site.dsn, 'sql_apm')
        probe.autocommit = True
        known = [blocker.get_backend_pid(), db.get_backend_pid()]
        child = subprocess.Popen([sys.executable, '-m', 'sql_apm', 'daily', 'run', '--config', str(site.path)], cwd=ROOT,
                                 env=dict(os.environ, SQL_APM_DSN=site.dsn), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 60
            with probe.cursor() as cur:
                while True:
                    cur.execute(others + ' AND ' + waiting, (known,))
                    if cur.fetchone()[0]:
                        break
                    assert time.monotonic() < deadline and child.poll() is None, (lock, child.poll(), child.stderr.read()[-500:])
                    time.sleep(0.05)
                # The step under test is the one that waits: its task is open and nothing else is.
                cur.execute("SELECT mode FROM task WHERE scope_id=%s AND state='running'", (cluster,))
                assert cur.fetchall() == [(mode,)], (lock, mode)
                if group:
                    os.killpg(child.pid, signal_number)
                else:
                    child.send_signal(signal_number)
                began = time.monotonic()
                code = child.wait(timeout=30)
                seconds = time.monotonic() - began
                last = child.stdout.read().splitlines()[-1]
                # Still under our lock: the run ended without waiting for it, holds nothing and left no session behind.
                cur.execute(CLUSTER_LOCK, (cluster,))
                assert cur.fetchone()[0] == 0
                deadline = time.monotonic() + 15
                while True:
                    cur.execute(others, (known,))
                    if not cur.fetchone()[0]:
                        break
                    assert time.monotonic() < deadline, lock
                    time.sleep(0.05)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()
            probe.close()
        return code, seconds, json.loads(last)
    finally:
        blocker.rollback()
        blocker.close()


def verify_stop_signal(v, site, db):
    """F008: a stop signal ends the run in bounded time although a statement is waiting."""
    measured = {}
    def aborted(label, signal_number, lock, mode, group=False, **more):
        code, seconds, last = stopped(site, db, signal_number, lock, mode, group, **more)
        assert code == 130 and last == dict(reason='operator_interrupt', state='interrupted') and seconds < 5, (label, code, seconds, last)
        assert many(db, 'SELECT state,reason FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1') == [('aborted', 'operator_interrupt')], label
        assert one(db, "SELECT state FROM mpp_daily_cluster c WHERE c.scope_id='C1' AND c.run_id=(SELECT run_id FROM mpp_daily_run ORDER BY started_at DESC LIMIT 1)") == 'aborted'
        assert [r[0] for r in problems(db)] == ['run_not_finished'], (label, problems(db))
        measured[label] = round(seconds, 2)
    day = date.today() - timedelta(days=4)
    # Import: the write of the day's records waits.
    site.settings['build'] = dict(interval_days='off')
    site.write()
    site.put('S1', day, count=40, base=700)
    aborted('import, SIGTERM', signal.SIGTERM, ('LOCK TABLE evidence_record IN ACCESS EXCLUSIVE MODE',), 'import_only')
    aborted('import, SIGINT', signal.SIGINT, ('LOCK TABLE evidence_record IN ACCESS EXCLUSIVE MODE',), 'import_only')
    aborted('import, SIGINT to the process group', signal.SIGINT, ('LOCK TABLE evidence_record IN ACCESS EXCLUSIVE MODE',), 'import_only', group=True)
    # Not a lock wait but a statement that is executing: every insert of a record sleeps.
    with db, db.cursor() as cur:
        cur.execute("CREATE FUNCTION slow_probe() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN PERFORM pg_sleep(300); RETURN NEW; END'")
        cur.execute('CREATE TRIGGER slow_probe BEFORE INSERT ON evidence_record FOR EACH ROW EXECUTE FUNCTION slow_probe()')
    try:
        aborted('import, a statement that is executing, SIGTERM', signal.SIGTERM, None, 'import_only', waiting="a.wait_event='PgSleep'")
        aborted('import, a statement that is executing, SIGINT', signal.SIGINT, None, 'import_only', waiting="a.wait_event='PgSleep'")
    finally:
        with db, db.cursor() as cur:
            cur.execute('DROP TRIGGER slow_probe ON evidence_record')
            cur.execute('DROP FUNCTION slow_probe()')
    out = site.cli('daily', 'run', '--config', site.path)
    assert out.returncode == 0 and one(db, 'SELECT state FROM import_batch WHERE batch_id=%s', (inbox.batch_id('S1', day),)) == 'complete'
    assert one(db, 'SELECT count(*) FROM evidence_record') == 40
    # Build: the calculation waits to write its results.
    site.settings.pop('build')
    site.write()
    aborted('build, SIGTERM', signal.SIGTERM, ('LOCK TABLE mpp_statistic IN ACCESS EXCLUSIVE MODE',), 'rebuild')
    aborted('build, SIGINT', signal.SIGINT, ('LOCK TABLE mpp_statistic IN ACCESS EXCLUSIVE MODE',), 'rebuild')
    aborted('build, SIGTERM to the process group', signal.SIGTERM, ('LOCK TABLE mpp_statistic IN ACCESS EXCLUSIVE MODE',), 'rebuild', group=True)
    out = site.cli('daily', 'run', '--config', site.path)
    assert out.returncode == 0 and cutoff(db, 'C1') == day, out.stdout[-800:]
    # Cleanup: the cleanup task waits to register the month it is about to clean (someone holds that month's row).
    pid = clone_build(db, one(db, "SELECT build_id FROM current_version WHERE scope_id='C1'"), 'stop-old', '2025-02-01', True)
    row = ('SELECT 1 FROM mpp_result_partition WHERE partition_id=%s FOR UPDATE', (pid,))
    aborted('cleanup, SIGTERM', signal.SIGTERM, row, 'cleanup')
    aborted('cleanup, SIGINT', signal.SIGINT, row, 'cleanup')
    assert one(db, 'SELECT cleaned_at IS NULL FROM mpp_result_partition WHERE partition_id=%s', (pid,))
    out = site.cli('daily', 'run', '--config', site.path)
    assert out.returncode == 0 and one(db, 'SELECT cleaned_at IS NOT NULL FROM mpp_result_partition WHERE partition_id=%s', (pid,))
    assert not status(db, 1)['problems'] and one(db, "SELECT count(*) FROM task WHERE state='running'") == 0
    v.require(True, 'F008 a stop signal that arrives while a statement of the import, the build or the cleanup waits behind a lock, or while a '
                    'statement of the import is executing, ends the run as aborted with exit 130, with the cluster released and no session '
                    'left behind; seconds from the signal to the exit: '
                    + json.dumps(measured) + '; the next run carries on and completes each step')


def verify(pg_bin):
    with instance(pg_bin) as (directory, env):
        v = Verification(pg_bin, directory, env)
        v.init()
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        db = connect(dsn, 'sql_apm')
        site = Site(directory, dsn, dict(S1='C1', S2='C2', S3='C3'))
        verify_changed_files(v, site, db)
        verify_busy_without_work(v, site, db)
        verify_interrupted_deletion(v, site, db)
        verify_lasting_problems(v, site, db)
        verify_success_age(v, site, db)
        v.init('check')
        db.close()
    with instance(pg_bin) as (directory, env):
        v2 = Verification(pg_bin, directory, env)
        v2.init()
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        db = connect(dsn, 'sql_apm')
        verify_stop_signal(v2, Site(directory, dsn, dict(S1='C1')), db)
        db.close()
        print('DAILY RECOVERY CHECKS:', v.completed + v2.completed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
