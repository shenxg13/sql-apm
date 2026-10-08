#!/usr/bin/env python3
"""Explicit full-data checks for the 1.11.0 query functions and dashboards.

Works on a private, stopped copy of a populated 1.10.0 database. Originals, SQL
text and identities stay in the private directory; reports hold only counts,
digests, times and sizes.

  upgrade  digest every table, create the read-only account, upgrade in place,
           digest again and compare (rows and contents must not change)
  audit    compare search candidates and detail-page numbers with independent
           computations straight from the base tables
  timing   measure the page queries on a long-lived connection
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import psycopg2
from database.retention import digest

PORT = 55520


def command(args, log=None):
    done = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if log:
        log.write_text(done.stdout + done.stderr)
    if done.returncode:
        raise RuntimeError('validation_command_failed: ' + str(args[0]))
    return done.stdout


def running(directory):
    return (directory / 'pgdata/postmaster.pid').exists()


def start(directory, pg_bin, settings=''):
    (directory / 'socket').mkdir(mode=0o700, exist_ok=True)
    command([pg_bin / 'pg_ctl', '-D', directory / 'pgdata', '-l', directory / 'server.log', '-o',
             '-p %d -k %s -c listen_addresses=127.0.0.1 %s' % (PORT, directory / 'socket', settings), '-w', 'start'])


def stop(directory, pg_bin):
    command([pg_bin / 'pg_ctl', '-D', directory / 'pgdata', '-m', 'fast', '-w', 'stop'])


def connection(directory, user='sql_apm'):
    db = psycopg2.connect(host=str(directory / 'socket'), port=PORT, dbname='sql_apm', user=user)
    with db, db.cursor() as cur:
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
    return db


def snapshot(db):
    with db, db.cursor() as cur:
        cur.execute("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                    "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")
        tables = [row[0] for row in cur]
        cur.execute("SELECT pg_database_size(current_database())")
        size = cur.fetchone()[0]
    result = {}
    for table in tables:
        # The receipt of the new version is the one row an upgrade adds.
        result[table] = digest(db, table, (), "WHERE version<>'1.11.0'" if table == 'schema_version' else '')
        print('digest', table, result[table]['rows'], flush=True)
    return dict(tables=result, database_bytes=size)


def catalog(db):
    with db, db.cursor() as cur:
        cur.execute("""SELECT jsonb_build_object(
            'tables',(SELECT count(*) FROM pg_class WHERE relnamespace='sql_apm'::regnamespace AND relkind IN ('r','p') AND NOT relispartition),
            'columns',(SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='sql_apm'::regnamespace
                AND c.relkind IN ('r','p') AND NOT c.relispartition AND a.attnum>0 AND NOT a.attisdropped),
            'constraints',(SELECT count(*) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid WHERE c.relnamespace='sql_apm'::regnamespace AND NOT c.relispartition),
            'indexes',(SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE c.relnamespace='sql_apm'::regnamespace AND NOT c.relispartition),
            'functions',(SELECT count(*) FROM pg_proc WHERE pronamespace='sql_apm'::regnamespace),
            'extensions',(SELECT jsonb_agg(extname ORDER BY extname) FROM pg_extension))""")
        return cur.fetchone()[0]


def upgrade(args):
    directory = args.directory.resolve()
    if not running(directory):
        start(directory, args.pg_bin)
    db = connection(directory)
    try:
        with db, db.cursor() as cur:
            cur.execute("SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")
            if cur.fetchone()[0] != '1.10.0':
                raise ValueError('source_must_be_1_10_0')
        before = dict(snapshot(db), catalog=catalog(db))
    finally:
        db.close()
    (directory / 'before.json').write_text(json.dumps(before))
    common = ['--host', directory / 'socket', '--port', PORT, '--pg-bin', args.pg_bin]
    began = time.monotonic()
    command([ROOT / 'scripts/db/initialize.sh', 'bootstrap'] + common + ['--admin-user', args.admin_user, '--admin-database', 'postgres'],
            directory / 'bootstrap.log')
    command([ROOT / 'scripts/db/initialize.sh', 'upgrade'] + common, directory / 'upgrade.log')
    seconds = time.monotonic() - began
    command([ROOT / 'scripts/db/initialize.sh', 'check'] + common, directory / 'check.log')
    db = connection(directory)
    try:
        after = dict(snapshot(db), catalog=catalog(db))
    finally:
        db.close()
    (directory / 'after.json').write_text(json.dumps(after))
    same = before['tables'] == after['tables']
    structure = {key: (before['catalog'][key], after['catalog'][key]) for key in before['catalog']}
    report = dict(state='ok' if same else 'failed', upgrade_seconds=round(seconds, 1), rows_and_contents_unchanged=same,
                  tables=len(after['tables']), rows=sum(item['rows'] for item in after['tables'].values()),
                  database_bytes=(before['database_bytes'], after['database_bytes']), catalog_before_after=structure,
                  only_functions_added=all(structure[key][0] == structure[key][1] for key in ('tables', 'columns', 'constraints', 'indexes', 'extensions')))
    (directory / 'upgrade-report.json').write_text(json.dumps(report, indent=1))
    print(json.dumps(report))
    return 0 if same else 1


FOLD = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz', ' \t\n\r\f\v')
WORDS = [['select'], ['select', 'from'], ['from', 'select'], ['='], ['order', 'by'], ['zz_no_search_match_51'], ['%_'], ['"'],
         ['select', '*', 'from', 'where', 'and']]
PASSAGES = ['select *', 'order by', 'where 1=1', 'zz_no_search_match_51', 'SELECT\n  *\tFROM', '" as "']


def rules(db):
    from sql_apm.ingestion.config import identity
    from sql_apm.sql.normalization import Normalizer
    engine = Normalizer()
    return engine, 'N:' + identity(engine.context)


def fetch(db, statement, values=()):
    with db, db.cursor() as cur:
        cur.execute(statement, values)
        return cur.fetchall()


def audit(args):
    """Search candidates of all three modes against an implementation that never calls the query layer."""
    directory = args.directory.resolve()
    if not running(directory):
        start(directory, args.pg_bin)
    owner, reader = connection(directory), connection(directory, 'sql_apm_ro')
    engine, norm = rules(owner)
    cases = [('words', ' '.join(terms), [term.translate(FOLD) for term in terms]) for terms in WORDS] + \
            [('passage', text, [text.translate(FOLD)]) for text in PASSAGES]
    began = time.monotonic()
    with owner, owner.cursor() as cur:
        cur.execute('CREATE TEMP TABLE oracle_match(case_id integer,sql_id text) ON COMMIT PRESERVE ROWS')
    import io
    buffers, texts = [io.StringIO() for _ in cases], 0
    with owner, owner.cursor(name='original_texts') as stream:
        stream.itersize = 20000
        stream.execute('SELECT sql_id,text FROM mpp_sql_text')
        for sql_id, text in stream:
            texts += 1
            folded = text.translate(FOLD)
            for index, (_, _, terms) in enumerate(cases):
                if all(term in folded for term in terms):
                    buffers[index].write('%d\t%s\n' % (index, sql_id))
    with owner, owner.cursor() as cur:
        for buffer in buffers:
            buffer.seek(0)
            cur.copy_expert('COPY oracle_match FROM STDIN', buffer)
        cur.execute('CREATE INDEX ON oracle_match(case_id,sql_id); ANALYZE oracle_match')
    report = dict(texts_scanned=texts, oracle_seconds=round(time.monotonic() - began, 1), cases=[])
    for index, (mode, text, terms) in enumerate(cases):
        expected = fetch(owner, """WITH cells AS (
                SELECT f.value,m.sql_id,o.scope_id,o.database,o.execution_user,count(*) n,max(o.end_at) last_at
                FROM oracle_match m JOIN mpp_fingerprint f ON f.sql_id=m.sql_id AND f.normalization_id=%s AND f.profile='mpp-csv/1' AND f.state='reliable'
                JOIN mpp_occurrence o ON o.sql_id=m.sql_id WHERE m.case_id=%s GROUP BY 1,2,3,4,5),
            per_identity AS (SELECT value,scope_id,database,execution_user,sum(n) n,max(last_at) last_at FROM cells GROUP BY 1,2,3,4),
            top AS (SELECT DISTINCT ON (value) value,scope_id,database,execution_user,n,last_at FROM per_identity ORDER BY value,n DESC,scope_id,database,execution_user)
            SELECT c.value,count(DISTINCT c.sql_id),sum(c.n)::bigint,(SELECT count(*) FROM per_identity i WHERE i.value=c.value),max(c.last_at),
                t.scope_id,t.database,t.execution_user,t.n::bigint,t.last_at,
                (SELECT count(*) FROM mpp_fingerprint x WHERE x.normalization_id=%s AND x.profile='mpp-csv/1' AND x.value=c.value),count(*) OVER ()
            FROM cells c JOIN top t USING(value) GROUP BY c.value,t.scope_id,t.database,t.execution_user,t.n,t.last_at
            ORDER BY 3 DESC,1 LIMIT 50""", (norm, index, norm))
        started = time.monotonic()
        shown = fetch(reader, """SELECT s.fingerprint,s.matched_texts,s.record_count,s.identities,s.last_at,i.scope_id,i.database,i.execution_user,
                s.top_records,s.top_last_at,s.structure_texts,s.total_structures
            FROM mpp_view_search(%s,%s,%s) s LEFT JOIN LATERAL mpp_view_identity(s.top_identity) i ON true""", (norm, mode, text))
        seconds = time.monotonic() - started
        assert shown == expected, ('search candidates differ', mode, index)
        report['cases'].append(dict(mode=mode, input=text, rows=len(shown), total_structures=shown[0][11] if shown else 0,
                                    seconds=round(seconds, 2), equal=True))
        print('search', mode, index, len(shown), round(seconds, 2), flush=True)
    # Complete SQL: stored originals re-normalised by the installed rules, looked up by fingerprint.
    sample = fetch(owner, "SELECT t.sql_id,t.text,f.value FROM mpp_sql_text t JOIN mpp_fingerprint f ON f.sql_id=t.sql_id AND f.normalization_id=%s "
                          "WHERE f.state='reliable' ORDER BY md5(t.sql_id) LIMIT %s", (norm, args.exact_sample))
    service = subprocess.Popen([sys.executable, '-m', 'sql_apm', 'fingerprint-service', '--port', '0'], cwd=ROOT, text=True,
                               env=dict(__import__('os').environ, SQL_APM_DSN='host=%s port=%d dbname=sql_apm user=sql_apm_ro' % (directory / 'socket', PORT)),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    port = json.loads(service.stdout.readline())['port']
    import base64
    import urllib.request
    states, durations = {}, []
    try:
        for sql_id, text, stored in sample:
            assert engine.normalize(text)['fingerprint']['value'] == stored
            expected = fetch(owner, """SELECT count(*),count(DISTINCT (o.scope_id,o.database,o.execution_user)),max(o.end_at)
                FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id) WHERE f.normalization_id=%s AND f.profile='mpp-csv/1' AND f.state='reliable' AND f.value=%s""", (norm, stored))[0]
            shown = fetch(reader, 'SELECT record_count,identities,last_at,only_sql_id FROM mpp_view_search(%s,%s,%s,NULL,NULL,NULL,NULL,NULL,%s,%s)',
                          (norm, 'exact', stored, 'count', sql_id))
            assert (shown == [] and expected[0] == 0) or shown == [expected + (sql_id,)], ('exact candidate differs', sql_id)
            body = json.dumps(dict(sql_b64=base64.b64encode(text.encode()).decode())).encode()
            started = time.monotonic()
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/v1/exact' % port, data=body), timeout=120) as response:
                answer = json.loads(response.read())
            durations.append(time.monotonic() - started)
            assert answer['fingerprint'] == stored and answer['exact_sql_id'] == sql_id
            baseline = fetch(owner, """SELECT EXISTS (SELECT FROM mpp_baseline_group g JOIN current_version v USING(scope_id) JOIN mpp_statistic s ON s.group_id=g.group_id
                AND s.build_id=v.build_id AND s.layer='overall' WHERE g.normalization_id=%s AND g.fingerprint_value=%s)""", (norm, stored))[0][0]
            wanted = 'has_baseline' if baseline else 'records_without_baseline' if expected[0] else 'not_seen'
            assert answer['state'] == wanted, ('state differs', sql_id)
            states[wanted] = states.get(wanted, 0) + 1
    finally:
        service.terminate()
        service.communicate(timeout=20)
    durations.sort()
    report['exact'] = dict(sample=len(sample), equal=True, states=states, service_seconds=dict(
        median=round(durations[len(durations) // 2], 3), p95=round(durations[int(len(durations) * 0.95)], 3), slowest=round(durations[-1], 3)))
    owner.close()
    reader.close()
    report['state'] = 'ok'
    (directory / 'audit-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(dict(state='ok', cases=len(report['cases']), exact=report['exact'])))
    return 0


def timing(args):
    """Repeated calls on one long-lived read-only connection, as Grafana's pool makes them."""
    directory = args.directory.resolve()
    if not running(directory):
        start(directory, args.pg_bin)
    owner, reader = connection(directory), connection(directory, 'sql_apm_ro')
    _, norm = rules(owner)
    settings = dict(fetch(owner, "SELECT name,setting||coalesce(unit,'') FROM pg_settings WHERE name IN ('shared_buffers','work_mem','max_parallel_workers_per_gather','max_worker_processes')"))
    sizes = fetch(owner, "SELECT (SELECT count(*) FROM mpp_occurrence),(SELECT count(*) FROM mpp_sql_text),(SELECT count(*) FROM mpp_statistic),pg_database_size(current_database())")[0]

    def measure(label, statement, values, repeats=3):
        seconds, rows = [], 0
        for _ in range(repeats):
            started = time.monotonic()
            rows = len(fetch(reader, statement, values))
            seconds.append(round(time.monotonic() - started, 3))
        print('timing', label, seconds, rows, flush=True)
        return dict(operation=label, first=seconds[0], repeats=seconds[1:], rows=rows)
    results = []
    for mode, text in [('words', 'zz_no_search_match_51'), ('words', 'select'), ('words', 'select from where'), ('words', '='),
                       ('passage', 'select *'), ('passage', 'order by'), ('passage', 'zz_no_search_match_51')]:
        results.append(dict(measure('search ' + mode + ': ' + text, 'SELECT * FROM mpp_view_search(%s,%s,%s)', (norm, mode, text), 6), group='search'))
    # The question left by the #47 review: the same function with and without parallel workers,
    # six consecutive calls each on this one connection.
    comparison = []
    for text in ('zz_no_search_match_51', 'select', '='):
        row = dict(input=text)
        for label, setting in (('serial', '0'), ('parallel', None)):
            with reader, reader.cursor() as cur:
                cur.execute('SET max_parallel_workers_per_gather=%s' % setting if setting else 'RESET max_parallel_workers_per_gather')
            row[label] = measure('fuzzy ' + label + ': ' + text, 'SELECT * FROM mpp_query_fuzzy(%s,%s)', (norm, text), 6)
        with reader, reader.cursor() as cur:
            cur.execute('RESET max_parallel_workers_per_gather')
        comparison.append(row)
    # Worst identities, chosen by counts only.
    picks = fetch(owner, """WITH identities AS MATERIALIZED (
            SELECT o.scope_id,o.database,o.execution_user,f.value,count(*) records,count(DISTINCT o.sql_id) texts,max(o.end_at) last_at,
                (array_agg(o.timing_type ORDER BY o.timing_type) FILTER (WHERE o.timing_type IN ('request','execute_first')))[1] timing
            FROM mpp_occurrence o JOIN mpp_fingerprint f ON f.sql_id=o.sql_id AND f.normalization_id=%s AND f.state='reliable'
            WHERE o.database IS NOT NULL AND o.execution_user IS NOT NULL GROUP BY 1,2,3,4)
        (SELECT 'most records',* FROM identities WHERE timing IS NOT NULL ORDER BY records DESC LIMIT 1) UNION ALL
        (SELECT 'most texts',* FROM identities WHERE timing IS NOT NULL ORDER BY texts DESC,records DESC LIMIT 1) UNION ALL
        (SELECT 'random',* FROM identities WHERE timing IS NOT NULL ORDER BY md5(value||scope_id) LIMIT 3)""", (norm,))
    for kind, scope, database, user, fingerprint, records, texts, last_at, timing_type in picks:
        token = fetch(reader, 'SELECT mpp_view_identity_token(%s,%s,%s)', (scope, database, user))[0][0]
        build = fetch(reader, 'SELECT build_id FROM mpp_view_versions(%s,%s) WHERE selectable LIMIT 1', (norm, token))
        build = build[0][0] if build else ''
        common = (norm, fingerprint, token)
        window = (last_at - __import__('datetime').timedelta(days=7), last_at + __import__('datetime').timedelta(milliseconds=1))
        panels = [('identities', 'SELECT * FROM mpp_view_identities(%s,%s)', common[:2]),
                  ('timing categories', 'SELECT * FROM mpp_view_timings(%s,%s,%s)', common),
                  ('versions', 'SELECT * FROM mpp_view_versions(%s,%s)', (norm, token)),
                  ('sql text', 'SELECT * FROM mpp_view_sql_text(%s,%s)', common[:2]),
                  ('baseline overall', 'SELECT * FROM mpp_view_statistics(%s,%s,%s,%s)', common + (build,)),
                  ('baseline by hour', "SELECT * FROM mpp_view_statistics(%s,%s,%s,%s,'hour')", common + (build,)),
                  ('baseline by day', "SELECT * FROM mpp_view_statistics(%s,%s,%s,%s,'day')", common + (build,)),
                  ('comparison', 'SELECT * FROM mpp_view_compare(%s,%s,%s,%s,%s,%s,%s)', common + (timing_type, build) + window),
                  ('summary tiles', 'SELECT outcome,count(*) FROM mpp_view_records(%s,%s,%s,%s,%s,%s) GROUP BY 1', common + (timing_type,) + window),
                  ('points', 'SELECT * FROM mpp_view_points(%s,%s,%s,%s,%s,%s,%s)', common + (timing_type,) + window + (600000,)),
                  ('counts', 'SELECT * FROM mpp_view_counts(%s,%s,%s,%s,%s,%s,%s)', common + (timing_type,) + window + (600000,)),
                  ('texts', 'SELECT * FROM mpp_view_texts(%s,%s,%s,%s,%s,%s,%s)', common + (timing_type, build) + window),
                  ('executions newest', 'SELECT * FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s)', common + (timing_type, build) + window),
                  ('executions slowest', "SELECT * FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,'slowest')", common + (timing_type, build) + window),
                  ('missing rules note', 'SELECT mpp_query_missing_rules(%s)', (norm,))]
        for label, statement, values in panels:
            results.append(dict(measure('detail [' + kind + '] ' + label, statement, values), group='detail', identity=kind,
                                identity_records=records, identity_texts=texts))
    for scope, day in fetch(owner, "SELECT scope_id,(SELECT date_trunc('day',max(end_at)) FROM mpp_occurrence o WHERE o.scope_id=s.scope_id) FROM scope s ORDER BY 1"):
        window = (day - __import__('datetime').timedelta(days=1), day)
        results.append(dict(measure('list: time-range ranking, 24 hours, cluster ' + scope, 'SELECT * FROM mpp_view_ranking(%s,%s,%s,%s)', (norm,) + window + (scope,)), group='list'))
        results.append(dict(measure('list: time-range ranking, 7 days, cluster ' + scope, 'SELECT * FROM mpp_view_ranking(%s,%s,%s,%s)',
                                    (norm, day - __import__('datetime').timedelta(days=7), day, scope)), group='list'))
    results.append(dict(measure('list: time-range ranking, 24 hours, all clusters', 'SELECT * FROM mpp_view_ranking(%s,%s,%s)', (norm,) + window), group='list'))
    for order in ('p95', 'samples', 'max'):
        results.append(dict(measure('list: current-baseline ranking by ' + order, 'SELECT * FROM mpp_view_baseline_ranking(%s,NULL,NULL,NULL,%s,%s)',
                                    (norm, 'request,execute_first', order)), group='list'))
    results.append(dict(measure('list: versions', 'SELECT * FROM mpp_query_versions(%s)', (norm,)), group='list'))
    owner.close()
    reader.close()
    targets = dict(search=10, detail=5, list=10)
    slowest = {group: max(max([item['first']] + item['repeats']) for item in results if item['group'] == group) for group in targets}
    report = dict(state='ok', evidence='measured', settings=settings, occurrences=sizes[0], texts=sizes[1], statistic_rows=sizes[2], database_bytes=sizes[3],
                  slowest_seconds=slowest, fuzzy_serial_against_parallel=comparison, results=results)
    (directory / 'timing-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(dict(state='ok', slowest_seconds=slowest, operations=len(results))))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['upgrade', 'audit', 'timing'])
    parser.add_argument('--directory', type=Path, required=True, help='含 pgdata 的私有目录（已停止的 1.10.0 真实库副本）')
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--admin-user', default='apm_test_admin')
    parser.add_argument('--exact-sample', type=int, default=300, help='audit：抽样多少份原文做完整 SQL 核对')
    args = parser.parse_args()
    return dict(upgrade=upgrade, audit=audit, timing=timing)[args.action](args)


if __name__ == '__main__':
    raise SystemExit(main())
