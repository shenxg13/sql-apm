"""Bounded structural fingerprint replay; never export production SQL or AST values."""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
from multiprocessing.connection import wait
from pathlib import Path
import platform
import sqlite3
import time

from pglast import parser

from sql_apm.diagnostics.mpp_full_audit import file_sha
from sql_apm.diagnostics.normalization_diff import bucket_context
from sql_apm.diagnostics.mpp_full_scan import ParserProcess, ROOT, MAX_BYTES, TIMEOUT, MEMORY_BYTES
from sql_apm.sql.mpp_parser import parse
from sql_apm.sql.normalization import Normalizer
from sql_apm.sql.structure import dumps

CACHES = ('mpp-input-cache.json', 'expanded-input-cache.json', 'broad-input-cache.json')
SOURCES = ('sql_apm/sql/normalization.py', 'sql_apm/sql/function_dictionary.py',
           'sql_apm/sql/type_policy.py', 'sql_apm/sql/approximate.py',
           'sql_apm/sql/mpp_parser.py', 'sql_apm/sql/pg_ast.py', 'sql_apm/sql/lexical.py',
           'sql_apm/sql/structure.py', 'sql_apm/diagnostics/normalization_replay.py',
           'sql_apm/diagnostics/mpp_full_scan.py', 'sql_apm/diagnostics/normalization_diff.py',
           'requirements.txt')
_ENGINE = None


def delta_audit(original, normalized):
    """Independent conservation check: only approved value, IN-list and global Hint gap changes."""
    changes, stack = Counter(), [(original, normalized, '', False, '')]
    while stack:
        left, right, path, eligible, parent = stack.pop()
        if isinstance(right, dict) and set(right) == {'SQLAPMInBucket'}:
            if (not eligible or not isinstance(left, list) or not left or not path.endswith('/A_Expr/rexpr/List/items')
                    or not all(isinstance(v, dict) and (set(v) == {'ParamRef'} or
                        set(v) == {'A_Const'} and set(v['A_Const']) & {'ival', 'fval', 'sval', 'bsval'})
                        for v in left)):
                raise ValueError('unexpected_in_bucket')
            size = len(left)
            expected = '1' if size == 1 else '2-10' if size <= 10 else '11-100' if size <= 100 else '>100'
            if right['SQLAPMInBucket'] != expected:
                raise ValueError('incorrect_in_bucket')
            changes[path + '/in_bucket'] += 1
        elif isinstance(right, dict) and set(right) == {'SQLAPMBusinessValue'}:
            if not isinstance(left, dict) or not (set(left) == {'ParamRef'} or
                    set(left) == {'A_Const'} and set(left['A_Const']) & {'ival', 'fval', 'sval', 'bsval'}):
                raise ValueError('unexpected_replacement')
            changes[path] += 1
        elif type(left) is not type(right):
            raise ValueError('unexpected_type_change')
        elif isinstance(left, dict):
            removed_gap = path == '/hints/*' and set(left) - set(right) == {'gap'}
            if set(left) != set(right) and not (removed_gap and set(right) < set(left)):
                raise ValueError('unexpected_field_change')
            if removed_gap:
                changes[path + '/gap_removed'] += 1
            stack.extend((left[key], value, path + '/' + key,
                          bucket_context(parent, key, eligible, v5=True), key) for key, value in right.items())
        elif isinstance(left, list):
            if len(left) != len(right):
                raise ValueError('unexpected_list_change')
            stack.extend((a, b, path + '/*', eligible, parent) for a, b in zip(left, right))
        elif left != right:
            raise ValueError('unexpected_value_change')
    return dict(changes)


def sanitized_worker(request):
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Normalizer()
    raw = request['sql'].encode('utf-8', 'surrogateescape')
    started = time.monotonic()
    result = _ENGINE.normalize(raw)
    timings = {'normalize': time.monotonic() - started}
    fingerprint = result['fingerprint']
    safe = dict(state=fingerprint['state'], reason=fingerprint['reason'],
                value=fingerprint['value'], diagnostics=result['diagnostics'],
                source_preserved=base64.b64decode(result['source']['bytes_base64']) == raw,
                repeat_stable=None)
    started = time.monotonic()
    safe['repeat_stable'] = dumps(_ENGINE.normalize(raw)) == dumps(result)
    timings['repeat'] = time.monotonic() - started
    if fingerprint['state'] == 'reliable':
        started = time.monotonic()
        original = parse(request['sql'])
        original.pop('version')
        safe['replacement_paths'] = delta_audit(original, result['normalized'])
        safe['statements'] = [next(iter(s['base'])) for s in original['statements']]
        safe['extensions'] = [e['kind'] for s in original['statements'] for e in s['extensions']]
        safe['hint_count'] = len(original['hints'])
        timings['conservation_audit'] = time.monotonic() - started
        started = time.monotonic()
        formatted = '/* normalization format */\n' + '\n '.join(
            request['sql'][token.start:token.end + 1] for token in parser.scan(request['sql']))
        changed = _ENGINE.normalize(formatted)
        safe['format_stable'] = changed['fingerprint'] == fingerprint
        safe['approximate_absent'] = result['approximate'] is None
        timings['format'] = time.monotonic() - started
    else:
        near = result['approximate']
        safe['approximate_state'] = near['state'] if near else None
        safe['structural_absent'] = result['normalized'] is None and fingerprint['value'] is None
    safe['phase_seconds'] = {k: round(v, 4) for k, v in timings.items()}
    safe['phase_budget_passed'] = all(v < TIMEOUT for v in timings.values())
    return safe


