"""Five independent time layers and the frozen baseline-formulas/1 contract."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, localcontext
import math

from sql_apm.training.config import TZ

LAYERS = ('overall', 'day', 'week', 'weekday', 'hour')
METRICS = ('min_ms', 'max_ms', 'mean_ms', 'p25_ms', 'p50_ms', 'p75_ms',
           'p90_ms', 'p95_ms', 'p99_ms', 'stddev_ms', 'cv', 'mad_ms',
           'iqr_ms', 'log_median', 'log_mad', 'p95_p50', 'p99_p50')


def quantile(ordered, p):
    position = (len(ordered) - 1) * (Decimal(str(p)) if isinstance(ordered[0], Decimal) else p)
    lower = int(position)
    fraction = position - lower
    return ordered[lower] if not fraction else ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower])


def metrics(values):
    """Decimal arithmetic preserves small spread on large decimal durations."""
    with localcontext() as context:
        context.prec = 40
        return _metrics(values)


def _metrics(values):
    values = sorted(Decimal(str(value)) for value in values)
    if not values:
        return dict.fromkeys(METRICS), dict.fromkeys(METRICS, 'no_samples')
    if any(not x.is_finite() or x < 0 for x in values):
        raise ValueError('invalid_sample_duration')
    n = len(values)
    mean = sum(values) / n
    stddev = (sum((x - mean) ** 2 for x in values) / n).sqrt()
    result = dict(min_ms=values[0], max_ms=values[-1], mean_ms=mean, stddev_ms=stddev)
    for name, p in [('p25', .25), ('p50', .5), ('p75', .75), ('p90', .9), ('p95', .95), ('p99', .99)]:
        result[name + '_ms'] = quantile(values, p)
    median = result['p50_ms']
    logs = [math.log1p(float(x)) for x in values]
    log_median = quantile(logs, .5)
    result.update(mad_ms=quantile(sorted(abs(x - median) for x in values), .5),
                  iqr_ms=result['p75_ms'] - result['p25_ms'], log_median=log_median,
                  log_mad=quantile(sorted(abs(x - log_median) for x in logs), .5),
                  cv=stddev / mean if mean else None,
                  p95_p50=result['p95_ms'] / median if median else None,
                  p99_p50=result['p99_ms'] / median if median else None)
    return result, {key: 'zero_denominator' for key, value in result.items() if value is None}


def week_start(day):
    return day - timedelta(days=day.weekday())


def window_keys(start, end):
    start, end = start.astimezone(TZ), end.astimezone(TZ)
    days = [(start + timedelta(days=i)).date() for i in range((end.date() - start.date()).days)]
    return dict(overall=[None], day=days, week=sorted({week_start(d) for d in days}),
                weekday=list(range(1, 8)), hour=list(range(24)))


def sufficiency(threshold, count, dates, weeks):
    kind = threshold['coverage_kind']
    actual = {'none': 0, 'active_days': len(dates), 'active_weeks': len(weeks)}[kind]
    result = {}
    for name in ('basic', 'p95', 'p99'):
        required = threshold[name + '_count']
        reasons = []
        if count < required:
            reasons.append('sample_count_below_min')
        if actual < threshold['coverage_min']:
            reasons.append(kind + '_below_min')
        result[name] = dict(required_count=required, actual_count=count, coverage_kind=kind,
                            required_coverage=threshold['coverage_min'], actual_coverage=actual,
                            met=not reasons, reasons=reasons)
    return result


def calculate_group(rows, start, end, thresholds):
    """Rows: (start instant, duration or None, included bool, unique reason codes).

    Caller supplies exactly one row per countable execution; unresolved samples
    count as exclusions, outside-window and batch-only events never enter here.
    Only this group's samples and results are retained in memory.
    """
    buckets = {layer: defaultdict(list) for layer in LAYERS}
    for row in rows:
        instant = row[0]
        if instant is None or not start <= instant < end:
            raise ValueError('sample_outside_window')
        local = instant.astimezone(TZ)
        keys = (None, local.date(), week_start(local.date()), local.isoweekday(), local.hour)
        for layer, key in zip(LAYERS, keys):
            buckets[layer][key].append(row)
    statistics, coverage = [], []
    for layer, keys in window_keys(start, end).items():
        computed, empty = [], []
        for key in keys:
            events = buckets[layer].get(key)
            if not events:
                empty.append(key)
                continue
            computed.append(key)
            included = [event for event in events if event[2]]
            reasons = Counter(code for event in events if not event[2] for code in set(event[3]))
            dates = sorted({event[0].astimezone(TZ).date() for event in included})
            weeks = sorted({week_start(day) for day in dates})
            instants = [event[0] for event in included]
            a, b, partial = start, end, None
            if layer in ('day', 'week'):
                a = datetime.combine(key, time(), TZ)
                b = a + timedelta(days=7 if layer == 'week' else 1)
                if layer == 'week':
                    partial = a < start or b > end
                a, b = max(a, start), min(b, end)
            values, nulls = metrics(event[1] for event in included)
            statistics.append(dict(layer=layer, bucket_date=key if layer in ('day', 'week') else None,
                bucket_number=key if layer in ('weekday', 'hour') else None,
                range_start=a, range_end=b, partial_week=partial,
                included_count=len(included), excluded_count=len(events)-len(included),
                exclusions_by_reason=dict(reasons), active_dates=dates, active_week_starts=weeks,
                first_sample_at=min(instants) if instants else None,
                last_sample_at=max(instants) if instants else None,
                **values, metric_null_reasons=nulls,
                sufficiency=sufficiency(thresholds[layer], len(included), dates, weeks)))
        coverage.append(dict(layer=layer, computed_keys=computed, empty_keys=empty))
    return statistics, coverage
