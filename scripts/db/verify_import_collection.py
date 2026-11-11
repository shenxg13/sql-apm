#!/usr/bin/env python3
"""Replay the shipped read-only psql collector against a disposable PG17 fixture."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from database.fixture import statements
from verify import Verification, instance, run


def verify(pg):
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env)
        v.init()
        v.sql(statements())
        v.sql("""
            UPDATE source_file SET locator='PRIVATE_FILE_SENTINEL.csv';
            UPDATE import_attempt SET started_at='2026-11-11 01:00:01+08',
                finished_at='2026-11-11 01:00:03+08';
            INSERT INTO mpp_daily_run(run_id,started_by,state,failed,started_at,finished_at,local_date,stale_after_hours)
                VALUES ('old-run','manual','finished',false,'2026-11-10 01:00+08','2026-11-10 01:01+08','2026-11-10',36),
                       ('new-run','manual','finished',false,'2026-11-11 01:00+08','2026-11-11 01:01+08','2026-11-11',36);
            INSERT INTO mpp_daily_cluster(run_id,scope_id,ordinal,state,import_state,stage_seconds)
                VALUES ('old-run','CL1',0,'done','done','{"import":10}'),
                       ('new-run','CL1',0,'done','done','{"import":7}'),
                       ('new-run','empty-cluster',1,'done','done','{"import":0.1}');
            INSERT INTO mpp_daily_day(run_id,scope_id,source_id,log_date,state,batch_id,file_count,byte_count,added_records,seconds)
                VALUES ('old-run','CL1','S1','2026-09-21','complete','B1',1,225,1,4),
                       ('new-run','CL1','S1','2026-09-21','complete','B1',1,225,1,5);
        """)
        before = v.sql("SELECT md5(string_agg(t::text,',' ORDER BY t::text)) FROM mpp_daily_run t")
        command = [pg/'psql','-X','-q','-w','-h',directory/'socket','-p','55473','-U','sql_apm',
                   '-d','sql_apm','--set=program_version=v1.3.1','--set=program_commit='+'a'*40,
                   '-f',ROOT/'scripts/performance/collect_import.sql']
        def collect(since, ok=True):
            return run(command+['--set=since='+since], env=env, ok=ok)
        data = json.loads(collect('2026-11-11 00:00+08').stdout)
        assert data['run_count'] == 1 and data['runs'][0]['run_id'] == 'new-run'
        assert data['identity']['schema_version'] == '1.15.0'
        assert len(data['counters']) == 2 and {x['relname'] for x in data['counters']} == {'mpp_query_record','mpp_query_pending'}
        assert 'stats_reset' in data and data['collected_at'] and data['postmaster_started_at']
        cluster = next(x for x in data['stage_reconciliation'] if x['scope_id']=='CL1')
        assert (cluster['import_seconds'],cluster['sum_day_seconds'],cluster['outside_day_seconds']) == (7,5,2)
        assert cluster['log_day_count'] == 1 and cluster['file_count'] == 1 and cluster['byte_count'] == 225
        assert len(data['attempts']) == 1 and data['attempts'][0]['before_commit_seconds'] == 2
        assert data['attempts'][0]['reliable_record_count'] == 1
        assert any(x['scope_id']=='empty-cluster' and x['added_records']==0 for x in data['stage_reconciliation'])
        total = int(v.sql('SELECT count(*) FROM mpp_sql_text').strip())
        scope = int(v.sql("SELECT count(DISTINCT sql_id) FROM mpp_occurrence WHERE scope_id='CL1'").strip())
        assert data['text_snapshot']['global_distinct_texts'] == total
        assert data['text_snapshot']['by_scope'] == [dict(scope_id='CL1',distinct_complete_texts=scope)]
        v.sql("INSERT INTO mpp_sql_text(sql_id,text,content_sha256) VALUES ('NEW','SELECT private_new_sql_84',sha256(convert_to('SELECT private_new_sql_84','UTF8')))")
        after = json.loads(collect('2026-11-11 00:00+08').stdout)
        assert after['text_snapshot']['global_distinct_texts']-data['text_snapshot']['global_distinct_texts'] == 1
        assert after['text_snapshot']['by_scope'] == data['text_snapshot']['by_scope']
        raw = json.dumps(after)
        for sensitive in ('PRIVATE_FILE_SENTINEL','password','locator','text_content','batch_id'):
            assert sensitive not in raw
        sql_texts = v.sql('SELECT text FROM mpp_sql_text').strip().splitlines()
        assert all(text not in raw for text in sql_texts)
        empty = json.loads(collect('2026-11-12 00:00+08').stdout)
        assert empty['run_count'] == 0 and empty['days'] == [] and empty['attempts'] == []
        assert json.loads(collect('2026-11-10 00:00+08').stdout)['run_count'] == 2
        collect("2026-11-11'; DELETE FROM sql_apm.mpp_daily_run; --", ok=False)
        assert before == v.sql("SELECT md5(string_agg(t::text,',' ORDER BY t::text)) FROM mpp_daily_run t")
        # PostgreSQL itself proves that every statement is valid in read-only mode.
        assert 'READ ONLY' in (ROOT/'scripts/performance/collect_import.sql').read_text()
        v.require(True,'collector: temporal selection, independent totals, safe quoting, empty runs and no disclosure')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
