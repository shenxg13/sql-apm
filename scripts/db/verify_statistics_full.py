#!/usr/bin/env python3
"""Explicit 55-file statistics acceptance; local private PG17 and redacted report."""
import argparse
from collections import Counter
from datetime import timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from psycopg2 import sql
from verify import instance,Verification
from baseline.oracle import assert_metrics,reference_sufficiency
from sql_apm.ingestion.config import canonical,identity,load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.storage.training import TrainingStore
from sql_apm.storage.statistics import StatisticsStore
from sql_apm.training.config import DECISION_VERSION, validate as configuration

SEED='20260930'
TZ=timezone(timedelta(hours=8))


class Memory:
    """Sample proportional memory for this Python tree and the private PG tree."""
    def __init__(self, db, root_pid=None):
        host=Path(db.get_dsn_parameters()['host'])
        self.pg=int((host.parent/'data/postmaster.pid').read_text().splitlines()[0])
        self.root_pid=root_pid or os.getpid()
        self.peaks=Counter();self.stop=threading.Event();self.samples=0
        self.thread=threading.Thread(target=self.run,daemon=True)

    def run(self):
        while not self.stop.is_set():
            parents={}
            for path in Path('/proc').glob('[0-9]*/stat'):
                try:parents[int(path.parent.name)]=int(path.read_text().rsplit(')',1)[1].split()[1])
                except (OSError,ValueError,IndexError):pass
            selected={self.root_pid:'python',self.pg:'postgres'}
            for _ in range(8):
                added={pid:selected[parent] for pid,parent in parents.items() if parent in selected and pid not in selected}
                if not added:break
                selected.update(added)
            values=Counter()
            for pid,kind in selected.items():
                try:
                    for line in Path('/proc/'+str(pid)+'/smaps_rollup').read_text().splitlines():
                        if line.startswith('Pss:'):values[kind]+=int(line.split()[1])*1024
                except OSError:pass
            values['total']=sum(values.values())
            for key,value in values.items():self.peaks[key]=max(self.peaks[key],value)
            self.samples+=1
            self.stop.wait(1)

    def __enter__(self):
        self.thread.start();return self

    def __exit__(self,*args):
        self.stop.set();self.thread.join(timeout=5)

    def report(self):
        return dict(method='1-second /proc smaps_rollup PSS sampling; process trees; bytes',samples=self.samples,peak_bytes=dict(self.peaks))


def digest(db,build):
    """Four independent 64-bit sums of SHA-256 rows, independent of row order."""
    output={}
    with db,db.cursor() as cur:
        for table in ('mpp_statistic','mpp_build_layer_count','mpp_build_timing_coverage'):
            cur.execute(sql.SQL('''WITH hashes AS MATERIALIZED (
                SELECT encode(sha256(convert_to((to_jsonb(t)-'build_id'-'partition_id')::text,'UTF8')),'hex') h
                FROM {} t WHERE build_id=%s)
                SELECT count(*),sum(('x'||substr(h,1,16))::bit(64)::bigint),sum(('x'||substr(h,17,16))::bit(64)::bigint),
                    sum(('x'||substr(h,33,16))::bit(64)::bigint),sum(('x'||substr(h,49,16))::bit(64)::bigint) FROM hashes''').format(sql.Identifier(table)),(build,))
            row=cur.fetchone();output[table]=dict(rows=row[0],sha256_limb_sums=[str(v) for v in row[1:]])
    return output


