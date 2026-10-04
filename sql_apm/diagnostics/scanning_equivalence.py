"""Explicit full-index old/new scan and normalization audit; exports no SQL.

Each completed bounded chunk is saved atomically. Rerunning the exact command
resumes only when source and implementation hashes match the original run.
No product watchdog is used: a slow reference must finish, not count as equal
because both workers timed out. Ordinary product isolation is tested separately.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
import platform
import sqlite3
import sys
import time

from pglast import parser

from sql_apm.sql import mpp_parser, normalization, scanning
from sql_apm.sql.structure import dumps

ROOT = Path(__file__).resolve().parents[2]
_STATE = None


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')
    temporary.replace(path)


def initialize(index, reference):
    global _STATE
    spec = importlib.util.spec_from_file_location('_scan_reference',
                                                str(Path(reference) / 'sql_apm/sql/mpp_parser.py'))
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    engine = normalization.Normalizer()
    _STATE = (readonly(index), old, engine)


def scan_result(function, sql):
    try:
        return ('tokens', function(sql))
    except (parser.ParseError, UnicodeError) as error:
        # Compare original error details in memory only (they can contain SQL).
        return ('error', type(error).__name__, error.args)


def equal_results(before, after):
    # Native dict/list equality recurses and fails on valid deep SQL trees.
    # Compare complete canonical bytes with the project's stack-based encoder.
    return dumps(before) == dumps(after)


def compare_chunk(bounds):
    db, old, engine = _STATE
    counters, fallbacks, states = Counter(), Counter(), Counter()
    differences = []
    started = time.monotonic()
    raw_scan = parser.scan
    begin, end = bounds
    for uid, expected_sha, raw in db.execute(
            'SELECT id,sha256,sql FROM inputs WHERE id>=? AND id<? ORDER BY id', bounds):
        counters['inputs'] += 1
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError('source_row_checksum_mismatch')
        sql = raw.decode('utf-8', 'surrogateescape')
        counters['ascii' if sql.isascii() else 'non_ascii'] += 1
        reference = scan_result(raw_scan, sql)
        native_original_calls = [0]

        def counted(text):
            if text is sql:
                native_original_calls[0] += 1
            return raw_scan(text)

        try:
            parser.scan = counted
            optimized = scan_result(scanning.scan, sql)
        finally:
            parser.scan = raw_scan
        if not sql.isascii() and native_original_calls[0]:
            fallbacks[scanning.fallback_reason(sql) or 'scanner_error'] += 1
        if reference != optimized:
            counters['scan_differences'] += 1
            differences.append(dict(id=uid, kind='scan', sha256=expected_sha))
        else:
            counters['scan_equal'] += 1
        counters['scan_' + reference[0]] += 1
        try:
            normalization.mpp_parser = old
            before = engine.normalize(raw)
        finally:
            normalization.mpp_parser = mpp_parser
        after = engine.normalize(raw)
        # Stronger than fingerprints alone: includes context, complete structure,
        # reasons, approximate result and diagnostics as well as source metadata.
        if not equal_results(before, after):
            counters['normalization_differences'] += 1
            differences.append(dict(id=uid, kind='normalization', sha256=expected_sha))
        else:
            counters['normalization_equal'] += 1
        states[after['fingerprint']['state']] += 1
    return dict(begin=begin, end=end, counts=dict(counters), fallbacks=dict(fallbacks),
                states=dict(states), differences=differences, seconds=time.monotonic() - started)


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    product = sorted((ROOT / 'sql_apm/sql').glob('*.py'))
    # The frozen adapter is the only changed normalization dependency. Verify
    # that every reused source matches the baseline, instead of assuming it.
    for path in product:
        if path.name not in ('mpp_parser.py', 'scanning.py'):
            if path.read_bytes() != (args.reference / path.relative_to(ROOT)).read_bytes():
                raise ValueError('unfrozen_normalization_dependency')
    with readonly(args.index) as db:
        total, first, last = db.execute('SELECT count(*),min(id),max(id) FROM inputs').fetchone()
        complete = json.loads(db.execute("SELECT value FROM meta WHERE key='collection_complete'").fetchone()[0])
        if not complete or total != 1497418 or first != 1 or last != total:
            raise ValueError('unexpected_source_index')
    metadata = dict(format='scanning-equivalence/1', source_sha256=sha256(args.index), inputs=total,
                    reference_sha256=sha256(args.reference / 'sql_apm/sql/mpp_parser.py'),
                    code_sha256={str(p.relative_to(ROOT)): sha256(p) for p in product + [Path(__file__)]},
                    chunk_size=args.chunk_size, python=platform.python_version(), platform=platform.platform(),
                    context=normalization.Normalizer().context, mode='full results; no reference timeout')
    manifest = args.output / 'manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text()) != metadata:
            raise ValueError('resume_provenance_mismatch')
    else:
        save(manifest, metadata)
    chunks = [(n, min(n + args.chunk_size, total + 1)) for n in range(1, total + 1, args.chunk_size)]
    pending = [bounds for bounds in chunks if not (args.output / ('chunk-%07d.json' % bounds[0])).exists()]
    started = time.monotonic()
    finished = len(chunks) - len(pending)
    print(json.dumps(dict(phase='start', inputs=total, pending_chunks=len(pending), workers=args.workers)), flush=True)
    with multiprocessing.get_context('spawn').Pool(args.workers, initialize, (args.index, args.reference),
                                                   maxtasksperchild=20) as pool:
        for result in pool.imap_unordered(compare_chunk, pending, chunksize=1):
            save(args.output / ('chunk-%07d.json' % result['begin']), result)
            finished += 1
            if finished % 20 == 0 or result['differences']:
                print(json.dumps(dict(phase='progress', finished_chunks=finished, total_chunks=len(chunks),
                                      differences=len(result['differences']), seconds=round(time.monotonic() - started, 3))), flush=True)
    totals, fallbacks, states, differences = Counter(), Counter(), Counter(), []
    worker_seconds = 0
    for begin, end in chunks:
        result = json.loads((args.output / ('chunk-%07d.json' % begin)).read_text())
        if (result['begin'], result['end']) != (begin, end):
            raise ValueError('chunk_bounds_mismatch')
        totals.update(result['counts']); fallbacks.update(result['fallbacks']); states.update(result['states'])
        differences.extend(result['differences']); worker_seconds += result['seconds']
    passed = totals['inputs'] == total and not differences
    report = dict(provenance=metadata, counts=dict(totals), fallbacks=dict(fallbacks), states=dict(states),
                  differences=differences, passed=passed, summed_worker_seconds=worker_seconds,
                  latest_session_seconds=time.monotonic() - started)
    save(args.output / 'report.json', report)
    print(json.dumps(dict(phase='complete', counts=dict(totals), fallbacks=dict(fallbacks), passed=passed)), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--index', required=True, type=Path)
    cli.add_argument('--reference', required=True, type=Path)
    cli.add_argument('--output', required=True, type=Path)
    cli.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    cli.add_argument('--chunk-size', type=int, default=500)
    args = cli.parse_args()
    if args.chunk_size < 1:
        cli.error('positive chunk size required')
    try:
        raise SystemExit(run(args))
    except Exception as error:
        print(json.dumps(dict(phase='failed', reason='equivalence_audit_failed',
                              exception_class=type(error).__name__)), flush=True)
        raise SystemExit(1) from None