def select_inputs(source, cache_root, edge_count):
    selected = {}
    for cache in CACHES:
        for row in json.loads((cache_root / cache).read_text())['rows']:
            digest = row['target']['sql_sha256']
            raw = row['sql'].encode('utf-8', 'surrogateescape')
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('cache_input_changed')
            found = source.execute('SELECT id,sql,locator FROM inputs WHERE sha256=?', (digest,)).fetchone()
            if not found or found[1] != raw:
                raise ValueError('baseline_input_mismatch')
            uid, _, _ = found
            record = selected.setdefault(uid, dict(input_id=uid, sql_sha256=digest, raw=raw,
                                         locator=row['target'], selection=[]))
            record['selection'].append(cache)
    for order, label in (('ASC', 'first'), ('DESC', 'last')):
        for uid, digest, raw, locator in source.execute(
                'SELECT id,sha256,sql,locator FROM inputs ORDER BY id ' + order + ' LIMIT ?', (edge_count,)):
            record = selected.setdefault(uid, dict(input_id=uid, sql_sha256=digest, raw=raw,
                                         locator=json.loads(locator), selection=[]))
            record['selection'].append(label + '_' + str(edge_count))
    return [selected[uid] for uid in sorted(selected)]


def replay(rows):
    pending, output, index = {}, [], 0
    pool = [ParserProcess(task=sanitized_worker, timeout=4 * TIMEOUT) for _ in range(4)]
    started = last_update = time.monotonic()
    try:
        while index < len(rows) or pending:
            for process in pool:
                if process.pending is None and index < len(rows):
                    row = rows[index]
                    index += 1
                    raw = row.pop('raw')
                    if hashlib.sha256(raw).hexdigest() != row['sql_sha256'] or len(raw) > MAX_BYTES:
                        raise ValueError('input_integrity_mismatch')
                    pending[row['input_id']] = row
                    process.submit(row['input_id'], raw)
                if process.pending is not None:
                    result = process.receive()
                    if result is not None:
                        uid, safe = result
                        row = pending.pop(uid)
                        row['result'] = safe
                        output.append(row)
            active = [p.connection for p in pool if p.pending is not None]
            if active:
                wait(active, timeout=.05)
            if time.monotonic() - last_update > 20:
                print(json.dumps({'completed': len(output), 'total': len(rows)}), flush=True)
                last_update = time.monotonic()
    finally:
        for process in pool:
            process.close()
    return sorted(output, key=lambda r: r['input_id']), round(time.monotonic() - started, 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=ROOT / 'var/parser-probe/full-scan.sqlite')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--edge-count', type=int, default=512)
    args = ap.parse_args()
    if not 0 <= args.edge_count <= 1024 or args.output.exists():
        ap.error('bounded edge count 0..1024 and a new output path required')
    paths = [args.source] + [ROOT / 'var/parser-probe' / p for p in CACHES]
    before = {str(p.relative_to(ROOT)): file_sha(p) for p in paths}
    sources = {p: file_sha(ROOT / p) for p in SOURCES}
    with sqlite3.connect('file:' + str(args.source.resolve()) + '?mode=ro', uri=True) as source:
        rows = select_inputs(source, ROOT / 'var/parser-probe', args.edge_count)
    records, seconds = replay(rows)
    after = {str(p.relative_to(ROOT)): file_sha(p) for p in paths}
    if before != after or sources != {p: file_sha(ROOT / p) for p in SOURCES}:
        raise ValueError('evidence_or_source_changed')
    groups, counts, categories = defaultdict(list), Counter(), Counter()
    failures = []
    for row in records:
        r = row['result']
        counts[r['state']] += 1
        categories.update(r.get('categories', []))
        if r.get('value'):
            groups[r['value']].append(row['input_id'])
        if not (r.get('repeat_stable') and r.get('source_preserved') and r.get('phase_budget_passed')) or (
                r['state'] == 'reliable' and not (r.get('format_stable') and r.get('approximate_absent'))) or (
                r['state'] != 'reliable' and not (r['state'] == 'unsupported_syntax' and r.get('structural_absent'))):
            failures.append(row['input_id'])
    engine = Normalizer()
    output = dict(context=engine.context, rule_snapshot=engine.rule_snapshot(), python=platform.python_version(),
                  sources=sources, inputs=before, input_artifacts_unchanged=True,
                  selection=dict(historical_samples=914, edge_count_each=args.edge_count, exact_dedup=True),
                  limits=dict(workers=4, max_sql_bytes=MAX_BYTES, bundle_timeout_seconds=4 * TIMEOUT, per_phase_seconds=TIMEOUT, memory_bytes=MEMORY_BYTES),
                  records=records, counts=dict(counts), categories=dict(categories),
                  reasons=dict(Counter(r['result'].get('reason') or r['result']['state'] for r in records)),
                  cluster_counts=dict(Counter(r['locator']['cluster'] for r in records)),
                  reliable_groups=len(groups), merged_groups={k:v for k,v in groups.items() if len(v)>1},
                  failures=failures, seconds=seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, sort_keys=True, indent=2) + '\n')
    print(json.dumps({k:output[k] for k in ('counts','reasons','reliable_groups','failures','seconds')}))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
