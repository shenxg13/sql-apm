"""Read-only duration evidence: redacted dimensions, thresholds and merge ratios.

This is a log-record diagnostic, not execution pairing or training eligibility.
Extraction is rule-independent; reporting requires audited v4/v5 snapshots.
"""
import argparse
from array import array
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import date
from functools import lru_cache
import hashlib
import io
import json
import math
from pathlib import Path
import re
import resource
import sqlite3
import statistics
import time

from sql_apm.diagnostics.mpp_full_scan import ROOT, MEMORY_BYTES
from sql_apm.diagnostics.normalization_diff import readonly, meta, put_meta, compare, merge_category
from sql_apm.diagnostics.log_supplement import file_sha, write_json
from sql_apm.diagnostics.statement_census import DigestReader, INLINE, origin

FORMAT = 'duration-dispersion/1'
DURATION_VALUE = re.compile(r'^duration:\s*([0-9]+(?:\.[0-9]+)?)\s+ms\b\s*', re.I)
THRESHOLDS = (30, 200, 1000)
BUCKETS = ('<=2', '2-5', '5-10', '>10')
SCHEMA = ('CREATE TABLE samples(input_id INTEGER,cluster TEXT,database_hash TEXT,'
          'user_hash TEXT,site TEXT,day TEXT,duration_ms REAL)')
DIMENSIONS = 'cluster,database_hash,user_hash,site'


def private_hash(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogateescape')).hexdigest()


def duration_record(row):
    """One duration record, one SQL identity; prefer the independent SQL field."""
    if len(row) != 30:
        raise ValueError('invalid_csv_columns')
    match = DURATION_VALUE.match(row[18])
    if not match:
        return None
    value = float(match.group(1))
    if not math.isfinite(value):
        raise ValueError('invalid_duration')
    sql, field = row[24], 'sql'
    if not sql.strip():
        message = row[18][match.end():]
        inline = INLINE.match(message)
        sql, field = (message[inline.end():], 'inline') if inline else (row[21], 'internal')
    if not sql.strip():
        return {'empty': True}
    return dict(raw=sql.encode('utf-8', 'surrogateescape'), field=field, duration_ms=value,
                day=date.fromisoformat(row[0][:10]).isoformat(),
                database_hash=private_hash(row[2]), user_hash=private_hash(row[1]), site=origin(row))


def _extract_file(task):
    source_path, root, key, evidence, output = task
    path = (root / key).resolve()
    if root.resolve() not in path.parents:
        raise ValueError('source_outside_root')
    before = path.stat()
    if before.st_size != evidence['bytes']:
        raise ValueError('source_size_mismatch')
    csv.field_size_limit(128 * 1024 * 1024)
    counts = Counter()
    with readonly(source_path) as source, sqlite3.connect(str(output)) as target:
        target.execute(SCHEMA)

        @lru_cache(maxsize=8192)
        def lookup(sha):
            row = source.execute('SELECT id FROM inputs WHERE sha256=?', (sha,)).fetchone()
            if row is None:
                raise ValueError('duration_input_missing')
            return row[0]

        batch = []
        with path.open('rb', buffering=0) as raw:
            hashing = DigestReader(raw)
            with io.TextIOWrapper(io.BufferedReader(hashing, 1024*1024),
                                  encoding='utf-8', errors='surrogateescape', newline='') as stream:
                for row in csv.reader(stream, strict=True):
                    counts['records'] += 1
                    item = duration_record(row)
                    if item is None:
                        continue
                    counts['duration_records'] += 1
                    if item.get('empty'):
                        counts['empty_sql'] += 1
                        continue
                    uid = lookup(hashlib.sha256(item.pop('raw')).hexdigest())
                    counts['samples'] += 1
                    counts[item['field']] += 1
                    counts['unknown_site'] += item['site'] == 'OTHER'
                    batch.append((uid, key.split('/', 1)[0], item['database_hash'], item['user_hash'],
                                  item['site'], item['day'], item['duration_ms']))
                    if len(batch) >= 10000:
                        target.executemany('INSERT INTO samples VALUES (?,?,?,?,?,?,?)', batch)
                        batch.clear()
                target.executemany('INSERT INTO samples VALUES (?,?,?,?,?,?,?)', batch)
            actual_sha, actual_bytes = hashing.sha.hexdigest(), hashing.used
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ino) !=
                (after.st_size, after.st_mtime_ns, after.st_ino) or actual_bytes != evidence['bytes'] or
                actual_sha != evidence['sha256'] or counts['records'] != evidence['counts']['records']):
            raise ValueError('source_evidence_mismatch')
    return dict(file_key=key, sha256=actual_sha, bytes=actual_bytes, counts=dict(counts))


