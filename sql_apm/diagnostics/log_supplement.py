"""Reproducible, SQL-free evidence for the seven-day log supplement."""
import argparse
from collections import Counter
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3

from sql_apm.diagnostics.statement_census import DigestReader


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_day(name):
    match = re.fullmatch(r'gpdb-(\d{4}-\d{2}-\d{2})_\d{6}\.csv(?:\.\d+)?', name)
    if not match:
        raise ValueError('invalid_file_date')
    return date.fromisoformat(match[1]).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')


def manifest(root, previous):
    """Carry old entries unchanged; independently read every CSV and date to EOF."""
    old = json.loads(previous.read_text())
    result = dict(format='log-supplement-manifest/1', previous_sha256=file_sha(previous),
                  clusters={}, validation={})
    csv.field_size_limit(128 * 1024 * 1024)
    for cluster, data in sorted(old['clusters'].items()):
        entries = {entry['file']: entry for entry in data['files']}
        paths = sorted((root / cluster).glob('*.csv*'))
        if not set(entries) <= {p.name for p in paths}:
            raise ValueError('historical_file_missing')
        added, checks = [], []
        for path in paths:
            before, days = path.stat(), Counter()
            day = file_day(path.name)
            with path.open('rb', buffering=0) as raw:
                digest = DigestReader(raw)
                with io.TextIOWrapper(io.BufferedReader(digest, 1024 * 1024),
                        encoding='utf-8', errors='surrogateescape', newline='') as stream:
                    for row in csv.reader(stream, strict=True):
                        if len(row) != 30:
                            raise ValueError('csv_column_mismatch')
                        # Only dates escape this reader; no SQL or source identifiers.
                        actual_day = date.fromisoformat(row[0][:10]).isoformat()
                        days[actual_day] += 1
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError('source_changed')
            if digest.used != before.st_size:
                raise ValueError('incomplete_read_or_file_date_mismatch')
            measured = dict(file=path.name, bytes=digest.used, sha256=digest.sha.hexdigest(),
                            counts=dict(records=sum(days.values())))
            if path.name in entries:
                entry = entries[path.name]
                if any(entry[key] != measured[key] for key in ('bytes', 'sha256')) or (
                        entry['counts']['records'] != measured['counts']['records']):
                    raise ValueError('historical_evidence_mismatch')
            else:
                added.append(measured)
            checks.append(dict(file=path.name, records_by_date=dict(days), read_to_eof=True,
                               stat_unchanged=True, columns=30, hash_rechecked=True, filename_date=day,
                               filename_date_matches_records=set(days) == {day}))
            print(json.dumps(dict(cluster=cluster, file=path.name, records=sum(days.values()))), flush=True)
        result['clusters'][cluster] = dict(files=data['files'] + added)
        result['validation'][cluster] = checks
    return result


def census_summary(directory, previous, evidence):
    old = json.loads(previous.read_text())['clusters']['120']
    expected = {e['file']: e for e in json.loads(evidence.read_text())['clusters']['120']['files']}
    results = [json.loads(path.read_text()) for path in sorted(directory.glob('*.json'))]
    if {r['file'] for r in results} != set(expected) or len(results) != len(expected):
        raise ValueError('census_file_set_mismatch')
    groups, counts = {}, Counter()
    for result in results:
        entry = expected[result['file']]
        if any(result[k] != entry[k] for k in ('bytes', 'sha256')) or (
                result['counts']['records'] != entry['counts']['records']):
            raise ValueError('census_evidence_mismatch')
        if result['counts'].get('unexpected_columns', 0):
            raise ValueError('census_column_mismatch')
        counts.update(result['counts'])
        for key, group in result['groups'].items():
            target = groups.setdefault(key, {})
            for field, value in group.items():
                if isinstance(value, dict):
                    target.setdefault(field, Counter()).update(value)
                else:
                    target[field] = target.get(field, 0) + value

    def channels(source):
        output = {}
        for key, group in source.items():
            channel = output.setdefault(key.split('/')[0], {})
            for field, value in group.items():
                if isinstance(value, dict):
                    channel.setdefault(field, Counter()).update(value)
                else:
                    channel[field] = channel.get(field, 0) + value
        return output

    # Fixed-label, bounded targets verify the new scan against original records.
    targets, seen = [], set()
    for result in results:
        for tag, example in sorted(result['examples'].items()):
            if '/batch:' in tag or tag in seen:
                continue
            seen.add(tag)
            target = dict(example, cluster='120', file=result['file'])
            if target not in targets:
                targets.append(target)
    return dict(format='log-supplement-census/1', evidence_sha256=file_sha(evidence),
                previous_sha256=file_sha(previous), counts=counts,
                previous_counts=old['counts'], channels=channels(groups),
                previous_channels=channels(old['groups']), replay_targets=targets,
                files=len(results), scan_seconds=sum(r['seconds'] for r in results))


