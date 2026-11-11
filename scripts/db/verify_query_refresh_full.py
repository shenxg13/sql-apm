#!/usr/bin/env python3
"""Explicit retained-history import/upgrade evidence; private copies only.

The caller supplies a stopped PG17 copy in DIRECTORY/pgdata. Each invocation
starts only that copy with a private Unix socket and stops it in finally.
Evidence files are exclusive-created; completed phases are never overwritten.
"""
import argparse
from contextlib import closing
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import psycopg2
from psycopg2 import sql
from verify_query_performance_full import save

STATS = """SELECT relname,seq_scan,seq_tup_read,idx_scan,idx_tup_fetch,n_tup_ins,n_tup_upd,n_tup_del
    FROM pg_stat_user_tables WHERE schemaname='sql_apm' ORDER BY relname"""


def query(db, statement, args=()):
    with db, db.cursor() as cur:
        cur.execute(statement, args)
        return cur.fetchall() if cur.description else []


def connect(args, admin=True):
    db = psycopg2.connect(host=str(args.directory / 'socket'), port=args.port,
                         dbname='sql_apm', user='apm_test_admin' if admin else 'sql_apm')
    query(db, "SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'; SET statement_timeout='30min'")
    return db


def command(args, label, words, env=None):
    tick = time.monotonic()
    with (args.directory / (args.stage + '-' + label + '.log')).open('x') as log:
        result = subprocess.run([str(w) for w in words], env=env, cwd=args.app,
                                stdout=log, stderr=subprocess.STDOUT)
    seconds = time.monotonic() - tick
    save(args.directory / (args.stage + '-' + label + '.json'),
         dict(seconds=seconds,returncode=result.returncode,command=[str(w) for w in words]))
    if result.returncode:
        raise RuntimeError(label + ' failed; evidence and stopped copy preserved')
    return seconds


def consistency(args, db):
    result = {}
    for case in (ROOT / 'scripts/db/query_records_consistency.sql').read_text().split('-- CASE ')[1:]:
        name, statement = case.split('\n', 1)
        tick = time.monotonic()
        result[name] = dict(differences=query(db, statement)[0][0], seconds=time.monotonic()-tick)
        print(json.dumps(dict(case=name, **result[name])), flush=True)
    save(args.directory / (args.stage + '-consistency.json'), result)
    assert not any(r['differences'] for r in result.values()), result


def file_times(args, db):
    rows=query(db, '''SELECT a.file_id,a.state,a.reliable_record_count,f.byte_count,
        extract(epoch FROM a.finished_at-a.started_at)
        FROM import_attempt a JOIN source_file f USING(file_id)
        WHERE a.batch_id=%s ORDER BY a.started_at,a.attempt_id''', (args.batch,))
    assert rows and all(r[1]=='succeeded' for r in rows), rows
    save(args.directory/(args.stage+'-file-times.json'),dict(
        columns=['file_id','state','records','bytes','before_commit_seconds'],rows=rows))


