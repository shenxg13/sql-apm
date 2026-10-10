"""Daily-run settings: local JSON, validated before any database write."""
import json
from pathlib import Path

from sql_apm.ingestion.config import IngestionError, load_sources, required_string
from sql_apm.training.config import TrainingError, load_retention

OFF = 'off'
# Every key the daily configuration accepts; anything else is refused.
KEYS = {'root': ('version', 'import_config', 'training_config', 'sources', 'workers', 'build', 'cleanup', 'raw_files',
                 'stale_after_hours'),
        'sources.{source}': ('directory',), 'build': ('interval_days', 'clusters'), 'build.clusters': ('{cluster}',),
        'cleanup': ('enabled',), 'raw_files': ('retention_days',)}
DEFAULTS = dict(interval_days=1, retention_days=45, stale_after_hours=48, workers=4)


class DailyError(ValueError):
    """Fixed public code, never configuration contents or driver text."""


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DailyError('duplicate_config_key')
        result[key] = value
    return result


def section(document, name):
    value = document.get(name, {})
    if not isinstance(value, dict) or set(value) - set(KEYS[name]):
        raise DailyError('invalid_' + name)
    return value


def days(value, code):
    """A positive whole number of days, or "off"."""
    if isinstance(value, str) and value == OFF:
        return None
    if type(value) is not int or value < 1:
        raise DailyError(code)
    return value


def load_config(path):
    """Everything a run needs, with every referenced file and directory already checked."""
    try:
        base = Path(path).resolve().parent
        document = json.loads(Path(path).read_text(), object_pairs_hook=unique)
        if not isinstance(document, dict):
            raise DailyError('invalid_daily_config')
        if type(document['version']) is not int or document['version'] != 1:
            raise DailyError('daily_config_version')
        if set(document) - set(KEYS['root']):
            raise DailyError('unknown_config_key')
        files = {}
        for name in ('import_config', 'training_config'):
            if not required_string(document[name]):
                raise DailyError('invalid_' + name)
            files[name] = str((base / document[name]).resolve())
        workers = document.get('workers', DEFAULTS['workers'])
        if type(workers) is not int or not 1 <= workers <= 8:
            raise DailyError('invalid_workers')
        stale = document.get('stale_after_hours', DEFAULTS['stale_after_hours'])
        if type(stale) is not int or not 1 <= stale <= 8760:
            raise DailyError('invalid_stale_after_hours')
        build = section(document, 'build')
        interval = days(build.get('interval_days', DEFAULTS['interval_days']), 'invalid_build_interval')
        overrides = build.get('clusters', {})
        if not isinstance(overrides, dict):
            raise DailyError('invalid_build')
        cleanup = section(document, 'cleanup').get('enabled', True)
        if type(cleanup) is not bool:
            raise DailyError('invalid_cleanup')
        raw_days = days(section(document, 'raw_files').get('retention_days', DEFAULTS['retention_days']),
                        'invalid_raw_retention')
        declared = document['sources']
        if not isinstance(declared, dict) or not declared:
            raise DailyError('daily_sources_required')
        clusters, registered = load_sources(files['import_config'])
        sources, seen = {}, set()
        for source_id, item in declared.items():
            if not isinstance(item, dict) or set(item) != set(KEYS['sources.{source}']) or not required_string(item['directory']):
                raise DailyError('invalid_daily_source')
            if source_id not in registered:
                raise DailyError('unregistered_source')
            directory = (base / item['directory']).resolve()
            if not directory.is_dir():
                raise DailyError('receiving_directory_missing')
            if directory in seen:
                raise DailyError('duplicate_receiving_directory')
            seen.add(directory)
            sources[source_id] = dict(directory=directory, cluster=registered[source_id]['cluster'],
                                      source=registered[source_id])
        used = [c for c in clusters if any(s['cluster'] == c for s in sources.values())]
        if set(overrides) - set(used):
            raise DailyError('unknown_build_cluster')
        intervals = {c: days(overrides[c], 'invalid_build_interval') if c in overrides else interval for c in used}
        for cluster in used:
            load_retention(files['training_config'], cluster)
        return dict(files, clusters=used, sources=sources, intervals=intervals, cleanup=cleanup,
                    raw_days=raw_days, workers=workers, stale_after_hours=stale)
    except (DailyError, IngestionError, TrainingError):
        raise
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        raise DailyError('invalid_daily_config') from None
