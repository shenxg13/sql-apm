"""Independent Decimal/reference-library oracle; no product statistics imports."""
from decimal import Decimal, localcontext
import statistics


def reference_metrics(samples):
    names = ('min_ms','max_ms','mean_ms','p25_ms','p50_ms','p75_ms','p90_ms','p95_ms','p99_ms',
             'stddev_ms','cv','mad_ms','iqr_ms','log_median','log_mad','p95_p50','p99_p50')
    if not samples:
        return dict.fromkeys(names)
    with localcontext() as context:
        context.prec = 40
        values = sorted(Decimal(str(x)) for x in samples)
        def q(p):
            pos = Decimal(len(values)-1)*Decimal(p)
            i = int(pos)
            return values[i]*(1-(pos-i)) + values[min(i+1,len(values)-1)]*(pos-i)
        median = statistics.median(values)
        mean = sum(values)/len(values)
        sigma = (sum((x-mean)**2 for x in values)/len(values)).sqrt()
        logs = [(1+x).ln() for x in values]
        log_median = statistics.median(logs)
        result = dict(min_ms=min(values), max_ms=max(values),mean_ms=mean,
            p25_ms=q('.25'),p50_ms=q('.5'),p75_ms=q('.75'),p90_ms=q('.9'),p95_ms=q('.95'),p99_ms=q('.99'),
            stddev_ms=sigma,cv=sigma/mean if mean else None,
            mad_ms=statistics.median(abs(x-median) for x in values),iqr_ms=q('.75')-q('.25'),
            log_median=log_median,log_mad=statistics.median(abs(x-log_median) for x in logs),
            p95_p50=q('.95')/median if median else None,p99_p50=q('.99')/median if median else None)
        return {k:float(v) if v is not None else None for k,v in result.items()}


def assert_metrics(actual, samples):
    expected = reference_metrics(samples)
    for key,value in expected.items():
        found = actual[key]
        if value is None:
            assert found is None, key
        else:
            assert found is not None and abs(float(found)-value) <= 1e-9 + 1e-10*abs(value), key
    return len(expected)


def reference_sufficiency(threshold, count, dates, weeks):
    """Independent logical ThresholdResult, including exact failure reasons."""
    coverage = len(dates) if threshold['coverage_kind']=='active_days' else len(weeks) if threshold['coverage_kind']=='active_weeks' else 0
    def result(name):
        minimum = threshold[name+'_count']
        passed_count, passed_coverage = count >= minimum, coverage >= threshold['coverage_min']
        failures = [('sample_count_below_min', passed_count),
                    (threshold['coverage_kind']+'_below_min', passed_coverage)]
        return dict(required_count=minimum, actual_count=count,
                    coverage_kind=threshold['coverage_kind'], required_coverage=threshold['coverage_min'],
                    actual_coverage=coverage, met=passed_count and passed_coverage,
                    reasons=[reason for reason,passed in failures if not passed])
    return {name: result(name) for name in ('basic','p95','p99')}
