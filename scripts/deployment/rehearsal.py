#!/usr/bin/env python3
"""Prepare fixed inputs and record actual CLI tasks against the Alma baseline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'docs/reports/data/log-supplement-manifest-2026-09-28.json'
BASELINE = ROOT / 'docs/reports/data/kylin-alma-baseline-2026-10-02.json'
CUTOFFS = {'119': ['2026-07-28', '2026-07-29', '2026-07-30', '2026-07-31'],
           '120': ['2026-09-16', '2026-09-17', '2026-09-18', '2026-09-19']}


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write('\n')


def prepare(args):
    manifest = json.loads(MANIFEST.read_text())
    expected = json.loads(BASELINE.read_text())['provenance']['input_manifest_sha256']
    if sha256(MANIFEST) != expected:
        raise ValueError('input_manifest_changed')
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = []
    for cluster, cutoffs in CUTOFFS.items():
        files = manifest['clusters'][cluster]['files']
        for file in files:
            path = args.logs / cluster / file['file']
            if sha256(path) != file['sha256']:
                raise ValueError('source_checksum_mismatch: ' + cluster + '/' + file['file'])
            hashes.append(file['sha256'] + '  ' + cluster + '/' + file['file'])
        source = 'daily-' + cluster
        doc = dict(version=1, clusters=[cluster], sources={source: dict(
            cluster=cluster, build='HashData Warehouse 3.13.13', timezone='UTC+08:00',
            declaration='Issue29-confirmed-local-master-files')}, batches={})
        selected = []
        for index, cutoff in enumerate(cutoffs):
            members = [f for f in files if
                       (f['file'][5:15] <= cutoff if index == 0 else f['file'][5:15] == cutoff)]
            selected.extend(f['sha256'] for f in members)
            doc['batches'][cluster + '-' + str(index)] = dict(
                source=source, files_confirmed_complete=True,
                dates=sorted({f['file'][5:15] for f in members}), files=[dict(
                    path=str((args.logs / cluster / f['file']).resolve()),
                    origin_key=cluster + '/' + f['file'], closed_and_copied=True) for f in members])
        if sorted(selected) != sorted(f['sha256'] for f in files):
            raise ValueError('batch_partition_incomplete')
        save(args.output / ('import-' + cluster + '.json'), doc)
        save(args.output / ('training-' + cluster + '.json'),
             dict(version=1, clusters=[cluster], window=dict(days=30)))
    (args.output / 'logs.SHA256SUMS').write_text('\n'.join(hashes) + '\n')
    print(json.dumps(dict(verified_files=len(hashes), batches=8)))


def footprint(db, root):
    with db.cursor() as cur:
        cur.execute('SELECT pg_database_size(current_database())')
        size = cur.fetchone()[0]
    disk = shutil.disk_usage(root)
    return dict(database_bytes=size, filesystem_used_bytes=disk.used, filesystem_free_bytes=disk.free)


def run_task(args):
    import psycopg2
    baseline = json.loads(BASELINE.read_text())
    # Reject code drift before an expensive replay.
    for path, expected in baseline['provenance']['product_code_sha256'].items():
        if sha256(ROOT / path) != expected:
            raise ValueError('product_code_changed: ' + path)
    cluster, step = args.cluster, args.step
    expected = baseline['clusters'][cluster][step]
    args.records.mkdir(parents=True, exist_ok=True)
    stem = cluster + '-' + str(step)
    log = args.records / (stem + '.log')
    if log.exists():
        raise ValueError('fresh_step_record_required')
    db = psycopg2.connect(os.environ['SQL_APM_DSN'])
    db.autocommit = True
    try:
        with db.cursor() as cur:
            cur.execute('SET search_path=sql_apm,pg_catalog')
            cur.execute('SELECT count(*) FROM publication WHERE scope_id=%s AND result=\'published\'', (cluster,))
            if cur.fetchone()[0] != step:
                raise ValueError('unexpected_publication_sequence')
            cur.execute('SELECT count(*) FROM import_attempt WHERE scope_id=%s', (cluster,))
            before_attempts = cur.fetchone()[0]
        before = footprint(db, args.data_root)
        command = [sys.executable, '-m', 'sql_apm']
        training = str(args.config / ('training-' + cluster + '.json'))
        if step < 4:
            command += ['full', '--config', str(args.config / ('import-' + cluster + '.json')),
                        '--source', 'daily-' + cluster, '--batch', stem,
                        '--training-config', training, '--workers', str(args.workers)]
        else:
            command += ['rebuild', '--cluster', cluster, '--training-config', training,
                        '--cutoff-date', CUTOFFS[cluster][-1]]
        started = time.monotonic()
        with log.open('x') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError('task_failed; inspect protected step log')
        payload = json.loads(log.read_text().splitlines()[-1])
        bid = payload['build']['build_id']
        observed = dict(cutoff_date=payload['cutoff_date'], window_start=payload['window_start'],
                        window_end=payload['window_end'], checks=payload['checks'],
                        build={k: payload['build'][k] for k in expected['build']})
        if 'import' in expected:
            observed['import'] = {k: payload['import'][k] for k in expected['import']}
            # Parallel parser completion order is not a semantic difference.
            observed['import']['files'].sort(key=lambda f: f['file_id'])
            expected['import']['files'].sort(key=lambda f: f['file_id'])
        with db.cursor() as cur:
            cur.execute('SELECT kind,layer,row_count,group_count FROM mpp_build_layer_count '
                        'WHERE build_id=%s ORDER BY kind,layer', (bid,))
            observed['layers'] = [list(row) for row in cur.fetchall()]
            cur.execute('SELECT count(*) FROM import_attempt WHERE scope_id=%s', (cluster,))
            observed['import_attempts'] = cur.fetchone()[0]
            cur.execute('SELECT stage_seconds FROM task WHERE task_id=%s', (payload['task_id'],))
            stages = cur.fetchone()[0]
        queries = {}
        for action in ('status', 'history'):
            result = subprocess.run([sys.executable, '-m', 'sql_apm', action, '--cluster', cluster],
                                    cwd=ROOT, capture_output=True, text=True, check=True)
            queries[action] = json.loads(result.stdout)
            save(args.records / (stem + '-' + action + '.json'), queries[action])
        previous = (json.loads((args.records / (cluster + '-' + str(step - 1) + '.json')).read_text())
                    ['build_id'] if step else None)
        def version_matches(version):
            return (version['build_id'] == bid and version['result'] == 'published'
                    and version['cutoff_date'] == expected['cutoff_date']
                    and all(datetime.fromisoformat(version[key]) == datetime.fromisoformat(expected[key])
                            for key in ('window_start', 'window_end')))
        chain_ok = (payload['state'] == 'succeeded' and payload['publication']['result'] == 'published'
                    and payload['publication']['previous_build_id'] == previous
                    and version_matches(queries['status']['current'])
                    and version_matches(queries['history']['versions'][0])
                    and len(queries['history']['versions']) == step + 1)
        passed = observed == expected and chain_ok and (step < 4 or before_attempts == observed['import_attempts'])
        record = dict(cluster=cluster, step=step, cutoff_date=payload['cutoff_date'], build_id=bid,
                      publication=payload['publication'], stages=stages,
                      seconds=round(time.monotonic() - started, 3), before=before,
                      after=footprint(db, args.data_root), observed=observed,
                      baseline_equal=observed == expected, version_chain_verified=chain_ok,
                      passed=passed, baseline_sha256=sha256(BASELINE))
        save(args.records / (stem + '.json'), record)
        print(json.dumps(dict(cluster=cluster, step=step, passed=passed, seconds=record['seconds'])))
        if not passed:
            raise ValueError('baseline_or_version_check_failed; compare record with baseline')
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--logs', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    task = sub.add_parser('run')
    task.add_argument('--cluster', choices=CUTOFFS, required=True)
    task.add_argument('--step', type=int, choices=range(5), required=True)
    task.add_argument('--config', type=Path, required=True)
    task.add_argument('--records', type=Path, required=True)
    task.add_argument('--data-root', type=Path, required=True)
    task.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args)
    elif args.cluster == '120' and args.step == 4:
        parser.error('120 has four baseline tasks')
    else:
        run_task(args)


if __name__ == '__main__':
    main()
