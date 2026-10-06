"""Month-level retention: short atomic DDL, then resumable group-row deletion."""
import time

from psycopg2 import sql
from psycopg2.errors import LockNotAvailable, QueryCanceled

from sql_apm.baseline.retention import expired
from sql_apm.ingestion.config import IngestionError, identity
from sql_apm.storage.tasks import Task

TABLES = ('mpp_statistic', 'mpp_observation_statistic')
GROUPS = ('mpp_build_group', 'mpp_build_observation_group')
LOCK_BUDGET = 10.0
CHUNK = 10000


def _clock(cur, reference_month):
    cur.execute("SELECT date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date")
    return reference_month or cur.fetchone()[0]


def _sizes(cur, pid):
    sizes = []
    for table in TABLES:
        cur.execute('SELECT coalesce(pg_total_relation_size(to_regclass(%s)),0)', (table+'_p'+str(pid),))
        sizes.append(cur.fetchone()[0])
    return sizes


def preview(db, scope, months=2, *, reference_month=None):
    """No lease, task, or writes. The reference date is internal acceptance only."""
    with db, db.cursor() as cur:
        # Stabilize the month marker and leaf catalog against concurrent cleanup.
        # These read locks do not occupy the cluster's task lease.
        cur.execute('LOCK TABLE ONLY mpp_statistic,ONLY mpp_observation_statistic IN ACCESS SHARE MODE')
        cur.execute('SELECT 1 FROM scope WHERE scope_id=%s', (scope,))
        if not cur.fetchone():
            raise IngestionError('unknown_cluster')
        current = _clock(cur, reference_month)
        cur.execute('''SELECT p.build_month FROM current_version v JOIN build b USING(build_id)
            JOIN mpp_result_partition p USING(partition_id) WHERE v.scope_id=%s''', (scope,))
        row = cur.fetchone(); protected = row[0] if row else None
        cur.execute('''SELECT p.partition_id,p.build_month,p.cleaned_at,p.groups_cleaned_at,
            count(b.build_id) FILTER (WHERE b.results_saved AND EXISTS
                (SELECT FROM publication pub WHERE pub.build_id=b.build_id AND pub.result='published')),
            count(b.build_id) FILTER (WHERE b.results_saved AND NOT EXISTS
                (SELECT FROM publication pub WHERE pub.build_id=b.build_id AND pub.result='published'))
            FROM mpp_result_partition p LEFT JOIN build b USING(partition_id)
            WHERE p.scope_id=%s GROUP BY p.partition_id ORDER BY p.build_month''', (scope,))
        partitions = cur.fetchall(); result = []
        for pid, month, cleaned, groups_cleaned, published, unpublished in partitions:
            state = ('cleaned' if cleaned else 'protected' if month == protected and expired(month,current,months)
                     else 'expired' if expired(month,current,months) else 'retained')
            sizes = _sizes(cur, pid)
            result.append(dict(partition_id=pid,build_month=month,state=state,
                reason={'cleaned':'results_cleaned','protected':'current_version_month',
                        'expired':'retention_expired','retained':'within_retention'}[state],
                published_builds=published,unpublished_builds=unpublished,
                statistic_bytes=sizes[0],observation_bytes=sizes[1],total_bytes=sum(sizes),
                cleaned_at=cleaned,groups_pending=bool(cleaned and not groups_cleaned)))
    return dict(scope='scope:'+identity(scope),retention_months=months,current_month=current,
                current_version_month=protected,expired_months=sum(p['state'] in ('expired','protected') for p in result),
                months=result)


def overdue_count(db, scope, months=2):
    # full's hint does not inspect leaf sizes or scan statistic/group rows.
    with db, db.cursor() as cur:
        current = _clock(cur, None)
        cur.execute('''SELECT count(*) FROM mpp_result_partition p WHERE p.scope_id=%s
            AND p.cleaned_at IS NULL AND
            (extract(year FROM p.build_month)*12+extract(month FROM p.build_month)) < %s''',
            (scope,current.year*12+current.month-months))
        return cur.fetchone()[0]


