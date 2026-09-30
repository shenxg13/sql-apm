from copy import deepcopy
from datetime import datetime,timedelta
import random
import unittest

from sql_apm.baseline.statistics import calculate_group,metrics,sufficiency,window_keys
from sql_apm.training.config import THRESHOLDS,TZ,validate,TrainingError
from baseline.oracle import assert_metrics


class StatisticsTests(unittest.TestCase):
    def test_independent_metrics(self):
        rng=random.Random(25)
        for values in [[],[0],[.001],[0,0],[0,0,1],[.0001,1.25,3,99],
                       [rng.random()*1e6 for _ in range(200)], [1e12+.001*i for i in range(50)]]:
            actual,reasons=metrics(values)
            assert_metrics(actual,values)
            self.assertEqual(set(reasons),{k for k,v in actual.items() if v is None})
        self.assertEqual(metrics([0])[1],dict.fromkeys(('cv','p95_p50','p99_p50'),'zero_denominator'))
        for value in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):metrics([value])

    def test_time_and_exclusions(self):
        start=datetime(2026,9,20,tzinfo=TZ);end=start+timedelta(days=9)
        rows=[(start,0,True,[]),(start+timedelta(days=1),1.5,True,[]),
              (start+timedelta(days=1,microseconds=-1),2,True,[]),
              (start+timedelta(days=3,hours=23),None,False,['a','a','b'])]
        stats,covers=calculate_group(rows,start,end,THRESHOLDS)
        for layer in THRESHOLDS:
            buckets=[s for s in stats if s['layer']==layer]
            self.assertEqual(sum(s['included_count'] for s in buckets),3)
            self.assertEqual(sum(s['excluded_count'] for s in buckets),1)
            c=next(c for c in covers if c['layer']==layer)
            self.assertFalse(set(c['computed_keys']) & set(c['empty_keys']))
            self.assertEqual(set(c['computed_keys']+c['empty_keys']),set(window_keys(start,end)[layer]))
        overall=stats[0];self.assertEqual(overall['exclusions_by_reason'],dict(a=1,b=1))
        assert_metrics(overall,[0,1.5,2])
        weeks=[s for s in stats if s['layer']=='week']
        self.assertEqual([(s['bucket_date'].isoformat(),s['partial_week']) for s in weeks],
                         [('2026-09-14',True),('2026-09-21',False)])
        excluded=next(s for s in stats if s['layer']=='day' and s['excluded_count'])
        assert_metrics(excluded,[])
        self.assertEqual(excluded['metric_null_reasons']['mean_ms'],'no_samples')
        self.assertEqual({s['bucket_number'] for s in stats if s['layer']=='hour'},{0,23})
        self.assertEqual({s['bucket_number'] for s in stats if s['layer']=='weekday'},{1,3,7})
        with self.assertRaises(ValueError):calculate_group([(end,1,True,[])],start,end,THRESHOLDS)

    def test_trailing_partial_week(self):
        start=datetime(2026,9,20,tzinfo=TZ);end=start+timedelta(days=9)
        stats,_=calculate_group([(end-timedelta(microseconds=1),0,True,[])],start,end,THRESHOLDS)
        week=next(s for s in stats if s['layer']=='week')
        self.assertTrue(week['partial_week'])
        self.assertEqual(week['bucket_date'].isoformat(),'2026-09-28')
        self.assertEqual(week['range_end'],end)

    def test_configured_thresholds(self):
        doc=dict(version=1,clusters=['test'],window=dict(cutoff_date='2026-09-30'),
                 thresholds=dict(week=dict(basic_count=2,coverage_min=1)))
        frozen=validate(doc,'test')
        self.assertEqual(frozen['thresholds']['week']['basic_count'],2)
        self.assertEqual(THRESHOLDS['week']['basic_count'],30)
        for bad in [dict(day=dict(coverage_min=7)),dict(week=dict(basic_count=True)),
                    dict(week=dict(coverage_kind='none')),dict(week=dict(p95_count=-1))]:
            with self.assertRaises(TrainingError):validate(dict(doc,thresholds=bad),'test')

    def test_every_threshold_boundary(self):
        for layer,config in THRESHOLDS.items():
            for label in ('basic','p95','p99'):
                minimum=config[label+'_count'];coverage=config['coverage_min']
                for n in (minimum-1,minimum,minimum+1):
                    for c in {max(0,coverage-1),coverage,coverage+1}:
                        got=sufficiency(config,n,list(range(c)),list(range(c)))[label]
                        self.assertEqual(got['met'],n>=minimum and c>=coverage,(layer,label,n,c))
        altered=deepcopy(THRESHOLDS['week']);altered['basic_count']=2;altered['coverage_min']=1
        self.assertTrue(sufficiency(altered,2,[1],[1])['basic']['met'])


if __name__=='__main__':unittest.main()
