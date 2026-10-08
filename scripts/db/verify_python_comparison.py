#!/usr/bin/env python3
"""Bounded counterexamples for the interpreter upgrade's full-table comparison."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests'), str(ROOT/'scripts/deployment')]
from verify import instance, Verification
from ingestion.test_reader import row, write_csv, configuration
from python_comparison import export, compare
from verify_python_unicode import affected_pattern
import psycopg2


def representative(db, number):
    # Modify a transaction-local copy, leaving the product's immutable group
    # trigger and actual selection logic untouched. The exporter resolves this
    # identical-column TEMP table through PostgreSQL's normal search path.
    with db.cursor() as cur:
        cur.execute('SET search_path=sql_apm,pg_catalog')
        cur.execute('CREATE TEMP TABLE mpp_observation_group ON COMMIT DROP AS SELECT * FROM sql_apm.mpp_observation_group')
        cur.execute('''UPDATE mpp_observation_group SET result_id=(
            SELECT r.result_id FROM mpp_approximate_result r
            JOIN mpp_approximate_input i USING(input_id) WHERE i.raw_bytes=%s)
            WHERE database<>'unrelated' ''', (('SELECT * FROM t WHERE id IN ('+str(number)+',').encode(),))
        assert cur.rowcount == 1


def reference_counterexamples(db, current):
    # Missing/FK-invalid data is injected only into constraint-free TEMP copies;
    # this proves the comparison itself validates the contract independently.
    rejected = []
    for label, statement in [
        ('missing', "UPDATE mpp_observation_group SET result_id='missing'"),
        ('unavailable', "CREATE TEMP TABLE mpp_approximate_result ON COMMIT DROP AS SELECT * FROM sql_apm.mpp_approximate_result; UPDATE mpp_approximate_result SET state='failed'"),
        ('rule', "UPDATE mpp_observation_group SET rule_id='wrong'"),
        ('value', "UPDATE mpp_observation_group SET approximate_value='wrong'"),
        ('scope', "UPDATE mpp_observation_group SET scope_id='wrong'"),
        ('profile', "UPDATE mpp_observation_group SET profile='wrong'"),
        ('database', "UPDATE mpp_observation_group SET database='wrong'"),
        ('user', "UPDATE mpp_observation_group SET execution_user='wrong'"),
        ('timing', "UPDATE mpp_observation_group SET timing_type='parse'"),
        ('no_event', 'CREATE TEMP TABLE mpp_occurrence_approximate ON COMMIT DROP AS SELECT * FROM sql_apm.mpp_occurrence_approximate WHERE false'),
    ]:
        representative(db, 2)
        with db.cursor() as cur:
            cur.execute(statement)
        try:
            export(db)
        except ValueError as error:
            assert 'invalid observation representative' in str(error), str(error)
        else:
            raise AssertionError('invalid representative accepted: '+label)
        finally:
            db.rollback()
        rejected.append(label)
    # Result 3 satisfies the group's rule/value FK and has a genuine event,
    # but only in another database. It must not become a candidate for this one.
    representative(db, 3)
    try:
        export(db)
    except ValueError as error:
        assert 'invalid observation representative' in str(error)
    else:
        raise AssertionError('unrelated event representative accepted')
    finally:
        db.rollback()
    rejected.append('other_group_event')
    # Both snapshots contain exactly the same result pool; only the chosen
    # representative differs. A nonidentity field must still distinguish them.
    for field, assignment in [('diagnostics', "diagnostics||ARRAY['synthetic']"),
                              ('replacements', 'replacements+1')]:
        snapshots = []
        for number in (1, 2):
            representative(db, number)
            with db.cursor() as cur:
                cur.execute('UPDATE mpp_approximate_result SET '+field+'='+assignment+'''
                    WHERE input_id=(SELECT input_id FROM mpp_approximate_input WHERE raw_bytes=%s)''',
                    (b'SELECT * FROM t WHERE id IN (2,',))
            snapshots.append(export(db))
        outcome = compare(*snapshots)
        assert outcome['different_tables'] == ['mpp_observation_group'], outcome
        assert not outcome['representative_equivalence']['differences'][0]['nonidentity_fields_equal']
        # Changing a result pool without changing selection remains exact too.
        assert 'mpp_approximate_result' in compare(current, snapshots[1])['different_tables']
        rejected.append('different_'+field)
    representative(db, 2)
    with db.cursor() as cur:
        cur.execute("UPDATE mpp_observation_group SET group_id=group_id||'different'")
    assert compare(current, export(db))['different_tables'] == ['mpp_observation_group']
    rejected.append('other_group_column')
    representative(db, 2)
    with db.cursor() as cur:
        cur.execute('CREATE TEMP TABLE mpp_occurrence_approximate ON COMMIT DROP AS SELECT * FROM sql_apm.mpp_occurrence_approximate')
        cur.execute('''UPDATE mpp_occurrence_approximate SET result_id=(
            SELECT result_id FROM mpp_approximate_result JOIN mpp_approximate_input USING(input_id)
            WHERE raw_bytes=%s) WHERE result_id=(
            SELECT result_id FROM mpp_approximate_result JOIN mpp_approximate_input USING(input_id)
            WHERE raw_bytes=%s)''', (b'SELECT * FROM t WHERE id IN (2,', b'SELECT * FROM t WHERE id IN (1,'))
        assert cur.rowcount == 1
    assert compare(current, export(db))['different_tables'] == ['mpp_occurrence_approximate']
    rejected.append('event_reference_remains_exact')
    # NULL timing is the explicit 'unknown' dimension, not a missing relation.
    representative(db, 2)
    with db.cursor() as cur:
        cur.execute('CREATE TEMP TABLE mpp_occurrence ON COMMIT DROP AS SELECT * FROM sql_apm.mpp_occurrence')
        cur.execute('UPDATE mpp_occurrence SET timing_type=NULL')
        cur.execute("UPDATE mpp_observation_group SET timing_type='unknown'")
    unknown = export(db)
    assert sorted(r['candidates'] for r in unknown['representatives'].values()) == [1, 2]
    return rejected


def main():
    # The compact regex must select exactly the requested set, including ranges,
    # punctuation, surrogate code points and supplementary planes.
    points = {0x5b, 0x5c, 0x5d, 0x870, 0x871, 0xd800, 0x10ffff}
    pattern = affected_pattern(points)
    assert {c for c in range(0x110000) if pattern.fullmatch(chr(c))} == points
    assert affected_pattern(set()) is None
    with tempfile.TemporaryDirectory(prefix='sql-apm-python-input-') as temporary:
        root = Path(temporary)
        csv, config, training = root/'input.csv', root/'import.json', root/'training.json'
        write_csv(csv, [row(text='SELECT 1', message='duration: 2 ms'),
                        row(text='SELECT 1', message='duration: 3 ms'),
                        row(text='SELECT * FROM t WHERE id IN (1,', message='duration: 4 ms'),
                        row(text='SELECT * FROM t WHERE id IN (2,', message='duration: 5 ms'),
                        row(text='SELECT * FROM t WHERE id IN (3,', message='duration: 6 ms', **{'2': 'unrelated'})])
        config.write_text(json.dumps(configuration(config, [csv])))
        training.write_text(json.dumps(dict(version=1, clusters=['C1'], window=dict(cutoff_date='2026-07-31'))))
        original = None
        pg = Path('/usr/pgsql-17/bin')
        for attempt in range(2):
            with instance(pg) as (directory, env):
                v = Verification(pg, directory, env)
                v.init()
                dsn = 'host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
                words = ['full', '--config', str(config), '--source', 'S1', '--batch', 'B1',
                         '--training-config', str(training), '--workers', '1']
                result = subprocess.run([sys.executable, '-m', 'sql_apm', *words], cwd=ROOT,
                    env=dict(env, SQL_APM_DSN=dsn, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
                assert result.returncode == 0, result.stdout+result.stderr
                with psycopg2.connect(dsn) as db:
                    representative(db, attempt+1)
                    current = export(db)
                    assert sorted(r['candidates'] for r in current['representatives'].values()) == [1, 2]
                    if original is None:
                        original = current
                        continue
                    outcome = compare(original, current)
                    assert outcome['passed'], outcome
                    assert outcome['representative_equivalence']['changed_groups'] == 1
                    assert outcome['representative_equivalence']['differences'][0]['old_candidates'] == 2
                    # Every included value matters: tiny numeric changes, state,
                    # evidence and multiplicity must be rejected without tolerance.
                    mutations = [
                        ("UPDATE mpp_statistic SET log_median=log_median+0.00000000000000000001 WHERE layer='overall'", 'mpp_statistic'),
                        ("UPDATE mpp_fingerprint SET reason='changed' WHERE state<>'reliable'", 'mpp_fingerprint'),
                        ("UPDATE task SET state='failed',reason='synthetic_failure'", 'task'),
                        ("DELETE FROM problem_evidence", 'problem_evidence'),
                    ]
                    tested = 0
                    for statement, table in mutations:
                        representative(db, 2)
                        with db.cursor() as cur:
                            cur.execute(statement)
                            changed = cur.rowcount
                        if not changed:
                            db.rollback()
                            continue
                        changed_snapshot = export(db)  # Rolls back mutation too.
                        assert table in compare(current, changed_snapshot)['different_tables']
                        tested += 1
                    assert tested >= 3, 'insufficient populated negative cases'
                    # Re-keying never excuses a broken derived identifier.
                    with db.cursor() as cur:
                        cur.execute("INSERT INTO mpp_sql_text(sql_id,text,content_sha256) VALUES ('S:bad-derived-id','SELECT 999 AS fault',sha256(convert_to('SELECT 999 AS fault','UTF8')))")
                        cur.execute("INSERT INTO mpp_fingerprint SELECT 'F:wrong','S:bad-derived-id',normalization_id,profile,state,value,reason FROM mpp_fingerprint LIMIT 1")
                    try:
                        export(db)
                    except ValueError as error:
                        assert 'derived identity formula' in str(error)
                    else:
                        raise AssertionError('invalid derived identity accepted')
                    finally:
                        db.rollback()
                    rejected = reference_counterexamples(db, current)
                    print(json.dumps(dict(passed=True, tables=57, independent_runs=2, negative_cases=tested+1,
                        representative_negative_cases=rejected,
                        representative_positive_cases=['different_equivalent_source', 'unknown_timing'])))


if __name__ == '__main__':
    main()
