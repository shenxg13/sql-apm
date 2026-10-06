"""A cluster lease and all mutable stage writes share one database session."""
from contextlib import contextmanager
import time
import uuid

from psycopg2.extras import Json

from sql_apm.ingestion.config import IngestionError


class Task:
    def __init__(self, db, scope, mode):
        self.db, self.scope, self.mode = db, scope, mode
        self.task_id = 'T:' + uuid.uuid4().hex
        self.stage = 'none'
        self.started = None
        self.seconds = {}
        self.failure = None

    def __enter__(self):
        with self.db, self.db.cursor() as cur:
            if self.mode in ('full', 'import_only'):
                cur.execute("INSERT INTO scope VALUES (%s,'mpp','mpp-csv/1','1.0.0') ON CONFLICT DO NOTHING", (self.scope,))
            else:
                cur.execute('SELECT 1 FROM scope WHERE scope_id=%s', (self.scope,))
                if not cur.fetchone():
                    raise IngestionError('unknown_cluster')
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,1835101))', (self.scope,))
            acquired = cur.fetchone()[0]
            if not acquired:
                cur.execute("SELECT task_id FROM task WHERE scope_id=%s AND state='running' ORDER BY started_at DESC LIMIT 1", (self.scope,))
                row = cur.fetchone()
                # Admission may race the first owner's registration commit.
                cur.execute('''INSERT INTO task (task_id,scope_id,mode,state,stage,busy_task_id,reason,finished_at)
                    VALUES (%s,%s,%s,'busy_rejected','none',%s,'cluster_busy',clock_timestamp())''',
                    (self.task_id,self.scope,self.mode,row[0] if row else None))
            else:
                self.recover(cur)
                cur.execute("INSERT INTO task (task_id,scope_id,mode,state,stage) VALUES (%s,%s,%s,'running','none')",
                            (self.task_id,self.scope,self.mode))
        if not acquired:
            raise IngestionError('cluster_busy')
        return self

    def recover(self, cur):
        cur.execute("""UPDATE mpp_cleanup_month m SET state='interrupted',reason='owner_exited',finished_at=clock_timestamp()
            FROM task t WHERE m.task_id=t.task_id AND t.scope_id=%s AND t.state='running'
                AND m.state IN ('pending','removing_groups')""", (self.scope,))
        # A calculated build can be orphaned during checks/publication. Exclude
        # committed publications: a kill immediately after commit is not rollback.
        cur.execute('''UPDATE build b SET state='interrupted',finished_at=clock_timestamp()
            WHERE b.scope_id=%s AND (b.state='running' OR (
                b.state='calculated' AND EXISTS (SELECT FROM task_build tb JOIN task t USING(task_id)
                    WHERE tb.build_id=b.build_id AND t.state='running')
                AND NOT EXISTS (SELECT FROM publication p WHERE p.build_id=b.build_id)))
            RETURNING build_id''', (self.scope,))
        for (build,) in cur.fetchall():
            cur.execute('''INSERT INTO problem (problem_id,level,build_id,code,reason,effect,count_unit,count,resolution)
                VALUES (%s,'build',%s,'owner_exited','owner_exited','block_publication','problem',1,'open')''',
                ('P:'+uuid.uuid4().hex,build))
        cur.execute("UPDATE import_attempt SET state='interrupted',finished_at=clock_timestamp() WHERE scope_id=%s AND state='running' RETURNING attempt_id,batch_id,file_id", (self.scope,))
        for attempt,batch,file in cur.fetchall():
            problem='P:'+uuid.uuid4().hex
            cur.execute("""INSERT INTO problem (problem_id,level,batch_id,file_id,code,reason,effect,count_unit,count,resolution)
                VALUES (%s,'file',%s,%s,'owner_exited','owner_exited','block_publication','file',1,'open')""",
                (problem,batch,file))
            cur.execute('INSERT INTO attempt_problem VALUES (%s,%s)',(attempt,problem))
        cur.execute("UPDATE task SET state='interrupted',reason='owner_exited',finished_at=clock_timestamp() WHERE scope_id=%s AND state='running'", (self.scope,))

    def set_stage(self, stage):
        self._elapsed()
        self.stage, self.started = stage, time.monotonic()
        with self.db, self.db.cursor() as cur:
            cur.execute("UPDATE task SET stage=%s,stage_seconds=%s WHERE task_id=%s AND state='running'",
                        (stage,Json(self.seconds),self.task_id))
            if cur.rowcount != 1:
                raise IngestionError('task_no_longer_running')

    def _elapsed(self):
        if self.started is not None:
            self.seconds[self.stage] = round(time.monotonic()-self.started,3)

    def link(self, cur, kind, identifier):
        if kind not in ('batch','build','publication'):
            raise ValueError('invalid_task_artifact')
        cur.execute('INSERT INTO task_'+kind+' VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                    (self.task_id,identifier,self.scope))

    def finish(self, cur, state, reason):
        self._elapsed()
        cur.execute("UPDATE task SET state=%s,reason=%s,finished_at=clock_timestamp(),stage_seconds=%s WHERE task_id=%s AND state='running'",
                    (state,reason,Json(self.seconds),self.task_id))

    def __exit__(self, error_type, error, traceback):
        self._elapsed()
        state = ('failed' if self.failure else 'succeeded') if error is None else (
            'interrupted' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'failed')
        reason = self.failure if error is None else ('operator_interrupt' if state=='interrupted' else self.stage+'_failed')
        try:
            self.db.rollback()
            with self.db, self.db.cursor() as cur:
                self.finish(cur,state,reason)
        finally:
            if not self.db.closed:
                try:
                    self.db.rollback()
                    with self.db, self.db.cursor() as cur:
                        cur.execute('SELECT pg_advisory_unlock(hashtextextended(%s,1835101))',(self.scope,))
                except Exception:
                    self.db.close()


@contextmanager
def task_context(db, scope, mode, existing=None):
    if existing is not None:
        if existing.db is not db or existing.scope != scope:
            raise IngestionError('task_connection_mismatch')
        yield existing
    else:
        with Task(db,scope,mode) as task:
            yield task
