#!/usr/bin/env python3
"""Synthetic end-to-end eligibility checks in a private PostgreSQL 17 instance."""
from collections import Counter
from copy import deepcopy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from verify import instance, Verification
from ingestion.test_reader import row, write_csv, configuration
from sql_apm.ingestion.config import load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.training.config import validate, TrainingError
from sql_apm.storage.training import TrainingStore


def verify():
    with instance(Path('/usr/pgsql-17/bin')) as (directory,env):
        v=Verification(Path('/usr/pgsql-17/bin'),directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'a.csv';cfg=root/'import.json'
            cases=[]
            def add(label,text='SELECT 1',message='duration: 1000 ms',line='1946',**fields):
                cases.append((label,row(line,message,text,**fields)))
            add('included')
            add('dictionary_template',"SELECT to_date('2021','YYYY')")
            add('pure',"SET work_mem='16MB'; BEGIN; COMMIT")
            add('mixed',"SET work_mem='16MB'; BEGIN; INSERT INTO t VALUES(1); COMMIT")
            add('batch_template','BEGIN; INSERT INTO t VALUES(22); COMMIT')
            add('batch_template_order','INSERT INTO t VALUES(22); BEGIN; COMMIT')
            add('batch_template_single','INSERT INTO t VALUES(22)')
            for timing,line in [('request','1946'),('parse','2219'),('bind','2603')]:
                add('template_'+timing,'SELECT * FROM t WHERE id=22',line=line)
                add('category_'+timing,'BEGIN',line=line)
            for timing,prefix in [('execute_first','execute'),('execute_fetch','execute fetch from')]:
                for name,text in [('template','SELECT * FROM t WHERE id=22'),('category','BEGIN')]:
                    add('anchor_'+name+timing,text,line='2764',message=prefix+' p: '+text)
                    add(name+'_'+timing,text,line='2843')
            add('template_structure_miss','SELECT * FROM t WHERE name=22')
            add('template_db_miss','SELECT * FROM t WHERE id=22',**{'2':'other'})
            add('template_user_miss','SELECT * FROM t WHERE id=22',**{'1':'other'})
            add('unpaired',line='2843')
            add('missing',text='')
            add('incomplete',text="SELECT 'cut")
            add('uncertain',text="SELECT '\\a'")
            for name,message in [('failed','some error'),('cancelled','canceling statement due to user request'),('timed_out','canceling statement due to statement timeout')]:
                add(name,message=message,line='1',**{'16':'ERROR','17':'57014'})
            add('unknown',message='duration: invalid ms')
            add('start_unknown',**{'0':'invalid timestamp'})
            add('long_success',message='duration: 86400000 ms')
            add('identity_missing',**{'1':''})
            add('template_identity_unknown','SELECT * FROM t WHERE id=22',**{'1':''})
            add('before',**{'0':'2026-07-02 00:00:00 CST'})
            add('first',message='duration: 0 ms',**{'0':'2026-07-02 00:00:00 CST'})
            add('last',message='duration: 0 ms',**{'0':'2026-07-31 23:59:59 CST'})
            add('after',message='duration: 0 ms',**{'0':'2026-08-01 00:00:00 CST'})
            for name,end,ms in [('touch_start','10:00:00','60000'),('overlap','10:01:00','120000'),
                                ('touch_end','10:31:00','60000'),('zero_start','10:00:00','0'),('zero_end','10:30:00','0')]:
                add(name,message='duration: '+ms+' ms',**{'0':'2026-07-23 '+end+' CST'})
            add('multiple','BEGIN',line='1',message='error',**{'16':'ERROR'})
            add('fingerprint_failed','SELECT * FROM t QUALIFY x=1')
            add('encoding',text='SELECT ENCODING_MARKER')
            write_csv(path,[r for label,r in cases]);path.write_bytes(path.read_bytes().replace(b'ENCODING_MARKER',b'\xff'))
            cfg.write_text(json.dumps(configuration(cfg,[path])))
            importer=Importer(dsn,workers=2,progress=lambda **kw:None)
            try: result=importer.run(load_config(cfg,'S1','B1'))
            finally:importer.close()
            v.require(result['state']=='complete','synthetic input imported')
            doc=dict(version=1,clusters=['C1','C2'],window=dict(cutoff_date='2026-07-31'),
                templates=[dict(id='business',sql='SELECT * FROM t WHERE id=1',description='synthetic',cluster='C1',database='synthetic_db',execution_user='synthetic_user'),
                           dict(id='dictionary',sql="SELECT to_date('2020','YYYY')",description='synthetic'),
                           dict(id='batch-template',sql='BEGIN; INSERT INTO t VALUES(1); COMMIT',description='synthetic')],
                exclusions=[dict(id='maintenance',cluster='C1',start='2026-07-23T10:00:00+08:00',end='2026-07-23T10:30:00+08:00',reason='synthetic')])
            # Complete original text with a stored structural refusal is distinct
            # from missing/incomplete text; it still carries a fingerprint reason.
            target=next(i for i,(n,r) in enumerate(cases,1) if n=='fingerprint_failed')
            v.sql("INSERT INTO mpp_sql_text VALUES('FAILED_SQL','SELECT * FROM t QUALIFY x=1',sha256(convert_to('SELECT * FROM t QUALIFY x=1','UTF8'))); "
                  "INSERT INTO mpp_fingerprint SELECT 'FAILED_FP','FAILED_SQL',normalization_id,'hashdata-csv/1','unsupported_syntax',NULL,'base_parser_rejected' FROM mpp_normalization; "
                  "UPDATE mpp_occurrence SET sql_state='complete',sql_id='FAILED_SQL' WHERE anchor_ref IN (SELECT record_id FROM evidence_record WHERE record_no="+str(target)+")")
            store=TrainingStore(dsn)
            try:
                snap=store.snapshot(validate(doc,'C1'),['B1'])
                def decisions(snapshot):
                    with store.db,store.db.cursor() as cur:
                        cur.execute('SELECT e.record_no,d.* FROM mpp_training_decisions(%s,%s) d JOIN mpp_occurrence o USING(analysis_id,occurrence_id) JOIN evidence_record e ON e.record_id=o.anchor_ref',(snapshot['input_id'],snapshot['config_id']))
                        cols=[d[0] for d in cur.description]
                        return {cases[r[0]-1][0]:dict(zip(cols,r)) for r in cur}
                got=decisions(snap)
                v.require(got['included']['state']=='included' and got['mixed']['state']=='included','default inclusion and mixed whole batch')
                v.require(got['batch_template']['reason_codes']==['blacklist_template'],'whole batch template matches normalized business constants')
                v.require(got['start_unknown']['reason_codes']==['start_unknown'] and got['start_unknown']['rule_evaluations']['window']=='not_evaluated','known duration with unknown start')
                v.require(got['pure']['reason_codes']==['blacklist_category'],'pure batch excluded once')
                for timing in ['request','parse','bind','execute_first','execute_fetch']:
                    v.require(got['template_'+timing]['reason_codes']==['blacklist_template'] and got['category_'+timing]['reason_codes']==['blacklist_category'],'both blacklist kinds: '+timing)
                for name in ['template_structure_miss','template_db_miss','template_user_miss','touch_start','touch_end','zero_end','first','last','long_success','batch_template_order','batch_template_single']:
                    v.require(got[name]['state']=='included','negative/boundary: '+name)
                for name in ['overlap','zero_start']:
                    v.require(got[name]['reason_codes']==['excluded_interval'],'interval: '+name)
                for name in ['before','after']:
                    v.require(got[name]['state']=='outside_window' and got[name]['count_scope']=='none','window: '+name)
                for name,code in [('failed','execution_failed'),('cancelled','execution_cancelled'),('timed_out','execution_timed_out')]:
                    v.require(code in got[name]['reason_codes'] and got[name]['state']=='excluded' and got[name]['rule_evaluations']['exclusion_interval']=='not_evaluated','outcome: '+name)
                for name,code in [('unknown','outcome_unknown'),('identity_missing','identity_missing'),('unpaired','association_unreliable'),('missing','sql_missing'),('incomplete','sql_incomplete'),('uncertain','sql_uncertain'),('encoding','sql_encoding_invalid'),('fingerprint_failed','fingerprint_failed')]:
                    v.require(code in got[name]['reason_codes'] and got[name]['state']=='unresolved','unresolved: '+name)
                v.require(got['template_identity_unknown']['rule_evaluations']['blacklist']=='not_evaluated','missing template scope is not a negative match')
                v.require(got['missing']['rule_evaluations']['blacklist']=='not_evaluated' and got['missing']['rule_evaluations']['fingerprint']=='not_evaluated','missing SQL never becomes negative blacklist evidence')
                v.require({'blacklist_category','execution_failed','duration_unknown','start_unknown'} <= set(got['multiple']['reason_codes']),'all known reasons retained')
                v.require(all(len(x['rule_evaluations'])==8 for x in got.values()),'all eight evaluations always present')
                for name,item in got.items():
                    direct=store.decision(snap['input_id'],snap['config_id'],item['analysis_id'],item['occurrence_id'])
                    v.require(direct['reason_codes']==item['reason_codes'] and direct['state']==item['state'],'record query agrees: '+name)
                groups=[];summary=store.summary(snap['input_id'],snap['config_id'],group_sink=groups.append)
                v.require(dict(summary['states'])==dict(Counter(x['state'] for x in got.values())) and sum(g['count'] for g in groups if g['reason'] is None)==len(got),'summary and per-record conservation')
                from verify_training_full import decision_digest
                digest1,digest2=decision_digest(store,snap),decision_digest(store,snap)
                v.require(digest1['count']==len(got) and digest1['hash_sums']==digest2['hash_sums'],'complete Decision fields repeat with identical per-row hash sums')

                from unittest.mock import patch
                with patch('sql_apm.storage.training.classify',side_effect=AssertionError('unexpected reclassification')):
                    repeat=store.snapshot(validate(doc,'C1'),['B1'])
                v.require(repeat['cache']['added_sql_results']==0 and repeat['rule_id']==snap['rule_id'],'same rules reuse exact original cache')
                changed=deepcopy(doc);changed['templates']=[];changed['exclusions']=[]
                newer=store.snapshot(validate(changed,'C1'),['B1'])
                v.require(decisions(snap)==got and decisions(newer)['template_request']['state']=='included','new config does not change old snapshot')
                for table,field,key in [('input_snapshot','input_id',snap['input_id']),('input_manifest','input_id',snap['input_id']),('input_file','input_id',snap['input_id']),('input_file_analysis','input_id',snap['input_id']),('config_snapshot','config_id',snap['config_id']),('training_config','config_id',snap['config_id'])]:
                    v.rejects("DELETE FROM "+table+" WHERE "+field+"='"+key+"'",'immutable '+table)
                v.rejects('UPDATE mpp_training_sql SET category_kind=\'none\'','immutable original-level results')
                v.require(v.sql('SELECT count(*) FROM mpp_decision')=='0' and v.sql('SELECT count(*) FROM build')=='0','no per-build Decision or Build created')
                # Same sample and template under another immutable normalization
                # context: both sides are recalculated, rather than mixing values.
                from sql_apm.sql.normalization import Normalizer
                from sql_apm.sql.function_dictionary import FunctionDictionary
                dictionary=store.engine.rule_snapshot()['dictionary']
                dictionary['rules_version']='99.0.0'
                for rule in dictionary['rules']:
                    if rule['name']=='to_date': rule['enabled']=False
                versioned=TrainingStore(dsn,normalizer=Normalizer(FunctionDictionary(dictionary)))
                try:
                    alt=versioned.snapshot(validate(doc,'C1'),['B1'])
                    v.require(alt['rule_id']!=snap['rule_id'] and alt['cache']['added_sql_results']>0,'normalization context creates new original results')
                    item=got['template_request']
                    decision=versioned.decision(alt['input_id'],alt['config_id'],item['analysis_id'],item['occurrence_id'])
                    v.require('blacklist_template' in decision['reason_codes'],'template example is recomputed under the new normalization context')
                    v.require(decisions(snap)==got,'old normalization snapshot retains results')
                    item=got['dictionary_template']
                    decision=versioned.decision(alt['input_id'],alt['config_id'],item['analysis_id'],item['occurrence_id'])
                    v.require('blacklist_template' in item['reason_codes'] and decision['state']=='included','changed dictionary semantics recompute both example and original; old result retained')
                finally:versioned.close()
                from unittest.mock import patch
                from sql_apm.training.categories import RULES
                rules=dict(RULES,version='statement-categories/test-version')
                with patch('sql_apm.storage.training.RULES',rules):
                    alt=store.snapshot(validate(doc,'C1'),['B1'])
                v.require(alt['rule_id']!=snap['rule_id'] and alt['cache']['added_sql_results']>0,'category version creates new cache rows')
                # Cluster scoping remains effective for the same SQL and identities.
                scoped=deepcopy(doc);scoped['templates'][0]['cluster']='C2'
                alt=store.snapshot(validate(scoped,'C1'),['B1'])
                v.require(decisions(alt)['template_request']['state']=='included','template cluster mismatch')
                v.require(v.sql('SELECT count(*)=count(DISTINCT (sql_id,rule_id)) FROM mpp_training_sql')=='t','one result per exact original and rule version')
                for sql in ["UPDATE input_snapshot SET frozen_at=current_timestamp WHERE input_id='"+snap['input_id']+"'",
                            "UPDATE config_snapshot SET exclusions='[]' WHERE config_id='"+snap['config_id']+"'",
                            "UPDATE input_file_analysis SET analysis_id=analysis_id WHERE input_id='"+snap['input_id']+"'"]:
                    v.rejects(sql,'sealed snapshot UPDATE rejected')
                later=root/'later.csv'
                write_csv(later,[row(text='SELECT 222',message='duration: 1000 ms')])
                cfg.write_text(json.dumps(configuration(cfg,[later],'LATER')))
                importer=Importer(dsn,workers=1,progress=lambda **kw:None)
                try:v.require(importer.run(load_config(cfg,'S1','LATER'))['state']=='complete','new completed input arrives after snapshots')
                finally:importer.close()
                v.require(decisions(snap)==got,'later import does not alter sealed input membership')
                different=store.snapshot(validate(changed,'C1'),['LATER'])
                import psycopg2
                try:store.summary(different['input_id'],snap['config_id'])
                except psycopg2.errors.RaiseException as error:
                    v.require(error.diag.message_primary=='training_cache_not_prepared','unprepared input/config combination fails instead of claiming fingerprint failure')
                else:raise AssertionError('unprepared rule result silently accepted')

                # Ambiguous or incomplete input is rejected before a snapshot exists.
                v.sql("UPDATE import_batch SET state='processing' WHERE batch_id='B1'")
                try:store.snapshot(validate(doc,'C1'),['B1'])
                except TrainingError as error:v.require(str(error)=='completed_batches_required','incomplete batch cannot enter snapshot')
                else:raise AssertionError('incomplete batch accepted')
                v.sql("UPDATE import_batch SET state='complete' WHERE batch_id='B1'; INSERT INTO analysis SELECT 'SECOND',scope_id,profile,mapping_version,parser_version,association_version,analysis_id,evidence_manifest FROM analysis WHERE analysis_id='"+got['included']['analysis_id']+"'; INSERT INTO analysis_file SELECT 'SECOND',file_id,scope_id FROM analysis_file WHERE analysis_id='"+got['included']['analysis_id']+"'")
                try:store.snapshot(validate(doc,'C1'),['B1'])
                except TrainingError as error:v.require(str(error)=='unique_file_analysis_required','ambiguous interpretation is not silently selected')
                else:raise AssertionError('ambiguous analysis accepted')
                original=got['included']['analysis_id']
                selected=store.snapshot(validate(doc,'C1'),['B1'],[original])
                v.require({k:(r['state'],r['reason_codes']) for k,r in decisions(selected).items()}=={k:(r['state'],r['reason_codes']) for k,r in got.items()},'explicit interpretation selects the original event set')
                public_config=root/'training.json'
                public_config.write_text(json.dumps(doc))
                cli_env=dict(os.environ,SQL_APM_DSN=dsn)
                command=[sys.executable,'-m','sql_apm','training']
                made=subprocess.run(command+['snapshot','--config',str(public_config),'--cluster','C1','--batch','B1','--analysis',original],cwd=ROOT,env=cli_env,capture_output=True,text=True,check=True)
                cli_snap=json.loads(made.stdout)
                shown=subprocess.run(command+['summary','--input',cli_snap['input_id'],'--config-id',cli_snap['config_id'],'--groups'],cwd=ROOT,env=cli_env,capture_output=True,text=True,check=True)
                v.require(all(secret not in made.stdout+shown.stdout for secret in ['synthetic_db','synthetic_user','SELECT','maintenance','business','batch-template']),'CLI snapshot and summary expose only opaque locators and counters')
                lines=[json.loads(line) for line in shown.stdout.splitlines()]
                v.require(lines[-1]['state']=='complete' and dict(lines[-1]['states'])==dict(summary['states']),'CLI summary agrees with database decisions')
                bad_env=dict(cli_env,SQL_APM_DSN='host=/nonexistent-training-socket password=DO_NOT_EXPOSE')
                failed=subprocess.run(command+['summary','--input','missing','--config-id','missing'],cwd=ROOT,env=bad_env,capture_output=True,text=True)
                v.require(failed.returncode==1 and 'DO_NOT_EXPOSE' not in failed.stdout+failed.stderr and 'training_failed' in failed.stdout,'driver failures redact connection details')
                # Cached work may remain after a failed snapshot; snapshot members
                # and configuration must become visible atomically.
                before=v.sql('SELECT count(*) FROM input_snapshot')
                bad=validate(doc,'C1');bad['window_days']=-1
                import psycopg2
                try:store.snapshot(bad,['B1'],[original])
                except psycopg2.IntegrityError:pass
                else:raise AssertionError('invalid snapshot committed')
                v.require(v.sql('SELECT count(*) FROM input_snapshot')==before,'failed snapshot transaction exposes no partial input members')
                invalid=deepcopy(doc);invalid['templates'][0]['sql']='SELECT incomplete FROM'
                try:store.snapshot(validate(invalid,'C1'),['B1'],[original])
                except TrainingError as error:v.require(str(error)=='template_not_reliably_normalized','invalid template fails closed')
                else:raise AssertionError('invalid template accepted')
            finally:store.close()
        v.init('check');v.init('upgrade')
        print('TRAINING CHECKS:',v.completed)

if __name__=='__main__':verify()
