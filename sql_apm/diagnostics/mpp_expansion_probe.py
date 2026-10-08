#!/usr/bin/env python3
"""Bounded new-shape sampling and replay; raw SQL is confined to ignored var/."""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import time

from pglast import parser

from sql_apm.sql.lexical import diagnose
from sql_apm.diagnostics.mpp_adapter_probe import probe, sha_file, EVIDENCE, MAX_BYTES
from sql_apm.diagnostics.parser_fidelity import digest
from sql_apm.diagnostics.statement_census import DURATION, INLINE, origin

ROOT = Path(__file__).resolve().parents[2]

PREFIX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 5000
PER_FILE = 6
BASE_CACHE = ROOT / 'var/parser-probe/mpp-input-cache.json'
BASE_RESULT = ROOT / 'docs/reports/data/mpp-adapter-probe-2026-09-27.json'


def shape(sql):
    """Sampling heuristic, never a fingerprint or proof of semantic identity."""
    seq, issues = diagnose(sql)
    if issues:
        return digest([seq, issues]), ['lexical:' + issues[0]]
    try:
        tokens = parser.scan(sql)
    except (parser.ParseError, UnicodeError):
        return digest([seq, 'scanner_rejected']), ['scanner_rejected']
    names = [t.name for t in tokens if t.name not in ('C_COMMENT', 'SQL_COMMENT')]
    words = [sql[t.start:t.end + 1].upper() if t.name not in
             ('SCONST', 'USCONST', 'FCONST', 'ICONST', 'C_COMMENT', 'SQL_COMMENT') else '' for t in tokens]
    features = ['category:' + s for s in sorted(set(seq))]
    for label, terms in {'partition': ('PARTITION', 'BY'), 'subpartition': ('SUBPARTITION',),
                         'external': ('EXTERNAL',), 'copy_segment': ('ON', 'SEGMENT'),
                         'alter_with': ('SET', 'WITH'), 'distribution': ('DISTRIBUTED',),
                         'window': ('OVER',), 'cte': ('WITH',), 'union': ('UNION',),
                         'grouping': ('GROUPING',), 'lateral': ('LATERAL',)}.items():
        if any(words[i:i + len(terms)] == list(terms) for i in range(len(words))):
            features.append(label)
    if len(seq) > 1:
        features.append('batch')
    if words.count('SELECT') > 1:
        features.append('multiple_selects')
    if any(sql[t.start:t.end + 1].startswith(('/*+', '--+')) for t in tokens
           if t.name in ('C_COMMENT', 'SQL_COMMENT')):
        features.append('hint')
    return digest(names), sorted(set(features))


def inputs(row):
    if row[24].strip():
        yield 'sql', row[24]
    message = DURATION.sub('', row[18], count=1)
    match = INLINE.match(message)
    if match:
        sql = message[match.end():]
        if sql.strip() and sql != row[24]:
            yield 'inline_distinct', sql


def prepare(root, cache):
    manifest = json.loads(EVIDENCE.read_text())
    baseline = json.loads(BASE_CACHE.read_text())
    if sha_file(BASE_CACHE) != json.loads(BASE_RESULT.read_text())['cache_sha256']:
        raise ValueError('baseline cache mismatch')
    old_files = {(r['target']['cluster'], r['target']['file']) for r in baseline['rows']}
    seen_shapes = {shape(r['sql'])[0] for r in baseline['rows']}
    seen_features = set()
    for r in baseline['rows']:
        seen_features.update((r['target']['cluster'], f) for f in shape(r['sql'])[1])
    rows, files = [], []
    csv.field_size_limit(128 * 1024 * 1024)
    for cluster, data in sorted(manifest['clusters'].items()):
        for entry in sorted(data['files'], key=lambda x: x['file']):
            if (cluster, entry['file']) in old_files:
                continue
            path = (root / cluster / entry['file']).resolve()
            if root not in path.parents:
                raise ValueError('source path outside root')
            before = path.stat()
            if before.st_size != entry['bytes']:
                raise ValueError('source size mismatch')
            with path.open('rb') as stream:
                prefix = stream.read(PREFIX_BYTES)
            full = len(prefix) == before.st_size
            prefix_sha = hashlib.sha256(prefix).hexdigest()
            if full and prefix_sha != entry['sha256']:
                raise ValueError('full source hash mismatch')
            # An incomplete final physical line cannot be a complete record.
            bounded = prefix if full else prefix[:prefix.rfind(b'\n') + 1]
            reader = csv.reader(io.StringIO(bounded.decode('utf-8', 'surrogateescape'), newline=''), strict=True)
            candidates, counts, previous_line = {}, Counter(), 0
            try:
                for number, row in enumerate(reader, 1):
                    if number > MAX_RECORDS:
                        counts['record_limit'] += 1
                        break
                    start, previous_line = previous_line + 1, reader.line_num
                    counts['records'] += 1
                    if len(row) != 30:
                        raise ValueError('unexpected source column count')
                    for field, sql in inputs(row):
                        counts['inputs'] += 1
                        if len(sql.encode('utf-8', 'surrogateescape')) > MAX_BYTES:
                            counts['oversized_inputs'] += 1
                            continue
                        key, features = shape(sql)
                        if key in seen_shapes or key in candidates:
                            counts['known_or_duplicate_shape'] += 1
                            continue
                        seq, issues = diagnose(sql)
                        target = {'cluster': cluster, 'file': entry['file'], 'record': number,
                                  'field': field, 'line_start': start, 'line_end': reader.line_num,
                                  'origin': origin(row), 'categories': list(seq), 'issues': list(issues),
                                  'sql_sha256': hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest(),
                                  'sampling_shape_sha256': key, 'features': features}
                        candidates[key] = {'target': target, 'sql': sql}
            except csv.Error:
                if full or reader.line_num != bounded.count(b'\n'):
                    raise ValueError('malformed CSV before bounded tail') from None
                counts['bounded_partial_record_discarded'] += 1
            counts['novel_candidate_shapes'] = len(candidates)
            for _ in range(min(PER_FILE, len(candidates))):
                def priority(item):
                    features = item[1]['target']['features']
                    unseen = sum((cluster, f) not in seen_features for f in features)
                    mpp = sum(f in ('partition', 'subpartition', 'external', 'copy_segment',
                                     'alter_with', 'distribution', 'hint') for f in features)
                    return unseen, mpp, len(features), -item[1]['target']['record'], item[0]
                key, chosen = max(candidates.items(), key=priority)
                del candidates[key]
                rows.append(chosen)
                seen_shapes.add(key)
                seen_features.update((cluster, f) for f in chosen['target']['features'])
                counts['selected'] += 1
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('source changed during sampling')
            files.append({'cluster': cluster, 'file': entry['file'], 'file_bytes': before.st_size,
                          'historical_file_sha256': entry['sha256'], 'full_file_hash_rechecked': full,
                          'read_prefix_bytes': len(prefix), 'read_prefix_sha256': prefix_sha,
                          'stat_unchanged': True, 'counts': dict(counts)})
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({'evidence_sha256': sha_file(EVIDENCE),
                                'baseline_cache_sha256': sha_file(BASE_CACHE),
                                'selection_source_sha256': sha_file(Path(__file__)),
                                'files': files, 'rows': rows}, ensure_ascii=True) + '\n')
    print(json.dumps({'files': len(files), 'selected': len(rows),
                      'read_bytes': sum(f['read_prefix_bytes'] for f in files)}), flush=True)


