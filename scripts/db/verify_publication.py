#!/usr/bin/env python3
"""Private PG17 orchestration, fault injection, publication and concurrency checks."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from verify import instance,Verification
from ingestion.test_reader import row,write_csv,configuration
from sql_apm.ingestion.config import load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.training.config import validate
from sql_apm.storage.training import TrainingStore
from sql_apm.storage.statistics import StatisticsStore,StatisticsError
from sql_apm.storage.ingestion import connect
from sql_apm.storage.tasks import Task
from sql_apm.storage.publication import PublicationStore,version_status,CHECKS
from sql_apm.baseline.workflow import run


def verify():
    with instance(Path('/usr/pgsql-17/bin')) as (directory,env):
        v=Verification(Path('/usr/pgsql-17/bin'),directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'input.csv';cfg=root/'import.json'
            write_csv(path,[row(text='SELECT 1',message='duration: 2 ms'),
                            row(text='SELECT 1',message='duration: 3 ms',**{'0':'2026-07-25 10:00:00 CST'})])
            doc=configuration(cfg,[path]);cfg.write_text(json.dumps(doc))
            ingestion=load_config(cfg,'S1','B1')
            config=validate(dict(version=1,clusters=['C1'],window=dict(cutoff_date='2026-07-31')),'C1')
            value=run(dsn,'sql_apm',config,ingestion,workers=1)
            assert value['publication']['result']=='published',value
            db=connect(dsn,'sql_apm')
            assert version_status(db,'C1')['current']['build_id']==value['build']['build_id']
            count=v.sql('SELECT count(*) FROM import_attempt')
            again=run(dsn,'sql_apm',config)
            assert again['publication']['previous_build_id']==value['build']['build_id']
            assert count==v.sql('SELECT count(*) FROM import_attempt')
            assert len(version_status(db,'C1',history=True)['versions'])==2
            v.require(True,'first publication, subsequent switch and rebuild without reimport')
            for command in ('status','history'):
                query=subprocess.run([sys.executable,'-m','sql_apm',command,'--cluster','C1'],
                    cwd=ROOT,env=dict(os.environ,SQL_APM_DSN=dsn),capture_output=True,text=True,timeout=20)
                assert query.returncode==0
                payload=json.loads(query.stdout)
                assert not any(secret in query.stdout for secret in ('synthetic_db','synthetic_user','SELECT'))
                if command=='status':assert payload['current']['build_id']==again['build']['build_id']
                else:assert len(payload['versions'])==2
            v.require(True,'status and history public commands return published versions without source identities')
            frozen=value['snapshot']
            def fresh():
                store=StatisticsStore(dsn)
                try:return store.calculate('C1',frozen['input_id'],frozen['config_id'])['build_id']
                finally:store.close()
            def check_publish(bid, fault=None):
                with Task(db,'C1','rebuild') as task:
                    pub=PublicationStore(db,task)
                    checks=pub.check(bid)
                    result=pub.publish(bid,fault)
                    assert v.sql("SELECT state FROM task WHERE task_id='"+task.task_id+"'") != 'running'
                return checks,result
            current=again['build']['build_id']
            for name,mutate,restore in [
                ('batch_complete',"UPDATE import_batch SET state='processing' WHERE batch_id='B1'", "UPDATE import_batch SET state='complete' WHERE batch_id='B1'"),
                ('rules_consistent',"ALTER TABLE training_config DISABLE TRIGGER training_immutable; UPDATE training_config SET decision_version='training-decision/99'", "UPDATE training_config SET decision_version='training-decision/1'; ALTER TABLE training_config ENABLE TRIGGER training_immutable"),
                ('results_complete',"DELETE FROM mpp_statistic WHERE build_id='{bid}' AND layer='day' AND bucket_date=(SELECT min(bucket_date) FROM mpp_statistic WHERE build_id='{bid}' AND layer='day')",''),
                ('results_saved',"UPDATE build SET results_saved=false WHERE build_id='{bid}'",''),
                ('counts_consistent',"UPDATE mpp_statistic SET excluded_count=excluded_count+1 WHERE build_id='{bid}' AND layer='overall'",''),
            ]:
                bid=fresh();v.sql(mutate.format(bid=bid))
                checks,result=check_publish(bid)
                assert checks[name]['state']=='failed',(name,checks)
                assert result['result']=='check_failed'
                assert version_status(db,'C1')['current']['build_id']==current
                if restore:v.sql(restore)
                v.require(True,'publication rejects '+name+' and preserves current')
            bid=fresh()
            v.sql("DELETE FROM mpp_statistic WHERE build_id='"+bid+"' AND layer='week'")
            checks,result=check_publish(bid)
            assert checks['results_complete']['state']=='failed' and result['result']=='check_failed'
            assert version_status(db,'C1')['current']['build_id']==current
            v.require(True,'publication rejects a missing whole layer as well as a single missing bucket')
            bid=fresh()
            constraint=v.sql("SELECT conname FROM pg_constraint WHERE conrelid='mpp_statistic'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%min_ms <= p25_ms%'")
            definition=v.sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='mpp_statistic'::regclass AND conname='"+constraint+"'")
            # Inject invalid bytes while checks are absent, then reinstall NOT VALID
            # so the checker must detect invalid existing rows itself.
            v.sql('ALTER TABLE mpp_statistic DROP CONSTRAINT '+constraint)
            v.sql("UPDATE mpp_statistic SET p95_ms=0 WHERE build_id='"+bid+"' AND layer='overall'")
            v.sql('ALTER TABLE mpp_statistic ADD CONSTRAINT '+constraint+' '+definition+' NOT VALID')
            checks,result=check_publish(bid)
            assert checks['values_consistent']['state']=='failed'
            v.sql("UPDATE mpp_statistic SET p95_ms=p90_ms WHERE build_id='"+bid+"' AND layer='overall'")
            v.sql('ALTER TABLE mpp_statistic VALIDATE CONSTRAINT '+constraint)
            v.require(True,'saved invalid quantile order rejected independently of catalog enforcement')
            bid=fresh()
            checks,result=check_publish(bid,lambda:(_ for _ in ()).throw(RuntimeError('synthetic')))
            assert result['result']=='publish_failed'
            assert version_status(db,'C1')['current']['build_id']==current
            v.require(True,'publication write failure rolls back pointer and records failed attempt')
            empty=validate(dict(version=1,clusters=['C1'],window=dict(cutoff_date='2020-01-31')),'C1')
            zero=run(dsn,'sql_apm',empty)
            assert zero['publication']['result']=='no_samples' and zero['state']=='succeeded'
            assert version_status(db,'C1')['current']['build_id']==current
            assert version_status(db,'new-cluster')['baseline_state']=='no_baseline'
            assert set(zero['checks'])==set(CHECKS) and all(c['state']=='passed' for c in zero['checks'].values())
            v.require(True,'all-five zero samples preserve old version; absent cluster has no baseline')
            db.close()
            # Only call/phase samples still form a publishable baseline.
            call=root/'calls.csv';write_csv(call,[row('2219',text='SELECT 7',message='duration: 1 ms')])
            call_doc=configuration(cfg,[call],'CALLS');call_doc['clusters']=['C2'];call_doc['sources']['S2']=call_doc['sources'].pop('S1');call_doc['sources']['S2']['cluster']='C2';call_doc['batches']['CALLS']['source']='S2'
            call_cfg=root/'calls.json';call_cfg.write_text(json.dumps(call_doc))
            call_config=validate(dict(version=1,clusters=['C2'],window=dict(cutoff_date='2026-07-31')),'C2')
            calls=run(dsn,'sql_apm',call_config,load_config(call_cfg,'S2','CALLS'),workers=1)
            assert calls['publication']['result']=='published' and calls['build']['timings']['request'][0]==0
            call_db=connect(dsn,'sql_apm')
            assert version_status(call_db,'C2')['current']['request_baseline_missing'] is True
            call_db.close()
            v.require(True,'call-only samples publish despite insufficient counts and absent request baseline')
            # Every exceptional stage stops before writing downstream products.
            for target,stage in [('sql_apm.ingestion.importer.Importer._run','import'),
                                 ('sql_apm.storage.training.TrainingStore._snapshot','snapshot'),
                                 ('sql_apm.storage.statistics.StatisticsStore._calculate','build'),
                                 ('sql_apm.storage.publication.PublicationStore.check','check')]:
                before=v.sql('SELECT count(*) FROM publication')
                with patch(target,side_effect=RuntimeError('synthetic')):
                    try:run(dsn,'sql_apm',config,ingestion,workers=1)
                    except RuntimeError:pass
                    else:raise AssertionError('injected_stage_did_not_stop')
                assert v.sql('SELECT count(*) FROM publication')==before
                assert v.sql("SELECT state FROM task ORDER BY started_at DESC LIMIT 1")=='failed'
            status_db=connect(dsn,'sql_apm')
            diagnostic=version_status(status_db,'C1');status_db.close()
            assert diagnostic['last_unpublished']['result']=='failed'
            assert diagnostic['last_unpublished']['reason']=='build_failed'
            assert not any(secret in json.dumps(diagnostic,default=str) for secret in ('synthetic_db','synthetic_user','SELECT'))
            v.require(True,'import, snapshot, calculation and check exceptions stop publication; diagnostics show latest failure without source identities')
            # A rebuild retry reuses imported data, but freezes fresh snapshots.
            attempts=v.sql('SELECT count(*) FROM import_attempt')
            for error,expected in [(RuntimeError,'failed'),(KeyboardInterrupt,'interrupted')]:
                failed={}
                def fail_after_creation(**event):
                    if event['phase']=='build_created':
                        failed['build_id']=event['build_id']
                        raise error('synthetic')
                try:run(dsn,'sql_apm',config,progress=fail_after_creation)
                except (StatisticsError,KeyboardInterrupt):pass
                else:raise AssertionError('retry_fixture_did_not_fail')
                previous=v.sql("SELECT state||':'||input_id||':'||config_id FROM build WHERE build_id='"+failed['build_id']+"'")
                assert previous.startswith(expected+':')
                retried=run(dsn,'sql_apm',config,retry_of=failed['build_id'])
                assert retried['publication']['result']=='published'
                assert retried['snapshot']['input_id'] not in previous
                assert retried['snapshot']['config_id'] not in previous
                assert v.sql("SELECT retry_of FROM build WHERE build_id='"+retried['build']['build_id']+"'")==failed['build_id']
                assert v.sql('SELECT count(*) FROM import_attempt')==attempts
            v.require(True,'rebuild references failed and interrupted builds with fresh snapshots and no reimport')
            from database.publication_concurrency import verify_concurrency
            verify_concurrency(v,dsn,root,cfg,config,frozen)
        v.init('check');v.init('upgrade')
        print('PUBLICATION CHECKS:',v.completed)


if __name__=='__main__':verify()