def extract_file(task):
    # Bound each CSV process; never expose raw exception text across the boundary.
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        return dict(state='complete', evidence=_extract_file(task))
    except Exception:
        return dict(state='extraction_failed')


def extract(source_path, root, cache_path, workers=4):
    if not 1 <= workers <= 8:
        raise ValueError('invalid_workers')
    if cache_path.exists() or source_path.resolve() == cache_path.resolve():
        raise ValueError('output_exists_or_conflicts')
    source_paths = ('sql_apm/diagnostics/duration_dispersion.py',
                    'sql_apm/diagnostics/normalization_diff.py', 'sql_apm/diagnostics/log_supplement.py',
                    'sql_apm/diagnostics/statement_census.py', 'sql_apm/diagnostics/mpp_full_scan.py')
    source_hashes = {path:file_sha(ROOT/path) for path in source_paths}
    before, code = file_sha(source_path), file_sha(Path(__file__))
    started = time.monotonic()
    with readonly(source_path) as source:
        metadata = meta(source)
        if metadata.get('collection_complete') is not True or metadata.get('record_dates') is not True:
            raise ValueError('incomplete_source')
        files = [(key, json.loads(evidence)) for key, evidence in source.execute(
                 'SELECT file_key,evidence FROM files ORDER BY file_key')]
    if not files:
        raise ValueError('missing_source_files')
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open('xb'):
        pass
    parts = cache_path.with_suffix(cache_path.suffix + '.parts')
    parts.mkdir()
    with sqlite3.connect(str(cache_path)) as cache:
        cache.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        cache.execute(SCHEMA)
        put_meta(cache, 'complete', False)
        cache.commit()
        tasks = [(source_path, root, key, evidence, parts / (str(i) + '.sqlite'))
                 for i, (key, evidence) in enumerate(files)]
        evidence_list, counts = [], Counter()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(extract_file, task): task for task in tasks}
            for future in as_completed(futures):
                result = future.result()
                if result['state'] != 'complete':
                    raise ValueError('extraction_failed')
                item = result['evidence']
                evidence_list.append(item)
                counts.update(item['counts'])
                # Private shards never contain SQL or unhashed user/database names.
                cache.execute('ATTACH DATABASE ? AS part', (str(futures[future][-1]),))
                cache.execute('INSERT INTO samples SELECT * FROM part.samples')
                cache.commit()
                cache.execute('DETACH DATABASE part')
                futures[future][-1].unlink()
                print(json.dumps(dict(files_completed=len(evidence_list), files=len(files),
                                      samples=counts['samples'])), flush=True)
        if before != file_sha(source_path) or source_hashes != {
                path:file_sha(ROOT/path) for path in source_paths}:
            raise ValueError('source_or_code_changed')
        result = dict(format=FORMAT, complete=True, source_sha256=before, extraction_code_sha256=code, sources=source_hashes,
                      files=sorted(evidence_list, key=lambda e:e['file_key']), counts=dict(counts),
                      limits=dict(workers=workers, worker_address_space_bytes=MEMORY_BYTES),
                      seconds=round(time.monotonic()-started, 3))
        for key, value in result.items():
            put_meta(cache, key, value)
        cache.commit()
    parts.rmdir()
    return result


class Median:
    def __init__(self):
        self.values = array('d')

    def step(self, value):
        self.values.append(value)

    def finalize(self):
        return statistics.median(self.values)


def ratio_bucket(minimum, maximum):
    # Equal zero medians have ratio one; zero vs positive is unbounded.
    ratio = maximum / minimum if minimum else (1 if maximum == 0 else math.inf)
    return next(label for edge, label in ((2,'<=2'), (5,'2-5'), (10,'5-10'), (math.inf,'>10'))
                if ratio <= edge)


