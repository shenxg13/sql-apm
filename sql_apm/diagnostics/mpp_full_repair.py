"""Reparse an immutable full-scan corpus and compare complete structural digests.

The old SQLite input is read-only. New results live in a separate ignored index.
Only counts, fixed diagnostics and source locators are exported.
"""
import argparse
from collections import Counter
import json
from multiprocessing.connection import wait
from pathlib import Path
import sqlite3
import time

from sql_apm.diagnostics.mpp_full_scan import ROOT, EVIDENCE, MAX_BYTES, ParserProcess, context, get_meta, emit
from sql_apm.diagnostics.mpp_full_audit import file_sha


def outcome(old, new):
    if old['state'] == 'prototype_parsed':
        if new['state'] != 'prototype_parsed':
            return 'regressed_state'
        return ('same_structure' if old['comparison_digest'] == new.get('comparison_digest_at_version')
                else 'changed_structure')
    if new['state'] == 'prototype_parsed':
        return 'repaired'
    if new['state'] != old['state']:
        return 'changed_failure_state'
    return 'same_failure' if old.get('reason', old.get('exception')) == new.get('reason', new.get('exception')) else 'changed_failure_reason'


def replay(source, destination, workers):
    started, last_progress, completed = time.monotonic(), time.monotonic(), 0
    old_context = get_meta(source, 'context')
    pool = [ParserProcess(worker_options={'comparison_version': old_context['prototype_version']}) for _ in range(workers)]
    destination.execute('CREATE TABLE results (input_id INTEGER PRIMARY KEY, result TEXT NOT NULL, comparison TEXT NOT NULL)')
    counts, states, reasons, changes, known = Counter(), Counter(), Counter(), [], {}

    def record(uid, new):
        nonlocal completed
        digest, location, old = known.pop(uid)
        comparison = outcome(old, new)
        counts[comparison] += 1
        states[new['state']] += 1
        reasons[new.get('reason', new.get('exception', new['state']))] += 1
        destination.execute('INSERT INTO results VALUES (?,?,?)', (uid, json.dumps(new), comparison))
        if comparison not in ('same_structure', 'same_failure'):
            occurrences = source.execute('SELECT sum(count) FROM occurrences WHERE input_id=?', (uid,)).fetchone()[0]
            changes.append(dict(input_id=uid, sql_sha256=digest, locator=json.loads(location),
                                occurrences=occurrences, old=old, new=new, comparison=comparison))
        completed += 1

    try:
        for phase in ('failures', 'successes'):
            operator = 'NOT LIKE' if phase == 'failures' else 'LIKE'
            rows = iter(source.execute('SELECT id,sha256,sql,locator,result FROM inputs WHERE result ' +
                                       operator + ' ? ORDER BY id', ('{"state": "prototype_parsed"%',)))
            exhausted = False
            while True:
                for process in pool:
                    while process.pending is None and not exhausted:
                        row = next(rows, None)
                        if row is None:
                            exhausted = True
                            break
                        uid, digest, raw, location, previous = row
                        known[uid] = (digest, location, json.loads(previous))
                        if len(raw) > MAX_BYTES:
                            record(uid, {'state': 'probe_size_limit'})
                        else:
                            process.submit(uid, raw)
                    if process.pending is not None:
                        result = process.receive()
                        if result is not None:
                            record(*result)
                active = [p for p in pool if p.pending is not None]
                if exhausted and not active:
                    break
                if time.monotonic() - last_progress > 20:
                    destination.commit()
                    emit(phase=phase, completed=completed, comparisons=dict(counts),
                         seconds=round(time.monotonic() - started, 1))
                    last_progress = time.monotonic()
                if active:
                    wait([p.connection for p in active], timeout=.05)
            destination.commit()
            emit(phase=phase + '_complete', completed=completed, comparisons=dict(counts))
    finally:
        for process in pool:
            process.close()
        destination.commit()
    expected = source.execute('SELECT count(*) FROM inputs').fetchone()[0]
    if completed != expected or known:
        raise ValueError('repair replay incomplete')
    return dict(completed=completed, comparisons=counts, unique_states=states, reasons=reasons,
                changes=changes, seconds=round(time.monotonic() - started, 3), workers=workers)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, default=ROOT / 'var/parser-probe/full-scan.sqlite')
    ap.add_argument('--database', type=Path, default=ROOT / 'var/parser-probe/full-repair.sqlite')
    ap.add_argument('--baseline-audit', type=Path, default=ROOT / 'docs/reports/data/mpp-full-audit-2026-09-27.json')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()
    source_path, target = args.input.resolve(), args.database.resolve()
    ignored = (ROOT / 'var').resolve()
    if ignored not in source_path.parents or ignored not in target.parents or target.exists():
        ap.error('input must be an ignored index; output index must be new and under var/')
    if not 1 <= args.workers <= 8 or args.output.resolve() in (source_path, target):
        ap.error('invalid workers or output path')
    baseline = json.loads(args.baseline_audit.read_text())
    before = file_sha(source_path)
    if before != baseline['database_sha256']:
        raise ValueError('baseline index hash mismatch')
    fixed_context = context(EVIDENCE)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(str(target)) as dest:
        if not get_meta(source, 'collection_complete') or source.execute('SELECT count(*) FROM inputs WHERE result IS NULL').fetchone()[0]:
            raise ValueError('baseline incomplete')
        result = replay(source, dest, args.workers)
        result['previous_context'] = get_meta(source, 'context')
    if file_sha(source_path) != before or context(EVIDENCE) != fixed_context:
        raise ValueError('baseline or parser context changed during replay')
    result.update(context=fixed_context, baseline_sha256=before,
                  source_sha256=file_sha(Path(__file__)),
                  purpose='Full versioned parser repair replay; no normalization or SQL execution')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    emit(completed=result['completed'], comparisons=dict(result['comparisons']), unique_states=dict(result['unique_states']))
    if result['comparisons'].get('regressed_state') or result['comparisons'].get('changed_structure'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
