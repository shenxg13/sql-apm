#!/usr/bin/env python3
"""Observation sample, grouping, numeric and atomicity acceptance in private PG17."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from verify import instance,Verification
from ingestion.test_reader import row,write_csv,configuration
from database.observations import assert_observation_rows
from sql_apm.ingestion.config import load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.storage.training import TrainingStore
from sql_apm.storage.statistics import StatisticsStore,StatisticsError,ResultWriter
from sql_apm.training.config import validate
from psycopg2.extras import RealDictCursor


def verify():
    with instance(Path('/usr/pgsql-17/bin')) as (directory,env):
        v=Verification(Path('/usr/pgsql-17/bin'),directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'observations.csv';cfg=root/'import.json'
            fragment='SELECT * FROM synthetic WHERE id IN (1,'
            values=[0,.001,1,3.5,20,100,2000]
            data=[row(text=fragment,message='duration: '+str(n)+' ms',**{'0':'2026-07-'+str(20+i)+' 10:00:00 CST'}) for i,n in enumerate(values)]
            data[0][0]='2026-07-20 00:00:00 CST'
            data[1][0]='2026-07-20 00:00:00 CST'
            data[2][0]='2026-07-02 00:00:00.001 CST'
            data[3][0]='2026-07-31 23:59:59 CST'
            labels=['failed','cancelled','timed_out','unpaired','unknown_timing','unknown_duration',
                    'identity_missing','excluded_interval','outside_window','incomplete','invalid_encoding',
                    'other_database','other_user','parse','unavailable','single','zero_only']
            for label in labels:
                text='/*' if label=='unavailable' else fragment
                data.append(row(text=text,message='duration: 0 ms',**{'0':'2026-07-23 09:00:00 CST','10':label}))
            data += [row(text='SELECT 1',message='duration: 0 ms'), row(text='',message='duration: 0 ms')]
            write_csv(path,data);cfg.write_text(json.dumps(configuration(cfg,[path])))
            importer=Importer(dsn,workers=1,progress=lambda **kw:None)
            try:assert importer.run(load_config(cfg,'S1','B1'))['state']=='complete'
            finally:importer.close()
            store=StatisticsStore(dsn)
            with store.db,store.db.cursor() as cur:
                cur.execute('SELECT occurrence_id FROM mpp_occurrence o JOIN evidence_record e ON e.record_id=o.anchor_ref ORDER BY e.record_no')
                ids=[r[0] for r in cur]
                for i,label in enumerate(labels,7):
                    assignments={
                        'failed':"outcome='failed',association_state='unpaired',association_reason='synthetic'", 'cancelled':"outcome='cancelled'", 'timed_out':"outcome='timed_out'",
                        'unpaired':"association_state='unpaired',association_reason='synthetic'",
                        'unknown_timing':"timing_type=NULL,timing_reason='synthetic'",
                        'unknown_duration':"duration_ms=NULL,estimated_start_at=NULL,start_basis=NULL,value_reasons='{\"duration_ms\":\"unknown\",\"estimated_start_at\":\"unknown\"}'",
                        'identity_missing':"database=NULL",
                        'excluded_interval':"estimated_start_at='2026-07-23 10:00:00+08',end_at='2026-07-23 10:00:00+08'",
                        'outside_window':"estimated_start_at='2026-08-01 00:00:00+08',end_at='2026-08-01 00:00:00+08'",
                        'incomplete':"sql_state='incomplete'", 'invalid_encoding':"sql_state='invalid_encoding'",
                        'other_database':"database='other_db'", 'other_user':"execution_user='other_user'",
                        'parse':"timing_type='parse',unit='call'", 'single':"database='single'", 'zero_only':"database='zero_only'",
                    }.get(label)
                    if assignments:cur.execute('UPDATE mpp_occurrence SET '+assignments+' WHERE occurrence_id=%s',(ids[i],))
                # One execution also has a second historical rule result. It must
                # form another observation group, never merge rule versions.
                cur.execute('''INSERT INTO mpp_approximate_rule
                    SELECT 'synthetic-rule/3','sql-approximate/3',profile,rules_digest,rules_ref,rules
                    FROM mpp_approximate_rule LIMIT 1''')
                cur.execute('''INSERT INTO mpp_approximate_result
                    SELECT 'synthetic-result/3',input_id,'synthetic-rule/3','sql-approximate/3',kind,state,
                        replace(value,'sql-approximate/2','sql-approximate/3'),reason,structural_reason,
                        observation_only,completeness,source_bytes_included,normalized,diagnostics,replacements
                    FROM mpp_approximate_result WHERE state='available' LIMIT 1''')
                cur.execute("INSERT INTO mpp_approximate_evidence SELECT 'synthetic-result/3',anchor_ref FROM mpp_occurrence WHERE occurrence_id=%s",(ids[0],))
                cur.execute("INSERT INTO mpp_occurrence_approximate SELECT analysis_id,occurrence_id,scope_id,'synthetic-rule/3','synthetic-result/3',anchor_ref,source_id FROM mpp_occurrence WHERE occurrence_id=%s",(ids[0],))
            training=TrainingStore(dsn)
            config=validate(dict(version=1,clusters=['C1'],window=dict(cutoff_date='2026-07-31'),templates=[],
                exclusions=[dict(id='pause',cluster='C1',start='2026-07-23T10:00:00+08:00',end='2026-07-23T10:30:00+08:00',reason='synthetic')]),'C1')
            snap=training.snapshot(config,['B1']);training.close()
            try:
                result=store.calculate('C1',snap['input_id'],snap['config_id']);bid=result['build_id']
                obs=result['observations']
                assert obs['observation_only'] and obs['groups']==8,obs
                scopes=dict(obs['scopes'])
                assert scopes==dict(group=20,identity_missing=1,start_unknown=1,outside_window=1,approximate_unavailable=1,sql_missing=1),scopes
                current=next(r for k,r in obs['rules'].items() if k!='synthetic-rule/3')
                assert current['included']==14 and current['excluded']==6,current
                assert current['reasons']==dict(execution_failed=1,execution_cancelled=1,execution_timed_out=1,
                    association_unreliable=2,timing_unknown=1,excluded_interval=1),current
                with store.db,store.db.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute('SELECT window_start,window_end FROM config_snapshot WHERE config_id=%s',(snap['config_id'],));window=cur.fetchone()
                    cur.execute('''SELECT g.group_id,o.estimated_start_at,o.duration_ms,d.state,d.reason_codes
                        FROM mpp_training_decisions(%s,%s) d JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
                        JOIN mpp_occurrence_approximate a USING(analysis_id,occurrence_id)
                        JOIN mpp_approximate_result r USING(result_id,rule_id)
                        JOIN mpp_observation_group g ON (g.scope_id,g.database,g.execution_user,g.rule_id,g.approximate_value,g.timing_type)=
                            (o.scope_id,o.database,o.execution_user,a.rule_id,r.value,coalesce(o.timing_type,'unknown'))
                        WHERE d.in_window IS TRUE''',(snap['input_id'],snap['config_id']))
                    events={}
                    for e in cur:
                        reasons=[r for r in e['reason_codes'] if r not in ('sql_uncertain','sql_incomplete','sql_encoding_invalid','fingerprint_failed')]
                        events.setdefault(e['group_id'],[]).append((e['estimated_start_at'],e['duration_ms'],not reasons,reasons))
                    checked=0
                    for gid,group in events.items():
                        cur.execute('SELECT * FROM mpp_observation_statistic WHERE build_id=%s AND group_id=%s',(bid,gid))
                        checked+=assert_observation_rows(cur.fetchall(),group,window['window_start'],window['window_end'])
                    assert checked>40
                v.require(True,'D1-D3: candidate scopes, rule isolation and every bucket/metric independently recomputed')
                # Foreign keys reject cross-family writes, even for legitimate observation IDs.
                v.rejects("INSERT INTO mpp_build_group SELECT * FROM mpp_build_observation_group LIMIT 1",'formal statistics reject observation group')
                v.rejects("INSERT INTO mpp_build_observation_group SELECT * FROM mpp_build_group LIMIT 1",'observation statistics reject reliable group')
                v.rejects("UPDATE mpp_baseline_group SET fingerprint_id='synthetic-result/3'",'reliable group rejects approximate result')
                v.rejects("INSERT INTO mpp_observation_group SELECT 'OG:bad-reference',scope_id,profile,database,execution_user,rule_id,result_id,'sha256:reliable',timing_type FROM mpp_observation_group LIMIT 1",'observation group requires actual available approximate value')
                v.rejects("UPDATE mpp_observation_group SET database='changed'",'referenced observation identity is immutable')
                v.sql("INSERT INTO scope VALUES ('C2','hashdata','hashdata-csv/1','1.0.0')")
                v.sql("INSERT INTO mpp_observation_group SELECT 'OG:other-cluster','C2',profile,database,execution_user,rule_id,result_id,approximate_value,timing_type FROM mpp_observation_group LIMIT 1")
                v.rejects("INSERT INTO mpp_build_observation_group SELECT partition_id,build_id,'OG:other-cluster' FROM build LIMIT 1",'cross-cluster observation association rejected')
                with store.db,store.db.cursor() as cur:
                    cur.execute((ROOT/'sql_apm/storage/statistics_sufficiency.sql').read_text(),{'build_id':bid})
                    assert all(not r[2].startswith('OG:') for r in cur)
                v.require(True,'threshold batch never reads observation groups')
                original_flush=ResultWriter.flush
                def fail_save(writer):
                    if writer.table=='mpp_observation_statistic':raise ValueError('synthetic SQL must stay private')
                    return original_flush(writer)
                for target,effect in [('sql_apm.storage.observations.calculate_group',ValueError('synthetic')),
                                      ('sql_apm.storage.statistics.ResultWriter.flush',fail_save)]:
                    with patch(target,side_effect=effect) if isinstance(effect,Exception) else patch(target,effect):
                        try:store.calculate('C1',snap['input_id'],snap['config_id'])
                        except StatisticsError as e:assert str(e)=='statistics_calculation_failed'
                        else:raise AssertionError('observation failure did not fail build')
                    failed=json.loads(v.sql('SELECT row_to_json(b) FROM build b ORDER BY started_at DESC LIMIT 1'))
                    assert failed['state']=='failed' and not failed['results_saved']
                    for table in ('mpp_statistic','mpp_build_group','mpp_build_coverage','mpp_build_timing_coverage','mpp_observation_statistic','mpp_build_observation_group'):
                        assert v.sql("SELECT count(*) FROM "+table+" WHERE build_id='"+failed['build_id']+"'")=='0'
                repeat=store.calculate('C1',snap['input_id'],snap['config_id'],failed['build_id'])
                assert repeat['observations']==obs
                v.require(True,'D4: observation calculation/save failure rolls back all formal and observation results; retry from scratch')
                shown=subprocess.run([sys.executable,'-m','sql_apm','statistics','--cluster','C1','--input',snap['input_id'],'--config-id',snap['config_id']],
                    env=dict(os.environ,SQL_APM_DSN=dsn),cwd=ROOT,capture_output=True,text=True,check=True)
                assert not any(s in shown.stdout+shown.stderr for s in ['synthetic_user','synthetic_db','SELECT','other_db','other_user'])
                assert json.loads(shown.stdout.splitlines()[-1])['observations']==json.loads(json.dumps(obs))
                v.require(True,'D7: CLI observation counts exclude SQL and database/user identities')
                # Exercise the exact full-volume validator and frozen baseline
                # on this bounded dataset before the expensive 55-file run.
                from verify_observations_full import frozen_store,rows_equal,reconcile_and_oracle
                Baseline,_=frozen_store();base=Baseline(dsn)
                try:original=base.calculate('C1',snap['input_id'],snap['config_id'])
                finally:base.close()
                assert rows_equal(store.db,original['build_id'],bid,
                    ('mpp_statistic','mpp_build_coverage','mpp_build_timing_coverage','mpp_build_group'))
                assert reconcile_and_oracle(store.db,snap,bid)['groups']==8
                assert rows_equal(store.db,bid,repeat['build_id'],('mpp_observation_statistic','mpp_build_observation_group'))
                v.require(True,'D6 validator self-check: independent population, all-group oracle, exact formal and repeat row comparison')

            finally:store.close()
        v.init('check');v.init('upgrade')
        print('OBSERVATION CHECKS:',v.completed)


if __name__=='__main__':verify()
