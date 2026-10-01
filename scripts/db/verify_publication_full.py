#!/usr/bin/env python3
"""Explicit production-shaped acceptance: private PG17, 55 files, nine tasks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from verify import instance,Verification
from verify_statistics_full import Memory,reconcile,sample_oracle
from verify_observations_full import reconcile_and_oracle,rows_equal,frozen_store
from sql_apm.ingestion.config import identity,canonical
from sql_apm.storage.ingestion import connect
from sql_apm.storage.publication import version_status
from sql_apm.storage.tasks import Task


def footprint(db,scope):
    with db,db.cursor() as cur:
        cur.execute('SELECT pg_database_size(current_database())');total=cur.fetchone()[0]
        cur.execute('''SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) FROM mpp_result_partition p
            JOIN pg_class c ON c.relnamespace=current_schema()::regnamespace
              AND c.relname IN ('mpp_statistic_p'||p.partition_id,'mpp_observation_statistic_p'||p.partition_id)
            WHERE p.scope_id=%s''',(scope,))
        return dict(database_bytes=total,result_partition_bytes=int(cur.fetchone()[0]))


def cluster(dsn,args,scope):
    manifest=json.loads(args.manifest.read_text())['clusters'][scope]['files']
    cutoffs={'119':['2026-07-28','2026-07-29','2026-07-30','2026-07-31'],
             '120':['2026-09-16','2026-09-17','2026-09-18','2026-09-19']}[scope]
    source='daily-'+scope
    doc=dict(version=1,clusters=[scope],sources={source:dict(cluster=scope,build='HashData Warehouse 3.13.13',
        timezone='UTC+08:00',declaration='Issue29-confirmed-local-master-files')},batches={})
    selected=[]
    for index,cutoff in enumerate(cutoffs):
        members=[f for f in manifest if (f['file'][5:15]<=cutoff if index==0 else f['file'][5:15]==cutoff)]
        assert members
        selected.extend(f['sha256'] for f in members)
        doc['batches'][scope+'-'+str(index)]=dict(source=source,files_confirmed_complete=True,
            dates=sorted({f['file'][5:15] for f in members}),files=[dict(path=str((args.root/scope/f['file']).resolve()),
                origin_key=scope+'/'+f['file'],closed_and_copied=True) for f in members])
    assert sorted(selected)==sorted(f['sha256'] for f in manifest)
    config=args.output/('import-'+scope+'.json');config.write_text(canonical(doc))
    training=args.output/('training-'+scope+'.json')
    training.write_text(canonical(dict(version=1,clusters=[scope],window=dict(days=30))))
    db=connect(dsn,'sql_apm')
    records=[]
    try:
        before=footprint(db,scope)
        for index in range(5 if scope=='119' else 4):
            command=[sys.executable,'-m','sql_apm']
            if index<4:
                command+=['full','--config',str(config),'--source',source,'--batch',scope+'-'+str(index),
                          '--training-config',str(training),'--workers',str(args.workers)]
                cutoff=cutoffs[index]
            else:
                command+=['rebuild','--cluster',scope,'--training-config',str(training),'--cutoff-date',cutoffs[-1]]
                cutoff=cutoffs[-1]
            log=args.output/(scope+'-task-'+str(index)+'.log')
            started=time.monotonic()
            with log.open('w') as out:
                child=subprocess.Popen(command,cwd=ROOT,env=dict(os.environ,SQL_APM_DSN=dsn),stdout=out,stderr=subprocess.STDOUT)
                with Memory(db,child.pid) as memory:
                    status=child.wait()
            assert status==0,'task_failed_'+scope+'_'+str(index)
            result=json.loads(log.read_text().splitlines()[-1])
            assert result['state']=='succeeded' and result['publication']['result']=='published'
            assert result['cutoff_date']==cutoff
            assert result['publication']['previous_build_id']==(records[-1]['result']['build']['build_id'] if records else None)
            if index<4:
                files=result['import']['files']
                members=doc['batches'][scope+'-'+str(index)]['files']
                assert len(files)==len(members) and all(f['state']=='succeeded' for f in files)
                expected={f['sha256'] for f in manifest if (f['file'][5:15]<=cutoff if index==0 else f['file'][5:15]==cutoff)}
                assert {f['file_id'] for f in files}=={'I:'+identity(source,h) for h in expected}
            with db,db.cursor() as cur:
                cur.execute('SELECT count(*) FROM import_attempt a WHERE scope_id=%s',(scope,));attempts=cur.fetchone()[0]
                cur.execute('SELECT kind,layer,row_count,group_count FROM mpp_build_layer_count WHERE build_id=%s ORDER BY kind,layer',(result['build']['build_id'],))
                layers=cur.fetchall()
                group_payload={}
                for table in ('mpp_build_group','mpp_build_observation_group','mpp_build_layer_count'):
                    cur.execute('SELECT count(*),coalesce(sum(pg_column_size(t)),0) FROM '+table+' t WHERE build_id=%s',
                                (result['build']['build_id'],))
                    count,payload=cur.fetchone();group_payload[table]=dict(rows=count,tuple_payload_bytes=int(payload))
            if index==4:assert attempts==records[-1]['import_attempts']
            after=footprint(db,scope)
            records.append(dict(result=result,seconds=round(time.monotonic()-started,3),memory=memory.report(),
                before=before,after=after,result_partition_growth_bytes=after['result_partition_bytes']-before['result_partition_bytes'],
                layers=layers,group_payload=group_payload,import_attempts=attempts))
            (args.output/(scope+'-report.json')).write_text(json.dumps(dict(tasks=records),default=str,indent=2))
            before=after
            print(canonical(dict(phase='full_task_verified',scope='scope:'+identity(scope),index=index,seconds=records[-1]['seconds'])),flush=True)
        assert len(version_status(db,scope,True)['versions'])==len(records)
        return records
    finally:db.close()


def final_oracle(dsn,scope,record,args):
    db=connect(dsn,'sql_apm');result=record['result'];build=result['build']['build_id'];snapshot=result['snapshot']
    try:
        evidence=dict(formal_reconciliation=reconcile(db,snapshot,build),formal_oracle=sample_oracle(db,build))
        with db,db.cursor() as cur:cur.execute('DROP TABLE acceptance_decisions')
        evidence['observation_oracle']=reconcile_and_oracle(db,snapshot,build)
        # Frozen #25 computation (coverage sink removed only) calculates all formal
        # rows under the exact same snapshot. No product arithmetic is replaced.
        legacy,sha=frozen_store();reference=legacy(dsn)
        try:
            with Task(reference.db,scope,'statistics'):
                expected=reference.calculate(scope,snapshot['input_id'],snapshot['config_id'])
            evidence['frozen_source_sha256']=sha
            evidence['all_formal_rows_equal']=rows_equal(db,expected['build_id'],build,
                ('mpp_statistic','mpp_build_group','mpp_build_timing_coverage'))
        finally:reference.close()
        (args.output/(scope+'-oracle.json')).write_text(json.dumps(evidence,default=str,indent=2))
        return evidence
    finally:db.close()


def main(args):
    if args.output.exists():raise ValueError('fresh_output_directory_required')
    args.output.mkdir(parents=True)
    report=dict(method='measured; private PG17; concurrent clusters; 1-second PSS sampling',
        memory_scope='per-command Python process tree; PostgreSQL process tree is shared by concurrent tasks',
        storage_scope='per-version net result partition growth includes table/index/TOAST; database samples include concurrent cluster activity',
        input_manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        local_120_days=7,production_120_30_days_measured=False,
        code_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'sql_apm').rglob('*.py'))+sorted((ROOT/'sql_apm/storage').glob('*.sql'))})
    started=time.monotonic()
    with instance(args.pg_bin) as (directory,env):
        v=Verification(args.pg_bin,directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        (args.output/'private-instance.json').write_text(json.dumps(dict(dsn=dsn)))
        db=connect(dsn,'sql_apm')
        report['initial_database_bytes']=footprint(db,'119')['database_bytes'];db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={scope:pool.submit(cluster,dsn,args,scope) for scope in ('119','120')}
            report['clusters']={scope:future.result() for scope,future in futures.items()}
        report['workflow_seconds']=round(time.monotonic()-started,3)
        db=connect(dsn,'sql_apm');report['workflow_database_bytes']=footprint(db,'119')['database_bytes'];db.close()
        report['oracles']={scope:final_oracle(dsn,scope,records[-1],args) for scope,records in report['clusters'].items()}
        db=connect(dsn,'sql_apm');report['final_database_bytes']=footprint(db,'119')['database_bytes'];db.close()
        report['total_seconds']=round(time.monotonic()-started,3)
        v.init('check');report['complete']=True
        (args.output/'report.json').write_text(json.dumps(report,default=str,indent=2))
    print(canonical(dict(complete=True)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,default=ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=4,choices=range(1,9))
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    main(parser.parse_args())
