#!/usr/bin/env python3
"""Small-history plan regressions for both deferred incremental refresh paths."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import psycopg2
from database.fixture import statements
from database.retention import digest
from verify import Verification, instance
from verify_query_performance import definition
from verify_query_performance_full import parse_notices, plan_nodes
from verify_query_records import reconcile

FUNCTIONS = ('mpp_query_merge_totals', 'mpp_query_refresh')


def plans(pg, output, candidate_schema=None):
    evidence = {}
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env)
        v.init()
        db = psycopg2.connect(host=str(directory / 'socket'), port=55473,
                             dbname='sql_apm', user='apm_test_admin')
        with db, db.cursor() as cur:
            cur.execute('SET search_path=sql_apm,pg_catalog')
            cur.execute(statements())
            cur.execute("""INSERT INTO mpp_sql_text(sql_id,text,content_sha256)
                SELECT 'history-'||n,'SELECT '||n,sha256(convert_to('SELECT '||n,'UTF8'))
                FROM generate_series(1,5000) n;
                INSERT INTO mpp_fingerprint
                SELECT 'history-fp-'||n,'history-'||n,normalization_id,profile,state,value,reason
                FROM mpp_fingerprint CROSS JOIN generate_series(1,5000) n WHERE sql_id='Q1';
                INSERT INTO evidence_record
                SELECT (jsonb_populate_record(NULL::evidence_record,to_jsonb(e)||
                    jsonb_build_object('record_id','history-anchor-'||n,'record_no',1000+n))).*
                FROM evidence_record e CROSS JOIN generate_series(1,30000) n WHERE e.record_id='R1';
                INSERT INTO mpp_occurrence
                SELECT (jsonb_populate_record(NULL::mpp_occurrence,to_jsonb(o)||
                    jsonb_build_object('occurrence_id','history-event-'||n,'anchor_ref','history-anchor-'||n,
                        'sql_id','history-'||(1+(n%5000))))).*
                FROM mpp_occurrence o CROSS JOIN generate_series(1,30000) n
                WHERE o.occurrence_id=(SELECT min(occurrence_id) FROM mpp_occurrence WHERE sql_id='Q1');""")
        with db, db.cursor() as cur:
            cur.execute('ANALYZE')
            cur.execute("LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; "
                        'SET auto_explain.log_nested_statements=on; SET auto_explain.log_analyze=on; '
                        'SET auto_explain.log_buffers=on; SET auto_explain.log_timing=off; '
                        'SET auto_explain.log_format=json; SET auto_explain.log_level=notice; '
                        'SET client_min_messages=notice')
        old = (ROOT / 'sql_apm/storage/versions/1.14.0.sql').read_text()
        fixed = (candidate_schema or ROOT / 'sql_apm/storage/schema.sql').read_text()
        db.notices = []  # Keep all nested plans; psycopg2's default retains 50.
        for case in ('metadata', 'file'):
            evidence[case] = {}
            for label, source in (('before', old), ('after', fixed)):
                with db.cursor() as cur:
                    for name in FUNCTIONS:
                        cur.execute(definition(source, name))
                    db.notices.clear()
                    if case == 'metadata':
                        cur.execute("""INSERT INTO mpp_sql_text(sql_id,text,content_sha256)
                            SELECT 'new-'||n,'SELECT new_column FROM new_table WHERE id='||n,
                                sha256(convert_to('SELECT new_column FROM new_table WHERE id='||n,'UTF8'))
                            FROM generate_series(1,32) n;
                            INSERT INTO mpp_fingerprint
                            SELECT 'new-fp-'||n,'new-'||n,normalization_id,profile,state,value,reason
                            FROM mpp_fingerprint CROSS JOIN generate_series(1,32) n WHERE sql_id='Q1'""")
                    else:
                        cur.execute("""INSERT INTO evidence_record
                            SELECT (jsonb_populate_record(NULL::evidence_record,to_jsonb(e)||
                                jsonb_build_object('record_id','new-anchor-'||n,'record_no',100000+n))).*
                            FROM evidence_record e CROSS JOIN generate_series(1,32) n WHERE e.record_id='R1';
                            INSERT INTO mpp_occurrence
                            SELECT (jsonb_populate_record(NULL::mpp_occurrence,to_jsonb(o)||
                                jsonb_build_object('occurrence_id','new-event-'||n,'anchor_ref','new-anchor-'||n))).*
                            FROM mpp_occurrence o CROSS JOIN generate_series(1,32) n
                            WHERE o.occurrence_id=(SELECT min(occurrence_id) FROM mpp_occurrence WHERE sql_id='Q1')""")
                    cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
                    observed = parse_notices(''.join(db.notices))
                    scans = [n for n in plan_nodes(observed)
                             if n.get('Relation Name') == 'mpp_query_record'
                             and n.get('Node Type') == 'Seq Scan' and n.get('Actual Loops', 0)]
                    if label == 'before':
                        assert scans, (case, 'frozen code must reproduce a history scan')
                    else:
                        assert not scans, (case, scans)
                        source_plans = [p for p in observed if 'CREATE TEMP TABLE query_source_events' in p.get('Query Text', '')]
                        assert source_plans, 'missing source-event nested plan'
                        assert not [n for n in plan_nodes(source_plans)
                                    if n.get('Relation Name') == 'mpp_occurrence'
                                    and n.get('Node Type') == 'Seq Scan' and n.get('Actual Loops', 0)], source_plans
                    evidence[case][label] = observed
                # Run the independent oracle on the uncommitted projected state.
                # reconcile() commits, so use a savepoint-free cursor here.
                with db.cursor() as cur:
                    for item in (ROOT / 'scripts/db/query_records_consistency.sql').read_text().split('-- CASE ')[1:]:
                        name, query = item.split('\n', 1)
                        cur.execute(query)
                        assert cur.fetchone()[0] == 0, (label, case, name)
                db.rollback()
            v.require(True, case + ': old code scans history; fixed code uses key probes; independent facts agree')
        db.close()
    if output:
        with output.open('x') as stream:
            json.dump(evidence, stream, indent=2)


def migration(pg):
    import hashlib
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env)
        v.init('bootstrap')
        old = (ROOT / 'sql_apm/storage/versions/1.14.0.sql').read_text()
        v.sql("CREATE SCHEMA sql_apm; SET apm.readonly_role='sql_apm_ro';" + old +
              "INSERT INTO schema_version(version,script_sha256) VALUES('1.14.0','" +
              hashlib.sha256(old.encode()).hexdigest() + "');" + statements())
        db = psycopg2.connect(host=str(directory / 'socket'), port=55473,
                             dbname='sql_apm', user='sql_apm')
        with db, db.cursor() as cur:
            cur.execute('SET search_path=sql_apm,pg_catalog')
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='sql_apm' AND tablename<>'schema_version'")
            tables = [r[0] for r in cur]
        before = {t: digest(db, t) for t in tables}
        # xmin/ctid prove the migration did not rewrite even identical data.
        physical = {t: v.sql('SELECT md5(string_agg(xmin::text||ctid::text,\',\' ORDER BY ctid)) FROM ' + t) for t in tables}
        receipt = v.sql('SELECT row_to_json(v) FROM schema_version v')
        v.init('upgrade')
        v.init('check')
        assert before == {t: digest(db, t) for t in tables}
        assert physical == {t: v.sql('SELECT md5(string_agg(xmin::text||ctid::text,\',\' ORDER BY ctid)) FROM ' + t) for t in tables}
        assert v.sql("SELECT row_to_json(v) FROM schema_version v WHERE version='1.14.0'") == receipt
        reconcile(db)
        v.init('upgrade')
        v.init('schema')
        v.init('check')
        db.close()
        v.require(True, 'populated 1.14.0 upgrade changes no business row or physical tuple; reruns pass')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--candidate-schema', type=Path,
                        help='explicit candidate DDL, including a frozen version for the negative control')
    args = parser.parse_args()
    plans(args.pg_bin, args.output, args.candidate_schema)
    migration(args.pg_bin)


if __name__ == '__main__':
    main()
