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
                        row(text='SELECT * FROM t WHERE id IN (1,', message='duration: 4 ms')])
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
                    current = export(db)
                    if original is None:
                        original = current
                        continue
                    outcome = compare(original, current)
                    assert outcome['passed'], outcome
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
                    print(json.dumps(dict(passed=True, tables=57, independent_runs=2, negative_cases=tested+1)))


if __name__ == '__main__':
    main()