def summarize(db):
    """Joined samples must expose old_fp/new_fp; rejected inputs stay in denominators."""
    db.create_aggregate('median', 1, Median)
    clusters = {cluster: dict(duration_samples=count, versions={}, merges={})
                for cluster, count in db.execute('SELECT cluster,count(*) FROM joined GROUP BY cluster')}
    for version, fp in (('v4', 'old_fp'), ('v5', 'new_fp')):
        db.execute('CREATE TEMP TABLE groups_' + version + ' AS SELECT ' + DIMENSIONS + ',' +
                   fp + ' AS fingerprint,count(*) AS samples,count(DISTINCT day) AS days,'
                   'median(duration_ms) AS median_ms FROM joined WHERE ' + fp +
                   ' IS NOT NULL GROUP BY ' + DIMENSIONS + ',' + fp)
        for cluster, summary in clusters.items():
            groups = list(db.execute('SELECT samples,days FROM groups_' + version +
                                     ' WHERE cluster=?', (cluster,)))
            accepted = sum(n for n, _ in groups)
            thresholds = {}
            for threshold in THRESHOLDS:
                eligible = [(n, days) for n, days in groups if n >= threshold and days >= 7]
                samples = sum(n for n, _ in eligible)
                thresholds[str(threshold)] = dict(groups=len(eligible), samples=samples,
                    fraction_all_duration_samples=samples/summary['duration_samples'],
                    fraction_fingerprinted_samples=samples/accepted if accepted else 0)
            summary['versions'][version] = dict(groups=len(groups), fingerprinted_samples=accepted,
                                               thresholds=thresholds)
    # Attribute each final merged group in the fixed SELECT -> JOIN -> sets order.
    db.create_function('merge_category', 2, merge_category)
    db.execute('CREATE TEMP TABLE subgroups AS SELECT ' + DIMENSIONS +
               ',new_fp,old_fp,min(select_fp) AS select_fp,min(join_fp) AS join_fp,'
               'count(*) AS samples,median(duration_ms) AS median_ms '
               'FROM joined WHERE old_fp IS NOT NULL AND new_fp IS NOT NULL GROUP BY ' +
               DIMENSIONS + ',new_fp,old_fp')
    db.execute('CREATE TEMP TABLE attributed AS SELECT s.*,c.category FROM subgroups s JOIN '
               '(SELECT ' + DIMENSIONS + ',new_fp,merge_category(count(DISTINCT select_fp),'
               'count(DISTINCT join_fp)) AS category FROM subgroups GROUP BY ' + DIMENSIONS +
               ',new_fp) c USING (' + DIMENSIONS + ',new_fp)')
    # The historical "WHERE" control means raw identity -> v4, including ALL v4
    # rules. It does not infer that an arbitrary SQL-text difference was a WHERE.
    db.execute('CREATE TEMP TABLE reference_subgroups AS SELECT ' + DIMENSIONS +
               ',old_fp AS new_fp,input_id AS old_fp,count(*) AS samples,'
               'median(duration_ms) AS median_ms FROM joined WHERE old_fp IS NOT NULL GROUP BY ' +
               DIMENSIONS + ',old_fp,input_id')
    for cluster, summary in clusters.items():
        summary['merges'] = distribution(db, 'attributed', cluster)
        summary['merges_by_category'] = {
            category: distribution(db, 'attributed', cluster, category)
            for category in ('select_list', 'join_on', 'set_branches')}
        summary['v4_raw_reference'] = distribution(db, 'reference_subgroups', cluster)
    return clusters


def distribution(db, table, cluster, category=None):
    condition = 'cluster=?' + (' AND category=?' if category else '')
    args = (cluster, category) if category else (cluster,)
    grouping = DIMENSIONS + ',new_fp'
    merged = db.execute('SELECT count(*) FROM (SELECT 1 FROM ' + table + ' WHERE ' +
                        condition + ' GROUP BY ' + grouping + ' HAVING count(*)>1)', args).fetchone()[0]
    buckets = {name: dict(groups=0, samples=0) for name in BUCKETS}
    comparable_groups = comparable_samples = 0
    for minimum, maximum, samples in db.execute(
            'SELECT min(median_ms),max(median_ms),sum(samples) FROM ' + table +
            ' WHERE ' + condition + ' AND samples>=5 GROUP BY ' + grouping + ' HAVING count(*)>=2', args):
        bucket = buckets[ratio_bucket(minimum, maximum)]
        bucket['groups'] += 1
        bucket['samples'] += samples
        comparable_groups += 1
        comparable_samples += samples
    for item in buckets.values():
        item['sample_fraction'] = item['samples']/comparable_samples if comparable_samples else 0
    return dict(new_merged_groups=merged, comparable_groups=comparable_groups,
                comparable_samples=comparable_samples, median_ratio_buckets=buckets)


