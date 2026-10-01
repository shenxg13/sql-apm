#!/usr/bin/env python3
"""Synthetic snapshot/statistics acceptance in a disposable PostgreSQL 17."""
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from verify import instance,Verification
from ingestion.test_reader import row,write_csv,configuration
from baseline.oracle import assert_metrics
from sql_apm.ingestion.config import load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.storage.training import TrainingStore
from sql_apm.storage.statistics import StatisticsStore,StatisticsError
from sql_apm.training.config import validate,TZ


def verify():
    with instance(Path('/usr/pgsql-17/bin')) as (directory,env):
        v=Verification(Path('/usr/pgsql-17/bin'),directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'a.csv';cfg=root/'import.json'
            values=[0,.001,1,3.5,20,100,2000]
            rows=[row(text='SELECT 1',message='duration: '+str(n)+' ms',**{'0':'2026-07-'+str(20+i)+' 10:00:00 CST'}) for i,n in enumerate(values)]
            rows += [row(text='BEGIN',message='duration: 0 ms',**{'0':'2026-07-23 10:00:00 CST'}),row(text='SELECT 1',message='duration: 0 ms',**{'0':'2026-07-23 10:00:00 CST'}),
                     row(text='',message='duration: 1 ms')]
            write_csv(path,rows);cfg.write_text(json.dumps(configuration(cfg,[path])))
            importer=Importer(dsn,workers=1,progress=lambda **kw:None)
            try:assert importer.run(load_config(cfg,'S1','B1'))['state']=='complete'
            finally:importer.close()
            training=TrainingStore(dsn)
            config=validate(dict(version=1,clusters=['C1','C2'],window=dict(cutoff_date='2026-07-31'),
                templates=[],exclusions=[dict(id='pause',cluster='C1',start='2026-07-23T10:00:00+08:00',end='2026-07-23T10:30:00+08:00',reason='synthetic')]),'C1')
            snap=training.snapshot(config,['B1']);training.close()
            store=StatisticsStore(dsn)
            v.sql("INSERT INTO input_snapshot VALUES ('UNSEALED','C1','explicit',NULL,current_timestamp)")
            try:
                for scope,inp,cfgid in [('C2',snap['input_id'],snap['config_id']),('C1','missing',snap['config_id']),('C1','UNSEALED',snap['config_id'])]:
                    try:store.calculate(scope,inp,cfgid)
                    except StatisticsError as e:assert str(e)=='sealed_snapshot_pair_required'
                    else:raise AssertionError('unsealed/cross-cluster snapshot accepted')
                v.require(v.sql('SELECT count(*) FROM build')=='0','invalid snapshot rejected before build creation')
                result=store.calculate('C1',snap['input_id'],snap['config_id'])
                bid=result['build_id']
                assert result['state']=='calculated' and result['results_saved']
                assert result['observations']['groups']==0 and result['observations']['layers']=={}
                assert v.sql('SELECT count(*) FROM mpp_observation_statistic')=='0'
                v.require(v.sql("SELECT results_saved AND state='calculated' FROM build WHERE build_id='"+bid+"'")=='t','successful build and complete result commit')
                allrows=json.loads(v.sql("SELECT jsonb_agg(to_jsonb(s)) FROM mpp_statistic s WHERE build_id='"+bid+"' AND layer='overall'"))
                main=next(s for s in allrows if s['included_count']==7)
                assert_metrics(main,values)
                excluded_only=next(s for s in allrows if s['included_count']==0)
                assert excluded_only['excluded_count']==1 and excluded_only['exclusions_by_reason']=={'blacklist_category':1,'excluded_interval':1}
                assert main['excluded_count']==1 and main['exclusions_by_reason']=={'excluded_interval':1}
                v.require(True,'independent numeric oracle and included/excluded counts')
                assert len(json.loads(v.sql("SELECT jsonb_agg(t) FROM mpp_build_timing_coverage t WHERE build_id='"+bid+"'")))==5
                assert v.sql("SELECT count(*) FROM mpp_statistic WHERE build_id='"+bid+"' AND included_count=0")!='0'
                v.require(v.sql('SELECT count(*) FROM mpp_decision')=='0','excluded-only buckets stored; Decisions remain transient')
                first=json.loads(v.sql("SELECT jsonb_agg(to_jsonb(s)-'build_id' ORDER BY group_id,layer,bucket_date,bucket_number) FROM mpp_statistic s WHERE build_id='"+bid+"'"))
                repeat=store.calculate('C1',snap['input_id'],snap['config_id'])
                second=json.loads(v.sql("SELECT jsonb_agg(to_jsonb(s)-'build_id' ORDER BY group_id,layer,bucket_date,bucket_number) FROM mpp_statistic s WHERE build_id='"+repeat['build_id']+"'"))
                v.require(first==second,'same snapshot repeats exactly')
                from database.statistics_projection import verify_sufficiency
                verify_sufficiency(v,store,config,snap,bid)
                # R1-F001: exercise the real success/failure writes with a local
                # clock ahead of both the watchdog window and a month boundary.
                for lead in (timedelta(seconds=90),timedelta(days=40)):
                    class Ahead(datetime):
                        @classmethod
                        def now(cls,tz=None):
                            return datetime.now(tz)+lead
                    with store.db,store.db.cursor() as cur:
                        cur.execute('SELECT clock_timestamp()');before=cur.fetchone()[0]
                    with patch('sql_apm.storage.statistics.datetime',Ahead,create=True):
                        success=store.calculate('C1',snap['input_id'],snap['config_id'])
                        with patch('sql_apm.storage.statistics.calculate_group',side_effect=ValueError('synthetic clock failure')):
                            try:store.calculate('C1',snap['input_id'],snap['config_id'])
                            except StatisticsError:pass
                            else:raise AssertionError('injected clock failure accepted')
                    with store.db,store.db.cursor() as cur:
                        cur.execute("SELECT b.build_id,b.state,b.results_saved,b.started_at,b.finished_at,p.build_month,clock_timestamp() FROM build b JOIN mpp_result_partition p USING(partition_id) WHERE b.started_at >= %s ORDER BY b.started_at",(before,))
                        timed=cur.fetchall();assert len(timed)==2
                        assert [(r[1],r[2]) for r in timed]==[('calculated',True),('failed',False)]
                        for build_id,state,saved,start,finish,month,after in timed:
                            assert before<=start<=finish<=after
                            assert build_id.startswith('B:'+start.astimezone(TZ).strftime('%Y%m%dT%H%M%S')+':')
                            assert month==start.astimezone(TZ).date().replace(day=1)
                        cur.execute('SELECT code FROM problem WHERE build_id=%s',(timed[-1][0],))
                        assert cur.fetchall()==[('statistics_calculation_failed',)]
                    v.require(True,'database clock controls success, immediate failure and partition month with local lead '+str(lead))
                for label,effect in [('compute',ValueError('private SQL')),('save',RuntimeError('private SQL')),('interrupt',KeyboardInterrupt())]:
                    target='sql_apm.storage.statistics.calculate_group' if label!='save' else 'sql_apm.storage.statistics.ResultWriter.flush'
                    with patch(target,side_effect=effect):
                        try:store.calculate('C1',snap['input_id'],snap['config_id'])
                        except (StatisticsError,KeyboardInterrupt):pass
                        else:raise AssertionError('injected failure accepted')
                    failed=json.loads(v.sql('SELECT row_to_json(b) FROM build b ORDER BY started_at DESC LIMIT 1'))
                    assert failed['state']==('interrupted' if label=='interrupt' else 'failed') and not failed['results_saved']
                    assert v.sql("SELECT count(*) FROM mpp_statistic WHERE build_id='"+failed['build_id']+"'")=='0'
                    assert v.sql("SELECT count(*) FROM problem WHERE build_id='"+failed['build_id']+"'")=='1'
                    v.require(True,label+' failure rolls back all results and records reason')
                def late_failure(event):
                    if event['phase']=='results_written':
                        raise RuntimeError('synthetic after all writes')
                try:store.calculate('C1',snap['input_id'],snap['config_id'],progress=late_failure)
                except StatisticsError:pass
                else:raise AssertionError('late save failure accepted')
                late=json.loads(v.sql('SELECT row_to_json(b) FROM build b ORDER BY started_at DESC LIMIT 1'))
                assert late['state']=='failed'
                for table in ('mpp_statistic','mpp_build_layer_count','mpp_build_timing_coverage','mpp_build_group'):
                    assert v.sql("SELECT count(*) FROM "+table+" WHERE build_id='"+late['build_id']+"'")=='0'
                v.require(True,'failure after every result write rolls back all four result relations')
                with patch('sql_apm.storage.statistics.subprocess.Popen',side_effect=OSError('synthetic')):
                    try:store.calculate('C1',snap['input_id'],snap['config_id'])
                    except StatisticsError:pass
                    else:raise AssertionError('watchdog creation failure accepted')
                assert v.sql('SELECT state FROM build ORDER BY started_at DESC LIMIT 1')=='failed'
                retried=store.calculate('C1',snap['input_id'],snap['config_id'],failed['build_id'])
                v.require(v.sql("SELECT retry_of FROM build WHERE build_id='"+retried['build_id']+"'")==failed['build_id'],'retry starts a distinct complete build')
                # Stop a child immediately after its committed Build registration.
                code="""import os,time
from sql_apm.storage.statistics import StatisticsStore
s=StatisticsStore(os.environ['SQL_APM_DSN'])
def progress(row):
 if row['phase']=='build_created':
  print(row['build_id'],flush=True)
  time.sleep(60)
s.calculate('C1',os.environ['TEST_INPUT'],os.environ['TEST_CONFIG'],progress=progress)
"""
                for sig in [signal.SIGTERM,signal.SIGKILL]:
                    child=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=dict(os.environ,SQL_APM_DSN=dsn,TEST_INPUT=snap['input_id'],TEST_CONFIG=snap['config_id']),stdout=subprocess.PIPE,text=True)
                    killed=child.stdout.readline().strip();assert killed.startswith('B:')
                    child.send_signal(sig);child.wait(timeout=10)
                    for _ in range(100):
                        state=v.sql("SELECT state FROM build WHERE build_id='"+killed+"'")
                        if state=='interrupted':break
                        time.sleep(.1)
                    v.require(state=='interrupted','watchdog records process signal '+str(sig))
                command=[sys.executable,'-m','sql_apm','statistics','--cluster','C1','--input',snap['input_id'],'--config-id',snap['config_id']]
                shown=subprocess.run(command,env=dict(os.environ,SQL_APM_DSN=dsn),cwd=ROOT,capture_output=True,text=True,check=True)
                assert not any(secret in shown.stdout+shown.stderr for secret in ['synthetic_db','synthetic_user','SELECT','pause'])
                v.require(json.loads(shown.stdout.splitlines()[-1])['state']=='calculated','CLI returns complete redacted counts')
                # Exercise the full-volume verifier itself on >1,000 synthetic groups.
                from verify_statistics_full import reconcile,sample_oracle,digest,Memory,sizes
                many=root/'many.csv'
                write_csv(many,[row(text='SELECT column_'+str(i)+' FROM synthetic',message='duration: 0.123 ms') for i in range(1010)])
                cfg.write_text(json.dumps(configuration(cfg,[many],'B2')))
                importer=Importer(dsn,workers=1,progress=lambda **kw:None)
                try:assert importer.run(load_config(cfg,'S1','B2'))['state']=='complete'
                finally:importer.close()
                training=TrainingStore(dsn)
                try:large=training.snapshot(config,['B1','B2'])
                finally:training.close()
                with Memory(store.db) as memory:
                    large_result=store.calculate('C1',large['input_id'],large['config_id'])
                assert memory.report()['samples']>0
                assert reconcile(store.db,large,large_result['build_id'])['coverage_complete']
                assert sample_oracle(store.db,large_result['build_id'])['groups']>=1000
                assert digest(store.db,large_result['build_id'])['mpp_statistic']['rows']>5000
                assert any(r['partition'] for r in sizes(store.db))
                v.require(True,'full acceptance reconciliation, 1000-group oracle, digest and measurement self-check')
            finally:store.close()
        v.init('check');v.init('upgrade')
        print('STATISTICS CHECKS:',v.completed)


if __name__=='__main__':verify()
