#!/usr/bin/env python3
"""Synthetic retention: preservation, actual process kills, lock deadlines, CLI."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import select
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from verify import instance,Verification
from database.retention import clone_build,contents,digest
from database.retention_edges import verify_edges,month_seconds
from database.session_wait import wait_for_backend_exit
from ingestion.test_reader import row,write_csv,configuration
from sql_apm.ingestion.config import load_config
from sql_apm.storage.cleanup import CleanupStore,preview
from sql_apm.storage.ingestion import connect
from sql_apm.storage.publication import version_status
from sql_apm.storage.tasks import Task
from sql_apm.storage.training import TrainingStore
from sql_apm.training.config import validate
from sql_apm.baseline.workflow import run


def verify(pg_bin):
    with instance(pg_bin) as (directory,env):
        v=Verification(pg_bin,directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        db=connect(dsn,'sql_apm')
        config=dict(version=1,clusters=['C1','C2'],window=dict(cutoff_date='2026-07-31'))
        training=directory/'training.json';training.write_text(json.dumps(config))
        import_path=directory/'import.json';path=directory/'input.csv'
        write_csv(path,[row(text='SELECT 1',message='duration: 2 ms'),
                        row(text='SELECT x FORM t',message='duration: 3 ms')])
        doc=configuration(import_path,[path]);import_path.write_text(json.dumps(doc))
        ingestion=load_config(import_path,'S1','B1')
        result=run(dsn,'sql_apm',validate(config,'C1'),ingestion,workers=1)
        assert result['expired_result_months']==0
        source=result['build']['build_id'];frozen=result['snapshot']
        assert int(v.sql('SELECT count(*) FROM mpp_build_observation_group'))>0
        # Real second-cluster data, rather than an empty namespace contrast.
        other_doc=configuration(import_path,[path],'B2');other_doc['clusters']=['C2']
        other_doc['sources']['S2']=dict(other_doc['sources'].pop('S1'),cluster='C2')
        other_doc['batches']['B2']['source']='S2';import_path2=directory/'import2.json'
        import_path2.write_text(json.dumps(other_doc))
        other_result=run(dsn,'sql_apm',validate(config,'C2'),load_config(import_path2,'S2','B2'),workers=1)
        pid=clone_build(db,source,'old-published','2026-01-01',True)
        clone_build(db,source,'old-unpublished','2026-01-01')
        clone_build(db,source,'never-saved','2026-01-01',saved=False)
        with db,db.cursor() as cur:
            cur.execute('SELECT p.build_month FROM build b JOIN mpp_result_partition p USING(partition_id) WHERE b.build_id=%s',(source,))
            source_month=cur.fetchone()[0]
        reference=date(source_month.year+1,1,1)
        clone_build(db,source,'retained',reference.isoformat(),True)
        # All fixtures are intentionally old except the current-version month.
        before=contents(db);current=version_status(db,'C1')['current']
        with Task(db,'C1','snapshot'):
            locked=contents(db)
            out=preview(db,'C1',2,reference_month=reference)
            assert contents(db)==locked
        assert next(m for m in out['months'] if m['partition_id']==pid)['published_builds']==1
        assert next(m for m in out['months'] if m['partition_id']==pid)['unpublished_builds']==1
        assert next(m for m in out['months'] if m['build_month']==source_month)['state']=='protected'
        v.require(True,'preview under busy cluster is read-only; old published/unpublished counts and current-month protection')
        # Operational config cannot affect the sealed training content or rule ID.
        store=TrainingStore(db=db);a=store.snapshot(validate(config,'C1'),['B1'])
        with_retention=dict(config,retention=dict(months=1,clusters={'C1':12}))
        b=store.snapshot(validate(with_retention,'C1'),['B1'])
        with db,db.cursor() as cur:
            cur.execute("SELECT to_jsonb(c)-'config_id',to_jsonb(t)-'config_id' FROM config_snapshot c JOIN training_config t USING(config_id) WHERE config_id=ANY(%s) ORDER BY config_id",([a['config_id'],b['config_id']],))
            values=cur.fetchall();assert values[0]==values[1]
        v.require(True,'retention changes neither stored config content nor training rule identity')
        hinted=run(dsn,'sql_apm',validate(config,'C1'),ingestion,workers=1)
        assert hinted['expired_result_months']==1
        current=version_status(db,'C1')['current']
        def cli(words, timeout=20):
            return subprocess.run([sys.executable,'-m','sql_apm']+words,cwd=ROOT,
                env=dict(os.environ,SQL_APM_DSN=dsn),capture_output=True,text=True,timeout=timeout)
        cleanup=['cleanup','--cluster','C1','--training-config',str(training)]
        original=contents(db)
        assert cli(cleanup).returncode==0 and contents(db)==original
        assert cli(cleanup+['--reference-month','2027-01-01']).returncode==2
        for document,reason in [(dict(config,retention={'months':0}),'invalid_retention_months'),
                                (dict(config,clusters=['C2']),'unknown_cluster'),([], 'invalid_training_config')]:
            training.write_text(json.dumps(document))
            for suffix in ([],['--execute']):
                bad=cli(cleanup+suffix)
                assert bad.returncode==1 and json.loads(bad.stdout)['reason']==reason,(reason,bad.stdout)
        training.write_text(json.dumps(config))
        with Task(db,'C1','snapshot'):
            busy=cli(cleanup+['--execute']);assert busy.returncode==1 and json.loads(busy.stdout)['reason']=='cluster_busy'
        commands=[['full','--config',str(import_path),'--source','S1','--batch','B1','--training-config',str(training)],
                  ['import','--config',str(import_path),'--source','S1','--batch','B1'],
                  ['rebuild','--cluster','C1','--training-config',str(training),'--cutoff-date','2026-07-31'],
                  ['training','snapshot','--cluster','C1','--config',str(training),'--batch','B1'],
                  ['statistics','--cluster','C1','--input',frozen['input_id'],'--config-id',frozen['config_id']]]
        def busy_entrances(stage,_):
            if stage=='admitted':
                for words in commands:
                    value=cli(words);assert value.returncode==1 and json.loads(value.stdout.splitlines()[-1])['reason']=='cluster_busy'
        # Retain all months so this probe verifies real cleanup admission without deletion.
        CleanupStore(db,fault=busy_entrances).execute('C1',10000)
        v.require(True,'cleanup busy rejection and all five writer entrypoints reject active cleanup; CLI parameter codes')
        # Persisted task state records both exact 10-second abandon paths.
        for kind,statement in [('other_cluster_build','LOCK TABLE mpp_statistic,mpp_build_group IN ROW EXCLUSIVE MODE'),
                               ('statistics_reader','SELECT 1 FROM mpp_statistic LIMIT 1')]:
            holder=connect(dsn,'sql_apm')
            with holder.cursor() as cur:cur.execute(statement)
            unchanged=contents(db,cleanup=True);old=digest(db,'mpp_statistic')
            out=CleanupStore(db).execute('C1',2,reference_month=reference)
            holder.rollback();holder.close()
            timed=[m for m in out['months'] if m['state']=='lock_timeout']
            assert out['state']=='failed' and timed
            assert all(m['released_bytes']==0 and m['before_bytes']==m['after_bytes'] for m in timed)
            elapsed=[month_seconds(m) for m in timed]
            assert all(s<=10 for s in elapsed),(kind,elapsed)
            assert contents(db,cleanup=True)==unchanged and digest(db,'mpp_statistic')==old
            v.require(True,kind+' abandons each month within lock budget and changes no results; month_seconds='+str([round(s,3) for s in elapsed]))
        preserved=contents(db,cleanup=True)
        other=digest(db,'mpp_statistic',where='WHERE partition_id<>%s',params=(pid,))
        out=CleanupStore(db).execute('C1',2,reference_month=reference)
        assert out['state']=='succeeded',out
        month=next(m for m in out['months'] if m['partition_id']==pid)
        assert month['released_bytes']>0 and month['after_bytes']==0
        assert month['formal_groups_deleted']>0 and month['observation_groups_deleted']>0
        assert preserved==contents(db,cleanup=True)
        assert other==digest(db,'mpp_statistic',where='WHERE partition_id<>%s',params=(pid,))
        assert version_status(db,'C1')['current']==current
        history=version_status(db,'C1',True)['versions']
        assert next(h for h in history if h['build_id']=='old-published')['results_cleaned']
        for table in ('mpp_build_group','mpp_build_observation_group'):
            assert v.sql('SELECT count(*) FROM '+table+' WHERE partition_id='+str(pid))=='0'
            with db.cursor() as cur:
                try:
                    cur.execute('INSERT INTO '+table+' SELECT %s,%s,group_id FROM '+table+' WHERE build_id=%s LIMIT 1',
                                (pid,'old-published',source))
                except Exception as error:assert error.diag.message_primary=='results_cleaned'
                else:raise AssertionError('cleaned_group_write_accepted')
            db.rollback()
        v.require(True,'formal and observation group writes into a cleaned month reject with results_cleaned')
        for query in ("SELECT * FROM mpp_coverage('old-published')", "SELECT * FROM mpp_coverage('old-unpublished',true)",
                      "SELECT * FROM mpp_read_statistics('old-published')", "SELECT * FROM mpp_read_statistics('old-unpublished',true)",
                      (ROOT/'sql_apm/storage/statistics_sufficiency.sql').read_text().replace('%(build_id)s',"'old-published'")):
            with db.cursor() as cur:
                try:cur.execute(query)
                except Exception as error:assert error.diag.message_primary=='results_cleaned'
                else:raise AssertionError('cleaned_query_returned_empty')
            db.rollback()
        assert v.sql("SELECT results_saved FROM build WHERE build_id='never-saved'")=='f'
        assert v.sql("SELECT count(*) FROM mpp_coverage('never-saved')")=='0'
        v.rejects("SELECT mpp_ensure_result_partition('C1','2026-01-01')",'cleaned month cannot be recreated')
        out=CleanupStore(db).execute('C1',2,reference_month=reference)
        assert all(m['released_bytes']==0 for m in out['months'])
        v.init('check');v.init('upgrade');v.init('check')
        v.require(True,'atomic removal, row preservation, history/query distinction, rerun, post-cleanup structure check')
        pending_pid=clone_build(db,source,'group-timeout','2024-01-01')
        holder=connect(dsn,'sql_apm')
        def hold_groups(stage,_):
            if stage=='partitions_committed':
                with holder.cursor() as cur:
                    cur.execute('LOCK TABLE mpp_build_group IN ACCESS EXCLUSIVE MODE')
        try:
            pending=CleanupStore(db,fault=hold_groups).execute('C1',2,reference_month=reference)
            outcome=next(m for m in pending['months'] if m['partition_id']==pending_pid)
            assert pending['state']=='failed' and outcome['reason']=='cleanup_groups_pending'
            assert outcome['cleaned_at'] and outcome['released_bytes']>0
            assert 9.5<=month_seconds(outcome)<=10,month_seconds(outcome)
        finally:
            holder.rollback();holder.close()
        assert CleanupStore(db).execute('C1',10000)['state']=='succeeded'
        assert v.sql('SELECT count(*) FROM mpp_build_group WHERE partition_id='+str(pending_pid))=='0'
        v.require(True,'group-phase lock conflict reports already-removed results; rerun finishes even with increased retention')
        # SIGKILL occurs inside the actual transaction, and between group batches.
        for i,stage in enumerate(('before_partitions','partitions_locked','partitions_dropped',
                                  'partitions_committed','groups_deleting','groups_committed')):
            month='2025-'+str(i+1).zfill(2)+'-01';bid='kill-'+stage
            kill_pid=clone_build(db,source,bid,month)
            code='''import json,os,time
from datetime import date
from sql_apm.storage.ingestion import connect
import sql_apm.storage.cleanup as cleanup
cleanup.CHUNK=1
db=connect(os.environ['SQL_APM_DSN'],'sql_apm')
def point(stage,pid):
 if stage==os.environ['STAGE']:
  print(json.dumps(dict(backend=db.get_backend_pid())),flush=True)
  time.sleep(300)
cleanup.CleanupStore(db,fault=point).execute('C1',2,reference_month=date.fromisoformat(os.environ['REFERENCE']))
'''
            child=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=dict(os.environ,SQL_APM_DSN=dsn,STAGE=stage,REFERENCE=reference.isoformat()),
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                assert select.select([child.stdout],[],[],20)[0],stage
                message=child.stdout.readline();assert message,child.stderr.read()
                evidence=json.loads(message);child.kill();child.wait(timeout=10)
                wait_for_backend_exit(db,evidence['backend'])
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=10)
                child.stdout.close();child.stderr.close()
            with db,db.cursor() as cur:
                cur.execute('SELECT cleaned_at IS NOT NULL,to_regclass(%s) IS NOT NULL FROM mpp_result_partition WHERE partition_id=%s',
                            ('mpp_statistic_p'+str(kill_pid),kill_pid))
                cleaned,exists=cur.fetchone();assert cleaned != exists
            recovered=CleanupStore(db).execute('C1',2,reference_month=reference)
            assert recovered['state']=='succeeded',recovered
            assert v.sql("SELECT count(*) FROM task WHERE mode='cleanup' AND state='running'")=='0'
            assert int(v.sql("SELECT count(*) FROM task WHERE mode='cleanup' AND state='interrupted' AND reason='owner_exited'"))==i+1
            assert v.sql("SELECT count(*) FROM mpp_cleanup_month WHERE state='interrupted' AND reason='owner_exited' AND partition_id="+str(kill_pid))=='1'
            assert v.sql('SELECT count(*) FROM mpp_build_group WHERE partition_id='+str(kill_pid))=='0'
            assert v.sql('SELECT count(*) FROM mpp_build_observation_group WHERE partition_id='+str(kill_pid))=='0'
            v.require(True,'SIGKILL '+stage+' preserves atomic visibility; interrupted task and restart finish all groups')
        verify_edges(v,db,dsn,source,other_result['build']['build_id'],source_month,reference,
                     lambda:run(dsn,'sql_apm',validate(config,'C1'),ingestion,workers=1))
        status=version_status(db,'C1');assert status['tasks'][0]['mode']=='cleanup'
        assert not any(s in json.dumps(status,default=str) for s in ('SELECT','synthetic_db','synthetic_user'))
        v.init('check');db.close()
        print('CLEANUP CHECKS:',v.completed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
