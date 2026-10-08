#!/usr/bin/env python3
"""Explicit R1 performance audit on an already upgraded, disposable full-data copy.

Starts/stops that copy only. Raw plans and selected SQL stay in the private output
directory; plans-summary.json contains only counts, hashes, timings and node names.
auto_explain is loaded in diagnostic admin sessions, never installed as an extension.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import psycopg2

from verify_search_full import ROOT, start, stop


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def plans(text):
    decoder = json.JSONDecoder()
    result = []
    for match in re.finditer(r'plan:\s*(?=\{)', text):
        value, _ = decoder.raw_decode(text[match.end():])
        result.append(value)
    return result


def scans(records):
    result = []

    def walk(node):
        if 'Relation Name' in node or 'Index Name' in node:
            result.append({k: node[k] for k in (
                'Node Type', 'Relation Name', 'Index Name', 'Actual Rows',
                'Actual Loops', 'Rows Removed by Filter') if k in node})
        for child in node.get('Plans', []):
            walk(child)

    for record in records:
        walk(record['Plan'])
    return result


def indexed_facts(nodes):
    for table in ('mpp_fingerprint', 'mpp_occurrence', 'mpp_baseline_group'):
        selected = [n for n in nodes if n.get('Relation Name') == table]
        assert selected, 'missing_nested_plan_' + table
        assert all(n['Node Type'] in ('Index Scan', 'Index Only Scan', 'Bitmap Heap Scan')
                   for n in selected), 'unexpected_full_scan_' + table


def run(args):
    directory = args.directory.resolve()
    if (directory/'pgdata/postmaster.pid').exists():
        raise ValueError('copy_must_be_stopped')
    private = directory/'plan-inputs'
    private.mkdir(mode=0o700, exist_ok=True)
    dsn = 'host=%s port=55474 dbname=sql_apm user=sql_apm' % (directory/'socket')
    start(directory, args.pg_bin)
    db = admin = None
    try:
        db = psycopg2.connect(dsn)
        db.autocommit = True
        cur = db.cursor()
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
        cur.execute('SELECT normalization_id FROM mpp_normalization')
        (norm,), = cur.fetchall()

        def timed(statement, params=(), repeat=3):
            elapsed = []
            for _ in range(repeat):
                began = time.monotonic()
                cur.execute(statement, params)
                cur.fetchall()
                elapsed.append(round((time.monotonic()-began)*1000, 3))
            return elapsed

        cur.execute('''CREATE TEMP TABLE plan_candidates AS
            SELECT f.value,count(*) records,count(DISTINCT f.sql_id) texts,
                count(DISTINCT (o.scope_id,o.database,o.execution_user)) identities
            FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
            WHERE f.normalization_id=%s AND f.profile='mpp-csv/1' AND f.state='reliable'
            GROUP BY f.value''', (norm,))
        picks = {}
        for label, order in (
            ('one_record', "records=1 ORDER BY md5(value||'r1-one')"),
            ('most_records', 'true ORDER BY records DESC,value'),
            ('most_identities', 'true ORDER BY identities DESC,value'),
            ('most_texts', 'true ORDER BY texts DESC,value')):
            cur.execute('SELECT value,records,identities,texts FROM plan_candidates WHERE '+order+' LIMIT 1')
            picks[label] = cur.fetchone()
        report = dict(cases={}, actual_plans={}, complete_commands={}, term_scaling=[])
        for label, (fp, records, identities, texts) in picks.items():
            report['cases'][label] = dict(
                structure_sha256=sha(fp), records=records, identities=identities, texts=texts,
                exact_ms=timed('SELECT mpp_query_exact(%s,%s)', (norm, fp)),
                fingerprint_find_ms=timed('SELECT mpp_query_search(%s,%s)', (norm, fp)))
            print(label, report['cases'][label], flush=True)

        admin = psycopg2.connect(host=str(directory/'socket'), port=55474,
                                 dbname='sql_apm', user='apm_test_admin')
        admin.autocommit = True
        ac = admin.cursor()
        ac.execute("LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; "
                   "SET auto_explain.log_analyze=on; SET auto_explain.log_nested_statements=on; "
                   "SET auto_explain.log_timing=off; SET auto_explain.log_format=json; "
                   "SET client_min_messages=log; SET search_path=sql_apm,pg_catalog; "
                   "SET plan_cache_mode=force_generic_plan")

        def actual(label, statement, params=(), require_indexes=False):
            del admin.notices[:]
            ac.execute(statement, params)
            ac.fetchall()
            raw = '\n'.join(admin.notices)
            (private/(label+'.plans')).write_text(raw)
            parsed = plans(raw)
            assert parsed, 'missing_auto_explain_' + label
            nodes = scans(parsed)
            if require_indexes:
                indexed_facts(nodes)
            report['actual_plans'][label] = nodes

        fp = picks['one_record'][0]
        cur.execute('SELECT scope_id,database,execution_user FROM mpp_query_hits(%s,%s)', (norm, fp))
        ident = cur.fetchone()
        for label, function, params in (
            ('exact', 'mpp_query_exact', (norm, fp)),
            ('fingerprint_find', 'mpp_query_search', (norm, fp)),
            ('filtered_hits', 'mpp_query_hits', (norm, fp)+ident)):
            actual(label, 'SELECT * FROM '+function+'('+','.join(['%s']*len(params))+')', params, True)
        for label, statement, params in (
            ('statistics', 'SELECT * FROM mpp_query_statistics(%s,%s,%s,%s,%s)', (norm,)+ident+(fp,)),
            ('history', 'SELECT mpp_query_history(%s,%s,%s,%s,%s)', (norm, fp)+ident),
            ('timeline', "SELECT mpp_query_history(%s,%s,%s,%s,%s,NULL,NULL,100,NULL,NULL,'hour')", (norm, fp)+ident),
            ('explicit_bounds', "SELECT * FROM mpp_query_time_bounds(%s,%s,%s,%s,%s,'2026-07-01','2026-08-01')", (norm, fp)+ident),
            ('versions', 'SELECT * FROM mpp_query_versions(%s,%s)', (norm, ident[0])),
            ('filtered_fuzzy', 'SELECT * FROM mpp_query_fuzzy(%s,%s,%s,%s,%s)', (norm, 'sql_apm_r1_never_seen_term')+ident)):
            actual(label, statement, params)
        assert not report['actual_plans']['explicit_bounds'], 'explicit_bounds_read_tables'
        ac.execute('PREPARE optional_occurrences(text,text,text,text,text) AS '
                   'SELECT * FROM mpp_query_occurrences($1,$2,$3,$4,$5) LIMIT 2')
        actual('occurrences_external_generic', 'EXECUTE optional_occurrences(%s,%s,%s,%s,%s)', (norm, fp)+ident)
        ac.execute('SET plan_cache_mode=force_custom_plan')
        actual('occurrences_external_custom', 'EXECUTE optional_occurrences(%s,%s,%s,%s,%s)', (norm, fp)+ident)
        ac.execute('SET plan_cache_mode=force_generic_plan')
        cur.execute('SELECT approximate_value,scope_id,database,execution_user FROM mpp_observation_group LIMIT 1')
        observation = cur.fetchone()
        if observation:
            actual('observations', 'SELECT * FROM mpp_query_observations(%s,%s,%s,%s)', observation)
        report['global_missing_rules_ms'] = timed('SELECT mpp_query_missing_rules(%s)', (norm,))

        # Same deterministic 20-statement sample used in R1; source text stays private.
        cur.execute(r'''SELECT text FROM (SELECT DISTINCT ON (f.value) f.value,t.text
            FROM mpp_sql_text t JOIN mpp_fingerprint f USING(sql_id)
            WHERE length(t.text) BETWEEN 40 AND 400 AND t.text !~ ';' AND t.text ~* '^\s*select'
            ORDER BY f.value,t.sql_id) s ORDER BY md5(value||'r1-b20') LIMIT 20''')
        texts = [r[0].strip() for r in cur]
        assert len(texts) == 20

        def cli(path, env):
            began = time.monotonic()
            p = subprocess.run([sys.executable, '-m', 'sql_apm', 'search', 'exact', '--file', str(path)],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            assert p.returncode == 0, 'exact_cli_failed'
            return json.loads(p.stdout), round(time.monotonic()-began, 6)

        for count in (1, 5, 20):
            path = private/('existing_%d.sql' % count)
            path.write_text(';\n'.join(texts[:count]))
            seconds = []
            for _ in range(2):
                value, elapsed = cli(path, dict(os.environ, SQL_APM_DSN=dsn))
                seconds.append(elapsed)
                if count == 1:
                    assert value['hits']
                else:
                    assert value['state'] == 'not_seen' and len(value['statement_hints']) == count
                    assert all(h['hits'] for h in value['statement_hints'])
            report['complete_commands'][str(count)] = dict(seconds=seconds, state=value['state'],
                hints_with_records=sum(bool(h['hits']) for h in value.get('statement_hints', [])))
            print('CLI', count, seconds, flush=True)

        cur.execute('''SELECT t.text FROM mpp_sql_text t JOIN mpp_fingerprint f USING(sql_id)
                       WHERE f.normalization_id=%s AND f.value=%s LIMIT 1''', (norm, fp))
        original = cur.fetchone()[0].rstrip().rstrip(';')
        path = private/'rare_hint.sql'
        path.write_text(original+';\nSELECT * FROM sql_apm_issue47_r1_never_seen;')
        ac.execute('SELECT pg_current_logfile()')
        logfile = ac.fetchone()[0]
        server_log = directory/'pgdata'/logfile if logfile else directory/'server.log'
        offset = server_log.stat().st_size
        options = '-c session_preload_libraries=auto_explain -c auto_explain.log_min_duration=0 '
        options += '-c auto_explain.log_analyze=on -c auto_explain.log_nested_statements=on '
        options += '-c auto_explain.log_timing=off -c auto_explain.log_format=json -c plan_cache_mode=force_generic_plan'
        value, _ = cli(path, dict(os.environ, SQL_APM_DSN=dsn.replace('user=sql_apm', 'user=apm_test_admin'),
                                  PGOPTIONS=options))
        assert value['state'] == 'not_seen' and value['statement_count'] == 2
        assert value['statement_hints'][0]['fingerprint'] == fp and value['statement_hints'][0]['hits']
        with server_log.open('rb') as log:
            log.seek(offset)
            raw = log.read().decode()
        (private/'cli_hint.plans').write_text(raw)
        nodes = scans(plans(raw))
        indexed_facts(nodes)
        report['actual_plans']['cli_hint'] = nodes

        for length in (10000, 25000, 50000, 100000, 200000):
            for label, value in (('ascii', 'A'*length), ('quoted_unicode', '"中 文"'*(length//5))):
                report['term_scaling'].append(dict(kind=label, characters=len(value), bytes=len(value.encode()),
                    milliseconds=timed('SELECT mpp_search_terms(%s)', (value,))))
        (directory/'plans-summary.json').write_text(json.dumps(report, indent=2))
        print('plans, complete commands, terms: PASS', flush=True)
    finally:
        if admin is not None:
            admin.close()
        if db is not None:
            db.close()
        stop(directory, args.pg_bin)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--pg-bin', default=Path('/usr/pgsql-17/bin'), type=Path)
    run(parser.parse_args())
