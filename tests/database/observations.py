"""Independent bucket, coverage, reason and metric oracle for observations."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from baseline.oracle import assert_metrics

TZ = timezone(timedelta(hours=8))


def assert_observation_rows(rows, events, start, end):
    """Events: (start, duration, included, exclusion reasons), independent of product."""
    expected = {}
    for event in events:
        instant = event[0].astimezone(TZ)
        assert start <= instant < end
        monday = instant.date()-timedelta(days=instant.weekday())
        for key in [('overall',None,None), ('day',instant.date(),None), ('week',monday,None),
                    ('weekday',None,instant.isoweekday()), ('hour',None,instant.hour)]:
            expected.setdefault(key,[]).append(event)
    assert len(rows)==len(expected)
    keys = {(r['layer'],r['bucket_date'],r['bucket_number']) for r in rows}
    assert keys==set(expected), 'observation_bucket_keys'
    for row in rows:
        selected=expected[(row['layer'],row['bucket_date'],row['bucket_number'])]
        included=[e for e in selected if e[2]]
        assert row['included_count']==len(included)
        assert row['excluded_count']==len(selected)-len(included)
        assert row['exclusions_by_reason']==dict(Counter(r for e in selected if not e[2] for r in set(e[3])))
        dates=sorted({e[0].astimezone(TZ).date() for e in included})
        assert row['active_dates']==dates
        assert row['active_week_starts']==sorted({d-timedelta(days=d.weekday()) for d in dates})
        instants=[e[0] for e in included]
        assert row['first_sample_at']==(min(instants) if instants else None)
        assert row['last_sample_at']==(max(instants) if instants else None)
        a,b,partial=start,end,None
        if row['layer'] in ('day','week'):
            a=datetime.combine(row['bucket_date'],datetime.min.time(),TZ)
            b=a+timedelta(days=7 if row['layer']=='week' else 1)
            if row['layer']=='week':partial=a<start or b>end
            a,b=max(a,start),min(b,end)
        assert (row['range_start'],row['range_end'],row['partial_week'])==(a,b,partial)
        assert_metrics(row,[e[1] for e in included])
        nulls={name:('no_samples' if not included else 'zero_denominator')
               for name in ('min_ms','max_ms','mean_ms','p25_ms','p50_ms','p75_ms','p90_ms','p95_ms',
                            'p99_ms','stddev_ms','cv','mad_ms','iqr_ms','log_median','log_mad','p95_p50','p99_p50')
               if row[name] is None}
        assert row['metric_null_reasons']==nulls
        assert 'sufficiency' not in row
    return len(rows)
