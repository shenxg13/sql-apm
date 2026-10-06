#!/usr/bin/env python3
"""Bounded, synthetic, private PG17 query-cost evidence; no existing DB connection."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import psycopg2
from psycopg2.extras import Json
from verify import instance, Verification
from database.statistics_batch import BATCH_SQL
from sql_apm.baseline.statistics import calculate_group
from sql_apm.storage.statistics import ResultWriter, STAT_COLUMNS
from sql_apm.training.config import THRESHOLDS, TZ

BUILD = 'synthetic-build'
PARAMS = {'build_id': BUILD}
FULL_FROM = '''FROM mpp_statistic s JOIN build b USING(build_id)
    JOIN config_snapshot c USING(config_id)
    CROSS JOIN LATERAL jsonb_to_record(mpp_statistic_sufficiency(
        c.statistics_version,c.thresholds,s.layer,s.included_count,
        s.active_dates,s.active_week_starts)) q(basic jsonb,p95 jsonb,p99 jsonb)'''
FULL_AGGREGATE = '''SELECT s.layer,count(*),
    count(*) FILTER (WHERE (q.basic->>'met')::boolean),
    count(*) FILTER (WHERE (q.p95->>'met')::boolean),
    count(*) FILTER (WHERE (q.p99->>'met')::boolean) ''' + FULL_FROM + '''
    WHERE s.build_id=%(build_id)s GROUP BY 1 ORDER BY 1'''
BATCH_AGGREGATE = '''SELECT layer,count(*),count(*) FILTER (WHERE basic_met),
    count(*) FILTER (WHERE p95_met),count(*) FILTER (WHERE p99_met)
    FROM (''' + BATCH_SQL + ''') result GROUP BY 1 ORDER BY 1'''
SINGLE = '''SELECT s.layer,s.bucket_date,s.bucket_number,mpp_statistic_sufficiency(
    c.statistics_version,c.thresholds,s.layer,s.included_count,s.active_dates,s.active_week_starts)
    FROM mpp_statistic s JOIN build b USING(build_id) JOIN config_snapshot c USING(config_id)
    WHERE s.build_id=%(build_id)s AND s.group_id=%(group_id)s'''


def timed(cur, query, params):
    start = time.perf_counter()
    cur.execute(query, params)
    result = cur.fetchall()
    return (time.perf_counter() - start) * 1000, result


def benchmark(groups, repetitions):
    rng = random.Random(20260930)
    rng.getrandbits(128)  # Align the synthetic distribution with the R2 probe seed.
    start = datetime(2026,8,21,tzinfo=TZ)
    end, data_start = start+timedelta(days=30), datetime(2026,9,13,tzinfo=TZ)
    with instance(Path('/usr/pgsql-17/bin')) as (directory, env):
        v = Verification(Path('/usr/pgsql-17/bin'), directory, env)
        v.init()
        with closing(psycopg2.connect(host=str(directory/'socket'),port=55473,dbname='sql_apm',
                              user='sql_apm',options='-csearch_path=sql_apm,pg_catalog')) as db:
            # A single temporary leaf-shaped relation uses the product columns,
            # checks and indexes; no partition traversal, WAL or importer is measured.
            with db.cursor() as cur:
                cur.execute('''CREATE TEMP TABLE mpp_statistic
                        (LIKE sql_apm.mpp_statistic INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
                    CREATE UNIQUE INDEX ON mpp_statistic
                        (partition_id,build_id,group_id,layer,bucket_date,bucket_number) NULLS NOT DISTINCT;
                    CREATE INDEX ON mpp_statistic (group_id,build_id,layer);
                    CREATE TEMP TABLE build (build_id text PRIMARY KEY,partition_id bigint,config_id text,
                        results_saved boolean DEFAULT false);
                    CREATE TEMP TABLE config_snapshot (config_id text PRIMARY KEY,statistics_version text,thresholds jsonb)''')
                cur.execute('INSERT INTO build VALUES (%s,1,%s)',(BUILD,'config'))
                cur.execute('INSERT INTO config_snapshot VALUES (%s,%s,%s)',('config','baseline-formulas/1',Json(THRESHOLDS)))
            writer = ResultWriter(db,'mpp_statistic',STAT_COLUMNS)
            gids = []
            for group in range(groups):
                gid = 'G:'+hashlib.sha256(str(group).encode()).hexdigest()
                gids.append(gid)
                n = min(int(rng.expovariate(1/5.1))+1,3000)
                excluded = rng.random() < .2
                events = [(data_start+timedelta(seconds=rng.uniform(0,7*86400)),
                           Decimal(str(round(rng.lognormvariate(3,1.5),3))),not excluded,
                           ['blacklist_category'] if excluded else []) for _ in range(n)]
                if group % 5000 == 0:
                    events += [(data_start+timedelta(seconds=rng.uniform(0,7*86400)),Decimal('12.5'),True,[])
                               for _ in range(20000)]
                computed, _ = calculate_group(sorted(events,key=lambda r:r[0]),start,end)
                for row in computed:
                    writer.add(dict(row,build_id=BUILD,partition_id=1,group_id=gid))
                if (group+1) % 10000 == 0:
                    print('PREPARED groups:',group+1,flush=True)
            writer.flush()
            db.commit()
            db.autocommit = True
            with db.cursor() as cur:
                for table in ('mpp_statistic','build','config_snapshot'):
                    cur.execute('VACUUM ANALYZE '+table)
                cur.execute('SELECT count(*) FROM mpp_statistic')
                rows = cur.fetchone()[0]
                # Compare every natural key, not only aggregate counts.
                cur.execute('CREATE TEMP TABLE batch_result AS '+BATCH_SQL,PARAMS)
                cur.execute('SELECT count(*) FROM batch_result')
                assert cur.fetchone()[0] == rows
                cur.execute('''SELECT count(*),count(*) FILTER (WHERE
                    (q.basic->>'met')::boolean IS DISTINCT FROM r.basic_met OR
                    (q.p95->>'met')::boolean IS DISTINCT FROM r.p95_met OR
                    (q.p99->>'met')::boolean IS DISTINCT FROM r.p99_met) '''+FULL_FROM+'''
                    JOIN batch_result r ON (r.partition_id,r.build_id,r.group_id,r.layer)=
                        (s.partition_id,s.build_id,s.group_id,s.layer)
                        AND r.bucket_date IS NOT DISTINCT FROM s.bucket_date
                        AND r.bucket_number IS NOT DISTINCT FROM s.bucket_number
                    WHERE s.build_id=%(build_id)s''',PARAMS)
                compared, mismatches = cur.fetchone()
                assert compared == rows and mismatches == 0
                print('PARITY rows:',rows,'mismatches:',mismatches,flush=True)
                # Warm both paths, alternate measurement order, retain all samples.
                _, full = timed(cur,FULL_AGGREGATE,PARAMS)
                _, batch = timed(cur,BATCH_AGGREGATE,PARAMS)
                assert full == batch
                samples = {'full': [], 'batch': []}
                queries = {'full': FULL_AGGREGATE, 'batch': BATCH_AGGREGATE}
                for run in range(repetitions):
                    for name in (('full','batch') if run % 2 == 0 else ('batch','full')):
                        elapsed, result = timed(cur,queries[name],PARAMS)
                        assert result == full
                        samples[name].append(round(elapsed,3))
                        print('MEASURED',name,'ms:',round(elapsed,3),flush=True)
                selected = rng.sample(gids,min(2000,groups))
                single = []
                for run in range(repetitions+1):
                    elapsed = sum(timed(cur,SINGLE,dict(PARAMS,group_id=gid))[0] for gid in selected)/len(selected)
                    if run:
                        single.append(round(elapsed,6))
                cur.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+BATCH_AGGREGATE,PARAMS)
                plan = cur.fetchone()[0][0]
                cur.execute("SELECT version(),current_setting('jit'),current_setting('work_mem'),current_setting('max_parallel_workers_per_gather')")
                server, jit, work_mem, parallel = cur.fetchone()
                report = dict(measured_at=datetime.now(timezone.utc).isoformat(),groups=groups,statistic_rows=rows,
                    seed=20260930,repetitions=repetitions,rowwise_compared=compared,rowwise_mismatches=mismatches,
                    distribution=full,aggregate_ms=samples,aggregate_median_ms={k:statistics.median(v) for k,v in samples.items()},
                    single_group_sample_size=len(selected),single_group_mean_ms_per_run=single,
                    single_group_median_mean_ms=statistics.median(single),batch_plan=plan,
                    environment=dict(python=sys.version,postgresql=server,jit=jit,work_mem=work_mem,
                                     max_parallel_workers_per_gather=parallel),
                    boundary='measured synthetic hot-cache single private temporary leaf; client execute/fetch included; preparation, WAL, partition traversal and concurrency excluded')
    report['source_head_sha'] = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    report['code_sha256'] = {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
        'sql_apm/storage/statistics_sufficiency.sql','sql_apm/storage/schema.sql',
        'sql_apm/baseline/statistics.py','sql_apm/storage/statistics.py',
        'tests/database/statistics_batch.py','scripts/db/benchmark_statistics_sufficiency.py')}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups',type=int,default=60000)
    parser.add_argument('--repetitions',type=int,default=5)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if not 1 <= args.groups <= 60000 or not 1 <= args.repetitions <= 10:
        parser.error('groups must be 1..60000; repetitions must be 1..10')
    # Reserve a new evidence file; never overwrite an existing report.
    with args.output.open('x') as output:
        json.dump(benchmark(args.groups,args.repetitions),output,indent=2,sort_keys=True)
        output.write('\n')
