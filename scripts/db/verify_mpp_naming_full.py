#!/usr/bin/env python3
"""Explicit Issue #33 replay: fresh PG17, stable source keys, all groups/metrics."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from verify import instance, Verification
import psycopg2


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, default=str) + '\n')


def stream_digest(db, query, params=()):
    value, count = hashlib.sha256(), 0
    with db.cursor(name='acceptance_stream') as cur:
        cur.itersize = 4000
        cur.execute(query, params)
        for row in cur:
            value.update((json.dumps(row, separators=(',', ':'), default=str) + '\n').encode())
            count += 1
    return dict(rows=count, sha256=value.hexdigest())


def export(db, result, scope):
    build = result['build']['build_id']
    frozen = result['snapshot']
    with db.cursor() as cur:
        cur.execute('SET search_path=sql_apm,pg_catalog')
        cur.execute("SET TIME ZONE 'Asia/Shanghai'")
        # The acceptance joins span all evidence. Keep their parallel memory
        # demand bounded, and plan against the just-imported cardinalities.
        # These settings affect only export, not the product CLI subprocess.
        cur.execute('SET LOCAL max_parallel_workers_per_gather=0')
        cur.execute("SET LOCAL work_mem='16MB'")
        for table in ('evidence_record', 'mpp_sql_text_evidence', 'mpp_fingerprint',
                      'mpp_approximate_evidence', 'mpp_approximate_result'):
            cur.execute('ANALYZE ' + table)
        counts = {}
        for name, query in {
            'records': 'SELECT count(*) FROM evidence_record WHERE scope_id=%s',
            'occurrences': 'SELECT count(*) FROM mpp_occurrence WHERE scope_id=%s',
            'sql_texts': 'SELECT count(DISTINCT sql_id) FROM mpp_sql_text_evidence JOIN evidence_record USING(record_id) WHERE scope_id=%s',
            'fingerprints': '''SELECT state,count(*) FROM mpp_fingerprint WHERE sql_id IN
                (SELECT sql_id FROM mpp_sql_text_evidence JOIN evidence_record USING(record_id) WHERE scope_id=%s) GROUP BY state ORDER BY state''',
            'approximate': '''SELECT state,count(*) FROM mpp_approximate_result WHERE result_id IN
                (SELECT result_id FROM mpp_approximate_evidence JOIN evidence_record USING(record_id) WHERE scope_id=%s) GROUP BY state ORDER BY state''',
            'problems': '''SELECT code,effect,count(*),sum(count) FROM problem p WHERE batch_id IN
                (SELECT batch_id FROM import_batch WHERE scope_id=%s) GROUP BY code,effect ORDER BY code,effect''',
        }.items():
            cur.execute(query, (scope,)); counts[name] = cur.fetchall()
        cur.execute('''CREATE TEMP TABLE acceptance_events ON COMMIT DROP AS
            SELECT d.*,o.database,o.execution_user,o.sql_state,
                jsonb_build_array(f.checksum_value,e.record_no,e.line_start,e.line_end,o.unit)::text AS stable_key
            FROM mpp_training_decisions(%s,%s) d JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
            JOIN evidence_record e ON e.record_id=o.anchor_ref JOIN source_file f USING(file_id)''',
            (frozen['input_id'], frozen['config_id']))
        cur.execute('''CREATE TEMP TABLE acceptance_members ON COMMIT DROP AS
            SELECT 'formal'::text kind,group_id,stable_key FROM acceptance_events WHERE count_scope='group'
            UNION ALL
            SELECT 'observation',g.group_id,e.stable_key FROM acceptance_events e
            JOIN mpp_occurrence_approximate a USING(analysis_id,occurrence_id)
            JOIN mpp_approximate_result r USING(result_id,rule_id)
            JOIN mpp_observation_group g ON g.scope_id=e.scope_id AND g.database=e.database
                AND g.execution_user=e.execution_user AND g.rule_id=a.rule_id AND g.approximate_value=r.value
                AND g.timing_type=coalesce(e.timing_type,'unknown')
            JOIN mpp_build_observation_group bg ON bg.group_id=g.group_id AND bg.build_id=%s
            WHERE e.reason_codes && ARRAY['sql_uncertain','sql_incomplete','sql_encoding_invalid','fingerprint_failed']
                AND e.in_window IS TRUE AND e.estimated_start_at IS NOT NULL
                AND e.sql_state<>'missing' AND r.state='available' ''', (build,))
        cur.execute('''CREATE TEMP TABLE acceptance_groups ON COMMIT DROP AS
            SELECT kind,group_id,min(stable_key) AS stable_group FROM acceptance_members GROUP BY kind,group_id''')
        cur.execute('CREATE UNIQUE INDEX ON acceptance_groups(kind,stable_group)')
        for kind, table in [('formal', 'mpp_build_group'), ('observation', 'mpp_build_observation_group')]:
            cur.execute('SELECT count(*) FROM ' + table + ' WHERE build_id=%s', (build,)); expected = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM acceptance_groups WHERE kind=%s', (kind,))
            assert cur.fetchone()[0] == expected, 'group_mapping_incomplete'
        cur.execute("SELECT name,state,reason FROM build_check WHERE build_id=%s ORDER BY name", (build,))
        checks = cur.fetchall()
        identifiers = {}
        for table, column in [('scope','system_kind'),('scope','profile'),('analysis','profile'),
                              ('analysis','mapping_version'),('analysis','parser_version'),
                              ('mpp_fingerprint','profile'),('mpp_approximate_rule','profile'),
                              ('config_snapshot','profile'),('build','profile'),('mpp_normalization','dictionary_rules_version')]:
            cur.execute('SELECT DISTINCT ' + column + ' FROM ' + table + ' ORDER BY 1')
            identifiers[table + '.' + column] = [row[0] for row in cur]
    report = dict(counts=counts, checks=checks, publication=result['publication']['result'])
    report['memberships'] = stream_digest(db, '''SELECT m.kind,g.stable_group,m.stable_key
        FROM acceptance_members m JOIN acceptance_groups g USING(kind,group_id)
        ORDER BY m.kind,g.stable_group,m.stable_key''')
    for kind, table in [('formal','mpp_statistic'),('observation','mpp_observation_statistic')]:
        report[kind] = stream_digest(db, '''SELECT g.stable_group,
            (to_jsonb(s)-ARRAY['partition_id','build_id','group_id'])::text FROM ''' + table + ''' s
            JOIN acceptance_groups g ON g.kind=%s AND g.group_id=s.group_id WHERE s.build_id=%s
            ORDER BY g.stable_group,s.layer,s.bucket_date,s.bucket_number''', (kind,build))
    db.commit()
    return report, identifiers


def main(args):
    if args.compare:
        left, right = [json.loads(p.read_text()) for p in args.compare]
        assert left.get('complete') is True and right.get('complete') is True, 'incomplete_replay'
        assert set(left['equivalence']) == set(right['equivalence']) == {'119', '120'}
        assert left['manifest_sha256'] == right['manifest_sha256']
        assert left['equivalence'] == right['equivalence'], 'naming_equivalence_failed'
        print(json.dumps(dict(all_equal=True, clusters=sorted(left['equivalence']))))
        return
    args.app_root = args.app_root.resolve(); args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest_path = ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json'
    manifest = json.loads(manifest_path.read_text())
    report = dict(method='measured; fresh private PG17; all member keys and all metric columns',
                  manifest_sha256=digest(manifest_path), equivalence={}, identifiers={}, tasks={},
                  clusters=args.clusters,
                  product_sha256={str(p.relative_to(args.app_root)):digest(p) for p in
                                  sorted((args.app_root/'sql_apm').rglob('*.py')) +
                                  sorted((args.app_root/'sql_apm/storage').glob('*.sql'))})
    with instance(args.pg_bin) as (directory, env):
        v = Verification(args.pg_bin,directory,env); v.init(root=args.app_root)
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        with psycopg2.connect(dsn) as db:
            for scope, details in sorted(manifest['clusters'].items()):
                if scope not in args.clusters:
                    continue
                files=details['files']
                for file in files:
                    assert digest(args.logs/scope/file['file'])==file['sha256'], 'source_checksum_mismatch'
                dates=sorted({f['file'][5:15] for f in files}); source='full-'+scope; batch='full-import-'+scope
                doc=dict(version=1,clusters=[scope],sources={source:dict(cluster=scope,
                    build='HashData Warehouse 3.13.13',timezone='UTC+08:00',declaration='Issue33-fixed-files')},
                    batches={batch:dict(source=source,files_confirmed_complete=True,dates=dates,
                        files=[dict(path=str((args.logs/scope/f['file']).resolve()),origin_key=scope+'/'+f['file'],
                                    closed_and_copied=True) for f in files])})
                config=args.output/('import-'+scope+'.json'); save(config,doc)
                training=args.output/('training-'+scope+'.json'); save(training,dict(version=1,clusters=[scope],window=dict(days=30)))
                command=[sys.executable,'-m','sql_apm','full','--config',str(config),'--source',source,'--batch',batch,
                         '--training-config',str(training),'--workers',str(args.workers)]
                started=time.monotonic(); log=args.output/(scope+'.log')
                with log.open('w') as stream:
                    status=subprocess.run(command,cwd=args.app_root,env=dict(os.environ,SQL_APM_DSN=dsn,PYTHONPATH=str(args.app_root)),
                                          stdout=stream,stderr=subprocess.STDOUT).returncode
                assert status==0, 'full_failed_'+scope
                result=json.loads(log.read_text().splitlines()[-1]); assert result['publication']['result']=='published'
                report['tasks'][scope]=dict(seconds=time.monotonic()-started,result=result)
                save(args.output/'report.json', report)
                print(json.dumps(dict(phase='full_finished',scope=scope,seconds=report['tasks'][scope]['seconds'])),flush=True)
                try:
                    report['equivalence'][scope],report['identifiers'][scope]=export(db,result,scope)
                except BaseException:
                    # Preserve diagnostics before the private instance cleans up.
                    shutil.copyfile(directory/'server.log', args.output/'export-failure-server.log')
                    raise
                save(args.output/'report.json',report)
                print(json.dumps(dict(phase='export_finished',scope=scope)),flush=True)
        v.init('check',root=args.app_root)
    report['complete']=True; save(args.output/'report.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,default=ROOT)
    parser.add_argument('--logs',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--clusters', nargs='+', choices=['119', '120'], default=['119', '120'])
    parser.add_argument('--compare',type=Path,nargs=2)
    main(parser.parse_args())
