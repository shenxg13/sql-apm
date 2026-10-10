"""Records of the daily run. Small committed writes on the run's own connection."""
import json
from pathlib import PurePath
import uuid

from psycopg2.extras import Json

from sql_apm.daily.inbox import batch_id
from sql_apm.ingestion.config import identity


class DailyStore:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        """Session lock held until the connection closes; False when another run has it."""
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(mpp_daily_lock_key())')
            return cur.fetchone()[0]

    def begin(self, started_by, today, stale_after_hours, clusters):
        run_id = 'D:' + uuid.uuid4().hex
        with self.db, self.db.cursor() as cur:
            # This session holds the lock, so any other running row lost its owner.
            cur.execute("""UPDATE mpp_daily_run SET state='unfinished',reason='owner_exited',
                finished_at=clock_timestamp() WHERE state='running'""")
            cur.execute('''INSERT INTO mpp_daily_run (run_id,started_by,state,local_date,stale_after_hours)
                VALUES (%s,%s,'running',%s,%s)''', (run_id, started_by, today, stale_after_hours))
            for ordinal, scope in enumerate(clusters):
                cur.execute("INSERT INTO mpp_daily_cluster (run_id,scope_id,ordinal,state) VALUES (%s,%s,%s,'pending')",
                            (run_id, scope, ordinal))
        return run_id

    def cluster_started(self, run_id, scope):
        with self.db, self.db.cursor() as cur:
            cur.execute("""UPDATE mpp_daily_cluster SET state='running',started_at=clock_timestamp()
                WHERE run_id=%s AND scope_id=%s""", (run_id, scope))

    def day(self, run_id, scope, source_id, day, state, reason, files, records, seconds):
        with self.db, self.db.cursor() as cur:
            cur.execute('''INSERT INTO mpp_daily_day (run_id,scope_id,source_id,log_date,state,reason,batch_id,
                    file_count,byte_count,added_records,seconds,files)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (run_id, scope, source_id, day, state, reason, batch_id(source_id, day), len(files),
                 sum(size for _, size in files), records, seconds,
                 Json([dict(name=name, size=size) for name, size in files] if state == 'complete' else [])))

    def cluster_finished(self, run_id, scope, record, problems):
        fields = ('state', 'reason', 'failed', 'newest_imported', 'build_state', 'build_reason', 'cutoff_date',
                  'build_id', 'publication_id', 'cleanup_state', 'cleanup_reason', 'months_cleaned',
                  'months_pending', 'released_bytes', 'raw_state', 'raw_reason', 'raw_days', 'raw_files',
                  'raw_bytes', 'stage_seconds')
        values = [Json(record[name]) if name == 'stage_seconds' else record[name] for name in fields]
        with self.db, self.db.cursor() as cur:
            cur.execute('UPDATE mpp_daily_cluster SET ' + ','.join(name + '=%s' for name in fields) +
                        ',finished_at=clock_timestamp() WHERE run_id=%s AND scope_id=%s', values + [run_id, scope])
            for seq, problem in enumerate(problems, 1):
                cur.execute('''INSERT INTO mpp_daily_problem (run_id,scope_id,seq,kind,source_id,log_date,
                        result_month,reason,file_name,file_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (run_id, scope, seq, problem['kind'], problem.get('source_id'), problem.get('log_date'),
                     problem.get('result_month'), problem.get('reason'), problem.get('file_name'),
                     problem.get('file_count')))

    def finish(self, run_id, state, failed, reason):
        with self.db, self.db.cursor() as cur:
            cur.execute("""UPDATE mpp_daily_run SET state=%s,failed=%s,reason=%s,finished_at=clock_timestamp()
                WHERE run_id=%s AND state='running'""", (state, failed, reason, run_id))

    def day_states(self, source_id, days):
        """State of each day's batch, and the names and sizes kept from its successful import."""
        ids = {batch_id(source_id, day): day for day in days}
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT batch_id,state FROM import_batch WHERE batch_id=ANY(%s)', (list(ids),))
            states = {ids[batch]: state for batch, state in cur}
            cur.execute('''SELECT DISTINCT ON (d.log_date) d.log_date,d.files FROM mpp_daily_day d
                JOIN mpp_daily_run r USING(run_id)
                WHERE d.source_id=%s AND d.log_date=ANY(%s) AND d.state='complete'
                ORDER BY d.log_date,r.started_at DESC,r.run_id DESC''', (source_id, list(days)))
            recorded = {day: [(f['name'], f['size']) for f in files] for day, files in cur}
            # A run killed between the import and its own record leaves only the frozen list.
            for batch, day in ids.items():
                if states.get(day) == 'complete' and day not in recorded:
                    cur.execute('SELECT evidence_manifest FROM analysis WHERE analysis_id=%s', ('A:' + identity(batch),))
                    row = cur.fetchone()
                    if row:
                        recorded[day] = [(PurePath(f['path']).name, None) for f in json.loads(row[0])['files']]
        return states, recorded

    def newest_imported(self, scope):
        with self.db, self.db.cursor() as cur:
            cur.execute('''SELECT max(d.declared_date) FROM batch_date d JOIN import_batch b USING(batch_id)
                WHERE b.scope_id=%s AND b.state='complete' ''', (scope,))
            return cur.fetchone()[0]

    def current_cutoff(self, scope):
        with self.db, self.db.cursor() as cur:
            cur.execute('''SELECT s.cutoff_date FROM current_version v JOIN build b USING(build_id)
                JOIN config_snapshot s USING(config_id) WHERE v.scope_id=%s''', (scope,))
            row = cur.fetchone()
            return row[0] if row else None


def status(db, limit=10):
    """Recent runs, where each cluster stands and what is open. Read-only."""
    def rows(cur):
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur]
    with db, db.cursor() as cur:
        cur.execute('SELECT * FROM mpp_daily_clusters() ORDER BY ordinal')
        clusters = rows(cur)
        cur.execute('SELECT * FROM mpp_daily_problems() ORDER BY scope_id NULLS FIRST,kind,source_id,log_date,result_month')
        problems = rows(cur)
        cur.execute('SELECT * FROM mpp_daily_recent(%s) ORDER BY run_started_at DESC,run_id DESC,ordinal', (limit,))
        runs = {}
        for row in rows(cur):
            head = {name: row.pop(name) for name in ('run_id', 'started_by', 'run_state', 'run_failed', 'run_reason',
                                                    'run_started_at', 'run_finished_at')}
            run = runs.setdefault(head['run_id'], dict(head, clusters=[]))
            if row['scope_id'] is not None:
                run['clusters'].append(row)
        cur.execute('''SELECT p.scope_id,p.source_id,p.file_name,p.file_count FROM mpp_daily_problem p
            WHERE p.kind='nonconforming_file' AND p.run_id=(SELECT run_id FROM mpp_daily_run
                ORDER BY started_at DESC,run_id DESC LIMIT 1) ORDER BY p.scope_id,p.seq''')
        other = rows(cur)
    return json.loads(json.dumps(dict(clusters=clusters, problems=problems, runs=list(runs.values()),
                                      nonconforming_files=other), default=str))