def reconcile(db,snapshot,build):
    start=time.monotonic()
    with db,db.cursor() as cur:
        cur.execute('''CREATE TEMP TABLE acceptance_decisions ON COMMIT PRESERVE ROWS AS
            SELECT group_id,timing_type,state,count_scope,reason_codes,estimated_start_at,duration_ms
            FROM mpp_training_decisions(%s,%s)''',(snapshot['input_id'],snapshot['config_id']))
        cur.execute('CREATE INDEX ON acceptance_decisions(group_id)');cur.execute('ANALYZE acceptance_decisions')
        cur.execute('''WITH layers AS (
            SELECT group_id,layer,sum(included_count) i,sum(excluded_count) e
            FROM mpp_statistic WHERE build_id=%s GROUP BY 1,2)
            SELECT count(*) FROM (SELECT group_id FROM layers GROUP BY 1
                HAVING count(*)<>5 OR min(i)<>max(i) OR min(e)<>max(e)) bad''',(build,))
        assert cur.fetchone()[0]==0,'layer_conservation'
        cur.execute("SELECT count(*) FILTER (WHERE state='included'),count(*) FILTER (WHERE state<>'included') FROM acceptance_decisions WHERE count_scope='group'")
        expected=cur.fetchone()
        cur.execute("SELECT sum(included_count),sum(excluded_count) FROM mpp_statistic WHERE build_id=%s AND layer='overall'",(build,))
        assert cur.fetchone()==expected,'decision_total_conservation'
        cur.execute("SELECT reason,count(*) FROM acceptance_decisions CROSS JOIN LATERAL unnest(reason_codes) reason WHERE count_scope='group' GROUP BY 1 ORDER BY 1")
        reasons=cur.fetchall()
        cur.execute("SELECT r.key,sum(r.value::bigint) FROM mpp_statistic s CROSS JOIN LATERAL jsonb_each_text(exclusions_by_reason) r WHERE build_id=%s AND layer='overall' GROUP BY 1 ORDER BY 1",(build,))
        assert cur.fetchall()==reasons,'reason_conservation'
        cur.execute('''SELECT count(*) FROM mpp_build_timing_coverage t LEFT JOIN (
            SELECT g.timing_type,sum(s.included_count) i,sum(s.excluded_count) e
            FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id)
            WHERE build_id=%s AND layer='overall' GROUP BY 1) x USING(timing_type)
            WHERE t.build_id=%s AND (t.included_count<>coalesce(x.i,0) OR t.excluded_count<>coalesce(x.e,0))''',(build,build))
        assert cur.fetchone()[0]==0,'timing_conservation'
        # Required keys use an independent SQL calendar and fixed weekday/hour sets.
        cur.execute('''CREATE TEMP TABLE acceptance_keys ON COMMIT DROP AS
            WITH config AS (SELECT * FROM config_snapshot WHERE config_id=%s),
            days AS (SELECT d::date AS sample_day FROM config CROSS JOIN LATERAL generate_series(
                window_start AT TIME ZONE 'Asia/Shanghai',
                (window_end AT TIME ZONE 'Asia/Shanghai')-interval '1 day',interval '1 day') d)
            SELECT 'overall'::text layer,'null'::jsonb key
            UNION ALL SELECT 'day',to_jsonb(sample_day) FROM days
            UNION SELECT 'week',to_jsonb(date_trunc('week',sample_day)::date) FROM days
            UNION ALL SELECT 'weekday',to_jsonb(i) FROM generate_series(1,7) i
            UNION ALL SELECT 'hour',to_jsonb(i) FROM generate_series(0,23) i''',(snapshot['config_id'],))
        cur.execute('''SELECT count(*) FROM mpp_coverage(%s) c WHERE true AND (
            jsonb_array_length(computed_keys)+jsonb_array_length(empty_keys) <>
                (SELECT count(*) FROM acceptance_keys k WHERE k.layer=c.layer)
            OR EXISTS (SELECT k.key FROM acceptance_keys k WHERE k.layer=c.layer
                       EXCEPT SELECT jsonb_array_elements(c.computed_keys||c.empty_keys)))''',(build,))
        assert cur.fetchone()[0]==0,'coverage_partition'
        cur.execute('''WITH computed AS (
            SELECT group_id,layer,jsonb_array_elements(computed_keys) key FROM mpp_coverage(%s)),
            actual AS (SELECT group_id,layer,coalesce(to_jsonb(bucket_date),to_jsonb(bucket_number),'null'::jsonb) key
                FROM mpp_statistic WHERE build_id=%s)
            SELECT count(*) FROM ((SELECT * FROM computed EXCEPT SELECT * FROM actual)
                UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM computed)) mismatch''',(build,build))
        assert cur.fetchone()[0]==0,'computed_keys_match_rows'
        cur.execute('''SELECT count(*) FROM (SELECT group_id,count(*) n FROM mpp_coverage(%s) GROUP BY 1) x WHERE n<>5''',(build,))
        assert cur.fetchone()[0]==0,'five_coverages'
        cur.execute('''SELECT s.layer,count(*),count(*) FILTER (WHERE (q.basic->>'met')::boolean),
            count(*) FILTER (WHERE (q.p95->>'met')::boolean),count(*) FILTER (WHERE (q.p99->>'met')::boolean)
            FROM mpp_statistic s JOIN build b USING(build_id) JOIN config_snapshot c USING(config_id)
            CROSS JOIN LATERAL jsonb_to_record(mpp_statistic_sufficiency(c.statistics_version,c.thresholds,
                s.layer,s.included_count,s.active_dates,s.active_week_starts)) q(basic jsonb,p95 jsonb,p99 jsonb)
            WHERE s.build_id=%s GROUP BY 1 ORDER BY 1''',(build,))
        distribution=cur.fetchall()
    return dict(layer_conservation=True,decisions_conserved=True,reasons_conserved=True,coverage_complete=True,
                included=expected[0],excluded=expected[1],group_reasons=reasons,
                layers_and_basic_p95_p99=distribution,seconds=round(time.monotonic()-start,3))


