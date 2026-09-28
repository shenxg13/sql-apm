#!/usr/bin/env python3
"""Bounded whole-parser replay; production SQL remains in ignored var/."""
import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import platform
import sys
import time
import hashlib

from pglast import parser

from sql_apm.sql.lexical import diagnose
from sql_apm.diagnostics.mpp_adapter_probe import EVIDENCE, MAX_BYTES, probe, sha_file
from sql_apm.diagnostics.mpp_expansion_probe import inputs, shape
from sql_apm.diagnostics.parser_fidelity import digest
from sql_apm.sql.pg_ast import pg_clean
from sql_apm.diagnostics.statement_census import origin

ROOT = Path(__file__).resolve().parents[2]

PREFIX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 10000
PER_FILE = 16
BASE_CACHES = [ROOT / 'var/parser-probe/mpp-input-cache.json',
               ROOT / 'var/parser-probe/expanded-input-cache.json']


def candidates_from_row(row):
    seen = set()
    for field, sql in inputs(row):
        seen.add(sql)
        yield field, sql
    if row[21].strip() and row[21] not in seen:
        yield 'internal', row[21]


def prepare(root, cache):
    manifest = json.loads(EVIDENCE.read_text())
    baseline = [row for path in BASE_CACHES for row in json.loads(path.read_text())['rows']]
    seen = {shape(row['sql'])[0] for row in baseline}
    files, selected, seen_categories = [], [], Counter()
    csv.field_size_limit(128 * 1024 * 1024)
    for cluster, data in sorted(manifest['clusters'].items()):
        for entry in sorted(data['files'], key=lambda x: x['file']):
            path = (root / cluster / entry['file']).resolve()
            if root not in path.parents:
                raise ValueError('source outside root')
            before = path.stat()
            if before.st_size != entry['bytes']:
                raise ValueError('source size mismatch')
            with path.open('rb') as stream:
                prefix = stream.read(PREFIX_BYTES)
            full = len(prefix) == before.st_size
            prefix_sha = hashlib.sha256(prefix).hexdigest()
            if full and prefix_sha != entry['sha256']:
                raise ValueError('full source hash mismatch')
            bounded = prefix if full else prefix[:prefix.rfind(b'\n') + 1]
            decoded = bounded.decode('utf-8', 'surrogateescape')
            stream = io.StringIO(decoded, newline='')
            reader = csv.reader(stream, strict=True)
            candidates, counts, previous_line = {}, Counter(), 0
            try:
                for number, row in enumerate(reader, 1):
                    if number > MAX_RECORDS:
                        counts['record_limit'] += 1
                        break
                    line_start, previous_line = previous_line + 1, reader.line_num
                    counts['records'] += 1
                    if len(row) != 30:
                        raise ValueError('source column mismatch')
                    for field, sql in candidates_from_row(row):
                        counts['inputs'] += 1
                        if len(sql.encode('utf-8', 'surrogateescape')) > MAX_BYTES:
                            counts['oversized_inputs'] += 1
                            continue
                        key, features = shape(sql)
                        if key in seen or key in candidates:
                            counts['known_or_duplicate_shape'] += 1
                            continue
                        categories, issues = diagnose(sql)
                        target = dict(cluster=cluster, file=entry['file'], record=number,
                                      field=field, line_start=line_start, line_end=reader.line_num,
                                      origin=origin(row), categories=list(categories), issues=list(issues),
                                      sql_sha256=hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest(),
                                      sampling_shape_sha256=key, features=features)
                        candidates[key] = {'target': target, 'sql': sql}
            except csv.Error as exc:
                if full or str(exc) != 'unexpected end of data' or stream.tell() != len(decoded):
                    raise ValueError('malformed CSV before bounded tail') from None
                counts['bounded_partial_record_discarded'] += 1
            counts['novel_candidate_shapes'] = len(candidates)
            for _ in range(min(PER_FILE, len(candidates))):
                def priority(item):
                    t = item[1]['target']
                    rarity = sum(1 / (seen_categories[(cluster, c)] + 1) for c in t['categories'])
                    extensions = sum(f in ('partition', 'subpartition', 'external', 'copy_segment',
                                          'alter_with', 'distribution', 'hint') for f in t['features'])
                    return rarity, extensions, t['record'] > 5000, len(t['features']), -t['record'], item[0]
                key, row = max(candidates.items(), key=priority)
                del candidates[key]
                selected.append(row)
                seen.add(key)
                seen_categories.update((cluster, c) for c in row['target']['categories'])
                counts['selected'] += 1
                counts['selected_after_record_5000'] += row['target']['record'] > 5000
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('source changed during sampling')
            files.append(dict(cluster=cluster, file=entry['file'], file_bytes=before.st_size,
                              historical_file_sha256=entry['sha256'], full_file_hash_rechecked=full,
                              read_prefix_bytes=len(prefix), read_prefix_sha256=prefix_sha,
                              stat_unchanged=True, counts=dict(counts)))
            print(json.dumps({'sampled_files': len(files), 'selected': len(selected)}), flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({'evidence_sha256': sha_file(EVIDENCE),
                                'baseline_cache_sha256': {str(p.relative_to(ROOT)): sha_file(p) for p in BASE_CACHES},
                                'selection_source_sha256': sha_file(Path(__file__)),
                                'files': files, 'rows': selected}, ensure_ascii=True) + '\n')