def report(cache_path, before_path, after_path, output):
    if output.exists():
        raise ValueError('output_exists')
    started = time.monotonic()
    paths = dict(cache=cache_path, before=before_path, after=after_path)
    hashes = {name:file_sha(path) for name,path in paths.items()}
    code = file_sha(Path(__file__))
    comparison = compare(before_path, after_path, require_v5=True)
    if not comparison['v5_acceptance_passed']:
        raise ValueError('v5_audit_failed')
    with readonly(cache_path) as db:
        extraction = meta(db)
        if (extraction.get('complete') is not True or extraction.get('format') != FORMAT or
                extraction['source_sha256'] != comparison['source_sha256']):
            raise ValueError('incompatible_duration_cache')
        if db.execute('SELECT count(*) FROM samples').fetchone()[0] != extraction['counts']['samples']:
            raise ValueError('duration_count_mismatch')
        # Attach in explicit read-only URI mode; all derived tables are TEMP.
        db.execute('ATTACH DATABASE ? AS old', (before_path.resolve().as_uri()+'?mode=ro',))
        db.execute('ATTACH DATABASE ? AS new', (after_path.resolve().as_uri()+'?mode=ro',))
        if db.execute('SELECT count(*) FROM samples s LEFT JOIN old.records o ON s.input_id=o.input_id '
                      'LEFT JOIN new.records n ON s.input_id=n.input_id WHERE o.input_id IS NULL '
                      'OR n.input_id IS NULL').fetchone()[0]:
            raise ValueError('snapshot_missing_duration_input')
        db.execute('CREATE TEMP VIEW joined AS SELECT s.*,o.fingerprint AS old_fp,n.fingerprint AS new_fp,'
                   'o.select_projection_sha256 AS select_fp,o.join_projection_sha256 AS join_fp '
                   'FROM samples s JOIN old.records o ON s.input_id=o.input_id '
                   'JOIN new.records n ON s.input_id=n.input_id')
        clusters = summarize(db)
    if hashes != {name:file_sha(path) for name,path in paths.items()} or code != file_sha(Path(__file__)):
        raise ValueError('evidence_or_code_changed')
    result = dict(format=FORMAT, complete=True, extraction=extraction, input_sha256=hashes,
                  report_code_sha256=code, contexts=comparison['contexts'],
                  comparison=comparison, clusters=clusters,
                  semantics=dict(dimensions=['cluster','database_hash','user_hash','source_location','fingerprint'],
                      date_basis='CSV record date, not execution start',
                      merge_categories=comparison['category_basis'],
                      v4_reference='raw input identity to v4; historical WHERE control includes all accepted v4 rules',
                      min_days=7, min_samples=list(THRESHOLDS), subgroup_min_samples=5,
                      weight='samples in v4 subgroups of size >=5, with at least two such subgroups per v5 group',
                      duration='milliseconds; one SQL identity per duration record; no Execute pairing or training filter'),
                  seconds=round(time.monotonic()-started, 3))
    write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    extraction = commands.add_parser('extract')
    extraction.add_argument('--source', type=Path, required=True)
    extraction.add_argument('--root', type=Path, required=True)
    extraction.add_argument('--cache', type=Path, required=True)
    extraction.add_argument('--workers', type=int, default=4)
    summary = commands.add_parser('report')
    summary.add_argument('--cache', type=Path, required=True)
    summary.add_argument('--before', type=Path, required=True)
    summary.add_argument('--after', type=Path, required=True)
    summary.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if (ROOT/'var').resolve() not in args.cache.resolve().parents:
            raise ValueError('cache_must_stay_under_var')
        if args.command == 'extract':
            result = extract(args.source, args.root, args.cache, args.workers)
            print(json.dumps(dict(state='complete', counts=result['counts'])))
        else:
            result = report(args.cache, args.before, args.after, args.output)
            print(json.dumps(dict(state='complete', clusters=result['clusters'])))
        return 0
    except Exception:
        print(json.dumps(dict(state='duration_diagnostic_failed', format=FORMAT)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
