#!/usr/bin/env python3
"""Bounded regressions for transactional query summaries and populated upgrades."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from verify import Verification, instance
from database.fixture import statements
from database import dashboard_data
from database import fingerprint_concurrency
from database.retention import digest
from sql_apm.baseline.workflow import run
from sql_apm.training.config import validate
from sql_apm.storage.ingestion import connect
from sql_apm.ingestion.config import identity
from sql_apm.ingestion.config import load_config
from sql_apm.sql.normalization import Normalizer
from ingestion.test_reader import row, write_csv
from verify_query_performance_full import panel_two, parse_notices, plan_nodes
from psycopg2.extras import Json
import psycopg2


def definition(source, name):
    start = source.index('CREATE OR REPLACE FUNCTION ' + name + '(')
    # Function bodies here end in one of the repository's two SQL spellings.
    candidates = [source.find(marker, start) for marker in ('END $function$;', '\nEND;')]
    end = min(pos for pos in candidates if pos >= 0)
    return source[start:end] + ('END $function$;' if source.startswith('END $function$;', end) else '\nEND;')


def training_equivalence(pg):
    """Frozen 1.12 functions are the oracle, including order and error precedence."""
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env); v.init()
        dsn = f'host={directory}/socket port=55473 dbname=sql_apm user=sql_apm'
        builds = dashboard_data.load(dsn, directory)
        db = connect(dsn, 'sql_apm')
        old = (ROOT / 'sql_apm/storage/versions/1.12.0.sql').read_text()
        names = ['mpp_training_decisions', 'mpp_query_missing_rules', 'mpp_query_training',
                 'mpp_query_history', 'mpp_view_executions', 'mpp_view_baseline_ranking']
        with db, db.cursor() as cur:
            for name in names:
                body = definition(old, name)
                for other in names:
                    body = body.replace(other + '(', 'reference_' + other + '(')
                cur.execute(body)
        def fetch(statement, values=()):
            with db, db.cursor() as cur:
                cur.execute(statement, values)
                return cur.fetchall()
        def compare(name, args):
            results = []
            for prefix in ('reference_', ''):
                try:
                    results.append(('rows', fetch('SELECT * FROM ' + prefix + name + '(' + ','.join(['%s']*len(args)) + ')', args)))
                except psycopg2.Error as error:
                    db.rollback()
                    results.append(('error', error.pgcode, error.diag.message_primary))
            assert results[0] == results[1], (name, results)
            return results[1]
        engine = Normalizer(); norm = 'N:' + identity(engine.context)
        fp = engine.normalize(dashboard_data.BUSY.format(status=1, day='2026-06-26'))['fingerprint']['value']
        token = fetch("SELECT mpp_view_identity_token('C1','shop','app_user')")[0][0]
        panel = panel_two(dict(norm=norm,fingerprint=fp,identity=token,timing='request',
                              start='2026-07-01 00:00:00+08:00',end='2026-07-24 00:00:00+08:00'))
        with db.cursor() as cur:
            try:
                for missing_count in (0,1):
                    if missing_count:
                        cur.execute("INSERT INTO mpp_sql_text(sql_id,text,content_sha256) "
                                    "SELECT 'warning72','SELECT 72',sha256(convert_to('SELECT 72','UTF8'))")
                    cur.execute(panel.replace('mpp_query_missing_rules(', 'reference_mpp_query_missing_rules('))
                    expected=cur.fetchall()
                    cur.execute(panel);actual=cur.fetchall()
                    assert expected==actual
                    assert any(row[1]=='提示' for row in actual)==bool(missing_count)
            finally:
                db.rollback()
        v.require(True, 'actual detail panel 2 output and warning visibility identical with zero and positive missing counts')
        ranking_cases = 0
        for order, scope, database, user, timings, minimum, limit in itertools.product(
                ('samples','p50','p95','p99','max','mean'), ('C1','C2',None), (None,'shop'), (None,'app_user'),
                ('','request,execute_first','request','execute_first','execute_fetch','parse','bind'),
                (0,100), (50,100,200,500)):
            compare('mpp_view_baseline_ranking', [norm, scope, database, user, timings, order, minimum, limit])
            ranking_cases += 1
        with db, db.cursor() as cur:
            cur.execute("UPDATE mpp_result_partition SET cleaned_at=clock_timestamp() WHERE partition_id=(SELECT partition_id FROM build WHERE build_id=%s)", (builds['current'],))
            for scope in ('C1', None):
                for prefix in ('reference_', ''):
                    cur.execute('SELECT * FROM '+prefix+'mpp_view_baseline_ranking(%s,%s)', (norm, scope))
                    if prefix: expected = cur.fetchall()
                    else: assert cur.fetchall() == expected
            db.rollback()
        v.require(True, f'{ranking_cases} ranking combinations equal frozen 1.12, including ranked_rows, sufficiency and cleaned current results')
        for order, limit, build in itertools.product(('latest', 'slowest'), (20, 200, 1000), (None, builds['older'], builds['current'])):
            compare('mpp_view_executions', [norm, fp, token, 'request', build, '2026-07-01', '2026-07-24',
                                           None, None, None, None, order, limit])
        for limit, build in itertools.product((20, 200, 1000), (None, builds['current'])):
            compare('mpp_query_history', [norm, fp, 'C1', 'shop', 'app_user', '2026-07-01', '2026-07-24',
                                         limit, None, build])
        records = [dict(analysis_id=a, occurrence_id=o) for a, o in fetch(
            'SELECT analysis_id,occurrence_id FROM mpp_occurrence ORDER BY analysis_id,occurrence_id LIMIT 1000')]
        for build in (builds['older'], builds['current'], builds['second']):
            compare('mpp_query_training', [build, Json(records)])
            compare('mpp_query_training', [build, Json(records[:1]*3)])
        # The first bad record still determines the error, even when the next
        # record has a different error. Build validation precedes page validation.
        missing = dict(analysis_id='absent', occurrence_id='absent')
        for build, page in [(None, []), ('absent', {}), (builds['current'], {}),
                            (builds['current'], None), (builds['current'], [missing, {}]),
                            (builds['current'], [{}, missing]), (builds['current'], records+[missing]),
                            (builds['current'], [])]:
            compare('mpp_query_training', [build, Json(page)])
        with db, db.cursor() as cur:
            cur.execute('ALTER TABLE training_config DISABLE TRIGGER USER')
            cur.execute("UPDATE training_config SET decision_version='historical/0'")
            cur.execute('ALTER TABLE training_config ENABLE TRIGGER USER')
            # Both functions run inside this transaction; roll back the deliberate
            # unsupported-rule fixture without changing later build inputs.
            for name in ('reference_mpp_query_training', 'mpp_query_training'):
                cur.execute('SELECT * FROM '+name+'(%s,%s)', (builds['current'], Json(records)))
                if name.startswith('reference_'): expected = cur.fetchall()
                else: assert cur.fetchall() == expected
            db.rollback()
        v.require(True, 'training and history equal frozen 1.12: latest/slowest, 20/200/1000, versions/no baseline, outside input/window, duplicates and errors')
        # Build the exact same sealed input with both implementations. This also
        # catches changes to the no-filter path shared by statistics builds.
        cfg = validate(dashboard_data.training(), 'C1')
        before = run(dsn, 'sql_apm', cfg)['build']['build_id']
        with db, db.cursor() as cur:
            cur.execute(definition(old, 'mpp_training_decisions'))
        after = run(dsn, 'sql_apm', cfg)['build']['build_id']
        for table in ('mpp_statistic', 'mpp_observation_statistic', 'mpp_build_group',
                      'mpp_build_observation_group', 'mpp_build_layer_count', 'mpp_build_timing_coverage'):
            assert digest(db, table, ('build_id',), 'WHERE build_id=%s', (before,)) == \
                   digest(db, table, ('build_id',), 'WHERE build_id=%s', (after,)), table
        v.require(True, 'complete builds: both statistic families, groups and layer/timing counts identical with old and new training functions')
        with db, db.cursor() as cur:
            cur.execute(definition((ROOT / 'sql_apm/storage/schema.sql').read_text(), 'mpp_training_decisions'))
            cur.execute('ALTER TABLE mpp_occurrence SET (autovacuum_enabled=false)')
            cur.execute('ANALYZE mpp_occurrence')
        incoming = directory / 'new-day.csv'
        write_csv(incoming, [row(text=dashboard_data.BUSY.format(status=1, day='2026-06-26'), message='duration: 72 ms',
            **{'0': '2026-07-24 12:00:00 CST', '1': 'new_user72', '2': 'new_database72'}) for _ in range(10000)])
        doc = json.loads((directory / 'import.json').read_text())
        doc['batches'] = {'B-new': dict(source='S1', files_confirmed_complete=True, dates=['2026-07-24'],
                                      files=[dict(path=str(incoming), closed_and_copied=True)])}
        config = directory / 'new-day.json'; config.write_text(json.dumps(doc))
        result = run(dsn, 'sql_apm', validate(dashboard_data.training('2026-07-24'), 'C1'), load_config(config, 'S1', 'B-new'), workers=1)
        assert result['state'] == 'succeeded'
        for kind, column, added in [('database','database','new_database72'), ('user','execution_user','new_user72')]:
            options = fetch('SELECT DISTINCT name FROM mpp_occurrence_name WHERE kind=%s AND item_count>0 ORDER BY name', (kind,))
            assert options == fetch('SELECT DISTINCT '+column+' FROM mpp_occurrence WHERE '+column+' IS NOT NULL ORDER BY 1')
            assert (added,) in options
        v.require(True, 'real next-day import automatically adds new database/user options, identical to all retained events')
        keys = fetch("""SELECT o.analysis_id,o.occurrence_id FROM mpp_occurrence o
            JOIN evidence_record e ON e.record_id=o.anchor_ref JOIN source_file f USING(file_id)
            WHERE f.locator=%s ORDER BY o.occurrence_id DESC LIMIT 1""", (str(incoming),))
        page = Json([dict(analysis_id=a, occurrence_id=o) for a, o in keys])
        assert len(keys) == 1
        compare('mpp_query_training', [result['build']['build_id'], page])
        admin = psycopg2.connect(dsn.replace('user=sql_apm', 'user=apm_test_admin'))
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SET search_path=sql_apm,pg_catalog; LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; "
                        "SET auto_explain.log_analyze=on; SET auto_explain.log_nested_statements=on; SET auto_explain.log_timing=off; "
                        "SET auto_explain.log_format=json; SET auto_explain.log_level=notice; SET client_min_messages=notice")
            for name in ('reference_mpp_query_training', 'mpp_query_training'):
                admin.notices.clear()
                cur.execute('SELECT * FROM '+name+'(%s,%s)', (result['build']['build_id'], page)); cur.fetchall()
                scans = [n for n in plan_nodes(parse_notices(''.join(admin.notices))) if n.get('Relation Name') == 'mpp_occurrence']
                assert scans
                if name.startswith('reference_'):
                    assert any(n.get('Rows Removed by Filter', 0) >= 9000 for n in scans), scans
                else:
                    assert all(n.get('Node Type') in ('Index Scan', 'Index Only Scan') and
                               all(key in n.get('Index Cond', '') for key in ('analysis_id', 'occurrence_id')) and
                               n.get('Rows Removed by Filter', 0) < 10 for n in scans), scans
        admin.close(); db.close()
        v.require(True, 'new analysis absent from statistics: old function scans the 10000-row batch; candidate uses both existing index key columns')


def summaries(v):
    for norm in ("'N1'", "'N2'", "'unknown'", 'NULL'):
        assert v.sql(f"""SELECT mpp_query_missing_rules({norm})=(
            SELECT count(*) FROM mpp_sql_text t WHERE NOT EXISTS (
                SELECT FROM mpp_fingerprint f WHERE f.sql_id=t.sql_id
                    AND f.normalization_id={norm} AND f.profile='mpp-csv/1'))""") == 't'
    for kind, column in (('database', 'database'), ('user', 'execution_user')):
        assert v.sql(f"SELECT DISTINCT name FROM mpp_occurrence_name WHERE kind='{kind}' AND item_count>0 ORDER BY name") == \
               v.sql(f'SELECT DISTINCT {column} FROM mpp_occurrence WHERE {column} IS NOT NULL ORDER BY {column}')
        assert v.sql(f"""WITH expected AS (SELECT analysis_id,{column} name,count(*) n FROM mpp_occurrence
            WHERE {column} IS NOT NULL GROUP BY analysis_id,{column}), actual AS (
            SELECT analysis_id,name,item_count n FROM mpp_occurrence_name WHERE kind='{kind}' AND item_count>0)
            SELECT NOT EXISTS((TABLE expected EXCEPT TABLE actual) UNION ALL (TABLE actual EXCEPT TABLE expected))""") == 't'


def verify(pg):
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env)
        v.init('bootstrap')
        old = (ROOT / 'sql_apm/storage/versions/1.12.0.sql').read_bytes()
        v.sql("SELECT set_config('apm.readonly_role','sql_apm_ro',false); CREATE SCHEMA sql_apm; SET search_path=sql_apm,pg_catalog;" +
              old.decode() + "INSERT INTO schema_version VALUES ('1.12.0','" + hashlib.sha256(old).hexdigest() + "',now());" + statements())
        tables = v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                       "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname").splitlines()
        def snapshot():
            return {t: v.sql('SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text),\'[]\') FROM "' + t + '" x' +
                            (" WHERE version='1.12.0'" if t == 'schema_version' else '')) for t in tables}
        before = snapshot()
        v.init('upgrade'); v.init('check')
        assert snapshot() == before
        summaries(v)
        v.require(True, 'populated 1.12 -> 1.13: existing rows and receipts preserved; summaries backfilled exactly')
        v.sql("INSERT INTO mpp_sql_text(sql_id,text,content_sha256) SELECT 'new','SELECT 72',sha256(convert_to('SELECT 72','UTF8'))")
        summaries(v)
        assert v.sql("SELECT mpp_query_missing_rules('N1')") == '1'
        v.sql("INSERT INTO mpp_fingerprint SELECT 'new-fp','new',normalization_id,profile,state,value,reason FROM mpp_fingerprint WHERE sql_id='SQL1'")
        # Fixture identifiers are inspected rather than assuming its original text id.
        if v.sql("SELECT count(*) FROM mpp_fingerprint WHERE sql_id='new'") == '0':
            v.sql("INSERT INTO mpp_fingerprint SELECT 'new-fp','new',normalization_id,profile,state,value,reason FROM mpp_fingerprint LIMIT 1")
        summaries(v)
        assert v.sql("SELECT mpp_query_missing_rules('N1')") == '0'
        v.sql("INSERT INTO mpp_fingerprint SELECT * FROM mpp_fingerprint ON CONFLICT DO NOTHING")
        summaries(v)
        v.sql("INSERT INTO mpp_normalization SELECT 'N2',algorithm_version,parser_version,dictionary_schema_version,'new',dictionary_digest_algorithm,'new',rules_ref FROM mpp_normalization LIMIT 1")
        assert v.sql("SELECT mpp_query_missing_rules('N2')") == '2'
        v.sql("UPDATE mpp_fingerprint SET normalization_id='N2' WHERE fingerprint_id='new-fp'")
        summaries(v)
        v.sql("DELETE FROM mpp_fingerprint WHERE fingerprint_id='new-fp'; DELETE FROM mpp_sql_text WHERE sql_id='new'")
        summaries(v)
        v.sql("BEGIN; INSERT INTO mpp_sql_text(sql_id,text,content_sha256) SELECT 'rollback','SELECT 73',sha256(convert_to('SELECT 73','UTF8')); ROLLBACK")
        summaries(v)
        v.require(True, 'missing-rule count: zero, new original, generated fingerprint, new/moved rule, conflict, delete and rollback')
        v.sql("UPDATE mpp_occurrence SET database='new_database',execution_user='new_user'")
        summaries(v)
        v.sql('UPDATE mpp_occurrence SET database=NULL,execution_user=NULL')
        summaries(v)
        v.sql("UPDATE mpp_occurrence SET database='restored',execution_user='restored'")
        summaries(v)
        v.sql('BEGIN; DELETE FROM mpp_occurrence_evidence; DELETE FROM mpp_decision; DELETE FROM mpp_input_occurrence; DELETE FROM mpp_occurrence; ROLLBACK')
        summaries(v)
        v.sql('TRUNCATE mpp_occurrence CASCADE')
        summaries(v)
        v.require(True, 'name options: update, NULL, rollback and truncate remain exact')
        for mode in ('schema', 'upgrade', 'check'):
            v.init(mode)
        summaries(v)
        v.sql('ALTER TABLE mpp_occurrence DISABLE TRIGGER mpp_occurrence_summary_insert')
        assert 'incompatible object: mpp_occurrence' in v.init('check', ok=False).stderr
        v.require(True, 'schema checks verify maintenance triggers; repeated initialization does not reset counts')
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env); v.init(); v.init('check')
        summaries(v)
        v.sql(statements()); summaries(v)
        v.require(True, 'fresh schema and populated upgrade use the same catalog and summary semantics')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--training-only', action='store_true')
    mode.add_argument('--concurrency-only', action='store_true')
    args = parser.parse_args()
    if not args.concurrency_only:
        if not args.training_only:
            verify(args.pg_bin)
        training_equivalence(args.pg_bin)
    if not args.training_only:
        with instance(args.pg_bin) as (directory, env):
            v = Verification(args.pg_bin, directory, env); v.init()
            dsn = f'host={directory}/socket port=55473 dbname=sql_apm user=sql_apm'
            fingerprint_concurrency.verify(v, dsn, directory)
