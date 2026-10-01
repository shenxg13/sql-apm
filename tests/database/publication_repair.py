"""R1 admission and month-preparation regressions using real CLI and PG locks."""
from datetime import timedelta
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

from psycopg2 import sql

from ingestion.test_reader import configuration, row, write_csv
from sql_apm.storage.ingestion import connect
from sql_apm.storage.statistics import StatisticsStore
from sql_apm.storage.training import TrainingStore
from sql_apm.training.config import validate

ROOT = Path(__file__).resolve().parents[2]


def command(dsn, args):
    result = subprocess.run([sys.executable, '-m', 'sql_apm'] + args, cwd=ROOT,
                            env=dict(os.environ, SQL_APM_DSN=dsn),
                            capture_output=True, text=True, timeout=15)
    return result.returncode, json.loads(result.stdout.splitlines()[-1])


def verify_admission(v, dsn, root):
    config = root / 'admission-training.json'
    config.write_text(json.dumps(dict(version=1, clusters=['UNKNOWN', 'REGISTER_IMPORT', 'REGISTER_FULL'],
                                     window=dict(cutoff_date='2026-07-31'))))
    before = v.sql('SELECT (SELECT count(*) FROM scope),(SELECT count(*) FROM task)')
    for args in [
        ['rebuild', '--cluster', 'UNKNOWN', '--training-config', str(config), '--cutoff-date', '2026-07-31'],
        ['training', 'snapshot', '--cluster', 'UNKNOWN', '--config', str(config), '--batch', 'absent'],
        ['statistics', '--cluster', 'UNKNOWN', '--input', 'absent', '--config-id', 'absent'],
    ]:
        code, result = command(dsn, args)
        assert code == 1 and result['reason'] == 'unknown_cluster', result
        assert v.sql('SELECT (SELECT count(*) FROM scope),(SELECT count(*) FROM task)') == before
    for action in ('status', 'history'):
        assert command(dsn, [action, '--cluster', 'UNKNOWN'])[0] == 0
    assert v.sql('SELECT (SELECT count(*) FROM scope),(SELECT count(*) FROM task)') == before
    v.require(True, 'R1-F003: three non-import commands reject unknown clusters without scope/task writes; queries stay read-only')
    for action in ('import', 'full'):
        scope = 'REGISTER_' + action.upper()
        csv = root / (scope + '.csv')
        write_csv(csv, [row(text='SELECT '+action+'_registration', message='duration: 1 ms')])
        path = root / (scope + '.json')
        doc = configuration(path, [csv], scope)
        doc['clusters'] = [scope]
        doc['sources'] = {scope: dict(doc['sources']['S1'], cluster=scope)}
        doc['batches'][scope]['source'] = scope
        path.write_text(json.dumps(doc))
        args = [action, '--config', str(path), '--source', scope, '--batch', scope, '--workers', '1']
        if action == 'full':
            args += ['--training-config', str(config)]
        code, result = command(dsn, args)
        assert code == 0, result
        assert v.sql("SELECT count(*) FROM scope WHERE scope_id='"+scope+"'") == '1'
        assert v.sql("SELECT state FROM task WHERE scope_id='"+scope+"'") == 'succeeded'
        if action == 'full':
            assert result['publication']['result'] == 'published'
    v.require(True, 'R1-F003: import and full still register fresh clusters and complete successfully')


