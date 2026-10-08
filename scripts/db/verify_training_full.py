#!/usr/bin/env python3
"""Explicit full training acceptance; private PG17, no production SQL output."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from verify import instance, Verification
from sql_apm.ingestion.config import canonical, identity, load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.storage.training import TrainingStore
from sql_apm.training.config import DECISION_VERSION, validate as configuration


def decision_digest(store, frozen):
    # Force every Decision field, including all eight evaluations and
    # reason references. A 256-bit row hash is the only TEMP payload;
    # four integer sums plus count give order-independent replay evidence.
    start=time.monotonic()
    with store.db,store.db.cursor() as cur:
        cur.execute("""WITH hashes AS MATERIALIZED (
            SELECT encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex') AS h
            FROM mpp_training_decisions(%s,%s) d)
            SELECT count(*),sum(('x'||substr(h,1,16))::bit(64)::bigint),
                sum(('x'||substr(h,17,16))::bit(64)::bigint),
                sum(('x'||substr(h,33,16))::bit(64)::bigint),
                sum(('x'||substr(h,49,16))::bit(64)::bigint) FROM hashes""",
            (frozen['input_id'],frozen['config_id']))
        values=[str(value) for value in cur.fetchone()]
    return dict(count=int(values[0]),hash_sums=values[1:],seconds=round(time.monotonic()-start,3))


def validate(dsn, output, compare_aliases=False):
    store=TrainingStore(dsn)
    report=dict(clusters={}, method='private PostgreSQL 17; product importer; original cache and SQL derivation; measured',
                normalization_context=store.context, decision_version=DECISION_VERSION, schema_version='1.10.0')
    report['code_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'sql_apm/training').glob('*.py'))+sorted((ROOT/'sql_apm/storage').glob('*.py'))+[ROOT/'sql_apm/storage/schema.sql']}
    try:
        with store.db,store.db.cursor() as cur:
            for key,query in {
                'outcomes':'SELECT outcome,count(*) FROM mpp_occurrence GROUP BY 1',
                'sql_states':'SELECT sql_state,count(*) FROM mpp_occurrence GROUP BY 1',
                'occurrences':'SELECT count(*) FROM mpp_occurrence',
                'sql_texts':'SELECT count(*) FROM mpp_sql_text',
            }.items():
                cur.execute(query);report[key]=cur.fetchall()
        assert report['occurrences']==[(7424804,)]
        assert dict(report['outcomes'])==dict(success=7367365,failed=57285,cancelled=90,timed_out=64)
        # Keep a reproducible source of comparison rather than copying SQL payloads.
        historical=json.loads((ROOT/'var/ingestion/acceptance/report.json').read_text()) if (ROOT/'var/ingestion/acceptance/report.json').exists() else None
        if historical:
            report['previous_sql_states']=historical['sql_states']
            assert dict(report['sql_states'])==historical['sql_states']
        totals=Counter()
        for scope,cutoff,day in [('119','2026-07-31','2026-07-23'),('120','2026-09-19','2026-09-19')]:
            doc=dict(version=1,clusters=['119','120'],window=dict(cutoff_date=cutoff),templates=[],
                exclusions=[dict(id='acceptance-interval',cluster=scope,start=day+'T10:00:00+08:00',end=day+'T10:30:00+08:00',reason='temporary acceptance interval')])
            if compare_aliases:
                from verify_training_aliases import old_snapshot, compare
                previous=old_snapshot(store,doc,scope,['full-import-'+scope])
            frozen=store.snapshot(configuration(doc,scope),['full-import-'+scope])
            # Hash streamed per-group counters, not all rows in memory or permanent Decisions.
            def summarize():
                digest=hashlib.sha256();count=[0]
                def sink(group):
                    digest.update((canonical(group)+'\n').encode());count[0]+=1
                result=store.summary(frozen['input_id'],frozen['config_id'],group_sink=sink)
                result.update(group_summary_sha256=digest.hexdigest(),group_summary_rows=count[0])
                return result

            first=summarize()
            complete_first=decision_digest(store,frozen);complete_repeat=decision_digest(store,frozen)
            assert complete_first['count']==complete_repeat['count'] and complete_first['hash_sums']==complete_repeat['hash_sums']

            with store.db,store.db.cursor() as cur:
                cur.execute('SELECT count(*) FROM mpp_occurrence WHERE scope_id=%s',(scope,));expected=cur.fetchone()[0]
            assert sum(n for state,n in first['states'])==expected==complete_first['count']
            for reason,state,n in first['reasons']:totals[reason]+=n
            report['clusters'][scope]=dict(snapshot=frozen,first=first,
                complete_first=complete_first,complete_repeat=complete_repeat,complete_digest_equal=True)
            if compare_aliases:
                baseline=json.loads((ROOT/'docs/reports/data/training-decisions-2026-09-29.json').read_text())
                report['clusters'][scope]['alias_comparison']=compare(store,previous,frozen,baseline['clusters'][scope]['first'],first)
            (output/'decision-report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
            print(canonical(dict(phase='cluster_verified',scope='scope:'+identity(scope),count=expected,cache_seconds=frozen['cache']['seconds'],derive_seconds=first['derive_seconds'])),flush=True)
        for outcome,reason in [('failed','execution_failed'),('cancelled','execution_cancelled'),('timed_out','execution_timed_out')]:
            assert totals[reason]==dict(report['outcomes'])[outcome]
        for state,reason in [('missing','sql_missing'),('incomplete','sql_incomplete'),('invalid_encoding','sql_encoding_invalid'),('uncertain','sql_uncertain')]:
            assert totals[reason]==dict(report['sql_states']).get(state,0)
        with store.db,store.db.cursor() as cur:
            cur.execute("SELECT count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.normalization_id=%s AND f.state<>'reliable'",(store.normalization_id,))
            assert totals['fingerprint_failed']==cur.fetchone()[0]
            cur.execute('SELECT (SELECT count(*) FROM mpp_decision),(SELECT count(*) FROM build),(SELECT count(*) FROM mpp_training_sql)')
            decisions,builds,cached=cur.fetchone();assert decisions==builds==0
            report['persistent_counts']=dict(decisions=decisions,builds=builds,original_results=cached)
            cur.execute("SELECT relname,pg_total_relation_size(relid) FROM pg_stat_user_tables WHERE relname IN ('mpp_training_rule','mpp_training_sql','input_snapshot','input_manifest','input_file_analysis','config_snapshot','training_config') ORDER BY 1")
            report['relation_bytes']=dict(cur.fetchall())
            cur.execute('''SELECT r.category_rules->>'version',count(*),sum(pg_column_size(s))
                FROM mpp_training_sql s JOIN mpp_training_rule r USING(rule_id) GROUP BY 1 ORDER BY 1''')
            report['cache_rows_and_payload_bytes_by_version']=cur.fetchall()
        report.update(reason_totals=dict(totals),complete=True)
        (output/'decision-report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    finally:store.close()
    return report


def main(args):
    if args.output.exists():raise ValueError('fresh_output_directory_required')
    args.output.mkdir(parents=True)
    manifest=json.loads(args.manifest.read_text())
    doc=dict(version=1,clusters=sorted(manifest['clusters']),sources={},batches={})
    for scope,details in sorted(manifest['clusters'].items()):
        source,batch='full-'+scope,'full-import-'+scope
        doc['sources'][source]=dict(cluster=scope,build='HashData Warehouse 3.13.13',timezone='UTC+08:00',declaration='Issue21-confirmed-local-master-files')
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
        validate(dsn,args.output,args.compare_aliases)
        v.init('check')
    print(canonical(dict(complete=True)))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,default=ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--compare-aliases',action='store_true',help='compare frozen category v2 and current rules against the merged Issue #21 counts')
    parser.add_argument('--workers',type=int,choices=range(1,9),default=4)
    main(parser.parse_args())