def scan_summary(source):
    """Group exact inputs by actual CSV record day; never infer days from filenames."""
    db = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    with db:
        complete = db.execute("SELECT value FROM meta WHERE key='collection_complete'").fetchone()
        if not complete or json.loads(complete[0]) is not True or db.execute(
                'SELECT count(*) FROM inputs WHERE result IS NULL').fetchone()[0]:
            raise ValueError('incomplete_source')
        dated = db.execute("SELECT value FROM meta WHERE key='record_dates'").fetchone()
        if not dated or json.loads(dated[0]) is not True:
            raise ValueError('record_dates_required')
        buckets = {}
        for key, encoded in db.execute('SELECT file_key,evidence FROM files ORDER BY file_key'):
            entry = json.loads(encoded)
            cluster = key.split('/', 1)[0]
            for day, count in entry['records_by_date'].items():
                for label in (cluster, cluster + '/' + day):
                    bucket = buckets.setdefault(label, dict(records=0, input_occurrences=0,
                        unique_inputs=0, unique_states=Counter(), occurrence_states=Counter(),
                        unique_reasons=Counter(), occurrence_reasons=Counter(),
                        bytes_1023_unique_states=Counter(), bytes_1023_occurrence_states=Counter()))
                    bucket['records'] += count
        rows = db.execute("""SELECT i.id,length(i.sql),i.result,o.file_key,o.day,sum(o.count)
            FROM inputs i JOIN occurrence_dates o ON o.input_id=i.id
            GROUP BY i.id,o.file_key,o.day ORDER BY i.id""")
        last, seen = None, set()
        for uid, size, encoded, key, day, count in rows:
            if uid != last:
                last, seen = uid, set()
            result = json.loads(encoded)
            state = result['state']
            reason = result.get('reason', result.get('exception', state))
            cluster = key.split('/', 1)[0]
            for label in (cluster, cluster + '/' + day):
                bucket = buckets[label]
                bucket['input_occurrences'] += count
                bucket['occurrence_states'][state] += count
                bucket['occurrence_reasons'][reason] += count
                if size == 1023:
                    bucket['bytes_1023_occurrence_states'][state] += count
                if label not in seen:
                    seen.add(label)
                    bucket['unique_inputs'] += 1
                    bucket['unique_states'][state] += 1
                    bucket['unique_reasons'][reason] += 1
                    if size == 1023:
                        bucket['bytes_1023_unique_states'][state] += 1
    db.close()
    return dict(format='log-supplement-scan/1', source_sha256=file_sha(source), buckets=buckets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('manifest')
    build.add_argument('--root', type=Path, required=True)
    build.add_argument('--previous', type=Path, required=True)
    census = commands.add_parser('census')
    census.add_argument('--directory', type=Path, required=True)
    census.add_argument('--previous', type=Path, required=True)
    census.add_argument('--evidence', type=Path, required=True)
    scan = commands.add_parser('scan')
    scan.add_argument('--source', type=Path, required=True)
    for command in (build, census, scan):
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output_exists')
    if args.command == 'manifest':
        result = manifest(args.root, args.previous)
    elif args.command == 'census':
        result = census_summary(args.directory, args.previous, args.evidence)
    else:
        result = scan_summary(args.source)
    write_json(args.output, result)


if __name__ == '__main__':
    main()
