"""Local JSON configuration, validated before database writes or SQL parsing."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path

from sql_apm.ingestion.config import required_string

DECISION_VERSION = 'training-decision/1'
TZ = timezone(timedelta(hours=8))
THRESHOLDS = {layer: dict(basic_count=30, p95_count=200, p99_count=1000,
                        coverage_kind=kind, coverage_min=minimum)
              for layer, kind, minimum in [('overall', 'active_days', 7), ('day', 'none', 0),
                                          ('week', 'active_days', 3), ('weekday', 'active_weeks', 4),
                                          ('hour', 'active_days', 7)]}


class TrainingError(ValueError):
    """Fixed public code, never user configuration or driver exception text."""


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TrainingError('duplicate_config_key')
        result[key] = value
    return result


def load_config(path, cluster, *, cutoff_date=None, window_days=None, with_retention=False):
    try:
        document = json.loads(Path(path).read_text(), object_pairs_hook=unique)
        if not isinstance(document, dict):
            raise TrainingError('invalid_training_config')
        if (cutoff_date is not None or window_days is not None) and not isinstance(document.get('window', {}), dict):
            raise TrainingError('invalid_window')
        if cutoff_date is not None:
            document.setdefault('window', {})['cutoff_date'] = cutoff_date
        if window_days is not None:
            document.setdefault('window', {})['days'] = window_days
        config = validate(document, cluster)
        return (config, retention_months(document, cluster)) if with_retention else config
    except TrainingError:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise TrainingError('invalid_training_config') from None


def validate(document, cluster):
    try:
        if type(document['version']) is not int or document['version'] != 1:
            raise TrainingError('training_config_version')
        if set(document) - {'version', 'clusters', 'window', 'templates', 'exclusions', 'thresholds', 'retention'}:
            raise TrainingError('unknown_config_key')
        clusters = document['clusters']
        if not isinstance(clusters, list) or not clusters or not all(required_string(c) for c in clusters) or len(set(clusters)) != len(clusters):
            raise TrainingError('invalid_cluster')
        if cluster not in clusters:
            raise TrainingError('unknown_cluster')
        retention_months(document, cluster)
        window = document['window']
        cutoff = date.fromisoformat(window['cutoff_date'])
        days = window.get('days', 30)
        if set(window) - {'cutoff_date', 'days'} or cutoff.isoformat() != window['cutoff_date'] or type(days) is not int or days <= 0:
            raise TrainingError('invalid_window')
        start = datetime.combine(cutoff-timedelta(days=days-1), time(), TZ)
        end = datetime.combine(cutoff+timedelta(days=1), time(), TZ)
        templates, exclusions = document.get('templates', []), document.get('exclusions', [])
        if not isinstance(templates, list) or not isinstance(exclusions, list):
            raise TrainingError('invalid_rule_list')
        ids = set()
        for item in templates:
            if set(item)-{'id', 'sql', 'cluster', 'database', 'execution_user', 'description'}:
                raise TrainingError('unknown_template_key')
            if not all(required_string(item[k]) for k in ('id', 'sql', 'description')):
                raise TrainingError('invalid_template')
            if any(not required_string(item[k]) for k in ('cluster', 'database', 'execution_user') if k in item):
                raise TrainingError('invalid_template_scope')
            if 'cluster' in item and item['cluster'] not in clusters:
                raise TrainingError('unknown_cluster')
            if item['id'] in ids:
                raise TrainingError('duplicate_rule_id')
            ids.add(item['id'])
        intervals = []
        for item in exclusions:
            if set(item) != {'id', 'cluster', 'start', 'end', 'reason'} or not all(required_string(item[k]) for k in item):
                raise TrainingError('invalid_exclusion')
            if item['cluster'] not in clusters:
                raise TrainingError('unknown_cluster')
            a, b = datetime.fromisoformat(item['start']), datetime.fromisoformat(item['end'])
            if a.utcoffset() != timedelta(hours=8) or b.utcoffset() != timedelta(hours=8) or a >= b:
                raise TrainingError('invalid_exclusion_interval')
            if item['id'] in ids:
                raise TrainingError('duplicate_rule_id')
            ids.add(item['id'])
            intervals.append(dict(rule_id=item['id'], scope_id=item['cluster'], start=a.isoformat(),
                                  end=b.isoformat(), reason=item['reason']))
        thresholds = deepcopy(THRESHOLDS)
        overrides = document.get('thresholds', {})
        if not isinstance(overrides, dict) or set(overrides)-set(THRESHOLDS):
            raise TrainingError('invalid_thresholds')
        for layer, values in overrides.items():
            if not isinstance(values, dict) or set(values)-{'basic_count','p95_count','p99_count','coverage_min'}:
                raise TrainingError('invalid_thresholds')
            if any(type(value) is not int or value < 0 for value in values.values()):
                raise TrainingError('invalid_thresholds')
            thresholds[layer].update(values)
            if layer == 'day' and thresholds[layer]['coverage_min'] != 0:
                raise TrainingError('invalid_thresholds')
        return dict(scope_id=cluster, cutoff_date=cutoff.isoformat(), window_days=days,
                    window_start=start.isoformat(), window_end=end.isoformat(), templates=deepcopy(templates),
                    exclusions=intervals, thresholds=thresholds)
    except TrainingError:
        raise
    except (ValueError, KeyError, TypeError, OverflowError):
        raise TrainingError('invalid_training_config') from None


def retention_months(document, cluster):
    """Operational policy is validated but never included in the sealed config."""
    retention = document.get('retention', {})
    if not isinstance(retention, dict) or set(retention) - {'months', 'clusters'}:
        raise TrainingError('invalid_retention')
    months, overrides = retention.get('months', 2), retention.get('clusters', {})
    if not isinstance(overrides, dict):
        raise TrainingError('invalid_retention')
    if any(type(n) is not int or n < 1 for n in [months] + list(overrides.values())):
        raise TrainingError('invalid_retention_months')
    if set(overrides) - set(document['clusters']):
        raise TrainingError('unknown_retention_cluster')
    return overrides.get(cluster, months)


def load_retention(path, cluster):
    # Cleanup has no training cutoff. Use an internal placeholder solely to
    # validate a full-style config whose cutoff normally comes from its batch.
    return load_config(path, cluster, cutoff_date='2000-01-01', with_retention=True)[1]
