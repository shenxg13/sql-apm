"""Explicit local declarations; configuration is data, never executable code."""
from datetime import date
import hashlib
import json
from pathlib import Path


class IngestionError(ValueError):
    """Only fixed public reason codes, never source contents or driver errors."""


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def identity(*parts):
    return hashlib.sha256(canonical(parts).encode('ascii')).hexdigest()


def required_string(value):
    return isinstance(value, str) and bool(value.strip()) and '\x00' not in value


def _document(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise IngestionError('duplicate_config_key')
            out[key] = value
        return out
    document = json.loads(Path(path).read_text(), object_pairs_hook=unique)
    if document['version'] != 1:
        raise IngestionError('config_version')
    clusters = document['clusters']
    if not isinstance(clusters, list) or len(set(clusters)) != len(clusters):
        raise IngestionError('ambiguous_cluster')
    if not all(required_string(x) for x in clusters):
        raise IngestionError('invalid_cluster')
    return document, clusters


def _source(sources, clusters, source_id):
    source = sources[source_id]
    if source['cluster'] not in clusters or not isinstance(source['cluster'], str):
        raise IngestionError('source_mapping_required')
    if source['build'] != 'HashData Warehouse 3.13.13' or source['timezone'] != 'UTC+08:00':
        raise IngestionError('unsupported_source_profile')
    if not required_string(source['declaration']):
        raise IngestionError('source_declaration_required')
    return source


def manifest(source_id, source, dates, entries):
    return dict(source_id=source_id, scope_id=source['cluster'], source=source, dates=sorted(dates),
                files=entries, manifest_digest=identity(source_id, source, sorted(dates), entries))


def load_sources(path):
    """Clusters in declared order and every registered source; no batch is read."""
    try:
        document, clusters = _document(path)
        sources = document['sources']
        if not isinstance(sources, dict):
            raise TypeError
        return list(clusters), {source_id: _source(sources, clusters, source_id) for source_id in sources}
    except IngestionError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise IngestionError('invalid_config_or_unregistered_source') from None


def load_config(path, source_id, batch_id):
    try:
        document, clusters = _document(path)
        source = _source(document['sources'], clusters, source_id)
        batch = document['batches'][batch_id]
        if batch['source'] != source_id or batch['files_confirmed_complete'] is not True:
            raise IngestionError('batch_confirmation_required')
        if not batch['dates'] or len(set(batch['dates'])) != len(batch['dates']):
            raise IngestionError('invalid_batch_dates')
        for day in batch['dates']:
            if date.fromisoformat(day).isoformat() != day:
                raise IngestionError('invalid_batch_dates')
        if not isinstance(batch['files'], list) or not batch['files']:
            raise IngestionError('empty_manifest')
        entries = []
        for item in batch['files']:
            if item['closed_and_copied'] is not True or not required_string(item['path']):
                raise IngestionError('file_confirmation_required')
            origin = item.get('origin_key')
            if origin is not None and not required_string(origin):
                raise IngestionError('invalid_origin_key')
            file = (Path(path).resolve().parent / item['path']).resolve()
            entries.append(dict(path=str(file), origin_key=origin, closed_and_copied=True))
        if len({e['path'] for e in entries}) != len(entries):
            raise IngestionError('duplicate_manifest_path')
        return dict(manifest(source_id, source, batch['dates'], entries), batch_id=batch_id)
    except IngestionError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise IngestionError('invalid_config_or_unregistered_source') from None
