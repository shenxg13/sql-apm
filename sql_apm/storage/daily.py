"""Records of the daily run. Small committed writes on the run's own connection, each made
when the thing it records happens: a run that is stopped or killed has still said it."""
from contextlib import contextmanager
import json
import uuid

from psycopg2.extras import Json

from sql_apm.daily.inbox import batch_id
from sql_apm.ingestion.config import identity

# The key a cluster task holds (sql_apm.storage.tasks); looked at here, never redefined.
CLUSTER_KEY = 'hashtextextended(%s,1835101)'


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
        """The result of importing one day. Unless the import was interrupted, the day has thereby been
        looked at: that, and the problem when it did not import whole, are written in the same transaction."""
        with self.db, self.db.cursor() as cur:
            cur.execute('''INSERT INTO mpp_daily_day (run_id,scope_id,source_id,log_date,state,reason,batch_id,
                    file_count,byte_count,added_records,seconds,files)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (run_id, scope, source_id, day, state, reason, batch_id(source_id, day), len(files),
                 sum(size for _, size in files), records, seconds,
                 Json([dict(name=name, size=size) for name, size in files] if state == 'complete' else [])))
            if state != 'interrupted':
                if state != 'complete':
                    self._problem(cur, run_id, scope, dict(kind='day_failed', source_id=source_id, log_date=day,
                                                           reason=reason, file_count=len(files)))
                self._examined(cur, run_id, scope, source_id, [day])

    FIELDS = ('state', 'reason', 'failed', 'import_state', 'newest_imported', 'build_state', 'build_reason', 'cutoff_date',
              'build_id', 'publication_id', 'cleanup_state', 'cleanup_reason', 'months_cleaned', 'months_pending',
              'released_bytes', 'raw_state', 'raw_reason', 'stage_seconds')

    def cluster_step(self, run_id, scope, record, *names, problems=()):
        """How far the run has come with the cluster, written when a step has its result and not only at
        the end; the problems the step leaves are written in the same transaction."""
        values = [Json(record[name]) if name == 'stage_seconds' else record[name] for name in names]
        with self.db, self.db.cursor() as cur:
            cur.execute('UPDATE mpp_daily_cluster SET ' + ','.join(name + '=%s' for name in names) +
                        ' WHERE run_id=%s AND scope_id=%s', values + [run_id, scope])
            for problem in problems:
                self._problem(cur, run_id, scope, problem)

    def cluster_finished(self, run_id, scope, record):
        self.cluster_step(run_id, scope, record, *self.FIELDS)
        with self.db, self.db.cursor() as cur:
            cur.execute('UPDATE mpp_daily_cluster SET finished_at=clock_timestamp() WHERE run_id=%s AND scope_id=%s',
                        (run_id, scope))

    @staticmethod
    def _problem(cur, run_id, scope, problem):
        cur.execute('''INSERT INTO mpp_daily_problem (run_id,scope_id,seq,kind,source_id,log_date,
                result_month,reason,file_name,file_count)
            SELECT %s,%s,coalesce(max(seq),0)+1,%s,%s,%s,%s,%s,%s,%s FROM mpp_daily_problem
            WHERE run_id=%s AND scope_id=%s''',
            (run_id, scope, problem['kind'], problem.get('source_id'), problem.get('log_date'),
             problem.get('result_month'), problem.get('reason'), problem.get('file_name'),
             problem.get('file_count'), run_id, scope))

    @staticmethod
    def _examined(cur, run_id, scope, source_id, days):
        cur.execute("""UPDATE mpp_daily_cluster SET examined_days=jsonb_set(examined_days,ARRAY[%s],
                coalesce(examined_days->%s,'[]'::jsonb)||%s) WHERE run_id=%s AND scope_id=%s""",
            (source_id, source_id, Json([day.isoformat() for day in days]), run_id, scope))

    def examined(self, run_id, scope, source_id, days, problems=()):
        """The run has looked at these days of the source and come to a conclusion about each; the problems
        it found there are written in the same transaction, at the moment they are found."""
        if days or problems:
            with self.db, self.db.cursor() as cur:
                for problem in problems:
                    self._problem(cur, run_id, scope, problem)
                if days:
                    self._examined(cur, run_id, scope, source_id, days)

    def finish(self, run_id, state, failed, reason):
        with self.db, self.db.cursor() as cur:
            cur.execute("""UPDATE mpp_daily_run SET state=%s,failed=%s,reason=%s,finished_at=clock_timestamp()
                WHERE run_id=%s AND state='running'""", (state, failed, reason, run_id))

    def cluster_busy(self, scope):
        """Does any session hold this cluster right now? Looks only; takes nothing."""
        with self.db, self.db.cursor() as cur:
            cur.execute("""SELECT EXISTS (SELECT FROM pg_locks l WHERE l.locktype='advisory' AND l.granted AND l.objsubid=1
                AND l.database=(SELECT d.oid FROM pg_database d WHERE d.datname=current_database())
                AND l.classid::bigint=((""" + CLUSTER_KEY + """>>32) & 4294967295)
                AND l.objid::bigint=(""" + CLUSTER_KEY + """ & 4294967295))""", (scope, scope))
            return cur.fetchone()[0]

    @contextmanager
    def cluster_held(self, scope):
        """Hold the cluster for a step that opens no task of its own; yields False when it is taken."""
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(' + CLUSTER_KEY + ')', (scope,))
            held = cur.fetchone()[0]
        try:
            yield held
        finally:
            if held:
                with self.db, self.db.cursor() as cur:
                    cur.execute('SELECT pg_advisory_unlock(' + CLUSTER_KEY + ')', (scope,))

    def day_states(self, source_id, days):
        """State of each day's batch, where one exists."""
        ids = {batch_id(source_id, day): day for day in days}
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT batch_id,state FROM import_batch WHERE batch_id=ANY(%s)', (list(ids),))
            return {ids[batch]: state for batch, state in cur}

    def day_files(self, source_id, day):
        """What the day's batch holds, by file name, with what was last proven about each file.

        The names are the batch's own list, frozen when it was first registered, and no
        others: the same content may be known under further names from other batches, and
        those are not files of this day. The content of each name comes from the import's
        committed rows. So both are there even when the run was killed right after the
        import. The proof and the deletion progress come from mpp_daily_file and may be
        missing for a name.
        """
        batch, known = batch_id(source_id, day), {}
        with self.db, self.db.cursor() as cur:
            # The importer keeps the frozen list with the batch's analysis, under this identifier.
            cur.execute('SELECT evidence_manifest FROM analysis WHERE analysis_id=%s', ('A:' + identity(batch),))
            row = cur.fetchone()
            names = [entry.get('origin_key') for entry in json.loads(row[0])['files']] if row else []
            cur.execute("""SELECT f.file_id,f.checksum_value,f.byte_count,f.declaration_evidence
                FROM batch_entry e JOIN source_file f USING(file_id)
                JOIN import_attempt a ON a.attempt_id=e.final_attempt_id
                WHERE e.batch_id=%s AND f.checksum_algorithm='sha256' AND a.state IN ('succeeded','duplicate_skipped')""",
                (batch,))
            contents = {}
            for file_id, sha256, size, evidence in cur.fetchall():
                declared = json.loads(evidence)
                for name in declared.get('origin_keys', []) + [declared.get('origin_key')]:
                    contents[name] = (file_id, sha256, size)     # one content per name within a source
            for name in names:
                if name in contents:
                    file_id, sha256, size = contents[name]
                    known[name] = dict(file_id=file_id, sha256=sha256, byte_count=size, stamp=None,
                                       removing=False, removed=False)
            cur.execute("""SELECT file_name,file_id,device,inode,byte_count,mtime_ns,ctime_ns,
                    removing_at IS NOT NULL AND cleared_at IS NULL,removed_at IS NOT NULL
                FROM mpp_daily_file WHERE source_id=%s AND log_date=%s""", (source_id, day))
            for name, file_id, device, inode, size, mtime, ctime, removing, removed in cur:
                if name in known and known[name]['file_id'] == file_id:
                    known[name].update(stamp=None if device is None else (device, inode, size, mtime, ctime),
                                       removing=removing, removed=removed)
        return known

    def file_verified(self, run_id, scope, source_id, day, name, file_id, stamp):
        """The file was just read and is the imported content; a file put back after its deletion lives again."""
        with self.db, self.db.cursor() as cur:
            cur.execute("""INSERT INTO mpp_daily_file AS f (source_id,log_date,file_name,scope_id,batch_id,file_id,
                    device,inode,byte_count,mtime_ns,ctime_ns,verified_run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_id,log_date,file_name) DO UPDATE SET file_id=excluded.file_id,device=excluded.device,
                    inode=excluded.inode,byte_count=excluded.byte_count,mtime_ns=excluded.mtime_ns,ctime_ns=excluded.ctime_ns,
                    verified_at=clock_timestamp(),verified_run_id=excluded.verified_run_id,
                    removing_at=CASE WHEN f.removed_at IS NULL AND f.cleared_at IS NULL THEN f.removing_at END,
                    removing_run_id=CASE WHEN f.removed_at IS NULL AND f.cleared_at IS NULL THEN f.removing_run_id END,
                    removed_at=NULL,removed_run_id=NULL,cleared_at=NULL,cleared_run_id=NULL""",
                (source_id, day, name, scope, batch_id(source_id, day), file_id, *stamp, run_id))

    def file_doubted(self, source_id, day, name):
        """The file was read and is not the imported content: no proof stands, so every later look reads it."""
        with self.db, self.db.cursor() as cur:
            cur.execute("""UPDATE mpp_daily_file SET device=NULL,inode=NULL,mtime_ns=NULL,ctime_ns=NULL
                WHERE source_id=%s AND log_date=%s AND file_name=%s""", (source_id, day, name))

    def removal_decided(self, run_id, source_id, day):
        """Written before the first file of the day goes, so that a file missing afterwards is known to be ours."""
        with self.db, self.db.cursor() as cur:
            cur.execute("""UPDATE mpp_daily_file SET removing_at=clock_timestamp(),removing_run_id=%s
                WHERE source_id=%s AND log_date=%s AND removing_at IS NULL AND removed_at IS NULL""",
                (run_id, source_id, day))

    def file_removed(self, run_id, scope, source_id, day, name):
        """The file is gone. The run's counts move in the same transaction: they are never written anywhere else."""
        with self.db, self.db.cursor() as cur:
            cur.execute("""WITH gone AS (UPDATE mpp_daily_file SET removed_at=clock_timestamp(),removed_run_id=%s
                    WHERE source_id=%s AND log_date=%s AND file_name=%s AND removed_at IS NULL RETURNING byte_count)
                UPDATE mpp_daily_cluster c SET raw_state='deleted',raw_files=c.raw_files+1,raw_bytes=c.raw_bytes+gone.byte_count
                FROM gone WHERE c.run_id=%s AND c.scope_id=%s""", (run_id, source_id, day, name, run_id, scope))

    def pending_removals(self, source_id):
        """Days whose deletion was decided and is not written down as complete."""
        with self.db, self.db.cursor() as cur:
            cur.execute("""SELECT DISTINCT log_date FROM mpp_daily_file
                WHERE source_id=%s AND removing_at IS NOT NULL AND cleared_at IS NULL ORDER BY 1""", (source_id,))
            return [row[0] for row in cur]

    def day_removed(self, run_id, scope, source_id, day):
        """Nothing of the day is left, its marker included. Counted once, whichever run comes to write it down:
        the day's rows are closed and the run's count of days moves in one transaction. True when it counted."""
        with self.db, self.db.cursor() as cur:
            cur.execute("""WITH done AS (UPDATE mpp_daily_file SET cleared_at=clock_timestamp(),cleared_run_id=%s
                    WHERE source_id=%s AND log_date=%s AND removing_at IS NOT NULL AND cleared_at IS NULL RETURNING 1),
                counted AS (UPDATE mpp_daily_cluster c SET raw_state='deleted',raw_days=c.raw_days+1
                    WHERE c.run_id=%s AND c.scope_id=%s AND EXISTS (SELECT FROM done) RETURNING 1)
                SELECT count(*) FROM counted""", (run_id, source_id, day, run_id, scope))
            return cur.fetchone()[0] == 1

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
