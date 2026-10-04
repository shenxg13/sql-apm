#!/usr/bin/env python3
"""Explicit clean-instance 119 first-batch benchmark against the Alma baseline."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts/deployment')]
from verify import instance, Verification
import rehearsal


def first_batch(dsn, args, directory):
    """Reuse frozen business expectations while recording the candidate's code."""
    expected = json.loads(rehearsal.BASELINE.read_text())['clusters']['119'][0]
    record_dir = args.output / 'records'
    record_dir.mkdir()
    config = args.output / 'config'
    command = [sys.executable, '-m', 'sql_apm', 'full', '--config', str(config / 'import-119.json'),
               '--source', 'daily-119', '--batch', '119-0', '--training-config', str(config / 'training-119.json')]
    # Omit --workers: this exercises the actual product default of four.
    started = time.monotonic()
    with (record_dir / '119-0.log').open('x') as stream:
        result = subprocess.run(command, cwd=args.app_root, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise ValueError('first_batch_failed')
    payload = json.loads((record_dir / '119-0.log').read_text().splitlines()[-1])
    build = payload['build']['build_id']
    observed = {key: payload[key] for key in ('cutoff_date', 'window_start', 'window_end', 'checks')}
    observed['build'] = {key: payload['build'][key] for key in expected['build']}
    observed['import'] = {key: payload['import'][key] for key in expected['import']}
    observed['import']['files'].sort(key=lambda item: item['file_id'])
    expected['import']['files'].sort(key=lambda item: item['file_id'])
    import psycopg2
    with psycopg2.connect(dsn) as db, db.cursor() as cur:
        cur.execute('SET search_path=sql_apm,pg_catalog')
        cur.execute('SELECT kind,layer,row_count,group_count FROM mpp_build_layer_count '
                    'WHERE build_id=%s ORDER BY kind,layer', (build,))
        observed['layers'] = [list(row) for row in cur]
        cur.execute('SELECT count(*) FROM import_attempt')
        observed['import_attempts'] = cur.fetchone()[0]
        cur.execute('SELECT stage_seconds FROM task WHERE task_id=%s', (payload['task_id'],))
        stages = cur.fetchone()[0]
        size = rehearsal.footprint(db, directory)
    queries = {}
    for action in ('status', 'history'):
        query = subprocess.run([sys.executable, '-m', 'sql_apm', action, '--cluster', '119'],
                               cwd=args.app_root, capture_output=True, text=True, check=True)
        queries[action] = json.loads(query.stdout)
        rehearsal.save(record_dir / ('119-0-' + action + '.json'), queries[action])
    timeout_count = sum(file.get('counts', {}).get('problem:fingerprint_normalization_timeout', 0)
                        for file in payload['import']['files'])
    chain = (payload['state'] == 'succeeded' and payload['publication']['result'] == 'published'
             and payload['publication']['previous_build_id'] is None
             and queries['status']['current']['build_id'] == build
             and len(queries['history']['versions']) == 1
             and queries['history']['versions'][0]['build_id'] == build)
    report = dict(method='measured; product default four workers; clean PG17; 119 first batch',
                  baseline_sha256=rehearsal.sha256(rehearsal.BASELINE),
                  input_manifest_sha256=rehearsal.sha256(rehearsal.MANIFEST),
                  product_sha256={str(p.relative_to(args.app_root)): rehearsal.sha256(p)
                                  for p in sorted((args.app_root / 'sql_apm').rglob('*.py'))},
                  seconds=time.monotonic() - started, stages=stages, observed=observed, footprint=size,
                  baseline_equal=observed == expected, version_chain_verified=chain,
                  normalization_timeouts=timeout_count, parser_workers=4,
                  passed=observed == expected and chain and timeout_count == 0)
    rehearsal.save(record_dir / '119-0.json', report)
    print(json.dumps({key: report[key] for key in ('seconds', 'baseline_equal', 'normalization_timeouts', 'passed')}), flush=True)
    if not report['passed']:
        raise ValueError('baseline_comparison_failed')


def main(args):
    args.output = args.output.resolve()
    args.app_root = args.app_root.resolve()
    if args.output.exists():
        raise ValueError('fresh_output_required')
    args.output.mkdir(parents=True)
    rehearsal.ROOT = args.app_root.resolve()
    rehearsal.prepare(SimpleNamespace(logs=args.logs.resolve(), output=args.output / 'config'))
    with instance(args.pg_bin) as (directory, env):
        v = Verification(args.pg_bin, directory, env)
        v.init(root=args.app_root.resolve())
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        os.environ['SQL_APM_DSN'] = dsn
        started = time.monotonic()
        first_batch(dsn, args, directory)
        v.init('check', root=args.app_root.resolve())
        (args.output / 'verification.json').write_text(json.dumps(dict(
            method='measured; clean disposable PG17; four parser workers; 119 first batch only',
            seconds=time.monotonic() - started, schema_check=True), indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=ROOT)
    parser.add_argument('--logs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    main(parser.parse_args())
