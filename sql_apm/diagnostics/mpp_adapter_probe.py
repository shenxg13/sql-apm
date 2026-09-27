#!/usr/bin/env python3
"""Bounded experiment for the MPP adapter; production SQL stays in ignored cache."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from sql_apm.sql.lexical import diagnose
from sql_apm.sql.structure import dumps as structure_dumps, loads as structure_loads
from sql_apm.diagnostics.statement_census import DURATION, INLINE, DigestReader, origin
from sql_apm.sql.mpp_parser import parse, Unsupported, VERSION
from sql_apm.diagnostics.parser_fidelity import digest

ROOT = Path(__file__).resolve().parents[2]

EVIDENCE = ROOT / 'docs/reports/data/statement-census-2026-09-26.json'
MAX_BYTES = 512 * 1024
TIMEOUT = 5


def sha_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root, cache):
    """Only immutable selected records; stop at the final target in each file."""
    evidence = json.loads(EVIDENCE.read_text())
    targets = defaultdict(list)
    manifests = {(c, f['file']): f for c, d in evidence['clusters'].items() for f in d['files']}
    for t in evidence['replay_targets']:
        targets[(t['cluster'], t['file'])].append(t)
    rows, files = [], []
    csv.field_size_limit(128 * 1024 * 1024)
    for key, selected in sorted(targets.items()):
        path = (root / key[0] / key[1]).resolve()
        if root not in path.parents:
            raise ValueError('source outside root')
        before = path.stat()
        if before.st_size != manifests[key]['bytes']:
            raise ValueError('source size mismatch')
        by_record = defaultdict(list)
        for t in selected:
            by_record[t['record']].append(t)
        last, previous_line = max(by_record), 0
        with path.open('rb') as raw:
            hashing = DigestReader(raw)
            with io.TextIOWrapper(io.BufferedReader(hashing), encoding='utf-8',
                                  errors='surrogateescape', newline='') as stream:
                reader = csv.reader(stream, strict=True)
                for number, row in enumerate(reader, 1):
                    start, previous_line = previous_line + 1, reader.line_num
                    if number in by_record:
                        if len(row) != 30:
                            raise ValueError('source column mismatch')
                        for t in by_record.pop(number):
                            if t['field'] == 'inline_distinct':
                                message = DURATION.sub('', row[18], count=1)
                                match = INLINE.match(message)
                                if not match:
                                    raise ValueError('source inline mismatch')
                                sql = message[match.end():]
                            else:
                                sql = row[{'sql': 24, 'internal': 21}[t['field']]]
                            sha = hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest()
                            seq, issues = diagnose(sql)
                            if (sha != t['sql_sha256'] or list(seq) != t['categories'] or list(issues) != t['issues']
                                    or start != t['line_start'] or reader.line_num != t['line_end']
                                    or origin(row) != t['origin']):
                                raise ValueError('source evidence mismatch')
                            rows.append({'target': t, 'sql': sql})
                    if number == last:
                        break
                prefix_sha, prefix_bytes = hashing.sha.hexdigest(), hashing.used
        after = path.stat()
        if by_record or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('source changed or missing record')
        files.append({'cluster': key[0], 'file': key[1], 'stop_record': last,
                      'file_bytes': before.st_size, 'historical_file_sha256': manifests[key]['sha256'],
                      'full_file_hash_rechecked': False, 'read_prefix_bytes': prefix_bytes,
                      'read_prefix_sha256': prefix_sha, 'stat_unchanged': True})
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({'evidence_sha256': sha_file(EVIDENCE), 'files': files,
                                'rows': rows}, ensure_ascii=True) + '\n')
    print(json.dumps({'prepared_records': len(rows), 'read_prefix_bytes': sum(f['read_prefix_bytes'] for f in files)}), flush=True)


def worker(request):
    try:
        tree = parse(request['sql'])
        result = {'state': 'prototype_parsed', 'comparison_digest': digest(tree),
                  'roots': [next(iter(s['base'])) for s in tree['statements']],
                  'extensions': dict(Counter(e['kind'] for s in tree['statements'] for e in s['extensions'])),
                  'hint_count': len(tree['hints'])}
        if request.get('comparison_version'):
            current_version = tree['version']
            tree['version'] = request['comparison_version']
            result['comparison_digest_at_version'] = digest(tree)
            tree['version'] = current_version
        if request.get('synthetic'):
            result['tree'] = tree
        return result
    except Unsupported as exc:
        return {'state': 'unsupported', 'reason': str(exc)}
    except Exception as exc:
        return {'state': 'probe_exception', 'exception': type(exc).__name__}


def probe(sql, synthetic=False):
    if len(sql.encode('utf-8', 'surrogateescape')) > MAX_BYTES:
        return {'state': 'probe_size_limit'}
    try:
        run = subprocess.run([sys.executable, '-m', 'sql_apm.diagnostics.mpp_adapter_probe', '--worker'],
                             input=json.dumps({'sql': sql, 'synthetic': synthetic}), text=True,
                             capture_output=True, cwd=ROOT, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return {'state': 'probe_timeout'}
    if run.returncode:
        return {'state': 'worker_failed', 'returncode': run.returncode}
    return structure_loads(run.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--root', type=Path, help='read originals once and prepare ignored cache')
    ap.add_argument('--cache', type=Path, default=ROOT / 'var/parser-probe/mpp-input-cache.json')
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    if args.worker:
        print(structure_dumps(worker(json.load(sys.stdin))))
        return
    from importlib.metadata import version
    if platform.python_version() != '3.9.5' or version('pglast') != '7.18':
        ap.error('requires Python 3.9.5 and pglast 7.18')
    cache = args.cache.resolve()
    if (ROOT / 'var').resolve() not in cache.parents:
        ap.error('raw cache must stay under repository ignored var/')
    output = args.output.resolve() if args.output else None
    if output is None or (args.root and (output == args.root.resolve() or args.root.resolve() in output.parents)):
        ap.error('--output required and must be outside input root')
    if args.root:
        if cache == args.root.resolve() or args.root.resolve() in cache.parents:
            ap.error('cache must be outside input root')
        prepare(args.root.resolve(), cache)
    cached = json.loads(cache.read_text())
    evidence = json.loads(EVIDENCE.read_text())
    expected = sorted(evidence['replay_targets'], key=lambda t: (t['cluster'], t['file'], t['record'], t['field']))
    actual = sorted([r['target'] for r in cached['rows']], key=lambda t: (t['cluster'], t['file'], t['record'], t['field']))
    if cached['evidence_sha256'] != sha_file(EVIDENCE) or actual != expected:
        raise ValueError('cache manifest mismatch')
    started = time.monotonic()
    rows = []
    for row in cached['rows']:
        sha = hashlib.sha256(row['sql'].encode('utf-8', 'surrogateescape')).hexdigest()
        if sha != row['target']['sql_sha256']:
            raise ValueError('cached SQL hash mismatch')
        outcome = probe(row['sql'])
        formatting = None
        if outcome['state'] == 'prototype_parsed':
            from pglast import parser as pg_parser
            # Raw tokens (including literal and Hint contents) remain byte-for-
            # byte text-equivalent; only outside gaps gain ordinary comments.
            changed = '\n/* spacing probe */ '.join(
                row['sql'][t.start:t.end + 1] for t in pg_parser.scan(row['sql']))
            repeated = probe(changed)
            formatting = {'state': repeated['state'],
                          'same': repeated.get('comparison_digest') == outcome['comparison_digest']}
        rows.append(dict(row['target'], result=outcome, formatting_check=formatting))
    old_cases = ROOT / 'tests/parser_probe/fixtures/parser-fidelity-cases.json'
    fixture = json.loads(old_cases.read_text())
    synthetics = [{'id': c['id'], 'result': probe(c['sql'])} for c in fixture['cases']]
    by_id = {r['id']: r['result'] for r in synthetics}
    relations = []
    for r in fixture['relations']:
        left, right = by_id[r['left']], by_id[r['right']]
        same = (left.get('comparison_digest') == right.get('comparison_digest'))
        relations.append(dict(r, passed=left['state'] == right['state'] == 'prototype_parsed' and same == r['same']))
    fresh_process = []
    for case_id in ('distributed', 'external_options', 'web_external', 'hint_unicode'):
        case = next(c for c in fixture['cases'] if c['id'] == case_id)
        repeated = probe(case['sql'])
        fresh_process.append({'case': case_id, 'same': repeated == by_id[case_id]})
    source_paths = [Path(__file__), ROOT / 'sql_apm/sql/pg_ast.py', ROOT / 'sql_apm/sql/structure.py',
                    ROOT / 'sql_apm/sql/lexical.py', ROOT / 'sql_apm/sql/mpp_parser.py',
                    ROOT / 'sql_apm/diagnostics/parser_fidelity.py',
                    ROOT / 'sql_apm/diagnostics/statement_census.py',
                    ROOT / 'tests/parser_probe/test_mpp_adapter.py']
    result = {'purpose': 'MPP adaptation proof of concept, no normalization or reliable fingerprints',
              'python': platform.python_version(), 'pglast': version('pglast'), 'prototype_version': VERSION,
              'source_sha256': {str(p.resolve().relative_to(ROOT)): sha_file(p) for p in source_paths},
              'cache_sha256': sha_file(cache), 'evidence_sha256': sha_file(EVIDENCE),
              'fixture_sha256': sha_file(old_cases), 'files': cached['files'], 'records': rows,
              'summary': dict(Counter(r['result']['state'] for r in rows)),
              'reasons': dict(Counter(r['result'].get('reason', r['result']['state']) for r in rows)),
              'synthetic_cases': synthetics, 'relations': relations, 'fresh_process': fresh_process,
              'limits': {'max_sql_bytes': MAX_BYTES, 'timeout_seconds': TIMEOUT},
              'replay_and_synthetic_seconds': round(time.monotonic() - started, 3)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'summary': result['summary'], 'reasons': result['reasons'],
                      'synthetics': dict(Counter(x['result']['state'] for x in synthetics)),
                      'relations_passed': sum(x['passed'] for x in relations),
                      'formatting_passed': sum(bool(x['formatting_check'] and x['formatting_check']['same']) for x in rows),
                      'seconds': result['replay_and_synthetic_seconds']}))


if __name__ == '__main__':
    main()