def sample_oracle(db,build):
    start=time.monotonic();metrics_checked=0;rows_checked=0
    with db,db.cursor() as cur:
        cur.execute('SELECT c.thresholds FROM build b JOIN config_snapshot c USING(config_id) WHERE b.build_id=%s',(build,))
        thresholds=cur.fetchone()[0]
        cur.execute("SELECT group_id FROM mpp_statistic WHERE build_id=%s AND layer='overall' ORDER BY md5(%s||group_id) LIMIT 1000",(build,SEED))
        groups={r[0] for r in cur}
        special={}
        for name,condition,order in [('largest','true','included_count DESC'),('single','included_count=1','group_id'),('excluded','excluded_count>0','excluded_count DESC')]:
            cur.execute('SELECT group_id FROM mpp_statistic WHERE build_id=%s AND layer=\'overall\' AND '+condition+' ORDER BY '+order+' LIMIT 1',(build,))
            found=cur.fetchone();assert found,'required_sample_stratum_missing'
            groups.add(found[0]);special[name]=found[0]
        for gid in sorted(groups):
            cur.execute('SELECT estimated_start_at,duration_ms,state,reason_codes FROM acceptance_decisions WHERE group_id=%s AND count_scope=\'group\'',(gid,))
            events=cur.fetchall()
            cur.execute('''SELECT s.*,mpp_statistic_sufficiency(c.statistics_version,c.thresholds,s.layer,
                s.included_count,s.active_dates,s.active_week_starts) AS sufficiency
                FROM mpp_statistic s JOIN build b USING(build_id) JOIN config_snapshot c USING(config_id)
                WHERE s.build_id=%s AND s.group_id=%s''',(build,gid))
            names=[col[0] for col in cur.description];statistics=[dict(zip(names,r)) for r in cur]
            for stored in statistics:
                chosen=[]
                for event in events:
                    instant=event[0].astimezone(TZ);layer=stored['layer']
                    if layer=='day' and instant.date()!=stored['bucket_date']:continue
                    if layer=='week' and (instant.date()-timedelta(days=instant.weekday()))!=stored['bucket_date']:continue
                    if layer=='weekday' and instant.isoweekday()!=stored['bucket_number']:continue
                    if layer=='hour' and instant.hour!=stored['bucket_number']:continue
                    chosen.append(event)
                included=[e for e in chosen if e[2]=='included']
                assert len(included)==stored['included_count'] and len(chosen)-len(included)==stored['excluded_count']
                assert dict(Counter(r for e in chosen if e[2]!='included' for r in set(e[3])))==stored['exclusions_by_reason']
                dates=sorted({e[0].astimezone(TZ).date() for e in included})
                weeks=sorted({d-timedelta(days=d.weekday()) for d in dates})
                assert dates==stored['active_dates'] and weeks==stored['active_week_starts']
                instants=[e[0] for e in included]
                assert stored['first_sample_at']==(min(instants) if instants else None)
                assert stored['last_sample_at']==(max(instants) if instants else None)
                assert stored['sufficiency']==reference_sufficiency(thresholds[stored['layer']],len(included),dates,weeks)
                metrics_checked+=assert_metrics(stored,[e[1] for e in included]);rows_checked+=1
            if rows_checked % 1000 < 30:
                print(canonical(dict(phase='sample_progress',rows=rows_checked)),flush=True)
    assert len(groups)>=1000
    return dict(seed=SEED,method='seeded MD5 ordering plus largest/single/excluded strata; Decimal independent oracle',
        groups=len(groups),threshold_results=rows_checked*3,group_selection_sha256=hashlib.sha256('\n'.join(sorted(groups)).encode()).hexdigest(),
        special=special,statistic_rows=rows_checked,metrics=metrics_checked,
        absolute_tolerance=1e-9,relative_tolerance=1e-10,seconds=round(time.monotonic()-start,3))


