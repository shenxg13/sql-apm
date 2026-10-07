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

from acceptance import command as measured_command, selected, verify_baseline

RESOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', str(RESOURCE_ROOT))).resolve()
MANIFEST = RESOURCE_ROOT / 'docs/reports/data/log-supplement-manifest-2026-09-28.json'
BASELINE = RESOURCE_ROOT / 'docs/reports/data/kylin-alma-baseline-2026-10-02.json'
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
    first_only = getattr(args, 'first_batch_119', False)
    batches = {'119': CUTOFFS['119'][:1]} if first_only else CUTOFFS
    for cluster, cutoffs in batches.items():
        files = manifest['clusters'][cluster]['files']
        if first_only:
            files = [file for file in files if file['file'][5:15] <= cutoffs[0]]
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
    print(json.dumps(dict(verified_files=len(hashes), batches=sum(map(len, batches.values())))))


def mpp_identifiers(cur):
    """Only versioned identifiers are exported, never source SQL identities."""
    identifiers = {}
    for table, column, expected_value in (
        ('scope', 'system_kind', 'mpp'), ('scope', 'profile', 'mpp-csv/1'),
        ('analysis', 'mapping_version', 'mpp-mapping/1'),
        ('analysis', 'parser_version', 'mpp-csv-reader/1'),
        ('mpp_fingerprint', 'profile', 'mpp-csv/1'),
        ('mpp_normalization', 'dictionary_rules_version', '1.0.2'),
        ('config_snapshot', 'profile', 'mpp-csv/1'), ('build', 'profile', 'mpp-csv/1')):
        cur.execute('SELECT DISTINCT ' + column + ' FROM ' + table + ' ORDER BY 1')
        values = [row[0] for row in cur]
        identifiers[table + '.' + column] = values
        if values != [expected_value]:
            raise ValueError('unexpected_mpp_identifier')
    return identifiers


def footprint(db, root):
    with db.cursor() as cur:
        cur.execute('SELECT pg_database_size(current_database())')
        size = cur.fetchone()[0]
    disk = shutil.disk_usage(root)
    return dict(database_bytes=size, filesystem_used_bytes=disk.used, filesystem_free_bytes=disk.free)


def attempt_counts(cur, cluster, allowed_interrupted):
    cur.execute('SELECT attempt_id,state FROM import_attempt WHERE scope_id=%s', (cluster,))
    rows = cur.fetchall()
    interrupted = {aid for aid, state in rows if state == 'interrupted'}
    if (interrupted != set(allowed_interrupted)
            or any(state not in ('succeeded', 'interrupted') for _, state in rows)):
        raise ValueError('unexpected_import_attempt_state')
    return dict(total=len(rows), succeeded=len(rows) - len(interrupted),
                interrupted=len(interrupted))


def verify_product(expected_commit=None):
    # New accepted implementations must name their immutable delivery commit.
    # Verify that package in full, while retaining the original golden counts.
    if expected_commit is not None:
        from verify_package import verify
        package = verify(ROOT, installed=True)
        if package['commit'] != expected_commit:
            raise ValueError('unexpected_program_commit')
        return package
    # Legacy commands still reject drift from the original Alma implementation.
    baseline = json.loads(BASELINE.read_text())
    release_path = ROOT / 'RELEASE.json'
    release_files = json.loads(release_path.read_text())['files'] if release_path.exists() else None
    for path, expected in baseline['provenance']['product_code_sha256'].items():
        # Non-runtime probes are deliberately omitted from the slim program.
        if release_files is not None and path.startswith('sql_apm/diagnostics/') and path not in release_files:
            continue
        if sha256(ROOT / path) != expected:
            raise ValueError('product_code_changed: ' + path)
    return dict(baseline_product_equal=True)


def run_task(args):
    import psycopg2
    baseline = json.loads(BASELINE.read_text())
    package = verify_product(args.program_commit)
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
            before_attempts = attempt_counts(cur, cluster, args.interrupted_attempt)
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
        events, resources = measured_command(ROOT, log, command[3:])
        payload = events[-1]
        selection = selected(events)
        selection_equal = None
        if args.selection_baseline:
            new_baseline = json.loads(args.selection_baseline.read_text())
            verify_baseline(json.loads((ROOT/'RELEASE.json').read_text()),new_baseline)
            selection_equal = selection == new_baseline['selection'][stem]
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
            after_attempts = attempt_counts(cur, cluster, args.interrupted_attempt)
            observed['import_attempts'] = after_attempts['total']
            cur.execute('SELECT stage_seconds FROM task WHERE task_id=%s', (payload['task_id'],))
            stages = cur.fetchone()[0]
            identifiers = mpp_identifiers(cur)
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
        comparable = dict(observed, import_attempts=after_attempts['succeeded'])
        baseline_equal = comparable == expected
        normalization_timeouts = sum(file.get('counts', {}).get('problem:fingerprint_normalization_timeout', 0)
                                     for file in payload.get('import', {}).get('files', []))
        passed = (baseline_equal and chain_ok and normalization_timeouts == 0
                  and (step < 4 or before_attempts == after_attempts)
                  and selection_equal is not False)
        record = dict(cluster=cluster, step=step, parser_workers=args.workers if step < 4 else None,
                      cutoff_date=payload['cutoff_date'], build_id=bid,
                      publication=payload['publication'], stages=stages,
                      seconds=round(time.monotonic() - started, 3), before=before,
                      after=footprint(db, args.data_root), observed=observed,
                      baseline_equal=baseline_equal, version_chain_verified=chain_ok,
                      import_attempt_audit=dict(before=before_attempts, after=after_attempts,
                                                allowed_interrupted=args.interrupted_attempt,
                                                baseline_successful=expected['import_attempts']),
                      normalization_timeouts=normalization_timeouts, selection=selection,
                      selection_baseline_equal=selection_equal, resources=resources,
                      identifiers=identifiers, identifiers_verified=True,
                      passed=passed, baseline_sha256=sha256(BASELINE), program_verification=package)
        save(args.records / (stem + '.json'), record)
        print(json.dumps(dict(cluster=cluster, step=step, passed=passed, seconds=record['seconds'],
                              normalization_timeouts=normalization_timeouts)))
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
    prep.add_argument('--first-batch-119', action='store_true',
                      help='Verify and prepare only the 26 files needed by Issue #34 P8')
    task = sub.add_parser('run')
    task.add_argument('--cluster', choices=CUTOFFS, required=True)
    task.add_argument('--step', type=int, choices=range(5), required=True)
    task.add_argument('--config', type=Path, required=True)
    task.add_argument('--records', type=Path, required=True)
    task.add_argument('--data-root', type=Path, required=True)
    task.add_argument('--workers', type=int, choices=range(1, 9), default=1)
    task.add_argument('--program-commit', help='Verify the delivered package at this exact commit; '
                      'without it require original Alma product hashes')
    task.add_argument('--selection-baseline', type=Path)
    task.add_argument('--interrupted-attempt', action='append', default=[],
                      help='Explicitly audited historical interruption ID; other non-success attempts fail')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args)
    elif args.cluster == '120' and args.step == 4:
        parser.error('120 has four baseline tasks')
    else:
        run_task(args)


if __name__ == '__main__':
    main()