def replay(cache, output):
    cached = json.loads(cache.read_text())
    if cached['evidence_sha256'] != sha_file(EVIDENCE):
        raise ValueError('source manifest changed')
    started, records = time.monotonic(), []
    for row in cached['rows']:
        sql, target = row['sql'], row['target']
        if hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest() != target['sql_sha256']:
            raise ValueError('cached input changed')
        result = probe(sql)
        formatting = None
        if result['state'] == 'prototype_parsed':
            changed = '\n/* expansion spacing */ '.join(sql[t.start:t.end + 1] for t in parser.scan(sql))
            repeated = probe(changed)
            formatting = {'state': repeated['state'],
                          'same': result['comparison_digest'] == repeated.get('comparison_digest')}
        records.append(dict(target, result=result, formatting_check=formatting))
    sources = ['sql_apm/sql/pg_ast.py', 'sql_apm/sql/lexical.py',
               'sql_apm/diagnostics/mpp_expansion_probe.py', 'sql_apm/diagnostics/mpp_adapter_probe.py',
               'sql_apm/sql/mpp_parser.py', 'sql_apm/diagnostics/statement_census.py']
    from importlib.metadata import version
    result = {'purpose': 'Expanded bounded parser experiment; no business fingerprints',
              'python': platform.python_version(), 'pglast': version('pglast'),
              'source_sha256': {p: sha_file(ROOT / p) for p in sources},
              'selection_source_sha256': cached['selection_source_sha256'],
              'evidence_sha256': sha_file(EVIDENCE), 'cache_sha256': sha_file(cache),
              'baseline_result_sha256': sha_file(BASE_RESULT), 'files': cached['files'],
              'limits': {'prefix_bytes': PREFIX_BYTES, 'max_records_per_file': MAX_RECORDS,
                         'max_selected_per_file': PER_FILE, 'max_sql_bytes': MAX_BYTES},
              'records': records, 'summary': dict(Counter(r['result']['state'] for r in records)),
              'reasons': dict(Counter(r['result'].get('reason', r['result']['state']) for r in records)),
              'seconds': round(time.monotonic() - started, 3)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'summary': result['summary'], 'reasons': result['reasons'],
                      'formatting_passed': sum(bool(r['formatting_check'] and r['formatting_check']['same']) for r in records),
                      'seconds': result['seconds']}))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path)
    ap.add_argument('--cache', type=Path, default=ROOT / 'var/parser-probe/expanded-input-cache.json')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    from importlib.metadata import version
    if platform.python_version() != '3.13.16' or version('pglast') != '7.18':
        ap.error('requires Python 3.13.16 and pglast 7.18')
    cache, output = args.cache.resolve(), args.output.resolve()
    if (ROOT / 'var').resolve() not in cache.parents:
        ap.error('cache must stay under ignored var/')
    if args.root:
        root = args.root.resolve()
        if any(p == root or root in p.parents for p in (cache, output)):
            ap.error('output and cache must be outside originals')
        prepare(root, cache)
    replay(cache, output)


if __name__ == '__main__':
    main()
