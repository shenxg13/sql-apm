"""Replay every original parser failure; export counts and fixed diagnostics only."""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
from multiprocessing.connection import wait
from pathlib import Path
import sqlite3
import time

from sql_apm.sql.approximate import analyze, VERSION, RULES_DIGEST
from sql_apm.diagnostics.mpp_adapter_probe import worker as parser_worker
from sql_apm.diagnostics.mpp_full_audit import file_sha
from sql_apm.diagnostics.mpp_full_scan import ROOT, ParserProcess, get_meta, emit, MEMORY_BYTES, TIMEOUT, MAX_BYTES

SOURCES = ('sql_apm/sql/approximate.py', 'sql_apm/sql/mpp_parser.py',
           'sql_apm/sql/lexical.py', 'sql_apm/sql/pg_ast.py', 'sql_apm/sql/structure.py',
           'sql_apm/diagnostics/mpp_approximate_replay.py',
           'sql_apm/diagnostics/mpp_full_scan.py', 'sql_apm/diagnostics/mpp_adapter_probe.py')


def sanitized_worker(request):
    raw = request['sql'].encode('utf-8', 'surrogateescape')
    result = analyze(raw)
    if result['parser_state'] == 'parsed':
        parsed = parser_worker({'sql': request['sql']})
        return dict(state=parsed['state'], comparison_digest=parsed.get('comparison_digest'),
                    parser_state='parsed', parser_reason=None, approximate_absent=True)
    approx = result['approximate']
    safe = dict(state='approximate_' + approx['state'] if approx else 'analysis_failed',
                parser_state=result['parser_state'], parser_reason=result['parser_reason'],
                structure_fingerprint_absent=result['structure_fingerprint'] is None)
    if approx:
        safe.update(approximate_reason=approx['reason'], fingerprint=approx['value'],
                    observation_only=approx['observation_only'])
        if 'source' in approx:
            source = approx['source']
            safe.update(source_preserved=base64.b64decode(source['bytes_base64']) == raw,
                        source_sha256=source['sha256'], replacements=approx['replacements'],
                        approximate_diagnostics=approx['diagnostics'])
    return safe


def comparison(previous, actual):
    if previous['state'] == 'prototype_parsed':
        return ('recovered_structure_unchanged' if actual['state'] == 'prototype_parsed' and
                actual.get('comparison_digest') == previous['comparison_digest'] and
                actual.get('approximate_absent') else 'regression')
    if (actual.get('parser_state') != 'unsupported' or
            actual.get('parser_reason') != previous.get('reason') or
            not actual.get('structure_fingerprint_absent')):
        return 'regression'
    if actual['state'] not in ('approximate_available', 'approximate_unavailable'):
        return 'approximation_failed'
    if not actual.get('observation_only') or not actual.get('source_preserved'):
        return 'regression'
    if (actual['state'] == 'approximate_available') != bool(actual.get('fingerprint')):
        return 'regression'
    return 'original_refusal_preserved'