def replay(cache, output):
    cached = json.loads(cache.read_text())
    if cached['evidence_sha256'] != sha_file(EVIDENCE):
        raise ValueError('source manifest changed')
    for name, sha in cached['baseline_cache_sha256'].items():
        if sha_file(ROOT / name) != sha:
            raise ValueError('baseline cache changed')
    records, started = [], time.monotonic()
    for row in cached['rows']:
        sql, target = row['sql'], row['target']
        if hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest() != target['sql_sha256']:
            raise ValueError('cached input changed')
        result = probe(sql, synthetic=True)
        tree = result.pop('tree', None)
        formatting, native = None, None
        if tree:
            changed = '\n/* broad spacing */ '.join(sql[t.start:t.end + 1] for t in parser.scan(sql))
            repeated = probe(changed, synthetic=True)
            formatting = {'state': repeated['state'], 'same_complete_tree': repeated.pop('tree', None) == tree}
            if all(not s['extensions'] for s in tree['statements']):
                try:
                    pg = json.loads(parser.parse_sql_json(sql))['stmts']
                    native = {'state': 'compared', 'same_complete_tree':
                              [pg_clean(s['stmt']) for s in pg] == [s['base'] for s in tree['statements']]}
                except parser.ParseError:
                    native = {'state': 'direct_pg_rejected'}
        records.append(dict(target, result=result, formatting_check=formatting, native_pg_check=native))
        if len(records) % 100 == 0:
            print(json.dumps({'replayed': len(records)}), flush=True)
    sources = ['sql_apm/sql/pg_ast.py', 'sql_apm/sql/lexical.py',
               'sql_apm/diagnostics/mpp_broad_replay.py', 'sql_apm/diagnostics/mpp_expansion_probe.py',
               'sql_apm/diagnostics/mpp_adapter_probe.py', 'sql_apm/sql/mpp_parser.py',
               'sql_apm/diagnostics/parser_fidelity.py', 'sql_apm/diagnostics/statement_census.py']
    from importlib.metadata import version
    evidence = dict(purpose='Bounded overall parser replay, not product fingerprint acceptance',
                    python=platform.python_version(), pglast=version('pglast'),
                    source_sha256={p: sha_file(ROOT / p) for p in sources},
                    selection_source_sha256=cached['selection_source_sha256'],
                    evidence_sha256=sha_file(EVIDENCE), cache_sha256=sha_file(cache),
                    baseline_cache_sha256=cached['baseline_cache_sha256'], files=cached['files'],
                    limits=dict(prefix_bytes=PREFIX_BYTES, max_records_per_file=MAX_RECORDS,
                                max_selected_per_file=PER_FILE, max_sql_bytes=MAX_BYTES),
                    records=records, summary=dict(Counter(r['result']['state'] for r in records)),
                    reasons=dict(Counter(r['result'].get('reason', r['result']['state']) for r in records)),
                    seconds=round(time.monotonic() - started, 3))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'summary': evidence['summary'], 'reasons': evidence['reasons'],
                      'seconds': evidence['seconds']}))
    if any(r['result']['state'] not in ('prototype_parsed', 'unsupported') or
           r['formatting_check'] and not r['formatting_check']['same_complete_tree'] or
           r['native_pg_check'] and r['native_pg_check'].get('same_complete_tree') is False for r in records):
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path)
    ap.add_argument('--prepare-only', action='store_true')
    ap.add_argument('--cache', type=Path, default=ROOT / 'var/parser-probe/broad-input-cache.json')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    from importlib.metadata import version
    if platform.python_version() != '3.9.5' or version('pglast') != '7.18':
        ap.error('requires Python 3.9.5 and pglast 7.18')
    cache, output = args.cache.resolve(), args.output.resolve()
    if (ROOT / 'var').resolve() not in cache.parents:
        ap.error('cache must stay under ignored var/')
    if args.root:
        root = args.root.resolve()
        if any(p == root or root in p.parents for p in (cache, output)):
            ap.error('output and cache must be outside originals')
        prepare(root, cache)
    if not args.prepare_only:
        replay(cache, output)


if __name__ == '__main__':
    main()
