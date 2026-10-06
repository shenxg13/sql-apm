#!/usr/bin/env python3
"""Issue #35: nine-task and daily-batch replays against an explicit checkout."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from verify import instance, Verification
from verify_mpp_naming_full import digest, save, export
from sql_apm.ingestion.mpp.reader import Records
from sql_apm.training.config import validate
import psycopg2


def command(args, dsn, label, words):
    log = args.output / (label + '.log')
    started = time.monotonic()
    with log.open('w') as stream:
        status = subprocess.run([sys.executable, '-m', 'sql_apm'] + words,
            cwd=args.app_root, env=dict(os.environ, SQL_APM_DSN=dsn, PYTHONPATH=str(args.app_root)),
            stdout=stream, stderr=subprocess.STDOUT).returncode
    assert status == 0, 'command_failed_' + label
    events = [json.loads(line) for line in log.read_text().splitlines()]
    result = events[-1]
    assert result['state'] in ('succeeded', 'complete'), 'incomplete_' + label
    return dict(result=result, seconds=round(time.monotonic()-started, 3),
                progress=[e for e in events[:-1] if e.get('phase') in
                          ('snapshot_finished', 'decisions_derived')])


def counts(db):
    with db, db.cursor() as cur:
        result = {}
        for table in ('evidence_record', 'mpp_occurrence', 'mpp_sql_text'):
            cur.execute('SELECT count(*) FROM ' + table)
            result[table] = cur.fetchone()[0]
        return result


def selection(db, result):
    with db, db.cursor() as cur:
        cur.execute('SELECT batch_id FROM input_batch WHERE input_id=%s ORDER BY batch_id',
                    (result['snapshot']['input_id'],))
        return [r[0] for r in cur]


def excluded_decisions(db, result, excluded, candidate):
    """Use the unchanged decision function; count every omitted state/reason."""
    frozen = result['snapshot']
    with db, db.cursor() as cur:
        cur.execute('''SELECT o.analysis_id,o.occurrence_id FROM mpp_occurrence o
            JOIN evidence_record e ON e.record_id=o.anchor_ref
            JOIN batch_entry b USING(file_id) WHERE b.batch_id=ANY(%s)
            ORDER BY o.analysis_id,o.occurrence_id LIMIT 1''', (excluded,))
        example = cur.fetchone()
        assert example is not None
        cur.execute('SELECT count(*) FROM mpp_training_decisions(%s,%s,%s,%s)',
                    (frozen['input_id'], frozen['config_id'], *example))
        returned = cur.fetchone()[0]
        assert returned == (0 if candidate else 1)
        if candidate:
            return dict(example_decisions=returned)
        cur.execute('''CREATE TEMP TABLE omitted_decisions ON COMMIT DROP AS
            SELECT d.* FROM mpp_training_decisions(%s,%s) d
            JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
            JOIN evidence_record e ON e.record_id=o.anchor_ref
            WHERE e.file_id IN (SELECT file_id FROM batch_entry WHERE batch_id=ANY(%s))''',
            (frozen['input_id'], frozen['config_id'], excluded))
        report = dict(example_decisions=returned)
        for name, sql in {
            'states': 'SELECT state,count(*) FROM omitted_decisions GROUP BY 1 ORDER BY 1',
            'count_scopes': 'SELECT count_scope,state,count(*) FROM omitted_decisions GROUP BY 1,2 ORDER BY 1,2',
            'reasons': '''SELECT count_scope,reason,count(*) FROM omitted_decisions
                CROSS JOIN LATERAL unnest(reason_codes) reason GROUP BY 1,2 ORDER BY 1,2''',
        }.items():
            cur.execute(sql); report[name] = cur.fetchall()
        return report


def record_build(args, db, record, scope):
    result = record['result']
    assert result['publication']['result'] == 'published'
    record['selected'] = selection(db, result)
    record['equivalence'], _ = export(db, result, scope)
    record['read_events'] = sum(r[-1] for r in result['build']['states'])
    return record


def scenario(args, manifest, bounds, daily, expected_outside=55808):
    records = {}
    with instance(args.pg_bin) as (directory, env):
        v = Verification(args.pg_bin, directory, env); v.init(root=args.app_root)
        dsn = 'host=' + str(directory/'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        with closing(psycopg2.connect(dsn)) as db:
            with db.cursor() as cur:
                cur.execute('SET search_path=sql_apm,pg_catalog')
                cur.execute("SET TIME ZONE 'Asia/Shanghai'")
            db.commit()
            for scope in (['119'] if daily else ['119', '120']):
                files = manifest['clusters'][scope]['files']
                dates = sorted({f['file'][5:15] for f in files})
                cutoffs = dates if daily else dict(
                    **{'119': ['2026-07-28','2026-07-29','2026-07-30','2026-07-31'],
                       '120': ['2026-09-16','2026-09-17','2026-09-18','2026-09-19']})[scope]
                source = 'window-' + scope
                label = ('daily-' if daily else 'nine-') + scope
                doc = dict(version=1, clusters=[scope], sources={source:dict(cluster=scope,
                    build='HashData Warehouse 3.13.13', timezone='UTC+08:00', declaration='Issue35-fixed-files')}, batches={})
                for i, cutoff in enumerate(cutoffs):
                    members = [f for f in files if
                        (f['file'][5:15] <= cutoff if i == 0 and not daily else f['file'][5:15] == cutoff)]
                    assert members
                    doc['batches'][label + '-' + str(i).zfill(2)] = dict(source=source,
                        files_confirmed_complete=True, dates=sorted({f['file'][5:15] for f in members}),
                        files=[dict(path=str((args.logs/scope/f['file']).resolve()),
                                    origin_key=scope+'/'+f['file'], closed_and_copied=True) for f in members])
                config = args.output / (label+'-import.json'); save(config, doc)
                training = args.output / (label+'-training.json')
                save(training, dict(version=1, clusters=[scope], window=dict(days=7 if daily else 30)))
                for i, batch in enumerate(doc['batches']):
                    words = ['import' if daily else 'full', '--config', str(config), '--source', source,
                             '--batch', batch, '--workers', str(args.workers)]
                    if not daily:
                        words += ['--training-config', str(training)]
                    record = command(args, dsn, batch, words)
                    if not daily:
                        record_build(args, db, record, scope)
                        assert len(record['selected']) == i+1
                        if scope == '119' and i == 3:
                            outside = sum(n for _, reason, n in record['result']['build']['reasons'] if reason == 'outside_window')
                            assert outside == expected_outside
                            record['outside_window'] = outside
                        records[batch] = record
                        save(args.output/(label+'-records.json'), records)
                    print(json.dumps(dict(phase='task_verified',task=batch,seconds=record['seconds'])), flush=True)
                if daily or scope == '119':
                    before = counts(db)
                    record = command(args, dsn, label+'-rebuild', ['rebuild', '--cluster', scope,
                        '--training-config', str(training), '--cutoff-date', cutoffs[-1]])
                    after = counts(db); assert before == after
                    record_build(args, db, record, scope)
                    record['storage_before'] = before; record['storage_after'] = after
                    if daily:
                        config_values = validate(dict(version=1, clusters=[scope],
                            window=dict(days=7, cutoff_date=cutoffs[-1])), scope)
                        start = config_values['window_start']
                        excluded = [b for b, details in doc['batches'].items() if all(
                            bounds[Path(f['path']).name]['last'] is not None and
                            bounds[Path(f['path']).name]['last'] < start for f in details['files'])]
                        assert excluded
                        expected = sorted(set(doc['batches']) - set(excluded)) if args.candidate else sorted(doc['batches'])
                        assert record['selected'] == expected
                        record['excluded_batches'] = excluded
                        record['omitted'] = excluded_decisions(db, record['result'], excluded, args.candidate)
                    records[label+'-rebuild'] = record
                    print(json.dumps(dict(phase='rebuild_verified',task=label,seconds=record['seconds'])), flush=True)
                if args.candidate:
                    with db, db.cursor() as cur:
                        cur.execute('SELECT checksum_value,first_log_at,last_log_at FROM source_file WHERE scope_id=%s', (scope,))
                        by_sha = {f['sha256']:bounds[f['file']] for f in files}
                        for sha, first, last in cur:
                            assert dict(first=first.isoformat() if first else None,
                                        last=last.isoformat() if last else None) == by_sha[sha]
                save(args.output/(label+'-records.json'), records)
        v.init('check', root=args.app_root)
    return records


def compare(paths):
    left, right = [json.loads(p.read_text()) for p in paths]
    assert left['complete'] and right['complete'] and not left['candidate'] and right['candidate']
    assert left['manifest_sha256'] == right['manifest_sha256']
    assert left['bounds'] == right['bounds']
    assert len(left['nine']) == len(right['nine']) == 9
    for key in left['nine']:
        a, b = left['nine'][key], right['nine'][key]
        assert a['selected'] == b['selected'] and a['equivalence'] == b['equivalence'], key
    a, b = left['daily']['daily-119-rebuild'], right['daily']['daily-119-rebuild']
    assert a['equivalence'] == b['equivalence']
    assert len(b['selected']) < len(a['selected'])
    assert set(a['selected']) - set(b['selected']) == set(b['excluded_batches'])
    for field in ('states','count_scopes','reasons'):
        def mapping(rows): return {tuple(r[:-1]): r[-1] for r in rows}
        old, new, omitted = mapping(a['result']['build'][field]), mapping(b['result']['build'][field]), mapping(a['omitted'][field])
        delta = {k: old.get(k,0)-new.get(k,0) for k in old.keys() | new.keys() if old.get(k,0) != new.get(k,0)}
        assert delta == omitted, field
    assert b['omitted']['example_decisions'] == 0
    print(json.dumps(dict(all_equal=True, nine_tasks=9, daily_batches_before=len(a['selected']),
        daily_batches_after=len(b['selected']), daily_events_before=a['read_events'],daily_events_after=b['read_events'])))


def main(args):
    if args.compare:
        return compare(args.compare)
    args.app_root = args.app_root.resolve(); args.output = args.output.resolve(); args.logs = args.logs.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    path = ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json'
    manifest = json.loads(path.read_text())
    bounds = {}
    for scope, detail in manifest['clusters'].items():
        for file in detail['files']:
            records = Records(args.logs/scope/file['file'])
            for _ in records: pass
            assert records.sha256 == file['sha256']
            bounds[file['file']] = dict(first=records.first_log_at.isoformat() if records.first_log_at else None,
                                       last=records.last_log_at.isoformat() if records.last_log_at else None)
    report = dict(method='measured; fresh private PG17 per scenario; all stable memberships and metric columns',
        candidate=args.candidate, manifest_sha256=digest(path), bounds=bounds,
        product_sha256={str(p.relative_to(args.app_root)):digest(p) for p in
            sorted((args.app_root/'sql_apm').rglob('*.py'))+sorted((args.app_root/'sql_apm/storage').glob('*.sql'))})
    save(args.output/'report.json', report)
    for name, daily in [('nine',False), ('daily',True)]:
        report[name] = scenario(args, manifest, bounds, daily)
        save(args.output/'report.json', report)
    report['complete'] = True; save(args.output/'report.json', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=ROOT)
    parser.add_argument('--logs', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--candidate', action='store_true')
    parser.add_argument('--compare', type=Path, nargs=2)
    main(parser.parse_args())
