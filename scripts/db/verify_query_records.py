#!/usr/bin/env python3
"""Isolated PostgreSQL regressions for the fingerprint/time query paths."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from verify import Verification, instance
from verify_query_performance import definition
from verify_query_performance_full import parse_notices, plan_nodes
from database.fixture import statements
from sql_apm.storage.ingestion import connect
import psycopg2


def statistics_guard(pg, output=None):
    evidence = {}
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env)
        v.init()
        dsn = f'host={directory}/socket port=55473 dbname=sql_apm user=apm_test_admin'
        db = connect(dsn, 'sql_apm')
        old = (ROOT / 'sql_apm/storage/versions/1.13.0.sql').read_text()
        with db, db.cursor() as cur:
            cur.execute(statements())
            cur.execute(definition(old, 'mpp_query_statistics').replace(
                'FUNCTION mpp_query_statistics(', 'FUNCTION reference_mpp_query_statistics('))
            # Anti-correlated identity fields deliberately overestimate the one
            # real group. This reproduces the production plan without disabling
            # any join or scan method.
            cur.execute("""INSERT INTO mpp_baseline_group
                SELECT (jsonb_populate_record(NULL::mpp_baseline_group,to_jsonb(g)||
                    jsonb_build_object('group_id','skew-'||n,
                    'database',CASE WHEN n<=2000 THEN g.database ELSE 'other-'||n END,
                    'execution_user',CASE WHEN n>2000 THEN g.execution_user ELSE 'other-'||n END))).*
                FROM mpp_baseline_group g CROSS JOIN generate_series(1,4000) n WHERE group_id='G1'""")
            cur.execute('ANALYZE mpp_baseline_group')
            cur.execute('CREATE TABLE statistics_calls (selected_group text)')
            # Instrument the real callee, keeping its body, volatility and cost.
            instrumented = definition(old, 'mpp_read_statistics').replace(
                'BEGIN\n', 'BEGIN\n    INSERT INTO statistics_calls VALUES (selected_group);\n', 1)
            cur.execute(instrumented)
            cur.execute("SELECT normalization_id,scope_id,database,execution_user,fingerprint_value FROM mpp_baseline_group WHERE group_id='G1'")
            args = list(cur.fetchone())
            cur.execute("LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; "
                        "SET auto_explain.log_analyze=on; SET auto_explain.log_nested_statements=on; "
                        "SET auto_explain.log_timing=off; SET auto_explain.log_format=json; "
                        "SET auto_explain.log_level=notice; SET client_min_messages=notice")
        for layer in ('overall', 'day', 'week', 'weekday', 'hour'):
            rows = []
            evidence[layer] = {}
            for prefix in ('reference_', ''):
                with db, db.cursor() as cur:
                    cur.execute('TRUNCATE statistics_calls')
                    db.notices.clear()
                    cur.execute('SELECT * FROM '+prefix+'mpp_query_statistics(%s,%s,%s,%s,%s,%s,%s)',
                                args + ['V1', layer])
                    rows.append(cur.fetchall())
                    plans = parse_notices(''.join(db.notices))
                    cur.execute('SELECT selected_group FROM statistics_calls')
                    calls = [row[0] for row in cur.fetchall()]
                    evidence[layer][prefix or 'fixed'] = dict(calls=calls, plans=plans)
                    if prefix:
                        assert None in calls, (layer, calls)
                        assert any(n.get('Function Name') == 'mpp_read_statistics' and n.get('Actual Loops', 0) > 1
                                   for n in plan_nodes(plans)), plans
                    else:
                        assert calls and None not in calls, calls
            assert rows[0] == rows[1], layer
            assert sum(row[11] == 'no_samples' for row in rows[1]) == 4, rows[1]
        v.require(True, 'K11: old function calls NULL under skewed repeated-call plan; fixed function never does; all five layers identical')
        # Existing group without a row in this layer remains no_samples too.
        with db, db.cursor() as cur:
            cur.execute("DELETE FROM mpp_statistic WHERE layer='hour'")
            cur.execute('SELECT * FROM reference_mpp_query_statistics(%s,%s,%s,%s,%s,%s,%s)', args+['V1','hour'])
            expected = cur.fetchall()
            cur.execute('SELECT * FROM mpp_query_statistics(%s,%s,%s,%s,%s,%s,%s)', args+['V1','hour'])
            assert cur.fetchall() == expected
            assert all(row[11] == 'no_samples' for row in expected)
        # Validation still occurs before the empty-group guard.
        for build, layer in [('absent','overall'), ('V1','absent')]:
            errors = []
            for prefix in ('reference_', ''):
                try:
                    with db, db.cursor() as cur:
                        cur.execute('SELECT * FROM '+prefix+'mpp_query_statistics(%s,%s,%s,%s,%s,%s,%s)', args+[build,layer])
                except psycopg2.Error as error:
                    errors.append((error.pgcode,error.diag.message_primary))
            assert len(errors)==2 and errors[0]==errors[1], errors
        with db, db.cursor() as cur:
            cur.execute('UPDATE mpp_result_partition SET cleaned_at=clock_timestamp()')
        for prefix in ('reference_', ''):
            try:
                with db, db.cursor() as cur:
                    cur.execute('SELECT * FROM '+prefix+'mpp_query_statistics(%s,%s,%s,%s,%s)',args)
            except psycopg2.Error as error:
                assert error.diag.message_primary == 'results_cleaned'
            else:
                raise AssertionError('cleaned build accepted')
        v.require(True, 'missing layer, version/layer errors and results_cleaned behavior preserved')
        db.close()
    if output:
        with output.open('x') as file:
            json.dump(evidence, file, ensure_ascii=False, indent=2)


def reconcile(db):
    results = {}
    sql = (ROOT / 'scripts/db/query_records_consistency.sql').read_text()
    with db, db.cursor() as cur:
        for case in sql.split('-- CASE ')[1:]:
            name, query = case.split('\n', 1)
            cur.execute(query)
            results[name] = cur.fetchone()[0]
    assert not any(results.values()), results
    return results


def maintenance(pg):
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env); v.init()
        db = connect(f'host={directory}/socket port=55473 dbname=sql_apm user=sql_apm', 'sql_apm')
        def execute(sql):
            with db, db.cursor() as cur:
                cur.execute(sql)
        def revision():
            with db, db.cursor() as cur:
                cur.execute('SELECT revision FROM mpp_query_state')
                return cur.fetchone()[0]
        execute(statements())
        reconcile(db)
        v.require(revision()==1, 'one revision for committed events with reliable fingerprints')
        execute('SELECT mpp_query_prepare_changes(); '
                'INSERT INTO pg_temp.mpp_query_rebuild_flag VALUES(true); SELECT mpp_query_refresh()')
        reconcile(db)
        v.require(revision()==1, 'rebuild with existing projection preserves totals and does not advance revision')
        execute("INSERT INTO mpp_sql_text(sql_id,text,content_sha256) VALUES('unused','SELECT 73',sha256(convert_to('SELECT 73','UTF8')));")
        execute("INSERT INTO mpp_fingerprint SELECT 'unused-fp','unused',normalization_id,profile,state,value,reason FROM mpp_fingerprint WHERE sql_id='Q1' ON CONFLICT DO NOTHING")
        reconcile(db)
        v.require(revision()==1, 'text and fingerprint-only commits do not advance revision')
        execute('UPDATE mpp_occurrence SET duration_ms=duration_ms')
        v.require(revision()==1, 'unchanged UPDATE leaves revision unchanged')
        with db, db.cursor() as cur:
            cur.execute('SAVEPOINT discarded')
            cur.execute("UPDATE mpp_occurrence SET database='discarded'")
            cur.execute('ROLLBACK TO discarded')
        v.require(revision()==1, 'savepoint rollback removes captured keys and pending marker')
        execute("UPDATE mpp_occurrence SET database='changed'")
        reconcile(db)
        v.require(revision()==2, 'identity UPDATE transfers all summary contributions')
        execute("UPDATE mpp_occurrence SET database=NULL,execution_user=NULL")
        reconcile(db)
        execute("INSERT INTO mpp_normalization SELECT 'N2',algorithm_version,parser_version,dictionary_schema_version,'next',dictionary_digest_algorithm,'next',rules_ref FROM mpp_normalization WHERE normalization_id='N1'")
        before=revision()
        execute("INSERT INTO mpp_fingerprint SELECT 'second-rule',sql_id,'N2',profile,state,value,reason FROM mpp_fingerprint WHERE fingerprint_id='P1'")
        reconcile(db)
        v.require(revision()==before+1, 'fingerprints added after events create separate rule facts and advance revision')
        # No baseline/decision references the second rule: exercise normal SQL
        # updates/deletes without disabling source integrity constraints.
        execute("UPDATE mpp_fingerprint SET state='normalization_failed',value=NULL,reason='synthetic' WHERE normalization_id='N2'")
        reconcile(db)
        execute("UPDATE mpp_fingerprint SET state='reliable',value='synthetic:new',reason=NULL WHERE normalization_id='N2'")
        reconcile(db)
        # The file transaction must take its projection snapshot after waiting
        # for the metadata lock, including a fingerprint committed meanwhile.
        from threading import Thread, Event
        import time
        other=connect(f'host={directory}/socket port=55473 dbname=sql_apm user=sql_apm','sql_apm')
        ready,errors=Event(),[]
        def commit_events():
            try:
                with other,other.cursor() as cur:
                    cur.execute("UPDATE mpp_occurrence SET database='concurrent'")
                    ready.set()
            except Exception as error:errors.append(error)
        with db.cursor() as cur:
            cur.execute("UPDATE mpp_fingerprint SET value='synthetic:committed' WHERE normalization_id='N2'")
        worker=Thread(target=commit_events);worker.start()
        assert ready.wait(5)
        deadline=time.monotonic()+5
        while True:
            with db.cursor() as cur:
                cur.execute("SELECT EXISTS(SELECT FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted)",(other.get_backend_pid(),))
                if cur.fetchone()[0]:break
            assert time.monotonic()<deadline,'event refresh did not wait for metadata'
            time.sleep(.01)
        db.commit();worker.join(10)
        assert not worker.is_alive() and not errors,errors
        other.close();reconcile(db)
        v.require(True,'concurrent file commit waits after event writes and sees the newly committed fingerprint')
        execute("DELETE FROM mpp_fingerprint WHERE normalization_id='N2'; DELETE FROM mpp_normalization WHERE normalization_id='N2'")
        reconcile(db)
        execute('TRUNCATE mpp_occurrence CASCADE')
        reconcile(db)
        v.require(True, 'failure/reliable transitions, removal and TRUNCATE preserve exact derived state')
        db.close()
        # Catalog rerun verifies triggers, functions, grants and new objects.
        v.init('check')
        v.require(True, 'current structure check accepts the maintained database')


def upgrade(pg):
    import hashlib
    from database.retention import digest
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env); v.init('bootstrap')
        old = (ROOT/'sql_apm/storage/versions/1.13.0.sql').read_text()
        v.sql("CREATE SCHEMA sql_apm; SET apm.readonly_role='sql_apm_ro';"+old+
              "INSERT INTO schema_version(version,script_sha256) VALUES('1.13.0','"+
              hashlib.sha256(old.encode()).hexdigest()+"');"+statements())
        db = psycopg2.connect(f'host={directory}/socket port=55473 dbname=sql_apm user=sql_apm')
        with db, db.cursor() as cur:
            cur.execute('SET search_path=sql_apm,pg_catalog')
        with db, db.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='sql_apm' AND tablename<>'schema_version'")
            tables = [r[0] for r in cur.fetchall()]
        before = {t:digest(db,t) for t in tables}
        receipt = v.sql("SELECT row_to_json(v) FROM schema_version v")
        v.init('upgrade'); v.init('check')
        for t in tables:
            assert before[t] == digest(db,t,('query_key',) if t in ('scope','analysis','mpp_normalization') else ()), t
        assert v.sql("SELECT row_to_json(v) FROM schema_version v WHERE version='1.13.0'")==receipt
        reconcile(db)
        v.init('upgrade');v.init('schema');v.init('check')
        reconcile(db)
        v.require(True,'populated 1.13 upgrade preserves every original column and receipt; backfill exact; reruns accepted')
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    statistics_guard(args.pg_bin, args.output)
    maintenance(args.pg_bin)
    upgrade(args.pg_bin)


if __name__ == '__main__':
    main()
