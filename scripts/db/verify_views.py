#!/usr/bin/env python3
"""Synthetic acceptance for the 1.11.0 read-only account, fingerprint service and
dashboard query functions, on a private disposable PostgreSQL 17.

Every expected value is computed straight from the base tables, not through the
query layer under test.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

RESOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', str(RESOURCE_ROOT))).resolve()
sys.path[:0] = [str(ROOT), str(RESOURCE_ROOT / 'tests')]
import psycopg2
from verify import instance, Verification
from database import dashboard_data as data
from database.retention import clone_build, contents
from sql_apm.ingestion.config import identity
from sql_apm.sql.normalization import MAX_BYTES, Normalizer
from sql_apm.storage.cleanup import CleanupStore
from sql_apm.storage.ingestion import connect

SOCKET_PORT = 55473
RANGE = ('2026-07-21 00:00:00+08', '2026-07-24 00:00:00+08')
IDENTITY = "o.scope_id='C1' AND o.database='shop' AND o.execution_user='app_user'"


class Database:
    def __init__(self, dsn):
        self.db = psycopg2.connect(dsn)
        with self.db, self.db.cursor() as cur:
            cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")

    def rows(self, statement, values=()):
        with self.db, self.db.cursor() as cur:
            cur.execute(statement, values)
            return cur.fetchall()

    def one(self, statement, values=()):
        return self.rows(statement, values)[0][0]

    def rejects(self, statement, text, values=()):
        try:
            self.rows(statement, values)
        except psycopg2.Error as error:
            self.db.rollback()
            assert text in str(error), (text, str(error))
        else:
            raise AssertionError('accepted: ' + statement)


def token(*parts):
    return base64.urlsafe_b64encode(json.dumps(list(parts), ensure_ascii=False).encode()).decode().rstrip('=')


def post(port, body, raw=None, length=None):
    payload = raw if raw is not None else json.dumps(body).encode()
    request = urllib.request.Request('http://127.0.0.1:%d/v1/exact' % port, data=payload,
                                     headers={'Content-Type': 'application/json'})
    if length is not None:
        request.add_unredirected_header('Content-Length', str(length))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def sql_b64(text):
    raw = text if isinstance(text, bytes) else text.encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


class Service:
    def __init__(self, dsn):
        self.process = subprocess.Popen([sys.executable, '-m', 'sql_apm', 'fingerprint-service', '--port', '0'],
                                        cwd=ROOT, env=dict(os.environ, SQL_APM_DSN=dsn), text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        first = json.loads(self.process.stdout.readline())
        assert first['state'] == 'listening' and first['host'] == '127.0.0.1', first
        self.port = first['port']

    def stop(self):
        self.process.terminate()
        output = self.process.communicate(timeout=20)[0]
        return [json.loads(line) for line in output.splitlines() if line.strip()]


def account(v, directory, owner, reader):
    """G3: the read-only account can run every query in scope and nothing else."""
    assert reader.one('SELECT current_user') == 'sql_apm_ro'
    assert reader.one('SHOW statement_timeout') == '2min' and reader.one('SHOW default_transaction_read_only') == 'on'
    assert reader.one('SHOW search_path') == 'sql_apm, pg_catalog'
    assert reader.one("SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) "
                      "FROM pg_roles WHERE rolname=current_user")
    tables = [r[0] for r in owner.rows("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                                       "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")]
    for table in tables:
        reader.rows('SELECT count(*) FROM "' + table + '"')
        assert reader.one("SELECT has_table_privilege(current_user,%s,'SELECT') AND NOT has_table_privilege(current_user,%s,"
                          "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')", ('sql_apm.' + table,) * 2), table
    assert len(tables) == 57
    v.require(True, 'G3: read-only account defaults; SELECT on all 57 tables and no other table privilege')
    refused = ["DELETE FROM mpp_occurrence", "UPDATE mpp_sql_text SET text='x'", "TRUNCATE mpp_occurrence",
               "INSERT INTO scope VALUES ('X','mpp','mpp-csv/1','1.0.0')", "CREATE TABLE sql_apm.probe (id integer)",
               "CREATE TABLE public.probe (id integer)", "CREATE TEMP TABLE probe (id integer)",
               "CREATE OR REPLACE FUNCTION sql_apm.mpp_view_rules() RETURNS text LANGUAGE sql RETURN 'x'",
               "DROP FUNCTION mpp_view_rules()", "ALTER TABLE scope ADD COLUMN probe text", "DROP TABLE scope",
               "CREATE INDEX probe ON mpp_occurrence (end_at)", "SELECT mpp_ensure_result_partition('C1','2027-01-01')",
               "ALTER ROLE sql_apm_ro SUPERUSER", "CREATE ROLE probe", "COPY scope FROM PROGRAM 'true'"]
    for statement in refused:
        reader.rejects(statement, 'read-only transaction' if not statement.startswith(('ALTER ROLE', 'CREATE ROLE', 'COPY')) else '')
    with reader.db, reader.db.cursor() as cur:
        cur.execute('SET default_transaction_read_only=off')
    for statement in refused:
        try:
            reader.rows(statement)
        except psycopg2.Error as error:
            reader.db.rollback()
            assert 'permission denied' in str(error) or 'must be owner' in str(error) or 'must be superuser' in str(error) \
                or 'Only roles with' in str(error), (statement, str(error))
        else:
            raise AssertionError('accepted without the session read-only default: ' + statement)
    assert contents(owner.db) == v.before
    v.require(True, 'G3: writes, DDL and role changes refused, and still refused by privileges with the session read-only default off')
    # The timeout is a role default set by the administrator and can be changed by running bootstrap again.
    v.init('bootstrap', extra=['--readonly-timeout', '300ms'])
    short = Database(v.reader_dsn)
    assert short.one('SHOW statement_timeout') == '300ms'
    short.rejects('SELECT pg_sleep(2)', 'statement timeout')
    short.db.close()
    v.init('bootstrap')
    again = Database(v.reader_dsn)
    assert again.one('SHOW statement_timeout') == '2min'
    again.db.close()
    for mode in ('schema', 'check', 'upgrade', 'all'):
        v.init(mode)
    assert contents(owner.db) == v.before
    v.require(True, 'G3: statement timeout enforced and configurable; reruns of every mode keep data and grants')


def service(v, owner, cli):
    """G5: the service answers like the command line and adds the identical text."""
    running = Service(v.reader_dsn)
    marker = "SELECT 'zz_private_marker_51' FROM never_logged"
    try:
        cases = [data.SPARSE, "select c.name from customers c where c.region='south'", 'SELECT nothing FROM never_seen_51',
                 'SELECT ?', "SELECT x FROM nowhere_51; " + data.SPARSE.replace('north', 'east'), data.BATCH, data.SPECIAL, marker]
        for text in cases:
            status, answer = post(running.port, dict(sql_b64=sql_b64(text)))
            code, expected = cli(['exact', '--sql', text])
            assert status == 200 and code == 0
            stored = owner.rows('SELECT sql_id FROM mpp_sql_text WHERE text=%s', (text,))
            assert answer.pop('exact_sql_id') == (stored[0][0] if stored else None), text[:20]
            assert answer.pop('input') == dict(bytes=len(text.encode()), sha256=hashlib.sha256(text.encode()).hexdigest())
            assert answer == expected, (answer, expected)
        # One line-break form per text in a browser editor: CR LF and LF count as the same text.
        with owner.db, owner.db.cursor() as cur:
            cur.execute("INSERT INTO mpp_sql_text(sql_id,text,content_sha256) VALUES ('crlf',%s,sha256(convert_to(%s,'UTF8')))", ('SELECT 1,\r\n2',) * 2)
        assert post(running.port, dict(sql_b64=sql_b64('SELECT 1,\n2')))[1]['exact_sql_id'] == 'crlf'
        assert post(running.port, dict(sql_b64=sql_b64('SELECT 1,\r\n2')))[1]['exact_sql_id'] == 'crlf'
        assert post(running.port, dict(sql_b64=sql_b64('SELECT 1, 2')))[1]['exact_sql_id'] is None
        multi = data.SPARSE.replace(' FROM', '\nFROM')
        assert post(running.port, dict(sql_b64=sql_b64(multi)))[1]['exact_sql_id'] is None
        with owner.db, owner.db.cursor() as cur:
            cur.execute("DELETE FROM mpp_sql_text WHERE sql_id='crlf'")
        states = [post(running.port, dict(sql_b64=sql_b64(text)))[1]['state'] for text in cases[:4]]
        assert states == ['has_baseline', 'has_baseline', 'not_seen', 'unreliable_fingerprint']
        assert post(running.port, dict(sql_b64=sql_b64(data.FAILING)))[1]['state'] == 'records_without_baseline'
        hints = post(running.port, dict(sql_b64=sql_b64(cases[4])))[1]
        assert hints['state'] == 'not_seen' and [h['state'] for h in hints['statement_hints']] == ['not_seen', 'has_baseline']
        filtered = post(running.port, dict(sql_b64=sql_b64(data.SPARSE), cluster='C2'))[1]
        assert [h['scope_id'] for h in filtered['hits']] == ['C2']
        v.require(True, 'G5: four outcomes, batch hints and filters equal the command line; identical stored text reported')
        over = b'x' * (MAX_BYTES + 1)
        status, answer = post(running.port, dict(sql_b64=sql_b64(over)))
        assert status == 200 and answer['state'] == 'unreliable_fingerprint' and answer['reason'] == 'input_size_limit'
        large = v.directory / 'large.sql'
        large.write_bytes(over)
        assert cli(['exact', '--file', str(large)])[1]['reason'] == 'input_size_limit'
        status, answer = post(running.port, None, raw=b'{}', length=50 * 1024 * 1024)
        assert status == 200 and answer['reason'] == 'input_size_limit'
        edge = 'SELECT ' + ','.join(['1'] * 200000)
        assert len(edge.encode()) <= MAX_BYTES and post(running.port, dict(sql_b64=sql_b64(edge)))[0] == 200
        for raw in (b'', b'not json', b'[]', b'{"sql":"x"}', b'{"sql_b64":5}', b'{"sql_b64":"***"}',
                    json.dumps(dict(sql_b64=sql_b64('SELECT 1'), extra=1)).encode(),
                    json.dumps(dict(sql_b64=sql_b64('SELECT 1'), cluster='')).encode()):
            assert post(running.port, None, raw=raw) == (400, dict(state='failed', reason='invalid_request')), raw
        for path, method in (('/v1/exact', 'GET'), ('/', 'GET'), ('/v1/other', 'POST')):
            request = urllib.request.Request('http://127.0.0.1:%d%s' % (running.port, path), method=method,
                                             data=b'{}' if method == 'POST' else None)
            try:
                urllib.request.urlopen(request, timeout=10)
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError(path)
        with urllib.request.urlopen('http://127.0.0.1:%d/v1/health' % running.port, timeout=10) as response:
            health = json.loads(response.read())
        assert health['rules']['normalization_id'] == v.norm and health['max_bytes'] == MAX_BYTES
        with owner.db, owner.db.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_stat_activity WHERE usename='sql_apm' AND application_name='' AND pid<>pg_backend_pid()")
        v.require(True, 'G5: oversized input answered as on the command line without reading it; malformed requests refused')
    finally:
        lines = running.stop()
    assert lines and all(set(line) == {'at', 'bytes', 'result', 'ms'} for line in lines), lines[:2]
    text = json.dumps(lines)
    assert 'zz_private_marker_51' not in text and 'never_logged' not in text and 'SELECT' not in text.upper().replace('"RESULT"', '')
    assert {'has_baseline', 'not_seen', 'unreliable_fingerprint', 'records_without_baseline', 'invalid_request'} <= {line['result'] for line in lines}
    assert contents(owner.db) == v.before
    refused = subprocess.run([sys.executable, '-m', 'sql_apm', 'fingerprint-service', '--host', '0.0.0.0', '--port', '0'],
                             cwd=ROOT, env=dict(os.environ, SQL_APM_DSN=v.reader_dsn), capture_output=True, text=True, timeout=30)
    assert refused.returncode == 2 and 'loopback' in refused.stderr
    down = Service('host=/nonexistent port=1 dbname=sql_apm user=sql_apm_ro')
    try:
        assert post(down.port, dict(sql_b64=sql_b64('SELECT 1'))) == (503, dict(state='failed', reason='database_unavailable'))
    finally:
        assert down.stop()[-1]['result'] == 'database_unavailable'
    v.require(True, 'G5: loopback only; log holds time, length, result class and duration and no SQL text; read-only; database loss reported')


def views(v, owner, reader, builds):
    norm, fp = v.norm, v.fingerprint
    busy = fp(data.BUSY.format(status=1, day='2026-06-26'))
    ident = token('C1', 'shop', 'app_user')
    current, older = builds['current'], builds['older']
    text_of = lambda status, day: owner.one('SELECT sql_id FROM mpp_sql_text WHERE text=%s', (data.BUSY.format(status=status, day=day),))
    # ---- tokens and rules
    for value in ('a"b\'c\\n$name ${x} [[y]] 中文\n\ttab', '', ' ', 'x' * 70000):
        assert reader.one('SELECT mpp_view_decode(mpp_view_encode(%s))', (value,)) == value
        assert reader.one('SELECT mpp_view_encode(%s)', (value,)) == base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')
    assert reader.rows('SELECT * FROM mpp_view_identity(%s)', (token('C1', None, 'u"x'),)) == [('C1', None, 'u"x')]
    assert reader.rows("SELECT * FROM mpp_view_identity('')") == [] and reader.rows('SELECT * FROM mpp_view_identity(NULL)') == []
    assert reader.one('SELECT mpp_view_identity_token(%s,%s,%s)', ('C1', 'shop', 'app_user')) == ident
    assert reader.one('SELECT mpp_view_rules()') == norm
    assert reader.one('SELECT mpp_query_text_id(%s)', (data.SPECIAL,)) == owner.one('SELECT sql_id FROM mpp_sql_text WHERE text=%s', (data.SPECIAL,))
    assert reader.one('SELECT mpp_query_text_id(%s)', (data.SPECIAL + ' ',)) is None
    for a, b, n in (('abc', 'abd', 2), ('', 'x', 0), ('same', 'same', 4), ('中文一', '中文二', 2), ('x' * 5000 + 'a', 'x' * 5000 + 'b', 5000)):
        assert reader.one('SELECT mpp_view_common_prefix(%s,%s)', (a, b)) == n
    v.require(True, 'tokens round-trip every character class; rules, identical-text lookup and prefix helper')
    # ---- G13: identities, timing categories, versions
    expected = owner.rows("SELECT o.scope_id,o.database,o.execution_user,count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) "
                          "WHERE f.value=%s GROUP BY 1,2,3 ORDER BY 4 DESC,1,2,3", (busy,))
    listed = reader.rows('SELECT scope_id,database,execution_user,record_count FROM mpp_view_identities(%s,%s)', (norm, busy))
    assert listed == expected and len(listed) == 3 and listed[0][:3] == ('C1', 'shop', 'app_user')
    extended, ext_ident = fp(data.EXTENDED), token('C1', 'shop', 'app_user')
    timings = reader.rows('SELECT timing,record_count,label FROM mpp_view_timings(%s,%s,%s)', (norm, extended, ext_ident))
    direct = dict(owner.rows("SELECT coalesce(o.timing_type,'unknown'),count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) "
                             "WHERE f.value=%s GROUP BY 1", (extended,)))
    assert [t[0] for t in timings] == ['execute_first', 'execute_fetch', 'parse', 'bind', 'unknown']
    assert {t[0]: t[1] for t in timings} == direct and '阶段或调用' in timings[0][2]
    assert [t[0] for t in reader.rows('SELECT timing FROM mpp_view_timings(%s,%s,%s)', (norm, busy, ident))] == ['request']
    assert reader.rows('SELECT timing,record_count FROM mpp_view_timings(%s,%s,%s)', (norm, fp(data.FAILING), ident)) == [('unknown', 28)]
    assert reader.rows('SELECT timing FROM mpp_view_timings(%s,%s,%s)', (norm, busy, token('C1', 'absent', 'nobody'))) == []
    versions = reader.rows('SELECT build_id,selectable,status,is_current FROM mpp_view_versions(%s,%s)', (norm, ident))
    assert versions == [(current, True, '当前生效', True), (older, True, '可选作参照', False)]
    v.require(True, 'G13: identities are exactly the combinations that occurred; timing categories in the agreed order; version list')
    # ---- G14/G15: statistics are the stored ones, for every layer and timing
    for layer in ('overall', 'day', 'week', 'weekday', 'hour'):
        for fingerprint, who in ((busy, ('C1', 'shop', 'app_user')), (extended, ('C1', 'shop', 'app_user'))):
            shown = reader.rows('SELECT to_jsonb(s) FROM mpp_view_statistics(%s,%s,%s,%s,%s) s', (norm, fingerprint, token(*who), current, layer))
            through = owner.rows('SELECT to_jsonb(s) FROM mpp_query_statistics(%s,%s,%s,%s,%s,%s,%s) s', (norm,) + who + (fingerprint, current, layer))
            assert shown == through and shown
            stored = owner.rows("""SELECT g.timing_type,s.bucket_date,s.bucket_number,s.included_count,s.excluded_count,s.exclusions_by_reason,
                    s.min_ms,s.max_ms,s.mean_ms,s.p25_ms,s.p50_ms,s.p75_ms,s.p90_ms,s.p95_ms,s.p99_ms,s.stddev_ms,s.cv,s.mad_ms,s.iqr_ms,
                    s.log_median,s.log_mad,s.p95_p50,s.p99_p50,cardinality(s.active_dates)
                FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id)
                WHERE s.build_id=%s AND s.layer=%s AND g.fingerprint_value=%s AND g.scope_id=%s AND g.database=%s AND g.execution_user=%s""",
                                (current, layer, fingerprint) + who)
            keys = ('timing_type', 'bucket_date', 'bucket_number', 'included_count', 'excluded_count', 'exclusions_by_reason',
                    'min_ms', 'max_ms', 'mean_ms', 'p25_ms', 'p50_ms', 'p75_ms', 'p90_ms', 'p95_ms', 'p99_ms', 'stddev_ms', 'cv',
                    'mad_ms', 'iqr_ms', 'log_median', 'log_mad', 'p95_p50', 'p99_p50', 'active_days')
            got = {(r[0]['timing_type'], r[0]['bucket_date'], r[0]['bucket_number']): r[0] for r in shown if r[0]['sample_state'] == 'available' or r[0]['excluded_count']}
            assert len(got) == len(stored)
            for row in stored:
                item = got[(row[0], row[1].isoformat() if row[1] else None, row[2])]
                for key, value in zip(keys[3:], row[3:]):
                    assert (float(item[key]) if isinstance(value, __import__('decimal').Decimal) else item[key]) == \
                        (float(value) if isinstance(value, __import__('decimal').Decimal) else value), (layer, key)
    overall = {r[0]['timing_type']: r[0] for r in reader.rows('SELECT to_jsonb(s) FROM mpp_view_statistics(%s,%s,%s,%s) s', (norm, busy, ident, current))}
    assert overall['request']['exclusions_by_reason'] == {'excluded_interval': 40}
    assert [k for k, r in overall.items() if r['sample_state'] == 'no_samples'] == ['execute_first', 'execute_fetch', 'parse', 'bind']
    medium = {r[0]['timing_type']: r[0] for r in reader.rows('SELECT to_jsonb(s) FROM mpp_view_statistics(%s,%s,%s,%s) s',
                                                              (norm, fp(data.MEDIUM), token('C1', 'crm', 'app_user'), current))}['request']['sufficiency']
    assert (medium['basic']['met'], medium['p95']['met'], medium['p99']['met']) == (True, True, False)
    assert medium['p99']['reasons'] == ['sample_count_below_min']
    for build in ('', None, 'no-such-build'):
        assert reader.rows('SELECT * FROM mpp_view_statistics(%s,%s,%s,%s)', (norm, busy, ident, build)) == []
    assert reader.rows('SELECT * FROM mpp_view_statistics(%s,%s,%s,%s)', ('N:other', busy, ident, current)) == []
    assert reader.rows('SELECT * FROM mpp_view_statistics(%s,%s,%s,%s)', (norm, busy, token('C1', None, 'app_user'), current)) == []
    v.require(True, 'G14/G15: five timings, five layers and every metric equal the stored statistics; exclusions and conditions; no version means no row')
    # ---- G16: comparison counts
    facts = owner.rows("SELECT o.duration_ms,o.outcome,o.end_at,o.sql_id,o.analysis_id,o.occurrence_id,o.timing_type FROM mpp_occurrence o "
                       "JOIN mpp_fingerprint f USING(sql_id) WHERE f.value=%s AND " + IDENTITY + " AND o.end_at>=%s AND o.end_at<%s "
                       "AND (o.timing_type='request' OR (o.timing_type IS NULL AND o.outcome<>'success'))", (busy,) + RANGE)
    timed = [r for r in facts if r[0] is not None]
    base = overall['request']
    compared = reader.rows('SELECT * FROM mpp_view_compare(%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE)
    for (name, value, met, known, above, share, expected_share, note), key in zip(compared, ('p50_ms', 'p95_ms', 'p99_ms')):
        count = sum(1 for r in timed if float(r[0]) > base[key])
        assert (float(value), met, known, above, note) == (base[key], True, len(timed), count, None)
        assert abs(float(share) - count / len(timed)) < 0.00006
    assert [float(r[6]) for r in compared] == [0.5, 0.05, 0.01]
    partial = reader.rows('SELECT reference,condition_met,above,above_share,note FROM mpp_view_compare(%s,%s,%s,%s,%s,%s,%s)',
                          (norm, fp(data.MEDIUM), token('C1', 'crm', 'app_user'), 'request', current) + RANGE)
    assert partial[2] == ('P99', False, None, None, '基线样本不足，不作参照') and partial[0][1] and partial[1][1]
    sparse = reader.rows('SELECT condition_met,above,note FROM mpp_view_compare(%s,%s,%s,%s,%s,%s,%s)',
                         (norm, fp(data.SPARSE), token('C1', 'crm', 'app_user'), 'request', current) + RANGE)
    assert sparse == [(False, None, '基线样本不足，不作参照')] * 3
    none = reader.rows('SELECT baseline_ms,note FROM mpp_view_compare(%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', '') + RANGE)
    assert none == [(None, '所选版本没有这一计时类别的基线')] * 3
    v.require(True, 'G16: counts and shares above the baseline equal an independent count; unmet conditions are not compared and say so')
    # ---- G17: points, slots, markers, filters, counts
    assert sorted((r[1], len([x for x in facts if x[1] == r[1]])) for r in reader.rows(
        'SELECT 1,outcome FROM mpp_view_records(%s,%s,%s,%s,%s,%s) GROUP BY outcome', (norm, busy, ident, 'request') + RANGE)) == \
        sorted((o, len([x for x in facts if x[1] == o])) for o in {x[1] for x in facts})
    assert len(reader.rows('SELECT 1 FROM mpp_view_records(%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request') + RANGE)) == len(facts)
    for step in (3600000, 60000, 1):
        points = reader.rows('SELECT end_at,duration_ms,kind,slot_count FROM mpp_view_points(%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request') + RANGE + (step,))
        slots = {}
        for duration, _, end, *_ in timed:
            slots.setdefault(int(end.timestamp() * 1000) // step, []).append((duration, end))
        expected_points = []
        for members in slots.values():
            expected_points.append((max(members, key=lambda m: (m[0], -m[1].timestamp())), 'slowest'))
            if len(members) > 1:
                expected_points.append((min(members, key=lambda m: (m[0], -m[1].timestamp())), 'fastest'))
        assert sorted((p[1], p[2]) for p in points) == sorted((m[0], kind) for m, kind in expected_points), step
        assert all((p[1], p[0]) in {(d, e) for d, _, e, *_ in timed} for p in points)
        assert all(p[1] is not None and p[1] > 0 for p in points)
    assert len(points) == len(timed) and {p[2] for p in points} == {'slowest'}
    bounded = reader.rows('SELECT duration_ms FROM mpp_view_points(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request') + RANGE + (1, None, 300, 600))
    assert len(bounded) == sum(1 for r in timed if 300 <= r[0] <= 600) and all(300 <= r[0] <= 600 for r in bounded)
    counted = {(r[0], r[1]): r[2] for r in reader.rows('SELECT slot_at,outcome,record_count FROM mpp_view_counts(%s,%s,%s,%s,%s,%s,%s)',
                                                         (norm, busy, ident, 'request') + RANGE + (3600000,))}
    direct = {}
    for _, outcome, end, *_ in facts:
        key = (end.replace(minute=0, second=0, microsecond=0), outcome)
        direct[key] = direct.get(key, 0) + 1
    assert counted == direct and {k[1] for k in counted} >= {'success', 'failed'}
    v.require(True, 'G17: each slot shows only its slowest and fastest real execution, one point when alone, every execution at a 1 ms step; '
                    'failures carry no duration; duration filters; counts per slot and outcome equal an independent count')
    # ---- G18: per-text table
    per_text = {}
    for duration, outcome, end, sql_id, *_ in facts:
        per_text.setdefault(sql_id, []).append(duration)
    def median(values):
        known = sorted(float(x) for x in values if x is not None)
        return None if not known else (known[len(known) // 2] if len(known) % 2 else (known[len(known) // 2 - 1] + known[len(known) // 2]) / 2)
    p95 = base['p95_ms']
    for order, key in (('count', lambda item: len(item[1])), ('median', lambda item: median(item[1])),
                       ('slowest', lambda item: float(max(x for x in item[1] if x is not None)))):
        shown = reader.rows('SELECT rank,sql_id,executions,known_executions,median_ms,slowest_ms,above_p95,above_p95_share,search_hit,range_texts,differing '
                            'FROM mpp_view_texts(%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + (order,))
        ranked = sorted(per_text.items(), key=lambda item: (-key(item), item[0]))[:10]
        assert [r[1] for r in shown] == [item[0] for item in ranked], order
        assert [r[0] for r in shown] == list(range(1, 11)) and all(r[9] == len(per_text) and not r[8] for r in shown)
        for row, (sql_id, values) in zip(shown, ranked):
            known = [float(x) for x in values if x is not None]
            above = sum(1 for x in known if x > p95)
            assert (row[2], row[3], float(row[5]), row[6]) == (len(values), len(known), max(known), above)
            assert abs(float(row[4]) - median(values)) < 0.0006 and abs(float(row[7]) - above / len(known)) < 0.00006
            assert row[10] and len(row[10]) < 40
    assert len(per_text) == 80
    chosen = text_of(3, '2026-06-28')
    only = reader.rows('SELECT DISTINCT sql_id FROM mpp_view_records(%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request') + RANGE + (chosen,))
    assert only == [(chosen,)]
    one = reader.rows('SELECT known_executions,above FROM mpp_view_compare(%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + (chosen,))
    assert one[1] == (len([x for x in per_text[chosen] if x is not None]), sum(1 for x in per_text[chosen] if x is not None and float(x) > p95))
    assert float(reader.one('SELECT baseline_ms FROM mpp_view_compare(%s,%s,%s,%s,%s,%s,%s,%s) LIMIT 1', (norm, busy, ident, 'request', current) + RANGE + (chosen,))) == base['p50_ms']
    hits = reader.rows('SELECT sql_id,search_hit FROM mpp_view_texts(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + ('count', 'passage', 'o.status = 3 AND'))
    status3 = {r[0] for r in owner.rows("SELECT sql_id FROM mpp_sql_text WHERE text LIKE %s", ('%o.status = 3 AND%',))}
    assert all(r[1] == (r[0] in status3) for r in hits) and [r[1] for r in hits] == [True] * 10
    words = reader.rows('SELECT sql_id,search_hit FROM mpp_view_texts(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + ('count', 'words', "'2026-06-28' status"))
    day = {r[0] for r in owner.rows("SELECT sql_id FROM mpp_sql_text WHERE text LIKE %s", ("%'2026-06-28'%",))}
    assert sum(r[1] for r in words) == 5 and all(r[1] == (r[0] in day) for r in words) and all(r[1] for r in words[:5])
    exact = reader.rows('SELECT sql_id,search_hit FROM mpp_view_texts(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + ('count', 'exact', '', chosen))
    assert exact[0] == (chosen, True) and not any(r[1] for r in exact[1:])
    single = reader.rows('SELECT differing,range_texts FROM mpp_view_texts(%s,%s,%s,%s,%s,%s,%s)', (norm, fp(data.MEDIUM), token('C1', 'crm', 'app_user'), 'request', current) + RANGE)
    assert single == [('（只有这一份原文）', 1)]
    reader.rejects('SELECT * FROM mpp_view_texts(%s,%s,%s,%s,%s,%s,%s,%s)', 'invalid_text_order', (norm, busy, ident, 'request', current) + RANGE + ('other',))
    v.require(True, 'G18: per-text numbers equal independent statistics in all three orders, ten texts at most; one text restricts records and comparison '
                    'while the baseline stays; texts hit by the search come first and are marked')
    # ---- G19: execution list
    for order, key in (('latest', lambda r: (r[2], r[4], r[5])), ('slowest', lambda r: (r[0] is not None, r[0] or 0, r[2], r[4], r[5]))):
        page = reader.rows('SELECT end_at,duration_ms,outcome,comparison,request_shape,sql_id,source_file,source_lines,training,analysis_id,occurrence_id,matching '
                           'FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + (None, None, None, None, order, 25))
        expected = sorted(facts, key=key, reverse=True)[:25]
        assert [(r[9], r[10]) for r in page] == [(r[4], r[5]) for r in expected], order
        assert all(r[11] == len(facts) and r[6] == 'c1.csv' and r[7].isdigit() and r[4] == 'single' for r in page)
        for row in page:
            duration = None if row[1] is None else float(row[1])
            wanted = '耗时未知' if duration is None else '高于 P99' if duration > base['p99_ms'] else '高于 P95' if duration > base['p95_ms'] \
                else '高于 P50' if duration > base['p50_ms'] else '不高于 P50'
            assert row[3] == wanted
    failed = reader.rows('SELECT outcome,duration_ms,training FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + (None, 'failed,cancelled'))
    assert len(failed) == sum(1 for r in facts if r[1] in ('failed', 'cancelled')) and all(r[1] is None for r in failed)
    assert {r[2] for r in failed} == {'被排除：执行失败、耗时未知、计时类别未知、开始时间未知', '被排除：执行被取消、耗时未知、计时类别未知、开始时间未知'}
    bounded = reader.rows('SELECT duration_ms FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm, busy, ident, 'request', current) + RANGE + (None, None, 300, 600, 'slowest', 1000))
    assert len(bounded) == sum(1 for r in timed if 300 <= r[0] <= 600)
    excluded = reader.rows("SELECT DISTINCT training FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s)", (norm, busy, ident, 'request', current, '2026-07-10 02:00+08', '2026-07-10 03:00+08'))
    assert excluded == [('被排除：落在排除时段',)]
    outside = reader.rows("SELECT DISTINCT training FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,NULL,'success')", (norm, busy, ident, 'request', older, '2026-07-20+08', '2026-07-21+08'))
    assert outside == [('在训练窗口之外',)]
    assert reader.rows("SELECT DISTINCT training,comparison FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                       (norm, busy, ident, 'request', '') + RANGE + (None, 'success', None, None, 'latest', 5)) == [('未选择基线版本', '没有基线')]
    reader.rejects('SELECT * FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', 'invalid_execution_order', (norm, busy, ident, 'request', current) + RANGE + (None, None, None, None, 'x'))
    reader.rejects('SELECT * FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', 'invalid_page_size', (norm, busy, ident, 'request', current) + RANGE + (None, None, None, None, 'latest', 1001))
    batch = reader.rows("SELECT DISTINCT request_shape FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s)", (norm, fp(data.BATCH), ident, 'request', current) + RANGE)
    assert batch == [('batch',)]
    v.require(True, 'G19: both orders take the first rows of the whole range; fields; training decisions included, excluded with reasons and outside the window')
    # ---- G20: rankings
    day = ('2026-07-23 00:00+08', '2026-07-24 00:00+08')
    direct = owner.rows("""SELECT o.scope_id,o.database,o.execution_user,f.value,o.timing_type,count(*),count(o.duration_ms),sum(o.duration_ms),max(o.duration_ms)
        FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.state='reliable' AND o.end_at>=%s AND o.end_at<%s
            AND o.timing_type IN ('request','execute_first') GROUP BY 1,2,3,4,5""", day)
    errors = dict((r[:4], r[4]) for r in owner.rows("""SELECT o.scope_id,o.database,o.execution_user,f.value,count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id)
        WHERE f.state='reliable' AND o.end_at>=%s AND o.end_at<%s AND o.timing_type IS NULL AND o.outcome<>'success' GROUP BY 1,2,3,4""", day))
    for order, key in (('count', lambda r: r[5]), ('total', lambda r: r[7]), ('mean', lambda r: r[7] / r[6]), ('slowest', lambda r: r[8]),
                       ('not_success', lambda r: errors.get(r[:4], 0))):
        ranked = reader.rows('SELECT scope_id,database,execution_user,fingerprint,timing_type,record_count,known_durations,total_ms,slowest_ms,not_success,mean_ms,ranked_rows,identity '
                             'FROM mpp_view_ranking(%s,%s,%s,NULL,NULL,NULL,%s,%s,%s)', (norm,) + day + ('request,execute_first', order, 1000))
        timed_rows = [r for r in ranked if r[4] is not None]
        assert sorted((r[:9] for r in timed_rows)) == sorted(direct)
        assert all(r[9] == errors.get(r[:4], 0) and abs(float(r[10]) - float(r[7] / r[6])) < 0.0006 for r in timed_rows)
        only_errors = [r for r in ranked if r[4] is None]
        assert [(r[:4], r[9]) for r in only_errors] == [(k, n) for k, n in errors.items() if k not in {r[:4] for r in direct}] and only_errors
        assert all(r[11] == len(ranked) and r[12] == token(r[0], r[1], r[2]) for r in ranked)
        values = [key(r[:9]) for r in timed_rows] if order != 'not_success' else [r[9] for r in ranked]
        assert values == sorted(values, reverse=True), order
    top = reader.rows('SELECT fingerprint,record_count FROM mpp_view_ranking(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (norm,) + day + ('C2', 'shop', 'app_user', 'request', 'count', 1))
    assert top == [(busy, 30)]
    reader.rejects('SELECT * FROM mpp_view_ranking(%s,%s,%s)', 'invalid_time_range', (norm, day[1], day[0]))
    stored = owner.rows("""SELECT g.scope_id,g.database,g.execution_user,g.fingerprint_value,g.timing_type,s.build_id,s.included_count,s.p50_ms,s.p95_ms,s.p99_ms,s.max_ms,s.mean_ms
        FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id) JOIN current_version v USING(build_id) WHERE s.layer='overall' AND s.included_count>0""")
    for order, index in (('samples', 6), ('p50', 7), ('p95', 8), ('p99', 9), ('max', 10), ('mean', 11)):
        ranked = reader.rows('SELECT scope_id,database,execution_user,fingerprint,timing_type,build_id,included_count,p50_ms,p95_ms,p99_ms,max_ms,mean_ms,basic_met,p95_met,p99_met,ranked_rows '
                             'FROM mpp_view_baseline_ranking(%s,NULL,NULL,NULL,%s,%s,0,1000)', (norm, '', order))
        assert sorted(r[:12] for r in ranked) == sorted(stored) and all(r[15] == len(stored) for r in ranked)
        assert [r[index] for r in ranked] == sorted((r[index] for r in ranked), reverse=True)
    conditions = {r[:5]: r[12:15] for r in ranked}
    assert conditions[('C1', 'shop', 'app_user', busy, 'request')] == (True, True, True)
    assert conditions[('C1', 'crm', 'app_user', fp(data.MEDIUM), 'request')] == (True, True, False)
    assert conditions[('C1', 'crm', 'app_user', fp(data.SPARSE), 'request')] == (False, False, False)
    few = reader.rows('SELECT included_count FROM mpp_view_baseline_ranking(%s,%s,NULL,NULL,%s,%s,%s,%s)', (norm, 'C1', 'request', 'samples', 300, 1000))
    assert few and all(r[0] >= 300 for r in few) and len(few) == sum(1 for r in stored if r[0] == 'C1' and r[4] == 'request' and r[6] >= 300)
    v.require(True, 'G20: time-range ranking and current-baseline ranking equal independent statistics in every order, with filters')
    # ---- G11/G12: search rows in one shape for the three modes
    columns = 'fingerprint,matched_texts,structure_texts,record_count,identities,total_structures,top_identity,top_records,top_last_at,only_sql_id'
    rows = reader.rows('SELECT ' + columns + ' FROM mpp_view_search(%s,%s,%s)', (norm, 'words', 'orders status'))
    busy_records = owner.one("SELECT count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.value=%s", (busy,))
    assert rows == [(busy, 80, 80, busy_records, 3, 1, ident, listed[0][3], owner.one(
        "SELECT max(o.end_at) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.value=%s AND " + IDENTITY, (busy,)), None)]
    one_text = reader.rows('SELECT ' + columns + ' FROM mpp_view_search(%s,%s,%s)', (norm, 'passage', '"a"."b" from "order items"'))
    assert len(one_text) == 1 and one_text[0][1] == 1 and one_text[0][9] == owner.one('SELECT sql_id FROM mpp_sql_text WHERE text=%s', (data.QUOTED,))
    exact = reader.rows('SELECT ' + columns + ' FROM mpp_view_search(%s,%s,%s,NULL,NULL,NULL,NULL,NULL,%s,%s)', (norm, 'exact', busy, 'count', chosen))
    assert exact == [(busy, None, 80, busy_records, 3, 1, ident, listed[0][3], rows[0][8], chosen)]
    assert reader.rows('SELECT ' + columns + ' FROM mpp_view_search(%s,%s,%s)', (norm, 'words', ' ' + busy + '\n')) == [exact[0][:9] + (None,)]
    scoped = reader.rows('SELECT record_count,identities,top_identity FROM mpp_view_search(%s,%s,%s,%s)', (norm, 'exact', busy, 'C2'))
    assert scoped == [(300, 1, token('C2', 'shop', 'app_user'))]
    for mode, text in (('words', ''), ('words', ' '.join(['x'] * 21)), ('passage', ' \n'), ('exact', fp('SELECT never FROM seen_51')), ('words', 'zz_nothing_51')):
        assert reader.rows('SELECT * FROM mpp_view_search(%s,%s,%s)', (norm, mode, text)) == []
    structures = owner.one("SELECT count(DISTINCT f.value) FROM mpp_fingerprint f WHERE f.state='reliable' AND EXISTS (SELECT FROM mpp_occurrence o WHERE o.sql_id=f.sql_id)")
    capped = reader.rows('SELECT total_structures FROM mpp_view_search(%s,%s,%s)', (norm, 'words', 'select'))
    assert structures > 50 and len(capped) == 50 and {r[0] for r in capped} == {structures}
    reader.rejects('SELECT * FROM mpp_view_search(%s,%s,%s)', 'invalid_search_mode', (norm, 'other', 'x'))
    note = lambda *values: dict((r[1], r[2]) for r in reader.rows('SELECT * FROM mpp_view_search_note(%s,%s,%s,%s,%s,%s,%s)', (norm,) + values))
    words = note('words', 'Orders  "a"\nSTATUS', None, None, None, None)
    assert words['检索方式'].startswith('按词') and words['切出的词'] == '共 3 个：orders  ｜  "a"  ｜  status'
    arrived = 'Orders  "a"\nSTATUS'
    assert words['到达数据库的输入'] == '%d 个字符，SHA-256 %s' % (len(arrived), hashlib.sha256(arrived.encode()).hexdigest())
    assert '超过 20 个；请改用“整段”方式' in note('words', ' '.join(['x'] * 21), None, None, None, None)['没有检索']
    assert note('words', ' ', None, None, None, None)['没有检索'] == '输入为空' and note('passage', '', None, None, None, None)['没有检索'] == '输入为空'
    assert note('passage', 'a b\nC', None, None, None, None)['比较的内容'].startswith('去掉空白、字母转小写后共 3 个字符')
    assert note('words', busy, None, None, None, None)['输入'].startswith('是一个结构指纹值')
    assert note('exact', None, busy, 'has_baseline', None, chosen)['结果'] == '库里有这个结构，并且有基线'
    assert chosen in note('exact', None, busy, 'has_baseline', None, chosen)['一字不差的原文']
    assert note('exact', None, fp(data.FAILING), None, None, None)['结果'].startswith('库里有这个结构的执行记录')
    assert note('exact', None, fp('SELECT never FROM seen_51'), None, None, None)['结果'] == '库里没有这个结构'
    assert note('exact', None, '', 'unreliable_fingerprint', 'input_size_limit', None)['结果'] == '无法生成可靠指纹：输入超过 512 KB 的上限'
    assert '语法不在支持范围（base_parser_rejected）' in note('exact', None, '', 'unreliable_fingerprint', 'base_parser_rejected', None)['结果']
    assert note('exact', None, '', 'service_unavailable', None, None)['结果'].startswith('指纹服务不可用')
    assert note('exact', None, '', '', None, None) == {'检索方式': note('exact', None, busy, None, None, None)['检索方式'], '结果': '还没有检索'}
    hints = base64.urlsafe_b64encode(json.dumps([dict(statement=1, fingerprint=fp('SELECT never FROM seen_51')), dict(statement=2, fingerprint=fp(data.SPARSE))]).encode()).decode().rstrip('=')
    assert reader.rows('SELECT statement,state,record_count FROM mpp_view_hints(%s,%s)', (norm, hints)) == [(1, 'not_seen', 0), (2, 'has_baseline', 38)]
    assert reader.rows("SELECT * FROM mpp_view_hints(%s,'')", (norm,)) == []
    shown = reader.rows('SELECT sql_id,sql_text,selected,structure_texts FROM mpp_view_sql_text(%s,%s,%s)', (norm, busy, chosen))
    assert shown == [(chosen, data.BUSY.format(status=3, day='2026-06-28'), True, 80)]
    example = reader.rows('SELECT sql_id,selected FROM mpp_view_sql_text(%s,%s)', (norm, busy))
    assert example == [(owner.one("SELECT min(sql_id) FROM mpp_fingerprint WHERE value=%s", (busy,)), False)]
    assert reader.rows('SELECT * FROM mpp_view_sql_text(%s,%s)', (norm, fp('SELECT never FROM seen_51'))) == []
    assert reader.one('SELECT length(sql_text) FROM mpp_view_sql_text(%s,%s)', (norm, fp(data.LONG))) == len(data.LONG) > 60000
    assert contents(owner.db) == v.before
    v.require(True, 'G11/G12: three modes give the same row shape with total, top identity and single-text entry; refusals give no row and a stated reason; hints; SQL text')


def guards(v, owner, reader, builds, dsn):
    """Versions that cannot serve as a reference stay visible and cannot be chosen."""
    norm, ident = v.norm, token('C1', 'shop', 'app_user')
    busy = v.fingerprint(data.BUSY.format(status=1, day='2026-06-26'))
    clone_build(owner.db, builds['current'], 'views-old', '2026-01-01', published=True)
    assert CleanupStore(owner.db).execute('C1', 2)['state'] == 'succeeded'
    versions = dict((r[0], r[1:]) for r in reader.rows('SELECT build_id,selectable,status,results_cleaned FROM mpp_view_versions(%s,%s)', (norm, ident)))
    assert versions['views-old'] == (False, '已清理，不能选作参照', True) and versions[builds['current']][0]
    assert reader.rows('SELECT * FROM mpp_view_statistics(%s,%s,%s,%s)', (norm, busy, ident, 'views-old')) == []
    other = reader.rows('SELECT selectable,status FROM mpp_view_versions(%s,%s)', ('N:other', ident))
    assert other and all(r == (False, '规则版本不同，不能选作参照') or r[1].startswith('已清理') for r in other)
    assert reader.one('SELECT count(*) FROM mpp_view_baseline_ranking(%s)', ('N:other',)) == 0
    # A record imported after the version's input was frozen, and a version whose decisions cannot be recomputed.
    from ingestion.test_reader import row, write_csv
    from sql_apm.ingestion.config import load_config
    from sql_apm.ingestion.importer import Importer
    later, config = v.directory / 'later.csv', v.directory / 'later.json'
    write_csv(later, [row(text=data.BUSY.format(status=1, day='2026-06-26'), message='duration: 7 ms',
                          **{'0': '2026-07-23 23:00:00.000000 CST', '1': 'app_user', '2': 'shop'})])
    document = json.loads((v.directory / 'import.json').read_text())
    document['batches'] = {'B3': {'source': 'S1', 'files_confirmed_complete': True, 'dates': ['2026-07-23'],
                                  'files': [{'path': str(later), 'closed_and_copied': True}]}}
    config.write_text(json.dumps(document))
    importer = Importer(dsn, 'sql_apm', 1)
    try:
        assert importer.run(load_config(config, 'S1', 'B3'))['state'] == 'complete'
    finally:
        importer.close()
    newest = reader.rows("SELECT training,duration_ms FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,'latest',1)",
                         (norm, busy, ident, 'request', builds['current']) + RANGE)
    assert newest == [('未选入该版本的输入', 7)]
    with owner.db, owner.db.cursor() as cur:
        cur.execute('ALTER TABLE training_config DISABLE TRIGGER USER')
        cur.execute("UPDATE training_config SET decision_version='historical/0'")
        cur.execute('ALTER TABLE training_config ENABLE TRIGGER USER')
    unavailable = reader.rows("SELECT DISTINCT training FROM mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,NULL,'success',NULL,NULL,'slowest',50)",
                              (norm, busy, ident, 'request', builds['current']) + RANGE)
    assert unavailable == [('该版本的判定规则不支持复算：判定规则版本不支持',)]
    assert owner.one('SELECT build_id FROM current_version WHERE scope_id=%s', ('C1',)) == builds['current']
    v.require(True, 'G13/G19: cleaned and other-rule versions are listed but cannot be chosen; later imports and unsupported decision versions are shown as such')


def verify(pg_bin):
    with instance(pg_bin) as (directory, env):
        v = Verification(pg_bin, directory, env)
        base_init = v.init
        v.init = lambda mode='all', names=None, ok=True, root=ROOT, extra=None: _init(v, base_init, mode, extra)
        v.init()
        v.directory = directory
        dsn = 'host=' + str(directory / 'socket') + ' port=%d dbname=sql_apm user=sql_apm' % SOCKET_PORT
        v.reader_dsn = dsn + '_ro'
        builds = data.load(dsn, directory)
        engine = Normalizer()
        v.norm = 'N:' + identity(engine.context)
        v.fingerprint = lambda text: engine.normalize(text)['fingerprint']['value']
        owner, reader = Database(dsn), Database(v.reader_dsn)
        v.before = contents(owner.db)

        def cli(words):
            done = subprocess.run([sys.executable, '-m', 'sql_apm', 'search'] + words, cwd=ROOT,
                                  env=dict(os.environ, SQL_APM_DSN=v.reader_dsn), text=True, capture_output=True, timeout=60)
            return done.returncode, json.loads(done.stdout)
        account(v, directory, owner, reader)
        reader.db.close()
        reader = Database(v.reader_dsn)
        service(v, owner, cli)
        views(v, owner, reader, builds)
        guards(v, owner, reader, builds, dsn)
        owner.db.close()
        reader.db.close()
        v.init('check')
        print('RESULT: read-only account, fingerprint service and dashboard query acceptance passed', flush=True)


def _init(v, base_init, mode, extra):
    if not extra:
        return base_init(mode)
    arguments = [ROOT / 'scripts/db/initialize.sh', mode] + v.args + ['--admin-user', 'apm_test_admin', '--admin-database', 'postgres'] + extra
    done = subprocess.run([str(a) for a in arguments], env=v.env, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(done.stderr + done.stdout)
    return done


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
