#!/usr/bin/env python3
"""Explicit full-volume query measurements on an already prepared private PG17.

Start/stop the retained instance separately. This entry never contacts production.
Raw parameters, rows and nested plans stay in the supplied ignored directory.
Each stage creates new files, and comparison checks every row in its original order.
"""
import argparse
from contextlib import closing
from datetime import datetime
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json
from database.retention import digest

ORDERS = ('samples', 'p50', 'p95', 'p99', 'max', 'mean')
TIMINGS = ('', 'request,execute_first', 'request', 'execute_first', 'execute_fetch', 'parse', 'bind')
SUMMARIES = {'mpp_sql_text_count', 'mpp_fingerprint_count', 'mpp_occurrence_name'}


def prepare(args):
    """Rebuild fixed logs using the explicitly frozen pre-change application."""
    out, app, pg = args.directory.resolve(), args.base_app.resolve(), args.pg_bin.resolve()
    source = app / 'sql_apm/storage/schema.sql'
    if hashlib.sha256(source.read_bytes()).hexdigest() != 'f94e468529bb2a0577252b474daa7d7c5bdfad8f36434247a33e338a71a77ba6':
        raise ValueError('frozen_1_12_0_required')
    out.mkdir(parents=True, mode=0o700)
    data, sock = out / 'pgdata', out / 'socket'; sock.mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(PYTHONPATH=str(app), PGCONNECT_TIMEOUT='5', SQL_APM_DSN=f'host={sock} port={args.port} dbname=sql_apm user=sql_apm')
    progress = []
    def command(label, words):
        tick = time.monotonic(); print('start', label, flush=True)
        with (out/(label+'.log')).open('x') as log:
            status = subprocess.run([str(w) for w in words], cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        progress.append(dict(label=label, seconds=time.monotonic()-tick, returncode=status))
        (out/'prepare-progress.json').write_text(json.dumps(progress, indent=2))
        if status:
            raise RuntimeError(label+' failed; database and evidence preserved')
        print('complete', label, round(progress[-1]['seconds'], 2), flush=True)
    command('prepare-input', [sys.executable, app/'scripts/deployment/rehearsal.py', 'prepare', '--logs', args.logs.resolve(), '--output', out/'config'])
    command('initdb', [pg/'initdb', '-D', data, '-U', 'apm_test_admin', '--auth-local=trust', '--auth-host=reject', '--encoding=UTF8', '--locale=C'])
    with (data/'postgresql.conf').open('a') as stream:
        stream.write(f"\nlisten_addresses=''\nport={args.port}\nunix_socket_directories='{sock}'\n"
                     "shared_buffers='512MB'\nwork_mem='16MB'\nmaintenance_work_mem='128MB'\n"
                     "max_connections=30\nmax_wal_size='2GB'\nmin_wal_size='256MB'\n"
                     "timezone='Asia/Shanghai'\nlog_statement='none'\nlog_min_error_statement='panic'\n")
    try:
        command('pg-start', [pg/'pg_ctl', '-D', data, '-l', out/'server.log', '-w', 'start'])
        command('initialize', [app/'scripts/db/initialize.sh', 'all', '--host', sock, '--port', args.port, '--pg-bin', pg,
                              '--admin-user', 'apm_test_admin', '--admin-database', 'postgres'])
        for cluster in ('120', '119'):
            for step in range(4):
                command(f'import-{cluster}-{step}', [sys.executable, '-m', 'sql_apm', 'import', '--config', out/f'config/import-{cluster}.json',
                        '--source', f'daily-{cluster}', '--batch', f'{cluster}-{step}', '--workers', '4'])
            command(f'build-{cluster}-1', [sys.executable, '-m', 'sql_apm', 'rebuild', '--cluster', cluster,
                    '--training-config', out/f'config/training-{cluster}.json', '--cutoff-date',
                    {'119': '2026-07-31', '120': '2026-09-19'}[cluster]])
        command('analyze', [pg/'psql', '-X', '-w', '-h', sock, '-p', args.port, '-U', 'sql_apm', '-d', 'sql_apm',
                            '-v', 'ON_ERROR_STOP=1', '-c', 'ANALYZE'])
    finally:
        if (data/'postmaster.pid').exists():
            command('pg-stop', [pg/'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, default=str, indent=2)
        stream.write('\n')


def connect(args, admin=False):
    directory = args.directory.resolve()
    if not (directory / 'pgdata/PG_VERSION').is_file() or (directory / 'pgdata/PG_VERSION').read_text().strip() != '17':
        raise ValueError('private_pg17_directory_required')
    db = psycopg2.connect(host=str(directory / 'socket'), port=args.port, dbname='sql_apm',
                          user='apm_test_admin' if admin else 'sql_apm')
    with db, db.cursor() as cur:
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
        cur.execute("SET statement_timeout='10min'")
    return db


def fetch(db, statement, values=()):
    with db, db.cursor() as cur:
        # Rendered dashboard URLs contain literal percent escapes. Only invoke
        # psycopg's placeholder adaptation when there are actual parameters.
        cur.execute(statement, values or None)
        return cur.fetchall() if cur.description else []


def context(args, db):
    path = args.directory / 'query-context.json'
    if path.exists():
        return json.loads(path.read_text())
    norm = fetch(db, 'SELECT normalization_id FROM build WHERE build_id=(SELECT build_id FROM current_version WHERE scope_id=%s)', ('120',))[0][0]
    # Highest-frequency SQL identity, independent of the ranking functions.
    row = fetch(db, """SELECT f.value,o.database,o.execution_user,count(*) n,max(o.end_at),
            mpp_view_identity_token('120',o.database,o.execution_user)
        FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id)
        WHERE o.scope_id='120' AND f.normalization_id=%s AND f.profile='mpp-csv/1' AND f.state='reliable'
            AND o.database IS NOT NULL AND o.execution_user IS NOT NULL
        GROUP BY f.value,o.database,o.execution_user ORDER BY n DESC,f.value,o.database,o.execution_user LIMIT 1""", (norm,))[0]
    fingerprint, database, user, count, latest, token = row
    bounds = fetch(db, "SELECT (%s::timestamptz-interval '7 days')::text,(%s::timestamptz+interval '1 millisecond')::text", (latest, latest))[0]
    timings = fetch(db, 'SELECT timing FROM mpp_view_timings(%s,%s,%s)', (norm, fingerprint, token))
    result = dict(norm=norm, fingerprint=fingerprint, database=database, user=user, count=count,
                  identity=token, start=bounds[0], end=bounds[1], timing=timings[0][0])
    save(path, result)
    return result


def ranked(norm, scope=None, database=None, user=None, timings='request,execute_first', order='p95', minimum=0, limit=500):
    return 'SELECT row_to_json(r)::text FROM mpp_view_baseline_ranking(%s,%s,%s,%s,%s,%s,%s,%s) r', \
           [norm, scope, database, user, timings, order, minimum, limit]


def matrix(c):
    for i, values in enumerate(itertools.product(ORDERS, ('120', '119', None), (None, c['database']),
            (None, c['user']), TIMINGS, (0, 100), (50, 100, 200, 500))):
        order, scope, database, user, timings, minimum, limit = values
        yield 'ranking-%04d' % i, *ranked(c['norm'], scope, database, user, timings, order, minimum, limit)


def detail_cases(db, c):
    build = fetch(db, "SELECT build_id FROM current_version WHERE scope_id='120'")[0][0]
    common = [c['norm'], c['fingerprint'], c['identity'], c['timing']]
    for order, limit, chosen in itertools.product(('latest', 'slowest'), (20, 200, 1000), (None, build)):
        values = common + [chosen, c['start'], c['end'], None, None, None, None, order, limit]
        yield 'detail-%s-%d-%s' % (order, limit, 'version' if chosen else 'none'), \
              'SELECT row_to_json(r)::text FROM mpp_view_executions('+','.join(['%s']*13)+') r', values
    for limit, chosen in itertools.product((20, 200, 1000), (None, build)):
        values = [c['norm'], c['fingerprint'], '120', c['database'], c['user'], c['start'], c['end'], limit, None, chosen]
        yield 'history-%d-%s' % (limit, 'version' if chosen else 'none'), \
              'SELECT mpp_query_history('+','.join(['%s']*10)+')::text', values


def panel_two(c):
    dashboard = json.loads((ROOT/'grafana/dashboards/mpp-detail.json').read_text())
    statement = next(p for p in dashboard['panels'] if p.get('id') == 2)['targets'][0]['rawSql']
    values = dict(norm=c['norm'], fp=c['fingerprint'], identity=c['identity'], timing=c['timing'],
                  sid='', sqlid='', part='1', version='', mode='exact', q='', hit='', dmin='', dmax='',
                  layer='overall', list_order='latest', text_order='count', status='')
    values['__url_time_range'] = urlencode({k: int(datetime.fromisoformat(c[v]).timestamp()*1000)
                                          for k,v in [('from','start'),('to','end')]})
    def replace(match):
        key, _, formatter = match[1].partition(':')
        value = values[key]
        if formatter == 'queryparam':
            value = urlencode({'var-'+key: value})
        return value.replace("'", "''")
    return re.sub(r'\$\{([^}]+)\}', replace, statement)


def dropdown_cases(version):
    for kind, column in (('database', 'database'), ('user', 'execution_user')):
        source = (f"SELECT DISTINCT name FROM mpp_occurrence_name WHERE kind='{kind}' AND item_count>0"
                  if version == '1.13.0' else f'SELECT DISTINCT {column} AS name FROM mpp_occurrence WHERE {column} IS NOT NULL')
        yield kind, "SELECT '全部', '*', 0 UNION ALL SELECT name,mpp_view_encode(name),1 FROM ("+source+") x ORDER BY 3,1", []


def rows(args):
    target = args.directory / (args.stage + '-rows.jsonl.gz')
    with closing(connect(args)) as db, gzip.open(target, 'xt') as stream:
        c = context(args, db)
        version = fetch(db, "SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")[0][0]
        cases = itertools.chain(matrix(c), detail_cases(db, c), [('detail-panel-2', panel_two(c), [])],
                                dropdown_cases(version))
        for n, (label, statement, values) in enumerate(cases):
            tick = time.monotonic(); result = fetch(db, statement, values); seconds = time.monotonic()-tick
            stream.write(json.dumps(dict(label=label, statement=statement, values=values, rows=result, seconds=seconds))+'\n')
            stream.flush()
            if n % 50 == 0:
                print(json.dumps(dict(stage=args.stage, cases=n+1, last_seconds=round(seconds, 3))), flush=True)
        # The cleaned marker is a temporary fixture; the real current build and
        # partitions remain intact after this transaction is rolled back.
        with db.cursor() as cur:
            try:
                cur.execute("UPDATE mpp_result_partition SET cleaned_at=clock_timestamp() WHERE partition_id="
                            "(SELECT b.partition_id FROM build b JOIN current_version v USING(build_id) WHERE v.scope_id='120')")
                for scope, order, limit in itertools.product(('120',None), ORDERS, (50,100,200,500)):
                    statement, values = ranked(c['norm'],scope,order=order,limit=limit)
                    tick=time.monotonic();cur.execute(statement,values);result=cur.fetchall()
                    stream.write(json.dumps(dict(label=f'cleaned-{scope}-{order}-{limit}',statement=statement,values=values,
                                                 rows=result,seconds=time.monotonic()-tick))+'\n')
                    stream.flush()
            finally:
                db.rollback()


class Notices:
    """A file sink avoids psycopg2's default limit of 50 retained notices."""
    def __init__(self, stream):
        self.stream = stream

    def append(self, message):
        self.stream.write(message)


def plan_nodes(value):
    if isinstance(value, list):
        for child in value:
            yield from plan_nodes(child)
    elif isinstance(value, dict):
        if 'Node Type' in value:
            yield value
        for key in ('Plan', 'Plans'):
            yield from plan_nodes(value.get(key, []))


def parse_notices(source):
    decoder, result, pos = json.JSONDecoder(), [], 0
    while True:
        pos = source.find('\n{', pos)
        if pos < 0:
            return result
        value, length = decoder.raw_decode(source[pos+1:])
        result.append(value); pos += length+1


def timing(args):
    out = args.directory / args.stage
    out.mkdir(mode=0o700)
    with closing(connect(args, admin=True)) as db:
        c = context(args, db)
        version = fetch(db, "SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")[0][0]
        candidates = [('ranking-'+str(scope)+'-'+order, *ranked(c['norm'], scope, order=order))
                      for scope, order in itertools.product(('120', None), ORDERS)]
        candidates += [x for x in detail_cases(db, c) if x[0] == 'detail-latest-200-version']
        build = fetch(db, "SELECT build_id FROM current_version WHERE scope_id='120'")[0][0]
        page = fetch(db, """SELECT coalesce(jsonb_agg(jsonb_build_object('analysis_id',r.analysis_id,'occurrence_id',r.occurrence_id)),'[]')
            FROM (SELECT * FROM mpp_view_records(%s,%s,%s,%s,%s,%s)
                ORDER BY end_at DESC,analysis_id DESC,occurrence_id DESC LIMIT 200) r""",
            [c['norm'], c['fingerprint'], c['identity'], c['timing'], c['start'], c['end']])[0][0]
        candidates.append(('training-200', 'SELECT * FROM mpp_query_training(%s,%s)', [build, Json(page)]))
        candidates.extend(dropdown_cases(version))
        candidates.append(('missing', 'SELECT mpp_query_missing_rules(%s)', [c['norm']]))
        candidates.append(('detail-panel-2', panel_two(c), []))
        report = dict(version=version, cases={})
        for label, statement, values in candidates:
            times = []
            for _ in range(3):
                tick = time.monotonic(); result = fetch(db, statement, values); times.append(time.monotonic()-tick)
            with (out/(label+'-plans.log')).open('x') as stream:
                db.notices = Notices(stream)
                fetch(db, "LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; SET auto_explain.log_analyze=on; "
                          "SET auto_explain.log_buffers=on; SET auto_explain.log_nested_statements=on; SET auto_explain.log_timing=off; "
                          "SET auto_explain.log_format=json; SET auto_explain.log_level=notice; SET client_min_messages=notice")
                fetch(db, statement, values)
                fetch(db, 'SET auto_explain.log_min_duration=-1; SELECT 1')
                db.notices = []
            plans = parse_notices((out/(label+'-plans.log')).read_text())
            scans = [{k: node[k] for k in ('Node Type','Relation Name','Index Name','Index Cond','Plan Rows','Actual Rows','Actual Loops',
                      'Rows Removed by Filter','Shared Hit Blocks','Shared Read Blocks','Heap Fetches') if k in node}
                     for node in plan_nodes(plans) if node.get('Relation Name','').startswith(('mpp_statistic','mpp_occurrence'))]
            report['cases'][label] = dict(seconds=times, median_seconds=statistics.median(times), rows=len(result), scans=scans)
            save(out/(label+'.json'), report['cases'][label])
            print(json.dumps(dict(stage=args.stage, case=label, seconds=times)), flush=True)
        save(out/'timing.json', report)


def cli(args):
    """Keep complete command output private; compare bytes across the upgrade."""
    app = (args.base_app or ROOT).resolve()
    env = {**os.environ, 'PYTHONPATH': str(app),
           'SQL_APM_DSN': f'host={args.directory.resolve()}/socket port={args.port} dbname=sql_apm user=sql_apm'}
    with closing(connect(args)) as db:
        c = context(args, db)
        build = fetch(db, "SELECT build_id FROM current_version WHERE scope_id='120'")[0][0]
    out = args.directory/(args.stage+'-cli'); out.mkdir(mode=0o700)
    common = ['--cluster','120','--database',c['database'],'--user',c['user']]
    cases = []
    for limit, version in itertools.product((20,200,1000), (False, True)):
        cases.append((f'search-{limit}-{version}', ['search','executions','--fingerprint',c['fingerprint'],
                      *common,'--start',c['start'],'--end',c['end'],'--limit',str(limit)] +
                      (['--version',build] if version else []), b''))
    for order, limit in itertools.product(('latest','slowest'), (20,200,1000)):
        cases.append((f'sqlfind-{order}-{limit}', ['sqlfind',*common,'--show','1','--view','history','--width','200',
                      '--history-start',c['start'],'--history-end',c['end'],'--history-order',order,'--limit',str(limit)],
                      c['fingerprint'].encode()))
    report = {}
    for label, options, raw in cases:
        tick=time.monotonic()
        result=subprocess.run([sys.executable,'-m','sql_apm',*options],cwd=app,env=env,input=raw,capture_output=True)
        seconds=time.monotonic()-tick
        (out/(label+'.out')).write_bytes(result.stdout); (out/(label+'.err')).write_bytes(result.stderr)
        report[label]=dict(seconds=seconds, returncode=result.returncode, sha256=hashlib.sha256(result.stdout).hexdigest())
        if result.returncode:
            raise RuntimeError(label+' failed; see private stderr')
        print(json.dumps(dict(stage=args.stage, case=label, seconds=seconds)),flush=True)
    save(out/'report.json',report)


def import_check(args):
    """Replay the same explicit real batch into fresh old/new private databases."""
    from verify import instance, Verification
    from verify_mpp_naming_full import stream_digest
    target = args.directory.resolve(); target.mkdir(mode=0o700)
    config = args.import_config.resolve()
    document = json.loads(config.read_text())
    source = document['batches'][args.batch]['source']
    report = {}
    stages=[('before',args.base_app.resolve()), ('after',ROOT)]
    if args.before is not None:
        # Reuse a completed same-input baseline for a fix/retest. The complete
        # row digests below still reject a different file set or import context.
        report['before']=json.loads(args.before.read_text())
        save(target/'before-reference.json',dict(path=str(args.before.resolve()),
            sha256=hashlib.sha256(args.before.read_bytes()).hexdigest()))
        stages=stages[1:]
    queries = {
        'texts': "SELECT encode(content_sha256,'hex'),text,search_text FROM mpp_sql_text ORDER BY content_sha256,text",
        'fingerprints': """SELECT encode(s.content_sha256,'hex'),f.normalization_id,f.profile,f.state,f.value,f.reason
            FROM mpp_fingerprint f JOIN mpp_sql_text s USING(sql_id) ORDER BY s.content_sha256,s.text,f.normalization_id""",
        'events': """SELECT (to_jsonb(o)-'sql_id')||jsonb_build_object('text_sha256',encode(s.content_sha256,'hex'))
            FROM mpp_occurrence o LEFT JOIN mpp_sql_text s USING(sql_id) ORDER BY o.analysis_id,o.occurrence_id""",
        'text_evidence': """SELECT e.record_id,encode(s.content_sha256,'hex') FROM mpp_sql_text_evidence e
            JOIN mpp_sql_text s USING(sql_id) ORDER BY e.record_id,s.content_sha256""",
        'approximate': """SELECT encode(i.source_sha256,'hex'),(to_jsonb(r)-ARRAY['result_id','input_id'])
            FROM mpp_approximate_result r JOIN mpp_approximate_input i USING(input_id)
            ORDER BY i.source_sha256,r.rule_id,r.structural_reason""",
        'approximate_events': """SELECT (to_jsonb(e)-'result_id')||jsonb_build_object('input_sha256',encode(i.source_sha256,'hex'))
            FROM mpp_occurrence_approximate e JOIN mpp_approximate_result r USING(result_id)
            JOIN mpp_approximate_input i USING(input_id) ORDER BY e.analysis_id,e.occurrence_id,e.rule_id""",
        'approximate_evidence': """SELECT e.record_id,encode(i.source_sha256,'hex'),r.rule_id,r.structural_reason
            FROM mpp_approximate_evidence e JOIN mpp_approximate_result r USING(result_id)
            JOIN mpp_approximate_input i USING(input_id) ORDER BY e.record_id,i.source_sha256,r.rule_id,r.structural_reason""",
        'problems': "SELECT to_jsonb(p)-'problem_id' FROM problem p ORDER BY (to_jsonb(p)-'problem_id')::text",
        'problem_evidence': """SELECT e.record_id,to_jsonb(p)-'problem_id' FROM problem_evidence e JOIN problem p USING(problem_id)
            ORDER BY e.record_id,(to_jsonb(p)-'problem_id')::text""",
    }
    for stage, app in stages:
        with instance(args.pg_bin, configuration="shared_buffers='512MB'\nwork_mem='16MB'\nmaintenance_work_mem='128MB'\n") as (directory, env):
            v=Verification(args.pg_bin,directory,env); v.init(root=app)
            env.update(PYTHONPATH=str(app),SQL_APM_DSN=f'host={directory}/socket port=55473 dbname=sql_apm user=sql_apm')
            tick=time.monotonic()
            with (target/(stage+'.log')).open('x') as log:
                result=subprocess.run([sys.executable,'-m','sql_apm','import','--config',str(config),
                    '--source',source,'--batch',args.batch,'--workers','4'],cwd=app,env=env,stdout=log,stderr=subprocess.STDOUT)
            seconds=time.monotonic()-tick
            if result.returncode:
                raise RuntimeError(stage+' import failed')
            outputs={}
            with closing(psycopg2.connect(env['SQL_APM_DSN'])) as db:
                with db,db.cursor() as cur:
                    cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'; ANALYZE")
                for table in ('analysis','analysis_file','evidence_record','mpp_occurrence_evidence','source',
                              'scope','source_file','mpp_normalization','mpp_approximate_rule'):
                    outputs[table]=digest(db,table)
                for label,statement in queries.items():
                    outputs[label]=stream_digest(db,statement)
                if stage=='after':
                    for kind,column in [('database','database'),('user','execution_user')]:
                        assert fetch(db,'SELECT DISTINCT name FROM mpp_occurrence_name WHERE kind=%s AND item_count>0 ORDER BY name',(kind,)) == \
                            fetch(db,'SELECT DISTINCT '+column+' FROM mpp_occurrence WHERE '+column+' IS NOT NULL ORDER BY 1')
                    assert fetch(db,'SELECT mpp_query_missing_rules(normalization_id) FROM mpp_normalization') == [(0,)]
            report[stage]=dict(seconds=seconds,results=outputs)
            save(target/(stage+'.json'),report[stage])
            print(json.dumps(dict(stage=stage,import_seconds=seconds,rows=outputs['events']['rows'])),flush=True)
    assert report['before']['results']==report['after']['results'], 'same_input_import_changed'
    save(target/'comparison.json',dict(equal=True,before_seconds=report['before']['seconds'],after_seconds=report['after']['seconds']))
    print('PASS: real same-input import results identical, name options automatic and missing count zero',flush=True)


def rebuild(args):
    app=(args.base_app or ROOT).resolve()
    env={**os.environ,'PYTHONPATH':str(app),
         'SQL_APM_DSN':f'host={args.directory.resolve()}/socket port={args.port} dbname=sql_apm user=sql_apm'}
    with closing(connect(args)) as db:
        prior=fetch(db,"SELECT build_id FROM current_version WHERE scope_id='120'")[0][0]
        before=fetch(db,'SELECT pg_database_size(current_database())')[0][0]
    tick=time.monotonic()
    log=args.directory/(args.stage+'.log')
    with log.open('x') as stream:
        result=subprocess.run([sys.executable,'-m','sql_apm','rebuild','--cluster','120',
                               '--training-config',str(args.training_config.resolve()),'--cutoff-date','2026-09-19'],
                              cwd=app,env=env,stdout=stream,stderr=subprocess.STDOUT)
    seconds=time.monotonic()-tick
    if result.returncode:
        raise RuntimeError('rebuild failed; evidence preserved')
    product=json.loads(log.read_text().splitlines()[-1]); build=product['build']['build_id']
    assert product['state']=='succeeded' and product['publication']['result']=='published'
    report=dict(seconds=seconds,stage_seconds=product['stage_seconds'],build=build,prior=prior,database_bytes_before=before)
    if args.compare_current:
        # Measure immediately after publication, before the long result digests
        # can give autovacuum additional time to mark the new heap pages visible.
        timing(args)
    with closing(connect(args)) as db:
        report['database_bytes_after']=fetch(db,'SELECT pg_database_size(current_database())')[0][0]
        if args.compare_current:
            compared={}
            for table in ('mpp_statistic','mpp_observation_statistic','mpp_build_group','mpp_build_observation_group',
                          'mpp_build_layer_count','mpp_build_timing_coverage'):
                old=digest(db,table,('build_id',),'WHERE build_id=%s',(prior,))
                new=digest(db,table,('build_id',),'WHERE build_id=%s',(build,))
                assert old==new, table
                compared[table]=new
                print(json.dumps(dict(equal=True,table=table,rows=new['rows'])),flush=True)
            report['equal_results']=compared
    save(args.directory/(args.stage+'.json'),report)
    print(json.dumps(dict(stage=args.stage,seconds=seconds,stage_seconds=product['stage_seconds'])),flush=True)


def continue_import(args):
    """Import a new synthetic day into the upgraded full database, in its own scope."""
    from ingestion.test_reader import row, write_csv
    out=args.directory/(args.stage+'-input');out.mkdir(mode=0o700)
    scope='issue72-upgrade';source='issue72-next-day';batch='issue72-next-day'
    with closing(connect(args)) as db:
        assert fetch(db,"SELECT version FROM schema_version WHERE version='1.13.0'")==[('1.13.0',)]
        assert not fetch(db,'SELECT FROM scope WHERE scope_id=%s',(scope,))
        versions=fetch(db,'SELECT * FROM current_version ORDER BY scope_id')
        for kind,name in [('database','issue72_new_database'),('user','issue72_new_user')]:
            assert not fetch(db,'SELECT FROM mpp_occurrence_name WHERE kind=%s AND name=%s',(kind,name))
    incoming=out/'new-day.csv'
    write_csv(incoming,[row(text='SELECT id FROM issue72_new_input WHERE id=72',message='duration: 72 ms',
        **{'0':'2026-09-20 12:00:00 CST','1':'issue72_new_user','2':'issue72_new_database'}) for _ in range(10000)])
    config=out/'import.json'
    save(config,dict(version=1,clusters=[scope],sources={source:dict(cluster=scope,build='HashData Warehouse 3.13.13',
        timezone='UTC+08:00',declaration='Issue72 synthetic post-upgrade day')},batches={batch:dict(source=source,
        dates=['2026-09-20'],files_confirmed_complete=True,files=[dict(path=str(incoming.resolve()),closed_and_copied=True)])}))
    env={**os.environ,'PYTHONPATH':str(ROOT),
         'SQL_APM_DSN':f'host={args.directory.resolve()}/socket port={args.port} dbname=sql_apm user=sql_apm'}
    tick=time.monotonic()
    with (args.directory/(args.stage+'.log')).open('x') as log:
        subprocess.run([sys.executable,'-m','sql_apm','import','--config',str(config.resolve()),'--source',source,
                        '--batch',batch,'--workers','1'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    seconds=time.monotonic()-tick
    with closing(connect(args)) as db:
        assert fetch(db,'SELECT count(*) FROM mpp_occurrence WHERE scope_id=%s',(scope,))==[(10000,)]
        assert fetch(db,'SELECT * FROM current_version ORDER BY scope_id')==versions
        for kind,column,name in [('database','database','issue72_new_database'),('user','execution_user','issue72_new_user')]:
            actual=fetch(db,'SELECT DISTINCT name FROM mpp_occurrence_name WHERE kind=%s AND item_count>0 ORDER BY name',(kind,))
            assert (name,) in actual
            assert actual==fetch(db,'SELECT DISTINCT '+column+' FROM mpp_occurrence WHERE '+column+' IS NOT NULL ORDER BY 1')
        norm=context(args,db)['norm']
        assert fetch(db,'SELECT mpp_query_missing_rules(%s)',(norm,))==[(0,)]
    report=dict(seconds=seconds,events=10000,new_names_automatic=True,current_versions_preserved=True)
    save(args.directory/(args.stage+'.json'),report);print(json.dumps(report),flush=True)


def upgrade(args):
    """Measure only the explicit private 1.12 -> 1.13 upgrade and its locks."""
    directory=args.directory.resolve()
    with closing(connect(args,admin=True)) as db:
        assert fetch(db,"SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")==[('1.12.0',)]
        before,lsn=fetch(db,'SELECT pg_database_size(current_database()),pg_current_wal_lsn()')[0]
        temp_before=fetch(db,'SELECT temp_bytes FROM pg_stat_database WHERE datname=current_database()')[0][0]
    stop=threading.Event(); observed={}; errors=[]; tick=time.monotonic()
    def sample():
        try:
            with closing(connect(args,admin=True)) as db:
                while not stop.is_set():
                    locks=fetch(db,"""SELECT DISTINCT c.relname,l.mode,l.granted FROM pg_locks l JOIN pg_class c ON c.oid=l.relation
                        JOIN pg_stat_activity a ON a.pid=l.pid
                        WHERE c.relnamespace='sql_apm'::regnamespace AND a.application_name='psql'
                            AND a.usename='sql_apm' AND l.pid<>pg_backend_pid() AND l.locktype='relation'""")
                    for relation,mode,granted in locks:
                        key=(relation,mode,granted); now=time.monotonic()-tick
                        observed.setdefault(key,[now,now])[1]=now
                    stop.wait(.2)
        except Exception as error:
            errors.append(type(error).__name__)
    monitor=threading.Thread(target=sample); monitor.start()
    common=['--host',str(directory/'socket'),'--port',str(args.port),'--pg-bin',str(args.pg_bin)]
    try:
        with (directory/(args.stage+'.log')).open('x') as stream:
            done=subprocess.run([str(ROOT/'scripts/db/initialize.sh'),'upgrade',*common],stdout=stream,stderr=subprocess.STDOUT)
        seconds=time.monotonic()-tick
    finally:
        stop.set();monitor.join()
    report=dict(seconds=seconds,returncode=done.returncode,database_bytes_before=before,sampling_errors=errors,
                locks=[dict(relation=k[0],mode=k[1],granted=k[2],first_seconds=v[0],last_seconds=v[1]) for k,v in sorted(observed.items())])
    with closing(connect(args,admin=True)) as db:
        report['database_bytes_after'],report['wal_bytes']=fetch(db,
            'SELECT pg_database_size(current_database()),pg_wal_lsn_diff(pg_current_wal_lsn(),%s)::bigint',(lsn,))[0]
        # This counter is cumulative bytes written, not peak scratch space.
        report['temporary_bytes_written']=fetch(db,
            'SELECT temp_bytes FROM pg_stat_database WHERE datname=current_database()')[0][0]-temp_before
    save(directory/(args.stage+'.json'),report)
    if done.returncode or errors:
        raise RuntimeError('upgrade or lock sampling failed; evidence preserved')
    with (directory/(args.stage+'-check.log')).open('x') as stream:
        subprocess.run([str(ROOT/'scripts/db/initialize.sh'),'check',*common],stdout=stream,stderr=subprocess.STDOUT,check=True)
    print(json.dumps({k:report[k] for k in ('seconds','returncode','database_bytes_before','database_bytes_after','wal_bytes')}),flush=True)


def cleanup(args):
    """Expire a full-size historical fixture, preserving actual current results."""
    from sql_apm.storage.cleanup import CleanupStore
    expected=json.loads(args.before.read_text())
    source=expected['build']; old='issue72-cleanup-fixture'
    with closing(connect(args)) as db:
        versions=fetch(db,'SELECT * FROM current_version ORDER BY scope_id')
        assert fetch(db,"SELECT build_id FROM current_version WHERE scope_id='120'")==[(source,)]
        assert not fetch(db,'SELECT FROM build WHERE build_id=%s',(old,))
        tick=time.monotonic()
        with db,db.cursor() as cur:
            cur.execute("SELECT mpp_ensure_result_partition('120','2026-01-01'::date)");pid=cur.fetchone()[0]
            patch=dict(build_id=old,partition_id=pid,started_at='2026-01-01T00:00:00+08:00',
                       finished_at='2026-01-01T00:00:01+08:00',results_saved=True,state='calculated',retry_of=None)
            cur.execute('INSERT INTO build SELECT (jsonb_populate_record(NULL::build,to_jsonb(b)||%s::jsonb)).* FROM build b WHERE build_id=%s',
                        (Json(patch),source))
            # Copy full rows directly; JSON round trips for millions of rows
            # would make fixture preparation more costly than the cleanup.
            for table in ('build_check','mpp_build_layer_count','mpp_build_timing_coverage','mpp_build_group',
                          'mpp_build_observation_group','mpp_statistic','mpp_observation_statistic'):
                cur.execute("SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 "
                            "AND NOT attisdropped AND attgenerated='' ORDER BY attnum",(table,))
                columns=[r[0] for r in cur]; replacements={'build_id':old,'partition_id':pid}
                expressions=[sql.Placeholder() if c in replacements else sql.Identifier('src',c) for c in columns]
                values=[replacements[c] for c in columns if c in replacements]+[source]
                cur.execute(sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {} src WHERE src.build_id=%s').format(
                    sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),
                    sql.SQL(',').join(expressions),sql.Identifier(table)),values)
            cur.execute("INSERT INTO publication VALUES (%s,'120',%s,%s,'published',NULL,'2026-01-01T00:00:02+08:00')",
                        ('PUB:'+old,old,source))
        fixture_seconds=time.monotonic()-tick
        version=fetch(db,"SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")[0][0]
        indexes=[]
        if version=='1.13.0':
            indexes=fetch(db,"""SELECT i.indexrelid,pg_relation_size(i.indexrelid) FROM pg_partition_tree('mpp_statistic_ranking_idx') t
                JOIN pg_index i ON i.indexrelid=t.relid WHERE i.indrelid=to_regclass('mpp_statistic_p'||%s::text)""",(pid,))
            assert len(indexes)==1 and indexes[0][1]>0
        else:
            assert version=='1.12.0' and fetch(db,"SELECT to_regclass('mpp_statistic_ranking_idx')")==[(None,)]
        tick=time.monotonic(); result=CleanupStore(db).execute('120',2);seconds=time.monotonic()-tick
        save(args.directory/(args.stage+'-result.json'),result)
        assert result['state']=='succeeded'
        month=next(m for m in result['months'] if m['partition_id']==pid)
        assert month['released_bytes']>0 and month['after_bytes']==0
        assert fetch(db,'SELECT * FROM current_version ORDER BY scope_id')==versions
        if indexes:
            assert not fetch(db,'SELECT FROM pg_class WHERE oid=%s',(indexes[0][0],))
        for table,reference in expected['equal_results'].items():
            assert digest(db,table,('build_id',),'WHERE build_id=%s',(source,))==reference,table
        for table in ('mpp_statistic','mpp_observation_statistic','mpp_build_group','mpp_build_observation_group'):
            assert fetch(db,'SELECT count(*) FROM '+table+' WHERE partition_id=%s',(pid,))==[(0,)]
    report=dict(version=version,seconds=seconds,fixture_seconds=fixture_seconds,ranking_index_bytes=indexes[0][1] if indexes else 0,
                released_bytes=month['released_bytes'],formal_groups_deleted=month['formal_groups_deleted'],
                observation_groups_deleted=month['observation_groups_deleted'],current_results_equal=True)
    save(args.directory/(args.stage+'.json'),report);print(json.dumps(report),flush=True)


def snapshot(args):
    result = {}
    with closing(connect(args)) as db:
        tables = [r[0] for r in fetch(db, "SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                                    "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")]
        for table in tables:
            if table in SUMMARIES:
                continue
            tick = time.monotonic()
            result[table] = digest(db, table, (), "WHERE version<>'1.13.0'" if table == 'schema_version' else '')
            print(json.dumps(dict(table=table, rows=result[table]['rows'], seconds=round(time.monotonic()-tick, 2))), flush=True)
    save(args.directory/(args.stage+'-tables.json'), result)


def compare(args):
    count = 0
    with gzip.open(args.before, 'rt') as before, gzip.open(args.after, 'rt') as after:
        for left, right in itertools.zip_longest(before, after):
            if left is None or right is None:
                raise AssertionError('case_count_changed')
            a, b = json.loads(left), json.loads(right)
            assert all(a[key] == b[key] for key in ('label', 'values', 'rows')), a['label']
            # The dashboard variables intentionally change their source SQL.
            if a['label'] not in ('database', 'user'):
                assert a['statement'] == b['statement'], a['label']
            count += 1
    print(json.dumps(dict(equal=True, cases=count)), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'rows', 'timing', 'snapshot', 'compare', 'cli', 'import_check', 'continue_import', 'rebuild', 'upgrade', 'cleanup'))
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--port', type=int, default=55472)
    parser.add_argument('--stage')
    parser.add_argument('--before', type=Path)
    parser.add_argument('--after', type=Path)
    parser.add_argument('--base-app', type=Path)
    parser.add_argument('--logs', type=Path)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--import-config', type=Path)
    parser.add_argument('--batch')
    parser.add_argument('--training-config', type=Path)
    parser.add_argument('--compare-current', action='store_true')
    args = parser.parse_args()
    if args.mode == 'compare':
        if args.before is None or args.after is None: parser.error('--before and --after are required')
    elif args.mode == 'import_check':
        if any(x is None for x in (args.directory,args.base_app,args.import_config,args.batch)):
            parser.error('--directory, --base-app, --import-config and --batch are required')
    elif args.mode == 'prepare':
        if args.directory is None or args.base_app is None or args.logs is None:
            parser.error('--directory, --base-app and --logs are required')
    elif args.directory is None or args.stage is None or not args.stage.replace('-', '').isalnum():
        parser.error('an explicit private --directory and simple --stage name are required')
    if args.mode=='rebuild' and args.training_config is None:
        parser.error('--training-config is required')
    if args.mode=='cleanup' and args.before is None:
        parser.error('--before must name the successful rebuild --compare-current result')
    globals()[args.mode](args)


if __name__ == '__main__':
    main()