def sizes(db):
    result=[]
    with db,db.cursor() as cur:
        cur.execute("SELECT relname,relkind,relispartition FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind IN ('r','p') ORDER BY relname")
        for name,kind,leaf in cur.fetchall():
            cur.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name)));count=cur.fetchone()[0]
            if kind=='p':
                cur.execute('SELECT coalesce(sum(pg_total_relation_size(relid)),0) FROM pg_partition_tree(%s::regclass) WHERE isleaf',(name,))
            else:cur.execute('SELECT pg_total_relation_size(%s::regclass)',(name,))
            result.append(dict(table=name,partition=leaf,rows=count,total_bytes=int(cur.fetchone()[0])))
    return result


def validate(dsn,output):
    output.mkdir(parents=True,exist_ok=True)
    training=TrainingStore(dsn);store=StatisticsStore(dsn)
    report=dict(schema_version='1.7.0',method='measured; private PG17; all 55 files reimported',clusters={},
                source_head_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                normalization_context=training.context,decision_version=DECISION_VERSION)
    imported=json.loads((output/'import-report.json').read_text())
    report['input_manifest_sha256']=imported['manifest_sha256']
    report['import_seconds']=imported['import_seconds']
    report['import_source_head_sha']=imported.get('import_source_head_sha',report['source_head_sha'])
    report['input_files']=sum(len(item['files']) for item in imported['first_runs'])
    report['code_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'sql_apm/baseline').glob('*.py'))+[ROOT/'sql_apm/storage/statistics.py',ROOT/'sql_apm/storage/schema.sql',Path(__file__),ROOT/'tests/baseline/oracle.py']}
    def save():
        (output/'statistics-report.json').write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n')
    try:
        with store.db,store.db.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='mpp_statistic' AND column_name='sufficiency'");assert cur.fetchone()[0]==0
            cur.execute('SELECT count(*) FROM mpp_occurrence');assert cur.fetchone()[0]==7424804
            cur.execute('SELECT count(*) FROM source_file');assert cur.fetchone()[0]==55
            cur.execute('SELECT version,script_sha256 FROM schema_version ORDER BY version');report['schema_receipts']=cur.fetchall()
        for scope,cutoff,day in [('119','2026-07-31','2026-07-23'),('120','2026-09-19','2026-09-19')]:
            doc=dict(version=1,clusters=['119','120'],window=dict(cutoff_date=cutoff),templates=[],
                exclusions=[dict(id='acceptance-interval',cluster=scope,start=day+'T10:00:00+08:00',end=day+'T10:30:00+08:00',reason='temporary acceptance interval')])
            frozen=training.snapshot(configuration(doc,scope),['full-import-'+scope])
            entry=report['clusters'][scope]=dict(snapshot=frozen);save()
            with Memory(store.db) as memory:
                first=store.calculate(scope,frozen['input_id'],frozen['config_id'],progress=lambda row:print(canonical(row),flush=True))
            entry.update(first=first,memory_first=memory.report());save()
            entry['reconciliation']=reconcile(store.db,frozen,first['build_id']);save()
            entry['oracle']=sample_oracle(store.db,first['build_id']);save()
            with store.db,store.db.cursor() as cur:cur.execute('DROP TABLE acceptance_decisions')
            first_digest=digest(store.db,first['build_id']);entry['first_digest']=first_digest;save()
            with Memory(store.db) as memory:
                repeated=store.calculate(scope,frozen['input_id'],frozen['config_id'],progress=lambda row:print(canonical(row),flush=True))
            entry.update(repeat=repeated,memory_repeat=memory.report());save()
            entry['repeat_digest']=digest(store.db,repeated['build_id'])
            assert entry['repeat_digest']==first_digest,'repeat_statistics_differ'
            entry['repeat_equal']=True;save()
            print(canonical(dict(phase='cluster_verified',scope='scope:'+identity(scope),groups=first['groups'])),flush=True)
        report['relation_sizes']=sizes(store.db)
        with store.db,store.db.cursor() as cur:
            cur.execute('SELECT count(*) FROM mpp_decision');assert cur.fetchone()[0]==0
            cur.execute("SELECT state,count(*) FROM build GROUP BY 1");report['build_states']=cur.fetchall()
        report['complete']=True;save()
    finally:training.close();store.close()


def main(args):
    if args.dsn:
        validate(args.dsn,args.output);return
    if not args.root:raise ValueError('explicit_input_root_required')
    if args.output.exists():raise ValueError('fresh_output_directory_required')
    args.output.mkdir(parents=True)
    manifest=json.loads(args.manifest.read_text())
    doc=dict(version=1,clusters=sorted(manifest['clusters']),sources={},batches={})
    for scope,details in sorted(manifest['clusters'].items()):
        source,batch='full-'+scope,'full-import-'+scope
        doc['sources'][source]=dict(cluster=scope,build='HashData Warehouse 3.13.13',timezone='UTC+08:00',declaration='Issue25-confirmed-local-master-files')
        doc['batches'][batch]=dict(source=source,files_confirmed_complete=True,dates=sorted({f['file'][5:15] for f in details['files']}),files=[dict(path=str((args.root/scope/f['file']).resolve()),origin_key=scope+'/'+f['file'],closed_and_copied=True) for f in details['files']])
    config=args.output/'import-config.json';config.write_text(canonical(doc))
    report=dict(manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),first_runs=[])
    with instance(args.pg_bin) as (directory,env):
        v=Verification(args.pg_bin,directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        started=time.monotonic();importer=Importer(dsn,workers=args.workers)
        try:
            for scope,details in sorted(manifest['clusters'].items()):
                result=importer.run(load_config(config,'full-'+scope,'full-import-'+scope))
                assert result['state']=='complete'
                assert {r['file_id'] for r in result['files']}=={'I:'+identity('full-'+scope,f['sha256']) for f in details['files']}
                report['first_runs'].append(dict(cluster=scope,**result))
                (args.output/'import-report.json').write_text(canonical(report))
        finally:importer.close()
        report['import_seconds']=round(time.monotonic()-started,3)
        (args.output/'import-report.json').write_text(canonical(report))
        validate(dsn,args.output);v.init('check')
    print(canonical(dict(complete=True)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--manifest',type=Path,default=ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dsn',help='acceptance-only connection to an already imported private instance')
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--workers',type=int,choices=range(1,9),default=4)
    main(parser.parse_args())