class CleanupStore:
    def __init__(self, db, *, fault=None):
        self.db, self.fault = db, fault

    def _point(self, stage, pid):
        if self.fault:
            self.fault(stage, pid)

    def execute(self, scope, months=2, *, reference_month=None):
        with Task(self.db, scope, 'cleanup') as task:
            self.task = task
            task.set_stage('cleanup')
            report = preview(self.db,scope,months,reference_month=reference_month)
            self._point('admitted', None)
            for month in report['months']:
                pid = month['partition_id']
                eligible = month['state']=='expired' or month['groups_pending']
                state = ('pending' if eligible else {'cleaned':'already_cleaned','protected':'protected',
                                                   'retained':'retained'}[month['state']])
                with self.db, self.db.cursor() as cur:
                    cur.execute('''INSERT INTO mpp_cleanup_month
                        (task_id,partition_id,state,reason,published_builds,unpublished_builds,before_bytes,after_bytes,finished_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN NULL ELSE clock_timestamp() END)''',
                        (task.task_id,pid,state,month['reason'],month['published_builds'],month['unpublished_builds'],
                         month['total_bytes'],month['total_bytes'],eligible))
                if not eligible:
                    continue
                removed = bool(month['cleaned_at'])
                try:
                    if not removed:
                        self._remove_partitions(pid)
                        removed = True
                    self._point('partitions_committed', pid)
                    self._remove_groups(pid)
                except (LockNotAvailable, QueryCanceled):
                    self._failed(pid,'failed' if removed else 'lock_timeout',
                                 'cleanup_groups_pending' if removed else 'cleanup_lock_timeout')
                    task.failure='cleanup_incomplete'
                except (KeyboardInterrupt, SystemExit):
                    self._failed(pid,'interrupted','operator_interrupt')
                    raise
                except Exception:
                    self._failed(pid,'failed','cleanup_month_failed')
                    task.failure='cleanup_incomplete'
            with self.db, self.db.cursor() as cur:
                cur.execute('''SELECT m.*,p.build_month,p.cleaned_at FROM mpp_cleanup_month m
                    JOIN mpp_result_partition p USING(partition_id) WHERE task_id=%s ORDER BY p.build_month''',
                    (task.task_id,))
                names = [d[0] for d in cur.description]
                outcomes = [dict(zip(names,row)) for row in cur]
        return dict(state='failed' if task.failure else 'succeeded',reason=task.failure,
                    task_id=task.task_id,scope=report['scope'],retention_months=months,
                    current_month=report['current_month'],current_version_month=report['current_version_month'],
                    months=outcomes)

    def _failed(self, pid, state, reason):
        self.db.rollback()
        with self.db, self.db.cursor() as cur:
            cur.execute('''UPDATE mpp_cleanup_month SET state=%s,reason=%s,finished_at=clock_timestamp()
                WHERE task_id=%s AND partition_id=%s''', (state,reason,self.task.task_id,pid))

    def _remove_partitions(self, pid):
        # Try the entire required lock set without queueing behind active readers
        # or another cluster's build. Failed attempts roll back *all* locks.
        # A single monotonic deadline also bounds any unexpected DDL lock wait.
        deadline = time.monotonic()+LOCK_BUDGET-0.1
        self._point('before_partitions', pid)
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise LockNotAvailable()
            try:
                with self.db, self.db.cursor() as cur:
                    # Required relation locks use NOWAIT. Unexpected catalog
                    # contention also gives up promptly, within the same budget.
                    cur.execute("SET LOCAL lock_timeout='1ms'")
                    began = time.monotonic()
                    parents = [sql.SQL('ONLY {}').format(sql.Identifier(t)) for t in TABLES]
                    leaves = [sql.Identifier(t+'_p'+str(pid)) for t in TABLES]
                    cur.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE NOWAIT').format(
                        sql.SQL(',').join(parents+[sql.Identifier(t) for t in GROUPS]+leaves)))
                    # Everything below is metadata / constant-row work. Do not
                    # inspect builds, statistics, or groups while holding AX.
                    self._point('partitions_locked', pid)
                    for leaf in leaves:
                        cur.execute(sql.SQL('DROP TABLE {}').format(leaf))
                    self._point('partitions_dropped', pid)
                    cur.execute('UPDATE mpp_result_partition SET cleaned_at=clock_timestamp() WHERE partition_id=%s', (pid,))
                    cur.execute('''UPDATE mpp_cleanup_month SET state='removing_groups',reason='results_cleaned',
                        after_bytes=0,released_bytes=before_bytes WHERE task_id=%s AND partition_id=%s''',
                        (self.task.task_id,pid))
                held = time.monotonic()-began
                # Commit duration included; updating the measurement itself
                # holds no result-table AX lock.
                with self.db, self.db.cursor() as cur:
                    cur.execute('UPDATE mpp_cleanup_month SET exclusive_seconds=%s WHERE task_id=%s AND partition_id=%s',
                                (held,self.task.task_id,pid))
                return
            except LockNotAvailable:
                self.db.rollback()
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.05,remaining))

    def _remove_groups(self, pid):
        for table, field in zip(GROUPS,('formal_groups_deleted','observation_groups_deleted')):
            last_key = ('', '')
            while True:
                started = time.monotonic()
                with self.db, self.db.cursor() as cur:
                    # Another cluster's brief DDL must not add an unbounded wait
                    # after statistics were removed. A retry resumes this phase.
                    cur.execute("SET LOCAL lock_timeout='1ms'")
                    # Advance along the partition-leading primary key. Repeating
                    # an unordered LIMIT from the beginning can rescan dead rows
                    # quadratically before VACUUM. ctid bounds the actual delete.
                    cur.execute(sql.SQL('''DELETE FROM {} WHERE ctid = ANY(ARRAY(
                        SELECT ctid FROM {} WHERE partition_id=%s AND (build_id,group_id)>(%s,%s)
                        ORDER BY build_id,group_id LIMIT %s)) RETURNING build_id,group_id''').format(
                        sql.Identifier(table),sql.Identifier(table)), (pid,*last_key,CHUNK))
                    count = cur.rowcount
                    if count:
                        # Project databases use C collation; UTF-8 key ordering
                        # matches Python. Memory is bounded by one small batch.
                        last_key = max(cur.fetchall())
                    self._point('groups_deleting', pid)
                    cur.execute(sql.SQL('''UPDATE mpp_cleanup_month SET {}={}+%s,group_seconds=group_seconds+%s,
                        state='removing_groups',reason='results_cleaned' WHERE task_id=%s AND partition_id=%s''').format(
                        sql.Identifier(field),sql.Identifier(field)),
                        (count,time.monotonic()-started,self.task.task_id,pid))
                self._point('groups_committed', pid)
                if count < CHUNK:
                    break
        with self.db, self.db.cursor() as cur:
            cur.execute('UPDATE mpp_result_partition SET groups_cleaned_at=clock_timestamp() WHERE partition_id=%s', (pid,))
            cur.execute('''UPDATE mpp_cleanup_month SET state='succeeded',reason='results_cleaned',finished_at=clock_timestamp()
                WHERE task_id=%s AND partition_id=%s''', (self.task.task_id,pid))
