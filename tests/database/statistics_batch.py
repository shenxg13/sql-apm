"""Batch met query: boundaries, version rejection, and persisted snapshot parity."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from psycopg2 import Error
from psycopg2.extras import Json, execute_values

from baseline.oracle import reference_sufficiency

ROOT = Path(__file__).resolve().parents[2]
BATCH_SQL = (ROOT / 'sql_apm/storage/statistics_sufficiency.sql').read_text()
KEY = 's.partition_id,s.build_id,s.group_id,s.layer,s.bucket_date,s.bucket_number'
FULL_SQL = 'SELECT ' + KEY + ''', mpp_statistic_sufficiency(
    c.statistics_version,c.thresholds,s.layer,s.included_count,s.active_dates,s.active_week_starts)
    FROM mpp_statistic s JOIN build b USING(build_id) JOIN config_snapshot c USING(config_id)
    WHERE s.partition_id=b.partition_id AND s.build_id=%(build_id)s'''


def flags(result):
    return tuple(result[label]['met'] for label in ('basic', 'p95', 'p99'))


def compare(cur, build_id, expected=None):
    cur.execute(BATCH_SQL, {'build_id': build_id})
    rows = cur.fetchall()
    actual = {r[:6]: r[6:] for r in rows}
    assert len(actual) == len(rows), 'batch query duplicated natural keys'
    cur.execute(FULL_SQL, {'build_id': build_id})
    full = {r[:6]: flags(r[6]) for r in cur.fetchall()}
    assert actual == full, 'batch and complete ThresholdResult differ'
    if expected is not None:
        assert actual == expected, 'batch flags differ from independent oracle'
    return actual


def verify_batch(v, store, thresholds, builds):
    with store.db, store.db.cursor() as cur:
        previous, changed = [compare(cur, bid) for bid in builds]
        assert previous and changed
        assert {key[3] for key in previous} == {'overall', 'day', 'week', 'weekday', 'hour'}
        assert sum(row[0] for row in previous.values()) < sum(row[0] for row in changed.values())
        assert compare(cur, 'missing-build') == {}
    v.require(True, 'batch met matches persisted default/custom builds and isolates their sealed snapshots')

    # Temporary relations shadow only this connection's tables; rollback restores
    # the real schema. Synthetic boundary rows never change a sealed snapshot.
    try:
        with store.db.cursor() as cur:
            cur.execute('''CREATE TEMP TABLE build (build_id text,partition_id bigint,config_id text);
                CREATE TEMP TABLE config_snapshot (config_id text,statistics_version text,thresholds jsonb);
                CREATE TEMP TABLE mpp_statistic (partition_id bigint,build_id text,group_id text,
                    layer text,bucket_date date,bucket_number smallint,included_count bigint,
                    active_dates date[],active_week_starts date[])''')
            custom = deepcopy(thresholds)
            zero = deepcopy(thresholds)
            huge = deepcopy(thresholds)
            for layer in thresholds:
                custom[layer].update(basic_count=2,p95_count=3,p99_count=4,coverage_min=0 if layer=='day' else 2)
                zero[layer].update(basic_count=0,p95_count=0,p99_count=0,coverage_min=0)
                huge[layer].update(basic_count=2**80,p95_count=2**80+1,p99_count=2**80+2,
                                   coverage_min=0 if layer=='day' else 2**80)
            for bid, limits in [('default',thresholds),('custom',custom),('zero',zero),('huge',huge)]:
                cur.execute('INSERT INTO config_snapshot VALUES (%s,%s,%s)',(bid,'baseline-formulas/1',Json(limits)))
                cur.execute('INSERT INTO build VALUES (%s,1,%s)',(bid,bid))
                rows, expected = [], {}
                for layer, threshold in limits.items():
                    counts = {0,2**63-1} if bid=='huge' else {0} | {
                        max(0, threshold[label+'_count']+delta)
                        for label in ('basic','p95','p99') for delta in (-1,0,1)}
                    coverages = {0,1} if bid=='huge' else {
                        max(0,threshold['coverage_min']+delta) for delta in (-1,0,1)}
                    for count in sorted(counts):
                        for coverage in sorted(coverages):
                            # Day/week counts deliberately differ to detect using the wrong array.
                            dates = [date(2026,7,1)+timedelta(days=i) for i in range(coverage*2 if layer=='weekday' else coverage)]
                            weeks = [date(2026,6,29)+timedelta(weeks=i) for i in range(coverage if layer=='weekday' else min(coverage,1))]
                            key = (1,bid,str(len(rows)),layer,None,None)
                            rows.append(key+(count,dates,weeks))
                            expected[key] = flags(reference_sufficiency(threshold,count,dates,weeks))
                # Same Build ID in another partition must never leak into the selected build.
                rows.append((2,bid,'wrong-partition','overall',None,None,0,[],[]))
                execute_values(cur,'INSERT INTO mpp_statistic VALUES %s',rows)
                compare(cur,bid,expected)
            v.require(True, 'batch/full/oracle parity at five-layer count and coverage boundaries, zero and huge legal minima')
            invalid = deepcopy(custom)
            invalid['week']['p99_count'] = -1
            for version, limits, message in [
                ('baseline-formulas/2',custom,'unsupported_statistics_version'),
                (None,custom,'unsupported_statistics_version'),
                ('baseline-formulas/1',invalid,'invalid_statistics_thresholds'),
                ('baseline-formulas/1',{},'invalid_statistics_thresholds'),
            ]:
                cur.execute('SAVEPOINT invalid_config')
                cur.execute('UPDATE config_snapshot SET statistics_version=%s,thresholds=%s WHERE config_id=%s',
                            (version,Json(limits),'custom'))
                try:
                    cur.execute(BATCH_SQL,{'build_id':'custom'})
                except Error as error:
                    assert error.diag.message_primary == message
                else:
                    raise AssertionError('batch accepted invalid formula version or thresholds')
                finally:
                    cur.execute('ROLLBACK TO SAVEPOINT invalid_config')
                    cur.execute('RELEASE SAVEPOINT invalid_config')
    finally:
        store.db.rollback()
    v.require(True, 'batch rejects unknown/missing formula versions and malformed thresholds through the complete-result function')
