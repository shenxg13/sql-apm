#!/usr/bin/env python3
"""Synthetic search/query acceptance on a private disposable PostgreSQL 17."""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

RESOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', str(RESOURCE_ROOT))).resolve()
sys.path[:0] = [str(ROOT), str(RESOURCE_ROOT / 'tests')]
from verify import instance, Verification
from database.retention import clone_build, contents
from ingestion.test_reader import row, write_csv, configuration
from sql_apm.ingestion.config import load_config, identity
from sql_apm.ingestion.importer import Importer
from sql_apm.baseline.workflow import run
from sql_apm.cli.search import query
from sql_apm.sql.normalization import Normalizer
from sql_apm.storage.ingestion import connect
from sql_apm.storage.cleanup import CleanupStore
from sql_apm.training.config import validate
from psycopg2.extras import Json


def verify(pg_bin):
    with instance(pg_bin) as (directory, env):
        v = Verification(pg_bin, directory, env); v.init()
        dsn = 'host=' + str(directory/'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        path, config_path = directory/'input.csv', directory/'import.json'
        sql_text = 'SELECT a, b FROM demo WHERE x = 1'
        records = [row(text=sql_text, message='duration: 2 ms'),
                   row(text='SELECT a,b\nFROM demo WHERE x=2', message='duration: 4 ms'),
                   row(text='SELECT a, b FROM demo WHERE x = $1', message='duration: 6 ms', **{'1':'second_user'}),
                   row(text='SELECT b,a FROM demo WHERE x=1', message='duration: 8 ms'),
                   row(text='SELECT a FROM failure_only', message='failed', **{'16':'ERROR'}),
                   row(text='SELECT a FROM old_only', message='duration: 1 ms', **{'0':'2026-06-01 00:00:00 CST'}),
                   row(text='SELECT x FORM t', message='duration: 3 ms'),
                   row(text=sql_text, message='canceling statement due to user request', **{'16':'ERROR','17':'57014'}),
                   row(text=sql_text, message='canceling statement due to statement timeout', **{'16':'ERROR','17':'57014'}),
                   row('2219', text=sql_text, message='duration: 1 ms'),
                   row('2603', text=sql_text, message='duration: 1 ms'),
                   row('2764', text=sql_text, message='execute p: '+sql_text),
                   row('2843', text=sql_text, message='duration: 1 ms'),
                   row('2764', text=sql_text, message='execute fetch from p: '+sql_text),
                   row('2843', text=sql_text, message='duration: 1 ms'),
                   row('2843', text=sql_text, message='duration: 1 ms')]
        records += [row(text='SELECT a FROM table_'+str(i), message='duration: 1 ms') for i in range(60)]
        write_csv(path, records)
        config_path.write_text(json.dumps(configuration(config_path,[path])))
        config = dict(version=1, clusters=['C1'], window=dict(cutoff_date='2026-07-31'))
        result = run(dsn, 'sql_apm', validate(config,'C1'), load_config(config_path,'S1','B1'), workers=1)
        build = result['build']['build_id']
        db = connect(dsn,'sql_apm'); engine=Normalizer();norm='N:'+identity(engine.context)
        fp = lambda s: engine.normalize(s)['fingerprint']['value']
        fingerprint = fp(sql_text)
        def call(name,*values):
            with db,db.cursor() as cur:return query(cur,name,values)
        def rows(command,values=()):
            with db,db.cursor() as cur:
                cur.execute(command,values);return cur.fetchall()
        def rejects(name,reason,*values):
            try:call(name,*values)
            except Exception as e:assert e.diag.message_primary==reason,(reason,str(e));db.rollback()
            else:raise AssertionError(reason)
        assert call('mpp_search_fold','A B\tC\nD\rE\fF\vVÄ')=='abcdefvÄ'

        assert call('mpp_search_terms',' a  " B c " d"')==['a','bc','d"']
        assert call('mpp_search_terms','"a,b" x = 1')==['a,b','x','=','1']
        for value in ('', ' \n\t', '""', '"  "'):
            rejects('mpp_search_terms','empty_search_input',value)
        rejects('mpp_search_terms','too_many_search_terms',' '.join(['x']*21))
        assert len(call('mpp_search_terms',' '.join(['x']*20)))==20
        v.require(True,'S2/S3: six ASCII spaces, ASCII-only case, phrases, unmatched quote, empty/20-term boundary')
        before=contents(db)
        def find(text,scope=None,database=None,user=None,start=None,end=None,order='count'):
            return call('mpp_query_search',norm,text,scope,database,user,start,end,order)
        found=find('DEMO "a,b"')
        assert found['match_kind']=='text' and found['total_structures']==1
        assert found['rows'][0]['matched_texts']==3
        assert find('b a demo')['total_structures']==2
        assert find('"b,a" demo')['total_structures']==1
        assert find('"WHERE x ="')['total_structures']==2
        assert find('demo "x = 2"')['rows'][0]['matched_texts']==1
        for value in ('%', '_missing_', 'no_such_text', '"x=99"'):
            assert find(value)['total_structures']==0
        many=find('table_');assert len(many['rows'])==50 and many['total_structures']==60
        assert find('demo',user='second_user')['rows'][0]['record_count']==1
        assert find('demo',scope='absent')['total_structures']==0
        assert find('demo',start='2030-01-01')['total_structures']==0
        assert find('demo',order='recent')['rows']
        v.require(True,'S3/S4: literal symbols, AND/phrase ordering, folded matches, grouping/counts/filtering/order and 50 cap')
        hit=call('mpp_query_exact',norm,fingerprint)
        assert hit['state']=='has_baseline' and len(hit['hits'])==2
        assert find(fingerprint)==hit
        assert hit['hits'][0]['has_unknown_timing'] and len(hit['hits'][0]['timing_types'])==5
        assert call('mpp_query_exact',norm,fp('SELECT a FROM failure_only'))['state']=='records_without_baseline'
        assert call('mpp_query_exact',norm,fp('SELECT a FROM old_only'))['state']=='records_without_baseline'
        assert call('mpp_query_exact',norm,fp('SELECT a FROM never_seen'))['state']=='not_seen'
        v.require(True,'S5/S6/S9: fingerprint direct lookup, all identities/timings, failure-only and outside-window records')
        def cli(words):
            proc=subprocess.run([sys.executable,'-m','sql_apm','search']+words,cwd=ROOT,
                env=dict(os.environ,SQL_APM_DSN=dsn),text=True,capture_output=True,timeout=30)
            try:obj=json.loads(proc.stdout)
            except ValueError:raise AssertionError(proc.stdout+proc.stderr)
            return proc.returncode,obj
        code,obj=cli(['exact','--sql','SELECT A, B FROM demo WHERE X=$1'])
        # Unquoted identifiers normalize case and native parameters match literals.
        assert code==0 and obj['fingerprint']==fingerprint and obj['state']=='has_baseline',obj
        code,obj=cli(['exact','--sql','SELECT x FORM t'])
        assert code==0 and obj['state']=='unreliable_fingerprint' and obj['observations'] and obj['reason'],obj
        for value in ('SELECT ?','SELECT #{x}','SELECT :name',"SELECT 'unclosed"):
            code,obj=cli(['exact','--sql',value]);assert code==0 and obj['state']=='unreliable_fingerprint',obj
        code,obj=cli(['exact','--sql',sql_text+'; SELECT a FROM never_seen'])
        assert code==0 and len(obj['statement_hints'])==2 and not obj['hints_are_batch_match'],obj
        assert obj['statement_hints'][0]['state']=='has_baseline'
        code,obj=cli(['exact','--sql',';'.join(['SELECT a FROM never_seen']*21)])
        assert code==0 and obj['hints_truncated'] and len(obj['statement_hints'])==20
        from sql_apm.sql.normalization import MAX_BYTES
        oversized=directory/'large.sql';oversized.write_bytes(b'x'*(MAX_BYTES+1))
        code,obj=cli(['exact','--file',str(oversized)])
        assert code==0 and obj['state']=='unreliable_fingerprint' and obj['reason']=='input_size_limit'
        exact_file=directory/'query.sql' ;exact_file.write_text(sql_text)
        assert cli(['exact','--file',str(exact_file)])[1]['fingerprint']==fingerprint
        bad_file=directory/'bad.sql';bad_file.write_bytes(b'\xff')
        assert cli(['exact','--file',str(bad_file)])[1]['state']=='unreliable_fingerprint'
        code,obj=cli(['find','DEMO "a,b"']);assert code==0
        obj.pop('rules');assert obj==found
        v.require(True,'S1/S6/S7/S8/S16: JSON CLI and DB agree; exact outcomes/observations, parameter and file input, batch hints capped')
        args=[norm,'C1','synthetic_db','synthetic_user',fingerprint]
        summary=call('mpp_query_baseline',*args)
        assert len(summary['rows'])==5 and summary['version']['build_id']==build
        assert {s['timing_type'] for s in summary['rows']}=={'request','parse','bind','execute_first','execute_fetch'}
        for layer in ('overall','day','week','weekday','hour'):
            assert call('mpp_query_baseline',*args,None,layer)['layer']==layer
        second=call('mpp_query_baseline',norm,'C1','synthetic_db','second_user',fingerprint)
        assert sum(s['sample_state']=='no_samples' for s in second['rows'])==4
        for s in summary['rows']:
            direct=rows("SELECT to_jsonb(s) FROM mpp_baseline_group g CROSS JOIN LATERAL mpp_read_statistics(%s,false,g.group_id) s WHERE g.scope_id='C1' AND g.database='synthetic_db' AND g.execution_user='synthetic_user' AND g.fingerprint_value=%s AND g.timing_type=%s AND s.layer='overall'",(build,fingerprint,s['timing_type']))[0][0]
            for key in ('included_count','excluded_count','exclusions_by_reason','min_ms','p50_ms','p95_ms','max_ms'):
                assert s[key]==direct[key]
        v.require(True,'S10/S11: five timing summary, no-sample rows, each layer and saved statistics equality')
        hist_args=[norm,fingerprint,'C1','synthetic_db','synthetic_user']
        history=call('mpp_query_history',*hist_args,None,None,100,None,build)
        flat=[x for g in history['groups'] for x in g['rows']]
        assert len(flat)==hit['hits'][0]['record_count']
        assert len(history['groups'])==6
        unknown=history['groups'][-1]['rows'];assert len(unknown)==3
        assert all(x['duration_ms'] is None and x['estimated_start_at'] is None for x in unknown)
        assert {x['outcome'] for x in unknown}=={'success','cancelled','timed_out'}
        assert any(x['training']['decision']=='included' for x in flat)
        assert any(x['training']['decision']=='excluded' for x in flat)
        page1=call('mpp_query_history',*hist_args,None,None,3)
        page2=call('mpp_query_history',*hist_args,None,None,3,Json(page1['next_cursor']))
        ids=lambda p:{(x['analysis_id'],x['occurrence_id']) for g in p['groups'] for x in g['rows']}
        assert len(ids(page1))==3 and not ids(page1)&ids(page2)
        rejects('mpp_query_history','invalid_page_size',*hist_args,None,None,1001)
        rejects('mpp_query_history','invalid_page_cursor',*hist_args,None,None,100,Json({}))
        rejects('mpp_query_history','invalid_time_range',*hist_args,'2026-08-01','2026-07-01')
        for bucket in ('hour','day'):
            timeline=call('mpp_query_history',*hist_args,None,None,100,None,None,bucket)
            assert sum(x['record_count'] for g in timeline['groups'] for x in g['rows'])==len(flat)
            request=timeline['groups'][0]['rows'][0]
            assert request['record_count']==2 and request['p50_ms']==3 and request['p95_ms']==3.9 and request['max_ms']==4
        assert contents(db)==before
        v.require(True,'S12/S13/S14/S16: independent history window, paging without duplicates, null unknown timing, decisions, quantiles, read-only digests')
        # Import after the saved snapshot: input membership uses files, not a batch heuristic.
        later=directory/'later.csv';write_csv(later,[row(text=sql_text, **{'0':'2026-07-24 00:00:00 CST'})])
        config_path.write_text(json.dumps(configuration(config_path,[later],'B2')))
        importer=Importer(dsn,'sql_apm',1)
        try:assert importer.run(load_config(config_path,'S1','B2'))['state']=='complete'
        finally:importer.close()
        history=call('mpp_query_history',*hist_args,None,None,100,None,build)
        flat=[x for g in history['groups'] for x in g['rows']]
        assert sum(x['training']['decision']=='not_in_version_input' for x in flat)==1
        assert 'warning' not in history
        with db,db.cursor() as cur:
            cur.execute("INSERT INTO mpp_sql_text(sql_id,text,content_sha256) VALUES ('unmapped','SELECT unmapped',sha256(convert_to('SELECT unmapped','UTF8')))")
        assert call('mpp_query_history',*hist_args)['warning']['missing_sql_texts']==1
        v.require(True,'S13/S15/S17: unselected file is not version input; unmapped originals warned; generated column automatic')
        # Historical rule support is represented without using current logic to fill it.
        with db,db.cursor() as cur:
            cur.execute('ALTER TABLE training_config DISABLE TRIGGER USER')
            cur.execute("UPDATE training_config SET decision_version='historical/0'")
            cur.execute('ALTER TABLE training_config ENABLE TRIGGER USER')
        history=call('mpp_query_history',*hist_args,None,None,100,None,build)
        states={x['training']['decision'] for g in history['groups'] for x in g['rows']}
        assert states=={'decision_unavailable','not_in_version_input'}
        clone_build(db,build,'search-old','2026-01-01',published=True)
        assert call('mpp_query_baseline',*args,'search-old')['version']['build_id']=='search-old'
        # Long result reader exercises the real cleanup deadline and its retry.
        holder=connect(dsn,'sql_apm')
        with holder.cursor() as cur:query(cur,'mpp_query_baseline',args+['search-old'])
        current_month=rows('SELECT build_month FROM mpp_result_partition p JOIN build b USING(partition_id) WHERE b.build_id=%s',(build,))[0][0]
        year,month=divmod(current_month.year*12+current_month.month+2,12)
        reference=date(year,month+1,1)
        out=CleanupStore(db).execute('C1',2,reference_month=reference)
        assert out['state']=='failed' and any(m['state']=='lock_timeout' for m in out['months'])
        holder.rollback();holder.close()
        assert CleanupStore(db).execute('C1',2,reference_month=reference)['state']=='succeeded'
        rejects('mpp_query_baseline','results_cleaned',*args,'search-old')
        rejects('mpp_query_baseline','normalization_version_mismatch','different','C1','synthetic_db','synthetic_user',fingerprint)
        versions=rows('SELECT to_jsonb(v) FROM mpp_query_versions(%s,%s) v',(norm,'C1'))
        assert any(r[0]['build_id']=='search-old' and r[0]['results_cleaned'] for r in versions)
        assert rows('SELECT build_id FROM current_version')[0][0]==build
        v.require(True,'S11/S13: old decisions unavailable, published history listed, clean/mismatched reference refused; cleanup deadline/retry, pointer unchanged')
        v.init('check')
        db.close()
        print('RESULT: search query acceptance passed',flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    verify(ap.parse_args().pg_bin)
