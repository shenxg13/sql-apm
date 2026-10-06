#!/usr/bin/env python3
"""Explicit real-log database copy: nine-version preparation, upgrade, retention."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import psycopg2
from psycopg2 import sql
from verify import instance,Verification
from verify_window_full import command
from database.retention import contents,digest
from sql_apm.storage.cleanup import CleanupStore,preview
from sql_apm.storage.publication import version_status


def save(path,value):
    path.write_text(json.dumps(value,default=str,indent=2)+'\n')


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def prepare(args):
    """Build the frozen 1.8 source, then dump it for an independent restore."""
    manifest=ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json'
    files=json.loads(manifest.read_text())['clusters']
    for scope,detail in files.items():
        for f in detail['files']:assert sha(args.logs/scope/f['file'])==f['sha256']
    args.app_root=args.reference.resolve()
    assert (args.app_root/'sql_apm/storage/schema.sql').read_bytes()==(ROOT/'sql_apm/storage/versions/1.8.0.sql').read_bytes()
    with instance(args.pg_bin,parent=args.output,configuration='shared_buffers=512MB\nwork_mem=64MB\nmax_wal_size=8GB\n') as (directory,env):
        v=Verification(args.pg_bin,directory,env);v.init(root=args.app_root)
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        def cluster(scope):
            cutoffs={'119':['2026-07-28','2026-07-29','2026-07-30','2026-07-31'],
                     '120':['2026-09-16','2026-09-17','2026-09-18','2026-09-19']}[scope]
            source='daily-'+scope
            doc=dict(version=1,clusters=[scope],sources={source:dict(cluster=scope,build='HashData Warehouse 3.13.13',
                timezone='UTC+08:00',declaration='Issue43-fixed-files')},batches={})
            for i,cutoff in enumerate(cutoffs):
                selected=[f for f in files[scope]['files'] if (f['file'][5:15]<=cutoff if i==0 else f['file'][5:15]==cutoff)]
                doc['batches'][scope+'-'+str(i)]=dict(source=source,files_confirmed_complete=True,
                    dates=sorted({f['file'][5:15] for f in selected}),files=[dict(path=str((args.logs/scope/f['file']).resolve()),
                        origin_key=scope+'/'+f['file'],closed_and_copied=True) for f in selected])
            cfg=args.output/('import-'+scope+'.json');save(cfg,doc)
            training=args.output/('training-'+scope+'.json');save(training,dict(version=1,clusters=[scope],window=dict(days=30)))
            records=[]
            for i in range(5 if scope=='119' else 4):
                words=(['full','--config',str(cfg),'--source',source,'--batch',scope+'-'+str(i),'--workers',str(args.workers)]
                       if i<4 else ['rebuild','--cluster',scope,'--cutoff-date',cutoffs[-1]])
                record=command(args,dsn,scope+'-task-'+str(i),words+['--training-config',str(training)])
                assert record['result']['publication']['result']=='published'
                records.append(record);save(args.output/(scope+'-report.json'),dict(tasks=records))
                print(json.dumps(dict(phase='source_task',cluster=scope,index=i,seconds=record['seconds'])),flush=True)
            return len(records)
        with ThreadPoolExecutor(2) as pool:tasks=list(pool.map(cluster,('119','120')))
        v.init('check',root=args.app_root)
        dump=args.output/'baseline-180.dump'
        subprocess.run([str(args.pg_bin/'pg_dump'),'-Fc','--no-owner','--no-acl','-d',dsn,'-f',str(dump)],check=True)
        save(args.output/'complete.json',dict(manifest_sha256=sha(manifest),tasks=tasks,dump_bytes=dump.stat().st_size))


def move_current(db,scope,month):
    """Internal fixture only: keep all nine version identities, move one month.

    Original metrics remain byte-for-byte equivalent. Only that build's physical
    partition and construction timestamps change, before preservation baselines.
    """
    tables=('mpp_build_group','mpp_build_observation_group','mpp_statistic','mpp_observation_statistic')
    with db,db.cursor() as cur:
        cur.execute('SELECT build_id FROM current_version WHERE scope_id=%s',(scope,));build=cur.fetchone()[0]
    before={t:digest(db,t,('partition_id',),where='WHERE build_id=%s',params=(build,)) for t in tables}
    with db,db.cursor() as cur:
        cur.execute('SELECT mpp_ensure_result_partition(%s,%s)',(scope,month));pid=cur.fetchone()[0]
        for table in tables:
            cur.execute(sql.SQL('CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT * FROM {} WHERE build_id=%s').format(
                sql.Identifier('move_'+table),sql.Identifier(table)),(build,))
        for table in reversed(tables):
            cur.execute(sql.SQL('DELETE FROM {} WHERE build_id=%s').format(sql.Identifier(table)),(build,))
        cur.execute('''UPDATE build SET partition_id=%s,
            finished_at=(%s::date::timestamp AT TIME ZONE 'Asia/Shanghai')+(finished_at-started_at),
            started_at=%s::date::timestamp AT TIME ZONE 'Asia/Shanghai' WHERE build_id=%s''', (pid,month,month,build))
        for table in tables:
            cur.execute(sql.SQL('UPDATE {} SET partition_id=%s').format(sql.Identifier('move_'+table)),(pid,))
            cur.execute(sql.SQL('INSERT INTO {} SELECT * FROM {}').format(sql.Identifier(table),sql.Identifier('move_'+table)))
    after={t:digest(db,t,('partition_id',),where='WHERE build_id=%s',params=(build,)) for t in tables}
    assert before==after
    return dict(build_id=build,build_month=month,partition_id=pid,result_rows=after)


def verify(args):
    report=dict(evidence='measured',schema_before='1.8.0',schema_after='1.9.0',contract_version='1.0.0',
        source=json.loads((args.dump.parent/'complete.json').read_text()),
        source_dump_sha256=sha(args.dump),preservation={},python=sys.version,
        code_sha256={str(p.relative_to(ROOT)):sha(p) for p in [
            ROOT/'sql_apm/storage/schema.sql',ROOT/'sql_apm/storage/cleanup.py',ROOT/'sql_apm/baseline/retention.py',
            ROOT/'sql_apm/storage/migrations/1.8.0-to-1.9.0.sql',
            ROOT/'scripts/db/verify_cleanup_full.py',ROOT/'tests/database/retention.py']})
    started=time.monotonic()
    with instance(args.pg_bin,parent=args.output,configuration='shared_buffers=512MB\nwork_mem=64MB\nmax_wal_size=8GB\n') as (directory,env):
        v=Verification(args.pg_bin,directory,env);v.init('bootstrap')
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        with (args.output/'restore.log').open('w') as log:
            subprocess.run([str(args.pg_bin/'pg_restore'),'--exit-on-error','--no-owner','--no-acl','-j','4','-d',dsn,str(args.dump)],
                check=True,stdout=log,stderr=subprocess.STDOUT)
        with closing(psycopg2.connect(dsn)) as db:
            with db,db.cursor() as cur:
                cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
                cur.execute('SHOW server_version');report['postgresql']=cur.fetchone()[0]
            before=contents(db)
            receipts=digest(db,'schema_version')
            print('UPGRADE: source hashes collected',flush=True)
            tick=time.monotonic();v.init('upgrade');v.init('check');report['upgrade_seconds']=time.monotonic()-tick
            after=contents(db);assert before==after
            assert digest(db,'schema_version',where="WHERE version='1.8.0'")==receipts
            report['preservation']['upgrade']=before
            with db,db.cursor() as cur:
                cur.execute('SELECT bool_and(cleaned_at IS NULL AND groups_cleaned_at IS NULL) FROM mpp_result_partition');assert cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM publication WHERE result='published'");assert cur.fetchone()[0]==9
                cur.execute('SELECT max(build_month) FROM mpp_result_partition');month=cur.fetchone()[0]
            print('UPGRADE: every pre-existing table hash unchanged',flush=True)
            # Existing versions were all constructed in one month. Move just
            # 120's current result to the first retained month; 119 stays protected.
            new_month=date(month.year+1,month.month,1)
            reference=date(new_month.year,new_month.month,1)
            report['fixture']=move_current(db,'120',new_month)
            save(args.output/'report-progress.json',report)
            current={s:version_status(db,s)['current'] for s in ('119','120')}
            preserved=contents(db,cleanup=True)
            previews={s:preview(db,s,2,reference_month=reference) for s in ('119','120')}
            eligible=[p['partition_id'] for data in previews.values() for p in data['months'] if p['state']=='expired']
            result_tables=('mpp_statistic','mpp_observation_statistic','mpp_build_group','mpp_build_observation_group')
            untouched={t:digest(db,t,where='WHERE NOT partition_id=ANY(%s)',params=(eligible,)) for t in result_tables}
            report['preview']=previews
            report['runs']={}
            for scope in ('119','120'):
                tick=time.monotonic();result=CleanupStore(db).execute(scope,2,reference_month=reference)
                assert result['state']=='succeeded',result
                report['runs'][scope]=dict(seconds=time.monotonic()-tick,result=result)
                save(args.output/'report-progress.json',report)
                print(json.dumps(dict(phase='cleanup_complete',cluster=scope,seconds=report['runs'][scope]['seconds'])),flush=True)
            assert contents(db,cleanup=True)==preserved
            assert {t:digest(db,t,where='WHERE NOT partition_id=ANY(%s)',params=(eligible,)) for t in result_tables}==untouched
            assert {s:version_status(db,s)['current'] for s in ('119','120')}==current
            for table in result_tables:
                with db,db.cursor() as cur:
                    cur.execute(sql.SQL('SELECT count(*) FROM {} WHERE partition_id=ANY(%s)').format(sql.Identifier(table)),(eligible,))
                    assert cur.fetchone()[0]==0
            report['preservation']['cleanup']=preserved
            report['preservation']['untouched_results']=untouched
            report['after']={s:preview(db,s,2,reference_month=reference) for s in ('119','120')}
            report['history']={s:version_status(db,s,True) for s in ('119','120')}
            v.init('check')
            report.update(complete=True,total_seconds=time.monotonic()-started)
            save(args.output/'report.json',report)
    print('PASS: real database upgrade and retention; all preservation hashes equal',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','verify'))
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--logs',type=Path)
    parser.add_argument('--dump',type=Path)
    parser.add_argument('--workers',type=int,default=4,choices=range(1,9))
    args=parser.parse_args();args.output=args.output.resolve()
    if args.action=='prepare' and (args.reference is None or args.logs is None):parser.error('prepare requires --reference and --logs')
    if args.action=='verify' and args.dump is None:parser.error('verify requires --dump')
    args.output.mkdir(parents=True,exist_ok=False)
    (prepare if args.action=='prepare' else verify)(args)
