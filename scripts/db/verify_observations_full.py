#!/usr/bin/env python3
"""Explicit 55-file observation acceptance; no production connections by default."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import types
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import verify_statistics_full as formal
from database.observations import assert_observation_rows
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from sql_apm.ingestion.config import canonical
from sql_apm.storage.statistics import StatisticsStore
from sql_apm.storage.observations import calculate_observations
from sql_apm.storage.training import TrainingStore
from sql_apm.training.config import validate as configuration,DECISION_VERSION

BASELINE_REF='f038932fe0d5453450b91a59d5239c95bacb0078'


def frozen_store():
    source=subprocess.check_output(['git','show',BASELINE_REF+':sql_apm/storage/statistics.py'],cwd=ROOT)
    module=types.ModuleType('frozen_formal_statistics')
    # The frozen computation remains the reference. 1.6.0 replaces only its
    # obsolete physical coverage sink; coverage itself is checked independently.
    adapted=source.decode().replace("hashdata-csv/1", "mpp-csv/1").replace("covers = ResultWriter(self.db,'mpp_build_coverage',COVER_COLUMNS)",
        "covers = type('DiscardCoverage',(),{'add':lambda self,row:None,'flush':lambda self:None})()")
    exec(compile(adapted,'frozen_formal_statistics.py','exec'),module.__dict__)
    return module.StatisticsStore,hashlib.sha256(source).hexdigest()


def rows_equal(db, left, right, tables):
    """Exact bidirectional bag equality via equal counts plus one EXCEPT ALL.

    PostgreSQL performs comparison without transferring millions of result rows.
    This is stronger than a hash and includes every persisted result field.
    """
    counts={}
    with db,db.cursor() as cur:
        for table in tables:
            cur.execute('''SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass
                AND attnum>0 AND NOT attisdropped AND attname NOT IN ('build_id','partition_id') ORDER BY attnum''',(table,))
            columns=sql.SQL(',').join(sql.Identifier(r[0]) for r in cur)
            cur.execute(sql.SQL('SELECT count(*) FROM {} WHERE build_id=%s').format(sql.Identifier(table)),(left,))
            n=cur.fetchone()[0]
            cur.execute(sql.SQL('SELECT count(*) FROM {} WHERE build_id=%s').format(sql.Identifier(table)),(right,))
            assert cur.fetchone()[0]==n,table+'_row_count'
            cur.execute(sql.SQL('''SELECT NOT EXISTS(
                (SELECT {columns} FROM {table} WHERE build_id=%s)
                EXCEPT ALL (SELECT {columns} FROM {table} WHERE build_id=%s))''').format(columns=columns,table=sql.Identifier(table)),(left,right))
            assert cur.fetchone()[0],table+'_rows_differ'
            counts[table]=n
    return counts


def reconcile_and_oracle(db, snapshot, build):
    """Derive expected population from ② independently of observation projection."""
    started=time.monotonic()
    with db,db.cursor() as cur:
        cur.execute('''CREATE TEMP TABLE observation_acceptance ON COMMIT PRESERVE ROWS AS
            SELECT d.analysis_id,d.occurrence_id,o.scope_id,o.database,o.execution_user,
                o.sql_state,o.timing_type,d.state,d.in_window,d.reason_codes,
                d.estimated_start_at,d.duration_ms,a.rule_id,r.value,r.state AS approximate_state,
                ARRAY(SELECT reason FROM unnest(d.reason_codes) reason
                    WHERE reason NOT IN ('sql_uncertain','sql_incomplete','sql_encoding_invalid','fingerprint_failed')) AS exclusions
            FROM mpp_training_decisions(%s,%s) d JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
            LEFT JOIN mpp_occurrence_approximate a USING(analysis_id,occurrence_id)
            LEFT JOIN mpp_approximate_result r USING(result_id,rule_id)
            WHERE o.sql_state<>'complete' OR 'fingerprint_failed'=ANY(d.reason_codes)''',(snapshot['input_id'],snapshot['config_id']))
        cur.execute('''CREATE TEMP TABLE observation_expected ON COMMIT PRESERVE ROWS AS
            SELECT a.*,g.group_id,(a.state='unresolved' AND cardinality(a.exclusions)=0) AS observed
            FROM observation_acceptance a LEFT JOIN mpp_observation_group g
              ON (g.scope_id,g.database,g.execution_user,g.rule_id,g.approximate_value,g.timing_type)=
                 (a.scope_id,a.database,a.execution_user,a.rule_id,a.value,coalesce(a.timing_type,'unknown'))
            WHERE a.in_window IS TRUE AND a.approximate_state='available' AND a.sql_state<>'missing'
                AND a.database IS NOT NULL AND a.execution_user IS NOT NULL''')
        cur.execute('SELECT count(*) FROM observation_expected WHERE group_id IS NULL');assert cur.fetchone()[0]==0,'missing_expected_group'
        cur.execute('CREATE INDEX ON observation_expected(group_id)');cur.execute('ANALYZE observation_expected')
        cur.execute('''WITH expected AS (SELECT group_id,count(*) FILTER(WHERE observed) i,count(*) FILTER(WHERE NOT observed) e
                FROM observation_expected GROUP BY 1), actual AS (
                SELECT group_id,included_count i,excluded_count e FROM mpp_observation_statistic WHERE build_id=%s AND layer='overall')
            SELECT count(*) FROM expected FULL JOIN actual USING(group_id) WHERE expected.i IS DISTINCT FROM actual.i OR expected.e IS DISTINCT FROM actual.e''',(build,))
        assert cur.fetchone()[0]==0,'observation_population_differences'
        cur.execute('''WITH layers AS (
            SELECT group_id,layer,sum(included_count) i,sum(excluded_count) e FROM mpp_observation_statistic WHERE build_id=%s GROUP BY 1,2)
            SELECT count(*) FROM (SELECT group_id FROM layers GROUP BY 1 HAVING count(*)<>5 OR min(i)<>max(i) OR min(e)<>max(e)) bad''',(build,))
        assert cur.fetchone()[0]==0,'observation_layer_conservation'
        cur.execute('''WITH expected AS (SELECT group_id,reason,count(*) n FROM observation_expected
                CROSS JOIN LATERAL unnest(exclusions) reason WHERE NOT observed GROUP BY 1,2), actual AS (
                SELECT group_id,r.key reason,r.value::bigint n FROM mpp_observation_statistic
                CROSS JOIN LATERAL jsonb_each_text(exclusions_by_reason) r WHERE build_id=%s AND layer='overall')
            SELECT count(*) FROM expected FULL JOIN actual USING(group_id,reason) WHERE expected.n IS DISTINCT FROM actual.n''',(build,))
        assert cur.fetchone()[0]==0,'observation_reason_conservation'
        # The classification table explains all uncertain/missing records,
        # including overlapping facts, with mutually exclusive terminal scopes.
        cur.execute('''SELECT sql_state,CASE WHEN in_window IS FALSE THEN 'outside_window'
                WHEN sql_state='missing' THEN 'sql_missing'
                WHEN approximate_state IS DISTINCT FROM 'available' THEN 'approximate_unavailable'
                WHEN database IS NULL OR execution_user IS NULL THEN 'identity_missing'
                WHEN estimated_start_at IS NULL THEN 'start_unknown'
                WHEN state='unresolved' AND cardinality(exclusions)=0 THEN 'observed' ELSE 'excluded' END,
                count(DISTINCT (analysis_id,occurrence_id)) FROM observation_acceptance GROUP BY 1,2 ORDER BY 1,2''')
        disposition=cur.fetchall()
        cur.execute('''SELECT sql_state,count(DISTINCT (analysis_id,occurrence_id)) FROM observation_acceptance GROUP BY 1 ORDER BY 1''')
        raw_counts=cur.fetchall()
        cur.execute('''SELECT rule_id,count(*) FILTER(WHERE observed),count(*) FILTER(WHERE NOT observed)
            FROM observation_expected GROUP BY 1 ORDER BY 1''');counts=cur.fetchall()
        cur.execute('''SELECT rule_id,reason,count(*) FROM observation_expected CROSS JOIN LATERAL unnest(exclusions) reason
            WHERE NOT observed GROUP BY 1,2 ORDER BY 1,2''');reasons=cur.fetchall()
        cur.execute('SELECT window_start,window_end FROM config_snapshot WHERE config_id=%s',(snapshot['config_id'],));start,end=cur.fetchone()
        cur.execute('SELECT DISTINCT group_id FROM observation_expected ORDER BY 1');groups=[r[0] for r in cur]
    checked=0
    with db:
        for i,gid in enumerate(groups):
            with db.cursor() as cur:
                cur.execute('SELECT estimated_start_at,duration_ms,observed,exclusions FROM observation_expected WHERE group_id=%s',(gid,))
                events=cur.fetchall()
            with db.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM mpp_observation_statistic WHERE build_id=%s AND group_id=%s',(build,gid))
                checked+=assert_observation_rows(cur.fetchall(),events,start,end)
            if (i+1)%1000==0:print(canonical(dict(phase='observation_oracle_progress',groups=i+1,total=len(groups))),flush=True)
    with db,db.cursor() as cur:
        cur.execute('DROP TABLE observation_expected,observation_acceptance')
    return dict(population_equal=True,layer_conservation=True,reason_conservation=True,sql_states=raw_counts,
        disposition=disposition,counts_by_rule=counts,reasons_by_rule=reasons,groups=len(groups),statistic_rows=checked,
        metrics=checked*17,absolute_tolerance=1e-9,relative_tolerance=1e-10,
        method='all groups; independent bucket/Decimal metrics oracle and independent eligibility projection',
        seconds=round(time.monotonic()-started,3))


def validate(dsn,output):
    output.mkdir(parents=True,exist_ok=True)
    training=TrainingStore(dsn);store=StatisticsStore(dsn)
    Baseline,baseline_sha=frozen_store();baseline=Baseline(dsn)
    imported=json.loads((output/'import-report.json').read_text())
    report=dict(schema_version='1.9.0',method='measured; private PG17; 55-file fresh import',clusters={},
        input_manifest_sha256=imported['manifest_sha256'],input_files=sum(len(r['files']) for r in imported['first_runs']),
        import_seconds=imported['import_seconds'],baseline_ref=BASELINE_REF,baseline_statistics_sha256=baseline_sha,
        decision_version=DECISION_VERSION,normalization_context=training.context)
    files=list((ROOT/'sql_apm/baseline').glob('*.py'))+[ROOT/'sql_apm/storage'/n for n in ('schema.sql','statistics.py','observations.py')]+[Path(__file__),ROOT/'tests/database/observations.py',ROOT/'tests/baseline/oracle.py']
    report['code_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    def save(): (output/'observation-report.json').write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n')
    try:
        with store.db,store.db.cursor() as cur:
            cur.execute('SELECT count(*) FROM source_file');assert cur.fetchone()[0]==55
            cur.execute('SELECT count(*) FROM mpp_occurrence');assert cur.fetchone()[0]==7424804
            cur.execute('SELECT sql_state,count(*) FROM mpp_occurrence GROUP BY 1 ORDER BY 1');report['import_sql_states']=cur.fetchall()
            totals=dict(report['import_sql_states']);assert {k:totals[k] for k in ('uncertain','incomplete','invalid_encoding','missing')}==dict(uncertain=91142,incomplete=897,invalid_encoding=20,missing=63355)
            cur.execute('SELECT version,script_sha256 FROM schema_version ORDER BY version');report['schema_receipts']=cur.fetchall()
        for scope,cutoff,day in [('119','2026-07-31','2026-07-23'),('120','2026-09-19','2026-09-19')]:
            doc=dict(version=1,clusters=['119','120'],window=dict(cutoff_date=cutoff),templates=[],
                exclusions=[dict(id='acceptance-interval',cluster=scope,start=day+'T10:00:00+08:00',end=day+'T10:30:00+08:00',reason='temporary acceptance interval')])
            snapshot=training.snapshot(configuration(doc,scope),['full-import-'+scope])
            entry=report['clusters'][scope]=dict(snapshot=snapshot);save()
            progress=lambda event:print(canonical(event),flush=True)
            with formal.Memory(store.db) as memory:
                original=baseline.calculate(scope,snapshot['input_id'],snapshot['config_id'],progress=progress)
            entry.update(baseline=original,baseline_memory=memory.report());save()
            observation_seconds=[]
            def observe(event):
                progress(event)
                if event['phase']=='observations_written':observation_seconds.append(event['seconds'])
            observation_memory=[]
            def measured_observations(*args,**kwargs):
                with formal.Memory(store.db) as phase_memory:
                    result=calculate_observations(*args,**kwargs)
                observation_memory.append(phase_memory.report())
                return result
            with patch('sql_apm.storage.statistics.calculate_observations',measured_observations), formal.Memory(store.db) as memory:
                first=store.calculate(scope,snapshot['input_id'],snapshot['config_id'],progress=observe)
            entry.update(first=first,first_memory=memory.report(),observation_memory=observation_memory[0],observation_seconds=observation_seconds[0],
                         total_seconds_delta=round(first['seconds']-original['seconds'],3));save()
            formal_tables=('mpp_statistic','mpp_build_timing_coverage','mpp_build_group')
            entry['formal_equal_rows']=rows_equal(store.db,original['build_id'],first['build_id'],formal_tables);save()
            entry['oracle']=reconcile_and_oracle(store.db,snapshot,first['build_id']);save()
            with formal.Memory(store.db) as memory:
                repeated=store.calculate(scope,snapshot['input_id'],snapshot['config_id'],progress=progress)
            entry.update(repeat=repeated,repeat_memory=memory.report());save()
            entry['repeat_equal_rows']=rows_equal(store.db,first['build_id'],repeated['build_id'],
                formal_tables+('mpp_observation_statistic','mpp_build_observation_group'))
            assert first['observations']==repeated['observations']
            entry['repeat_equal']=True;save()
            print(canonical(dict(phase='observation_cluster_verified',groups=first['observations']['groups'])),flush=True)
        report['relation_sizes']=formal.sizes(store.db)
        with store.db,store.db.cursor() as cur:
            cur.execute('SELECT count(*) FROM mpp_decision');assert cur.fetchone()[0]==0
            cur.execute('SELECT state,count(*) FROM build GROUP BY 1');report['build_states']=cur.fetchall()
        report['complete']=True;save()
    finally:training.close();store.close();baseline.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--manifest',type=Path,default=ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dsn',help='acceptance-only; already freshly imported private instance')
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--workers',type=int,choices=range(1,9),default=4)
    args=parser.parse_args()
    if args.dsn:validate(args.dsn,args.output)
    else:
        formal.validate=validate
        formal.main(args)


if __name__=='__main__':main()