def import_batch(args, db):
    config = json.loads(args.config.read_text())
    batch = config['batches'][args.batch]
    installed=query(db,"SELECT script_sha256 FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")[0][0]
    assert installed == hashlib.sha256((args.app/'sql_apm/storage/schema.sql').read_bytes()).hexdigest(), 'application/schema mismatch'
    save(args.directory / (args.stage + '-stats-before.json'), query(db, STATS))
    # No auto_explain during paired timing. Plans are collected in another run.
    env = {k:v for k,v in os.environ.items() if not k.startswith('PG')}
    env.update(PYTHONPATH=str(args.app), SQL_APM_DSN=
               f'host={args.directory}/socket port={args.port} dbname=sql_apm user=sql_apm')
    words = [sys.executable, '-m', 'sql_apm', 'import', '--config', args.config,
             '--source', batch['source'], '--batch', args.batch, '--workers', '4']
    if args.seed is not None:
        # Align generated source IDs across independently imported copies so the
        # min(sql_id) example selection is reproducible too. Natural-key exports
        # still exclude all derived surrogate keys. Only this harness is seeded.
        wrapper = args.directory / (args.stage + '-seeded-cli.py')
        with wrapper.open('x') as stream:
            stream.write("import random,runpy,uuid\nif __name__ == '__main__':\n"
                         f"    rng=random.Random({args.seed})\n"
                         "    uuid.uuid4=lambda:uuid.UUID(int=rng.getrandbits(128),version=4)\n"
                         "    runpy.run_module('sql_apm',run_name='__main__')\n")
        words = [sys.executable, wrapper] + words[3:]
    settings = {
        'session_preload_libraries': 'auto_explain', 'auto_explain.log_min_duration': '0',
        'auto_explain.log_nested_statements': 'on', 'auto_explain.log_analyze': 'on',
        'auto_explain.log_buffers': 'on', 'auto_explain.log_timing': 'off',
        'auto_explain.log_format': 'json',
    }
    previous = dict(item.split('=', 1) for item in
                    (query(db, "SELECT rolconfig FROM pg_roles WHERE rolname='sql_apm'")[0][0] or []))
    try:
        if args.plans:
            for key, value in settings.items():
                query(db, sql.SQL('ALTER ROLE sql_apm SET {} = {}').format(sql.Identifier(key), sql.Literal(value)))
        seconds = command(args, 'import', words, env)
    finally:
        if args.plans:
            for key in settings:
                if key in previous:
                    query(db, sql.SQL('ALTER ROLE sql_apm SET {} = {}').format(sql.Identifier(key), sql.Literal(previous[key])))
                else:
                    query(db, sql.SQL('ALTER ROLE sql_apm RESET {}').format(sql.Identifier(key)))
    save(args.directory / (args.stage + '-stats-after.json'), query(db, STATS))
    counts = query(db, """SELECT (SELECT count(*) FROM evidence_record WHERE scope_id=%s),
        (SELECT count(*) FROM mpp_occurrence WHERE scope_id=%s)""",
        (config['sources'][batch['source']]['cluster'],)*2)
    save(args.directory / (args.stage + '-counts.json'), dict(counts=counts,seconds=seconds,seed=args.seed,
         config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
         app_schema_sha256=hashlib.sha256((args.app/'sql_apm/storage/schema.sql').read_bytes()).hexdigest()))
    print(json.dumps(dict(stage=args.stage,seconds=seconds,counts=counts)),flush=True)
    file_times(args,db)


def upgrade(args, db):
    catalog = """SELECT c.relname,c.relfilenode,pg_relation_size(c.oid) FROM pg_class c
        WHERE c.relnamespace='sql_apm'::regnamespace AND c.relkind IN ('r','p','i') ORDER BY c.relname"""
    before = query(db,catalog)
    save(args.directory/(args.stage+'-catalog-before.json'),before)
    save(args.directory/(args.stage+'-stats-before.json'),query(db,STATS))
    seconds = command(args,'upgrade',[args.app/'scripts/db/initialize.sh','upgrade',
        '--host',args.directory/'socket','--port',args.port,'--pg-bin',args.pg_bin])
    command(args,'structure-check',[args.app/'scripts/db/initialize.sh','check',
        '--host',args.directory/'socket','--port',args.port,'--pg-bin',args.pg_bin])
    after = query(db,catalog)
    save(args.directory/(args.stage+'-catalog-after.json'),after)
    save(args.directory/(args.stage+'-stats-after.json'),query(db,STATS))
    assert before == after, 'migration changed relation identity or size'
    print(json.dumps(dict(upgrade_seconds=seconds,relations_unchanged=len(before))),flush=True)
    consistency(args,db)


