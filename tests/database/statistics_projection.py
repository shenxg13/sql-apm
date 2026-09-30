"""Database-derived ThresholdResult boundaries and frozen snapshot semantics."""
from copy import deepcopy
from datetime import date,timedelta

from psycopg2 import Error
from psycopg2.extras import Json

from baseline.oracle import reference_sufficiency
from sql_apm.storage.training import TrainingStore

EXPRESSION='mpp_statistic_sufficiency(c.statistics_version,c.thresholds,s.layer,s.included_count,s.active_dates,s.active_week_starts)'


def verify_sufficiency(v,store,config,snapshot,first_build):
    def derive(version,thresholds,layer,count,dates,weeks):
        with store.db,store.db.cursor() as cur:
            cur.execute('SELECT mpp_statistic_sufficiency(%s,%s,%s,%s,%s::date[],%s::date[])',
                        (version,Json(thresholds),layer,count,dates,weeks))
            return cur.fetchone()[0]
    customized=deepcopy(config['thresholds'])
    for layer,value in customized.items():
        value.update(basic_count=10,p95_count=20,p99_count=30,coverage_min=0 if layer=='day' else 1)
    for name,thresholds in [('defaults',config['thresholds']),('custom',customized)]:
        for layer,threshold in thresholds.items():
            for label in ('basic','p95','p99'):
                minimum=threshold[label+'_count'];coverage=threshold['coverage_min']
                for count in (minimum-1,minimum,minimum+1):
                    for number in {max(0,coverage-1),coverage,coverage+1}:
                        dates=[date(2026,7,1)+timedelta(days=i) for i in range(number*(2 if layer=='weekday' else 1))]
                        weeks=[date(2026,6,29)+timedelta(weeks=i) for i in range(number if layer=='weekday' else min(number,1))]
                        actual=derive('baseline-formulas/1',thresholds,layer,count,dates,weeks)
                        assert actual==reference_sufficiency(threshold,count,dates,weeks),(name,layer,label,count,number)
            assert derive('baseline-formulas/1',thresholds,layer,0,[],[])==reference_sufficiency(threshold,0,[],[])
            v.require(True,name+' '+layer+' SQL ThresholdResult boundaries and empty samples match independent oracle')
    for version in ['baseline-formulas/2',None]:
        try:derive(version,config['thresholds'],'overall',0,[],[])
        except Error as error:assert error.diag.message_primary=='unsupported_statistics_version'
        else:raise AssertionError('unknown statistics version accepted')
    v.require(True,'unknown or missing statistics version never reinterprets thresholds as v1')
    for thresholds,layer in [({},'overall'),(config['thresholds'],'unknown'),
                              ({'day':dict(customized['day'],coverage_min=1)},'day'),
                              ({'week':dict(customized['week'],p99_count=-1)},'week')]:
        try:derive('baseline-formulas/1',thresholds,layer,0,[],[])
        except Error as error:assert error.diag.message_primary in ('invalid_statistics_thresholds','invalid_statistic_coverage')
        else:raise AssertionError('invalid thresholds accepted')
    v.require(True,'incomplete thresholds, invalid layers and invalid minima are rejected')
    changed=deepcopy(config)
    for value in changed['thresholds'].values():value.update(basic_count=1,p95_count=1,p99_count=1,coverage_min=0)
    training=TrainingStore(store.dsn,store.schema)
    try:next_snapshot=training.snapshot(changed,['B1'])
    finally:training.close()
    next_build=store.calculate('C1',snapshot['input_id'],next_snapshot['config_id'])
    with store.db,store.db.cursor() as cur:
        cur.execute('SELECT s.build_id,'+EXPRESSION+''' FROM mpp_statistic s JOIN build b USING(build_id)
            JOIN config_snapshot c USING(config_id) WHERE s.build_id=ANY(%s) AND layer='overall' AND included_count=7''',
            ([first_build,next_build['build_id']],))
        results=dict(cur.fetchall())
        assert results[first_build]['basic']['required_count']==30 and not results[first_build]['basic']['met']
        assert results[next_build['build_id']]['basic']['required_count']==1 and results[next_build['build_id']]['basic']['met']
    v.require(True,'new snapshot thresholds do not alter historical build ThresholdResult')
    from database.statistics_batch import verify_batch
    verify_batch(v,store,config['thresholds'],[first_build,next_build['build_id']])