def verify_month_preparation(v, dsn, root, other_snapshot):
    db = connect(dsn, 'sql_apm')
    with db, db.cursor() as cur:
        cur.execute('SELECT clock_timestamp()')
        month = cur.fetchone()[0].date().replace(day=1)
        future = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        cur.execute("SELECT partition_id FROM mpp_result_partition WHERE scope_id='C1' AND build_month=%s", (future,))
        pid = cur.fetchone()[0]
        cur.execute('SELECT count(*) FROM build WHERE partition_id=%s', (pid,))
        assert cur.fetchone()[0] == 0
        # Remove only unused, empty future partitions in this private fixture.
        for table in ('mpp_statistic', 'mpp_observation_statistic'):
            child = table + '_p' + str(pid)
            cur.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(child)))
            assert cur.fetchone()[0] == 0
            cur.execute(sql.SQL('DROP TABLE {}').format(sql.Identifier(child)))
        cur.execute('DELETE FROM mpp_result_partition WHERE partition_id=%s', (pid,))
    training = root / 'partition-training.json'
    training.write_text(json.dumps(dict(version=1, clusters=['C1'], window=dict(cutoff_date='2026-07-31'))))
    store = TrainingStore(dsn)
    try:
        first_snapshot = store.snapshot(validate(dict(version=1, clusters=['REGISTER_IMPORT'],
            window=dict(cutoff_date='2026-07-31')), 'REGISTER_IMPORT'), ['REGISTER_IMPORT'])
    finally:
        store.close()
    code = '''import json,os
from sql_apm.storage.statistics import StatisticsStore
store=StatisticsStore(os.environ['SQL_APM_DSN'])
def hold(event):
 if event['phase']=='results_written':
  print(json.dumps(dict(backend=store.db.get_backend_pid())),flush=True)
  input()
try:
 store.calculate('C2',os.environ['INPUT'],os.environ['CONFIG'],progress=hold)
finally:store.close()
'''
    child = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT,
        env=dict(os.environ, SQL_APM_DSN=dsn, INPUT=other_snapshot['input_id'], CONFIG=other_snapshot['config_id']),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first = None
    try:
        assert select.select([child.stdout], [], [], 20)[0], 'other_build_not_ready'
        backend = json.loads(child.stdout.readline())['backend']
        with db, db.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_locks WHERE pid=%s AND granted AND mode='RowExclusiveLock' AND relation IN ('mpp_build_group'::regclass,'mpp_build_observation_group'::regclass)", (backend,))
            assert cur.fetchone()[0] == 2
        started = time.monotonic()
        status, result = command(dsn, ['rebuild', '--cluster', 'C1', '--training-config', str(training),
                                       '--cutoff-date', '2026-07-31'])
        elapsed = time.monotonic() - started
        assert status == 0 and result['publication']['result'] == 'published', result
        with db, db.cursor() as cur:
            cur.execute("SELECT count(*) FROM mpp_result_partition WHERE scope_id='C1' AND build_month=%s", (future,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT state FROM pg_stat_activity WHERE pid=%s", (backend,))
            assert cur.fetchone()[0] == 'idle in transaction'
        v.require(True, 'R1-F002: rebuild publishes while another real build holds both shared group write locks; optional month skipped; seconds='+str(round(elapsed, 3)))
        first_code = '''import json,os
from sql_apm.storage.statistics import StatisticsStore
store=StatisticsStore(os.environ['SQL_APM_DSN'])
print(json.dumps(dict(backend=store.db.get_backend_pid())),flush=True)
try:
 store.calculate('REGISTER_IMPORT',os.environ['INPUT'],os.environ['CONFIG'])
finally:store.close()
'''
        first = subprocess.Popen([sys.executable, '-c', first_code], cwd=ROOT,
            env=dict(os.environ, SQL_APM_DSN=dsn, INPUT=first_snapshot['input_id'], CONFIG=first_snapshot['config_id']),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert select.select([first.stdout], [], [], 20)[0]
        first_backend = json.loads(first.stdout.readline())['backend']
        deadline = time.monotonic() + 5
        while True:
            with db, db.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_locks WHERE pid=%s AND NOT granted AND mode='ShareRowExclusiveLock' AND relation='mpp_build_group'::regclass", (first_backend,))
                waiting = cur.fetchone()[0] == 1
            if waiting:
                break
            assert first.poll() is None and time.monotonic() < deadline, 'first_month_lock_not_observed'
            time.sleep(0.01)
        with db, db.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='2s'")
            cur.execute('SELECT count(*) FROM mpp_build_group')
            cur.fetchone()
        child.communicate('\n', timeout=20)
        assert child.returncode == 0
        first.communicate(timeout=20)
        assert first.returncode == 0
        v.require(True, 'R1-F002: first required month waits for the other build transaction; ordinary group reads continue; completes after release')
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=10)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
        if first is not None:
            if first.poll() is None:
                first.kill(); first.wait(timeout=10)
            first.stdout.close(); first.stderr.close()
        db.close()
    status, result = command(dsn, ['rebuild', '--cluster', 'C1', '--training-config', str(training),
                                   '--cutoff-date', '2026-07-31'])
    assert status == 0 and result['publication']['result'] == 'published'
    assert v.sql("SELECT count(*) FROM mpp_result_partition WHERE scope_id='C1' AND build_month='"+future.isoformat()+"'") == '1'
    v.require(True, 'R1-F002: later build creates both deferred future partitions after the other transaction ends')
    holder = connect(dsn, 'sql_apm')
    probe = connect(dsn, 'sql_apm')
    store = StatisticsStore(dsn)
    try:
        for table in ('mpp_build_group', 'mpp_build_observation_group'):
            with holder, holder.cursor() as lock, store.db, store.db.cursor() as cur:
                lock.execute(sql.SQL('LOCK TABLE {} IN ROW EXCLUSIVE MODE').format(sql.Identifier(table)))
                store._prebuild_next_month(cur, 'C1', month.replace(year=2035))
                cur.execute("SELECT count(*) FROM mpp_result_partition WHERE scope_id='C1' AND build_month=%s", (month.replace(year=2035),))
                assert cur.fetchone()[0] == 0
                # On second-lock failure the first acquired lock must be released
                # before this caller's transaction ends, not just at commit.
                with probe, probe.cursor() as check:
                    check.execute('LOCK TABLE mpp_build_group,mpp_build_observation_group IN ROW EXCLUSIVE MODE NOWAIT')
        v.require(True, 'R1-F002: either shared group write lock defers optional DDL without retaining a partial exclusive lock')
        for table in ('mpp_statistic', 'mpp_observation_statistic'):
            with holder, holder.cursor() as lock, store.db, store.db.cursor() as cur:
                lock.execute(sql.SQL('LOCK TABLE ONLY {} IN SHARE UPDATE EXCLUSIVE MODE').format(sql.Identifier(table)))
                cur.execute("SET LOCAL statement_timeout='2s'")
                store._prebuild_next_month(cur, 'C1', month.replace(year=2036))
                cur.execute("SELECT count(*) FROM mpp_result_partition WHERE scope_id='C1' AND build_month=%s", (month.replace(year=2036),))
                assert cur.fetchone()[0] == 0
                with probe, probe.cursor() as check:
                    check.execute('LOCK TABLE mpp_build_group,mpp_build_observation_group IN ROW EXCLUSIVE MODE NOWAIT')
        v.require(True, 'R1-F002: either parent DDL lock defers optional preparation before taking referenced-table locks')
    finally:
        store.close(); probe.close(); holder.close()
