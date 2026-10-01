#!/usr/bin/env python3
"""Explicit 1.5 private-instance evidence, exact coverage comparison, then upgrade."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import psycopg2
from psycopg2 import sql


def digest(db,build):
    result={}
    with db,db.cursor() as cur:
        for table in ('mpp_statistic','mpp_observation_statistic','mpp_build_group','mpp_build_observation_group','mpp_build_timing_coverage'):
            cur.execute(sql.SQL('''WITH hashed AS MATERIALIZED (
                SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {} t WHERE build_id=%s)
                SELECT count(*),sum(('x'||substr(h,1,16))::bit(64)::bigint),sum(('x'||substr(h,17,16))::bit(64)::bigint),
                    sum(('x'||substr(h,33,16))::bit(64)::bigint),sum(('x'||substr(h,49,16))::bit(64)::bigint) FROM hashed''').format(sql.Identifier(table)),(build,))
            row=cur.fetchone();result[table]=dict(rows=row[0],sha256_limb_sums=[str(x) for x in row[1:]])
    return result


def main(args):
    if args.output.exists():raise ValueError('fresh_output_file_required')
    legacy=json.loads(args.legacy.read_text());dsn=legacy['dsn']
    db=psycopg2.connect(dsn)
    params=db.get_dsn_parameters()
    host=Path(params['host'])
    if host.name!='socket' or host.parent.parent!=Path('/tmp') or not host.parent.name.startswith('sql-apm-pg-'):
        db.close();raise ValueError('private_test_instance_required')
    with db,db.cursor() as cur:
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
        cur.execute("SELECT max(version) FROM schema_version");assert cur.fetchone()[0]=='1.5.0'
    report=dict(method='measured; full saved results; exact bidirectional coverage equality before destructive migration',
                baseline_sha=legacy['baseline_sha'],clusters={})
    with db,db.cursor() as cur:
        cur.execute('SELECT pg_database_size(current_database())')
        report['database_bytes_before']=cur.fetchone()[0]
        cur.execute("SELECT coalesce(sum(pg_total_relation_size(relid)),0) FROM pg_partition_tree('mpp_build_coverage') WHERE isleaf")
        report['coverage_relation_bytes_before']=int(cur.fetchone()[0])
    started=time.monotonic()
    # Install just the pure helper, then remove it to restore the frozen catalog.
    schema=(ROOT/'sql_apm/storage/schema.sql').read_text()
    function=schema[schema.index('CREATE OR REPLACE FUNCTION mpp_coverage('):]
    with db,db.cursor() as cur:cur.execute(function)
    for scope,item in legacy['builds'].items():
        build=item['result']['build_id'];entry=report['clusters'][scope]={}
        entry['before']=digest(db,build)
        with db,db.cursor() as cur:
            cur.execute('CREATE TEMP TABLE derived_coverage ON COMMIT DROP AS SELECT * FROM mpp_coverage(%s)',(build,))
            cur.execute('''SELECT NOT EXISTS ((SELECT group_id,layer,computed_keys,empty_keys FROM mpp_build_coverage WHERE build_id=%s
                EXCEPT ALL SELECT * FROM derived_coverage) UNION ALL (SELECT * FROM derived_coverage
                EXCEPT ALL SELECT group_id,layer,computed_keys,empty_keys FROM mpp_build_coverage WHERE build_id=%s))''',(build,build))
            assert cur.fetchone()[0],'coverage_diff'
            cur.execute('SELECT count(*) FROM derived_coverage');entry['equal_coverage_rows']=cur.fetchone()[0]
        print(json.dumps(dict(phase='coverage_equal',scope=scope,rows=entry['equal_coverage_rows'])),flush=True)
    with db,db.cursor() as cur:cur.execute('DROP FUNCTION mpp_coverage(text,boolean,text)')
    params=db.get_dsn_parameters();db.close()
    command=[str(ROOT/'scripts/db/initialize.sh'),'upgrade','--host',params['host'],'--port',params['port'],
        '--pg-bin',str(args.pg_bin)]
    subprocess.run(command,stdout=subprocess.DEVNULL,check=True,timeout=1800)
    db=psycopg2.connect(dsn)
    with db,db.cursor() as cur:cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
    for scope,item in legacy['builds'].items():
        entry=report['clusters'][scope]
        entry['after']=digest(db,item['result']['build_id'])
        assert entry['before']==entry['after'],'result_changed_on_upgrade'
    with db,db.cursor() as cur:
        cur.execute("SELECT max(version),to_regclass('mpp_build_coverage') IS NULL FROM schema_version")
        assert cur.fetchone()==('1.6.0',True)
        cur.execute('SELECT pg_database_size(current_database())')
        report['database_bytes_after']=cur.fetchone()[0]
        cur.execute("SELECT pg_total_relation_size('mpp_build_layer_count')")
        report['layer_count_relation_bytes_after']=cur.fetchone()[0]
    db.close()
    for mode in ('all','check','upgrade'):
        repeat=command[:];repeat[1]=mode
        if mode=='all':repeat+=['--admin-user','apm_test_admin','--admin-database','postgres']
        subprocess.run(repeat,stdout=subprocess.DEVNULL,check=True,timeout=120)
    report.update(seconds=round(time.monotonic()-started,3),complete=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(complete=True)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--legacy',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    main(p.parse_args())