def exports(args, db):
    queries = {
        'text_keys': 'SELECT t.sql_id FROM mpp_query_text_key t',
        'structures': '''SELECT n.normalization_id,s.profile,s.fingerprint_value,s.text_count,s.example_sql_id
            FROM mpp_query_structure s JOIN mpp_normalization n ON n.query_key=s.rule_key''',
        'memberships': '''SELECT n.normalization_id,k.sql_id,s.profile,s.fingerprint_value
            FROM mpp_query_membership m JOIN mpp_query_text_key k USING(sql_key)
            JOIN mpp_normalization n ON n.query_key=m.rule_key JOIN mpp_query_structure s USING(structure_key)''',
        'identities': '''SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user
            FROM mpp_query_identity i JOIN mpp_query_structure s USING(structure_key)
            JOIN mpp_normalization n ON n.query_key=i.rule_key JOIN scope c ON c.query_key=i.scope_key''',
        'records': '''SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user,
            a.analysis_id,r.occurrence_id,k.sql_id,r.end_at,r.estimated_start_at,r.duration_ms,r.timing_type,r.outcome,r.unit,r.request_shape
            FROM mpp_query_record r JOIN mpp_normalization n ON n.query_key=r.rule_key
            JOIN scope c ON c.query_key=r.scope_key JOIN analysis a ON a.query_key=r.analysis_key
            JOIN mpp_query_identity i USING(identity_key) JOIN mpp_query_structure s USING(structure_key)
            JOIN mpp_query_text_key k USING(sql_key)''',
        'identity_totals': '''SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user,
            t.timing_type,t.record_count,t.success_count,t.first_at,t.last_at
            FROM mpp_query_identity_total t JOIN mpp_query_identity i USING(identity_key)
            JOIN mpp_query_structure s USING(structure_key) JOIN mpp_normalization n ON n.query_key=i.rule_key
            JOIN scope c ON c.query_key=i.scope_key''',
        'text_totals': '''SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user,k.sql_id,
            t.record_count,t.first_at,t.last_at FROM mpp_query_text_total t JOIN mpp_query_identity i USING(identity_key)
            JOIN mpp_query_structure s USING(structure_key) JOIN mpp_normalization n ON n.query_key=t.rule_key
            JOIN scope c ON c.query_key=i.scope_key JOIN mpp_query_text_key k USING(sql_key)''',
        'state': 'SELECT * FROM mpp_query_state',
        'pending': 'SELECT * FROM mpp_query_pending',
    }
    result = {}
    for name,statement in queries.items():
        path = args.directory / (args.stage + '-' + name + '.rows.gz')
        tick = time.monotonic()
        with path.open('xb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=1) as stream:
            with db,db.cursor() as cur:
                # Every natural row contributes one SHA256, preserving duplicate
                # multiplicity. No raw business identity leaves this connection.
                cur.copy_expert("COPY (SELECT encode(sha256(convert_to(row_to_json(x)::text,'UTF8')),'hex') value FROM ("+
                                statement+') x ORDER BY value) TO STDOUT',stream)
        h=hashlib.sha256();rows=0
        with gzip.open(path,'rb') as stream:
            for line in stream:h.update(line);rows+=1
        result[name]=dict(rows=rows,sha256=h.hexdigest(),seconds=time.monotonic()-tick)
        print(json.dumps(dict(table=name,**result[name])),flush=True)
    save(args.directory / (args.stage+'-exports.json'),result)


def compare(args):
    before=json.loads((args.directory/(args.stage+'-exports.json')).read_text())
    after=json.loads((args.other/(args.stage+'-exports.json')).read_text())
    assert before.keys()==after.keys()
    compared={}
    for name in before:
        rows=0
        with gzip.open(args.directory/(args.stage+'-'+name+'.rows.gz'),'rb') as left, \
                gzip.open(args.other/(args.stage+'-'+name+'.rows.gz'),'rb') as right:
            for rows,(a,b) in enumerate(itertools.zip_longest(left,right),1):
                assert a==b,(name,rows,'natural row differs')
        assert rows==before[name]['rows']==after[name]['rows']
        assert before[name]['sha256']==after[name]['sha256']
        compared[name]=dict(rows=rows,equal=True,sha256=before[name]['sha256'])
    save(args.directory/(args.stage+'-comparison.json'),compared)
    print(json.dumps(compared),flush=True)


def lifecycle(args, db):
    """Regenerate missing fingerprints through the real CLI, build and clean up."""
    document = json.loads(args.config.read_text())
    batch = document['batches'][args.batch]
    scope = document['sources'][batch['source']]['cluster']
    selected = query(db, '''SELECT DISTINCT o.sql_id FROM mpp_occurrence o
        WHERE o.scope_id=%s AND o.sql_id IS NOT NULL AND NOT EXISTS
            (SELECT FROM mpp_training_sql t WHERE t.sql_id=o.sql_id)
        ORDER BY o.sql_id LIMIT 16''', (scope,))
    assert len(selected) == 16, 'sixteen previously untrained originals required'
    selected = [r[0] for r in selected]
    expected = query(db, 'SELECT sql_id,normalization_id,profile,state,value,reason FROM mpp_fingerprint '
                     'WHERE sql_id=ANY(%s) ORDER BY sql_id,normalization_id,profile', (selected,))
    assert len(expected) == 16
    query(db, 'DELETE FROM mpp_fingerprint WHERE sql_id=ANY(%s)', (selected,))
    config = args.directory / (args.stage+'-training.json')
    save(config,dict(version=1,clusters=[scope],window=dict(days=30,cutoff_date=max(batch['dates']))))
    env = {k:v for k,v in os.environ.items() if not k.startswith('PG')}
    env.update(PYTHONPATH=str(args.app), SQL_APM_DSN=
               f'host={args.directory}/socket port={args.port} dbname=sql_apm user=sql_apm')
    command(args,'fingerprints',[sys.executable,'-m','sql_apm','training','snapshot','--config',config,
                                '--cluster',scope,'--batch',args.batch],env)
    actual = query(db, 'SELECT sql_id,normalization_id,profile,state,value,reason FROM mpp_fingerprint '
                   'WHERE sql_id=ANY(%s) ORDER BY sql_id,normalization_id,profile', (selected,))
    assert actual == expected, 'regenerated fingerprints differ'
    consistency(args,db)
    command(args,'rebuild',[sys.executable,'-m','sql_apm','rebuild','--training-config',config,
                           '--cluster',scope,'--cutoff-date',max(batch['dates'])],env)
    assert query(db, 'SELECT count(*) FROM current_version WHERE scope_id=%s', (scope,)) == [(1,)]
    # Use this new scope's complete real build as the old-month fixture. There
    # is no need to copy unrelated historical clusters' millions of statistics.
    from database.retention import clone_build, digest
    from sql_apm.storage.cleanup import CleanupStore
    source=query(db,'SELECT build_id FROM current_version WHERE scope_id=%s',(scope,))[0][0]
    versions=query(db,'SELECT * FROM current_version ORDER BY scope_id')
    revision=query(db,'SELECT revision FROM mpp_query_state')
    expected={table:digest(db,table,('build_id',),'WHERE build_id=%s',(source,)) for table in
              ('mpp_statistic','mpp_observation_statistic','mpp_build_group','mpp_build_observation_group')}
    save(args.directory/(args.stage+'-cleanup-before.json'),dict(build=source,equal_results=expected))
    pid=clone_build(db,source,'issue82-cleanup-fixture','2026-02-01',published=True)
    indexes=query(db,"""SELECT i.indexrelid FROM pg_partition_tree('mpp_statistic_ranking_idx') t
        JOIN pg_index i ON i.indexrelid=t.relid WHERE i.indrelid=to_regclass('mpp_statistic_p'||%s::text)""",(pid,))
    assert len(indexes)==1
    result=CleanupStore(db).execute(scope,2)
    assert result['state']=='succeeded',result
    month=next(m for m in result['months'] if m['partition_id']==pid)
    assert month['released_bytes']>0 and month['after_bytes']==0,month
    assert not query(db,'SELECT FROM pg_class WHERE oid=%s',indexes[0])
    assert query(db,'SELECT * FROM current_version ORDER BY scope_id')==versions
    assert query(db,'SELECT revision FROM mpp_query_state')==revision
    assert expected=={table:digest(db,table,('build_id',),'WHERE build_id=%s',(source,)) for table in expected}
    for table in expected:
        assert query(db,'SELECT count(*) FROM '+table+' WHERE partition_id=%s',(pid,))==[(0,)]
    save(args.directory/(args.stage+'-lifecycle.json'),dict(regenerated=16,published=True,cleanup=result,
         current_results_unchanged=True,query_revision_unchanged=revision))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('import','upgrade','consistency','exports','compare','lifecycle','file-times'))
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--stage',required=True)
    parser.add_argument('--port',type=int,default=55482)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--config',type=Path)
    parser.add_argument('--batch')
    parser.add_argument('--seed',type=int)
    parser.add_argument('--other',type=Path,help='other export directory for compare')
    parser.add_argument('--plans',action='store_true',help='separate diagnostic import, excluded from paired timing')
    args=parser.parse_args()
    if args.mode in ('import','lifecycle') and (args.config is None or not args.batch):
        parser.error('import/lifecycle require --config and --batch')
    if args.mode == 'file-times' and not args.batch:
        parser.error('file-times requires --batch')
    args.directory=args.directory.resolve();args.app=args.app.resolve()
    if args.mode=='compare':
        if args.other is None:parser.error('compare requires --other')
        compare(args)
        return
    if args.config:args.config=args.config.resolve()
    data=args.directory/'pgdata';socket=args.directory/'socket'
    if not (data/'PG_VERSION').is_file() or (data/'PG_VERSION').read_text().strip()!='17':
        raise ValueError('stopped_private_pg17_copy_required')
    if (data/'postmaster.pid').exists():raise ValueError('copy_is_already_running')
    socket.mkdir(exist_ok=True,mode=0o700)
    with (data/'postgresql.conf').open('a') as stream:
        stream.write(f"\nlisten_addresses=''\nport={args.port}\nunix_socket_directories='{socket}'\nlogging_collector=off\n")
    try:
        command(args,'start',[args.pg_bin/'pg_ctl','-D',data,'-l',args.directory/(args.stage+'-server.log'),'-w','start'])
        with closing(connect(args)) as db:
            if args.mode=='import':import_batch(args,db)
            elif args.mode=='upgrade':upgrade(args,db)
            elif args.mode=='consistency':consistency(args,db)
            elif args.mode=='lifecycle':lifecycle(args,db)
            elif args.mode=='file-times':file_times(args,db)
            else:exports(args,db)
    finally:
        if (data/'postmaster.pid').exists():
            command(args,'stop',[args.pg_bin/'pg_ctl','-D',data,'-m','fast','-w','stop'])


if __name__=='__main__':
    main()
