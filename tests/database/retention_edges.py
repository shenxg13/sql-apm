"""R1 retention regressions: independent expectations and real lock holders."""
from datetime import date
from threading import Timer
from unittest.mock import patch

from psycopg2.errors import LockNotAvailable

from database.retention import clone_build
from sql_apm.storage.cleanup import CleanupStore,GROUPS,preview,overdue_count
from sql_apm.storage.ingestion import connect


def month_seconds(outcome):
    return (outcome['finished_at']-outcome['started_at']).total_seconds()


def shift(month,offset):
    year,zero_month=divmod(month.year*12+month.month-1+offset,12)
    return date(year,zero_month+1,1).isoformat()


def verify_edges(v,db,dsn,source,other_source,current,reference,full):
    def outcome(result,pid):
        return next(m for m in result['months'] if m['partition_id']==pid)

    def group_counts(pid):
        with db,db.cursor() as cur:
            result=[]
            for table in GROUPS:
                cur.execute('SELECT count(*) FROM '+table+' WHERE partition_id=%s',(pid,))
                result.append(cur.fetchone()[0])
            return result

    def release(holder):
        holder.rollback();holder.close()

    # Boundary expectations are constants, never another call to the function.
    boundary=clone_build(db,source,'hint-boundary',shift(current,-2))
    expired=clone_build(db,source,'hint-expired',shift(current,-3))
    clone_build(db,other_source,'hint-other-cluster',shift(current,-4))
    assert full()['expired_result_months']==1
    cleaned=CleanupStore(db).execute('C1',2)
    assert outcome(cleaned,boundary)['state']=='retained'
    assert outcome(cleaned,expired)['state']=='succeeded'
    assert full()['expired_result_months']==0
    assert overdue_count(db,'C1')==0
    v.require(True,'full hint is exactly 1 then 0; N boundary, cleaned months and other cluster are excluded')

    # Give C2 a different, expired current month and put a C1 result there too.
    other_month=shift(current,-5)
    clone_build(db,other_source,'other-current',other_month,True)
    candidate=clone_build(db,source,'not-my-current',other_month)
    with db,db.cursor() as cur:
        cur.execute('''UPDATE current_version c SET build_id=p.build_id,publication_id=p.publication_id,
            last_success_at=p.at FROM publication p WHERE c.scope_id='C2' AND p.build_id='other-current' ''')
    first=preview(db,'C1',2,reference_month=reference)
    second=preview(db,'C2',2,reference_month=reference)
    assert next(m for m in first['months'] if m['build_month']==current)['state']=='protected'
    assert next(m for m in first['months'] if m['partition_id']==candidate)['state']=='expired'
    assert next(m for m in second['months'] if m['build_month'].isoformat()==other_month)['state']=='protected'
    assert CleanupStore(db).execute('C1',2)['state']=='succeeded'
    v.require(True,'different current months protect only their own clusters, including an expired current month')

    # R1-F001: VACUUM's relation lock on either group table is compatible with
    # both partition removal and ordinary batched DELETE.
    for index,table in enumerate(GROUPS,1):
        pid=clone_build(db,source,'vacuum-lock-'+table,'2023-0'+str(index)+'-01')
        expected=group_counts(pid);holder=connect(dsn,'sql_apm')
        try:
            with holder.cursor() as cur:cur.execute('LOCK TABLE '+table+' IN SHARE UPDATE EXCLUSIVE MODE')
            result=CleanupStore(db).execute('C1',2);month=outcome(result,pid)
            assert result['state']=='succeeded' and month['cleaned_at'],result
            assert month_seconds(month)<=10,month_seconds(month)
            assert [month['formal_groups_deleted'],month['observation_groups_deleted']]==expected
            assert group_counts(pid)==[0,0]
        finally:release(holder)
        v.require(True,'held SHARE UPDATE EXCLUSIVE on '+table+' permits removal, marker and complete group deletion')

    for index,table in enumerate(GROUPS,3):
        pid=clone_build(db,source,'brief-lock-'+table,'2023-0'+str(index)+'-01')
        holder=connect(dsn,'sql_apm');timers=[]
        def briefly_hold(stage,target):
            if stage=='partitions_committed' and target==pid:
                with holder.cursor() as cur:cur.execute('LOCK TABLE '+table+' IN ACCESS EXCLUSIVE MODE')
                timer=Timer(0.3,release,args=(holder,));timer.start();timers.append(timer)
        try:
            result=CleanupStore(db,fault=briefly_hold).execute('C1',2);month=outcome(result,pid)
            assert result['state']=='succeeded' and group_counts(pid)==[0,0],result
            assert 0.25<=month_seconds(month)<=10,month_seconds(month)
        finally:
            for timer in timers:timer.join()
            if not holder.closed:release(holder)
        v.require(True,'brief group-phase lock on '+table+' is retried and finishes in the same invocation')

    # The second phase gets the remaining budget, not a fresh ten seconds.
    pid=clone_build(db,source,'shared-budget','2023-05-01')
    statistic_holder=connect(dsn,'sql_apm');group_holder=connect(dsn,'sql_apm');timers=[]
    def both_phases(stage,target):
        if target!=pid:return
        if stage=='before_partitions':
            with statistic_holder.cursor() as cur:cur.execute('LOCK TABLE ONLY mpp_statistic IN ROW EXCLUSIVE MODE')
            timer=Timer(2,release,args=(statistic_holder,));timer.start();timers.append(timer)
        if stage=='partitions_committed':
            with group_holder.cursor() as cur:cur.execute('LOCK TABLE mpp_build_group IN ACCESS EXCLUSIVE MODE')
    try:
        result=CleanupStore(db,fault=both_phases).execute('C1',2);month=outcome(result,pid)
        assert result['state']=='failed' and month['reason']=='cleanup_groups_pending',result
        assert month['cleaned_at'] and month['released_bytes']>0
        assert 9.5<=month_seconds(month)<=10,month_seconds(month)
    finally:
        for timer in timers:timer.join()
        if not statistic_holder.closed:release(statistic_holder)
        release(group_holder)
    assert CleanupStore(db).execute('C1',10000)['state']=='succeeded'
    assert group_counts(pid)==[0,0]
    v.require(True,'partition and group lock conflicts share one monthly budget; persisted month_seconds='+str(round(month_seconds(month),3)))

    for index in range(5):pid=clone_build(db,source,'multi-batch-'+str(index),'2023-06-01')
    expected=group_counts(pid);assert all(n>=5 for n in expected)
    rolled_back=[]
    def rollback_batch(stage,target):
        if stage=='groups_deleting' and target==pid and not rolled_back:
            rolled_back.append(True)
            raise LockNotAvailable()
    with patch('sql_apm.storage.cleanup.CHUNK',1):
        result=CleanupStore(db,fault=rollback_batch).execute('C1',2)
    month=outcome(result,pid)
    assert result['state']=='succeeded' and group_counts(pid)==[0,0],result
    assert [month['formal_groups_deleted'],month['observation_groups_deleted']]==expected
    assert rolled_back and month_seconds(month)<=10
    v.require(True,'small complete batches and a rolled-back delete preserve exact counts and leave no formal/observation rows')