def replay(source, previous_db, destination, workers=4):
    destination.execute('CREATE TABLE results (input_id INTEGER PRIMARY KEY, result TEXT NOT NULL, comparison TEXT NOT NULL)')
    pool = [ParserProcess(task=sanitized_worker) for _ in range(workers)]
    rows = iter(source.execute('SELECT id,sha256,sql,locator FROM inputs WHERE result NOT LIKE ? ORDER BY id',
                               ('{"state": "prototype_parsed"%',)))
    pending, counts, states, weighted, reasons, values = {}, Counter(), Counter(), Counter(), {}, Counter()
    changed, examples, completed, exhausted = [], defaultdict(list), 0, False
    started = last_update = time.monotonic()
    identities = hashlib.sha256()

    def record(uid, result):
        nonlocal completed
        digest, locator, old, occurrences = pending.pop(uid)
        check = comparison(old, result)
        state = result['state']
        counts[check] += 1
        states[state] += 1
        weighted[state] += occurrences
        key = old.get('reason', old['state'])
        reason = reasons.setdefault(key, dict(unique_inputs=0, occurrences=0, results=Counter(),
                                               replacement_inputs=0, total_replacements=0))
        reason['unique_inputs'] += 1
        reason['occurrences'] += occurrences
        reason['results'][state] += 1
        reason['replacement_inputs'] += bool(result.get('replacements'))
        reason['total_replacements'] += result.get('replacements', 0)
        if result.get('fingerprint'):
            values[result['fingerprint']] += 1
        entry = dict(input_id=uid, sql_sha256=digest, locator=locator,
                     previous_reason=key, result=result, comparison=check)
        if len(examples[key]) < 2:
            examples[key].append(entry)
        if check in ('regression', 'approximation_failed'):
            changed.append(entry)
        destination.execute('INSERT INTO results VALUES (?,?,?)', (uid, json.dumps(result, sort_keys=True), check))
        completed += 1

    try:
        while True:
            for process in pool:
                if process.pending is None and not exhausted:
                    row = next(rows, None)
                    if row is None:
                        exhausted = True
                    else:
                        uid, digest, raw, location = row
                        if hashlib.sha256(raw).hexdigest() != digest or len(raw) > MAX_BYTES:
                            raise ValueError('source input integrity mismatch')
                        old_row = previous_db.execute('SELECT result FROM results WHERE input_id=?', (uid,)).fetchone()
                        if old_row is None:
                            raise ValueError('previous parser result missing')
                        old = json.loads(old_row[0])
                        occurrences = source.execute('SELECT sum(count) FROM occurrences WHERE input_id=?', (uid,)).fetchone()[0]
                        pending[uid] = (digest, json.loads(location), old, occurrences)
                        identities.update(('%s:%s\n' % (uid, digest)).encode('ascii'))
                        process.submit(uid, raw)
                if process.pending is not None:
                    result = process.receive()
                    if result is not None:
                        record(*result)
            active = [p for p in pool if p.pending is not None]
            if exhausted and not active:
                break
            if time.monotonic() - last_update > 20:
                destination.commit()
                emit(completed=completed, states=dict(states), seconds=round(time.monotonic() - started, 1))
                last_update = time.monotonic()
            if active:
                wait([p.connection for p in active], timeout=.05)
    finally:
        for process in pool:
            process.close()
        destination.commit()
    if pending:
        raise ValueError('replay incomplete')
    return dict(completed=completed, comparisons=counts, states=states, occurrences=weighted,
                reasons=reasons, approximate_groups=len(values), groups_with_multiple_inputs=sum(n > 1 for n in values.values()),
                selected_input_ids_and_hashes_sha256=identities.hexdigest(), changes=changed,
                examples=dict(examples), seconds=round(time.monotonic() - started, 3), workers=workers)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, default=ROOT / 'var/parser-probe/full-scan.sqlite')
    ap.add_argument('--previous', type=Path, default=ROOT / 'var/parser-probe/full-repair.sqlite')
    ap.add_argument('--database', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args()
    paths = [p.resolve() for p in (args.input, args.previous, args.database)]
    if any((ROOT / 'var').resolve() not in p.parents for p in paths) or len(set(paths)) != 3:
        ap.error('distinct databases must stay under ignored var/')
    if paths[2].exists() or args.output.exists() or args.output.resolve() in paths or not 1 <= args.workers <= 8:
        ap.error('outputs must be new; workers must be between 1 and 8')
    evidence = json.loads((ROOT / 'docs/reports/data/mpp-full-repair-2026-09-27.json').read_text())
    before = [file_sha(p) for p in paths[:2]]
    if before[0] != evidence['baseline_sha256']:
        raise ValueError('original corpus hash mismatch')
    context = {name: file_sha(ROOT / name) for name in SOURCES}
    for name in SOURCES[1:5]:
        if context[name] != evidence['context']['sources'][name]:
            raise ValueError('parser core changed since version5 replay')
    with sqlite3.connect(paths[0].as_uri() + '?mode=ro', uri=True) as source, \
            sqlite3.connect(paths[1].as_uri() + '?mode=ro', uri=True) as previous, \
            sqlite3.connect(str(paths[2])) as destination:
        if not get_meta(source, 'collection_complete'):
            raise ValueError('corpus incomplete')
        if previous.execute('SELECT count(*) FROM results').fetchone()[0] != evidence['completed']:
            raise ValueError('previous replay incomplete')
        result = replay(source, previous, destination, args.workers)
    if result['completed'] != evidence['comparisons']['same_failure'] + evidence['comparisons']['repaired']:
        raise ValueError('original failure coverage mismatch')
    if before != [file_sha(p) for p in paths[:2]] or context != {name: file_sha(ROOT / name) for name in SOURCES}:
        raise ValueError('input or code changed during replay')
    result.update(algorithm_version=VERSION, rules_digest=RULES_DIGEST, sources=context,
                  baseline_sha256=before[0], previous_results_sha256=before[1],
                  result_database_sha256=file_sha(paths[2]), source_databases_unchanged=True,
                  limits=dict(input_bytes=MAX_BYTES, timeout_seconds=TIMEOUT, worker_address_space_bytes=MEMORY_BYTES),
                  purpose='Observation-only approximate replay of all original failures; no SQL execution or baseline statistics')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    emit(completed=result['completed'], comparisons=dict(result['comparisons']), states=dict(result['states']),
         approximate_groups=result['approximate_groups'], seconds=result['seconds'])
    if result['changes']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
