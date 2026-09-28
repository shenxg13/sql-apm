"""Capture private-index fingerprints and compare partitions without exporting SQL.

Run this same module in each checkout. Snapshots contain only IDs, digests,
fixed status/reason codes and counts. An optional v3-to-v4 projection is an
independent, diagnostic-only oracle; it never changes the product result.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
from itertools import zip_longest
import json
from multiprocessing.connection import wait
from pathlib import Path
import platform
import sqlite3
import time

from sql_apm.diagnostics.mpp_full_audit import file_sha
from sql_apm.diagnostics.mpp_full_scan import ParserProcess, ROOT, MAX_BYTES, TIMEOUT, MEMORY_BYTES
from sql_apm.sql.normalization import Normalizer
from sql_apm.sql.structure import dumps, loads

FORMAT = 'normalization-diff/1'
SOURCES = ('sql_apm/sql/normalization.py', 'sql_apm/sql/mpp_parser.py',
           'sql_apm/sql/function_dictionary.py', 'sql_apm/sql/type_policy.py',
           'sql_apm/sql/approximate.py', 'sql_apm/sql/pg_ast.py', 'sql_apm/sql/lexical.py',
           'sql_apm/sql/structure.py', 'sql_apm/diagnostics/mpp_full_scan.py',
           'sql_apm/diagnostics/normalization_diff.py', 'requirements.txt')
_ENGINE = None


class EvidenceError(ValueError):
    """Only fixed, public diagnostic codes may be passed to this exception."""


def digest(tree):
    return hashlib.sha256(dumps(tree).encode('ascii')).hexdigest()


def project_v4(tree):
    """Audit frozen v3 output, without calling the v4 walker or bucket helper.

    A bare business marker in an IN right-hand list is possible only in the
    old walker's business context. A cast/function/expression is never a bare
    marker. This leaves every other field, ordering and Hint anchor unchanged.
    """
    tree = loads(dumps(tree))
    counts = Counter()
    for hint in tree['hints']:
        if 'gap' in hint:
            del hint['gap']
            counts['global_gaps_removed'] += 1
    stack = [tree['statements']]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            expr = node.get('A_Expr', {})
            container = expr.get('rexpr', {}).get('List', {})
            values = container.get('items')
            if (expr.get('kind') == 'AEXPR_IN' and isinstance(values, list) and values
                    and all(v == {'SQLAPMBusinessValue': {}} for v in values)):
                length = len(values)
                label = next(label for maximum, label in ((1, '1'), (10, '2-10'),
                             (100, '11-100'), (float('inf'), '>100')) if length <= maximum)
                container['items'] = {'SQLAPMInBucket': label}
                counts['in_lists_bucketed'] += 1
            stack.extend(node.values())
    return tree, dict(counts)


def capture_worker(request):
    global _ENGINE
    try:
        if _ENGINE is None:
            _ENGINE = Normalizer()
        result = _ENGINE.normalize(request['sql'].encode('utf-8', 'surrogateescape'))
        fp = result['fingerprint']
        safe = dict(state=fp['state'], reason=fp['reason'], fingerprint=fp['value'],
                    structure_sha256=None, v4_projection_sha256=None,
                    in_lists=0, hint_count=0)
        if fp['state'] == 'reliable':
            tree = result['normalized']
            safe['structure_sha256'] = digest(tree)
            safe['hint_count'] = len(tree['hints'])
            if request.get('project_v4'):
                projected, changes = project_v4(tree)
                safe['v4_projection_sha256'] = digest(projected)
                safe['in_lists'] = changes.get('in_lists_bucketed', 0)
            else:
                safe['in_lists'] = result['diagnostics'].get('in_lists_bucketed', 0)
        return safe
    except Exception:
        # Never send exception text (which can contain SQL) through the pipe.
        return dict(state='capture_failed', reason='capture_failed')


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def meta(db):
    return {key: json.loads(value) for key, value in db.execute('SELECT key,value FROM meta')}


def put_meta(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value, sort_keys=True)))


def code_hashes():
    return {name: file_sha(ROOT / name) for name in SOURCES}


def selected_rows(source, markers=(), ids=None, ignore_case=False):
    """Byte matching is OR, not SQL syntax recognition; strings may also match."""
    markers = tuple(m.upper() if ignore_case else m for m in markers)
    found = set()
    for uid, sha, raw in source.execute('SELECT id,sha256,sql FROM inputs ORDER BY id'):
        if ids is not None and uid not in ids:
            continue
        if markers and not any(m in (raw.upper() if ignore_case else raw) for m in markers):
            continue
        if hashlib.sha256(raw).hexdigest() != sha:
            raise EvidenceError('input_digest_mismatch')
        found.add(uid)
        count = source.execute('SELECT coalesce(sum(count),0) FROM occurrences WHERE input_id=?',
                               (uid,)).fetchone()[0]
        yield uid, sha, raw, count
    if ids is not None and found != ids:
        raise EvidenceError('selected_ids_missing')


def capture(source_path, output, workers=4, markers=(), ids=None, ignore_case=False):
    if not 1 <= workers <= 8 or (markers and ids is not None):
        raise EvidenceError('invalid_selection_or_workers')
    if source_path.resolve() == output.resolve():
        raise EvidenceError('output_conflicts_with_source')
    engine = Normalizer()
    context = engine.context
    projection = (context['algorithm_version'] == 'sql-normalization/3'
                  and context['parser_version'] == 'mpp-adapter-probe/8')
    before, sources = file_sha(source_path), code_hashes()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation; interruption leaves an explicitly incomplete snapshot.
    with output.open('xb'):
        pass
    started = last_progress = time.monotonic()
    done, states, pending = 0, Counter(), {}
    pool = [ParserProcess(task=capture_worker, worker_options={'project_v4': projection})
            for _ in range(workers)]
    with readonly(source_path) as source, sqlite3.connect(str(output)) as target:
        target.executescript('''
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE records (input_id INTEGER PRIMARY KEY, sql_sha256 TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT, fingerprint TEXT, structure_sha256 TEXT,
                v4_projection_sha256 TEXT, in_lists INTEGER NOT NULL, hint_count INTEGER NOT NULL,
                occurrences INTEGER NOT NULL);
        ''')
        for key, value in dict(format=FORMAT, complete=False, context=context,
                rule_snapshot=engine.rule_snapshot(), sources=sources, source_sha256=before,
                python=platform.python_version(), project_v4=projection,
                selection=dict(kind='ids' if ids is not None else 'markers' if markers else 'all',
                    marker_sha256=[hashlib.sha256(m).hexdigest() for m in markers],
                    ids_sha256=digest(sorted(ids)) if ids is not None else None,
                    ignore_ascii_case=ignore_case),
                limits=dict(workers=workers, max_sql_bytes=MAX_BYTES,
                    timeout_seconds=TIMEOUT, worker_address_space_bytes=MEMORY_BYTES)).items():
            put_meta(target, key, value)
        target.commit()
        rows, exhausted = iter(selected_rows(source, markers, ids, ignore_case)), False

        def record(uid, safe):
            nonlocal done
            sha, count = pending.pop(uid)
            state = safe['state']
            target.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                (uid, sha, state, safe.get('reason') or (state if state != 'reliable' else None),
                 safe.get('fingerprint'), safe.get('structure_sha256'), safe.get('v4_projection_sha256'),
                 safe.get('in_lists', 0), safe.get('hint_count', 0), count))
            done += 1
            states[state] += 1

        try:
            while True:
                for process in pool:
                    if process.pending is None and not exhausted:
                        row = next(rows, None)
                        if row is None:
                            exhausted = True
                        else:
                            uid, sha, raw, count = row
                            pending[uid] = (sha, count)
                            if len(raw) > MAX_BYTES:
                                record(uid, dict(state='normalization_failed', reason='input_size_limit'))
                            else:
                                process.submit(uid, raw)
                    if process.pending is not None:
                        result = process.receive()
                        if result is not None:
                            record(*result)
                active = [p.connection for p in pool if p.pending is not None]
                if exhausted and not active:
                    break
                if active:
                    wait(active, timeout=.05)
                if time.monotonic() - last_progress >= 20:
                    target.commit()
                    print(json.dumps(dict(completed=done, states=states)), flush=True)
                    last_progress = time.monotonic()
            if before != file_sha(source_path) or sources != code_hashes():
                raise EvidenceError('input_or_code_changed')
            put_meta(target, 'counts', dict(states))
            put_meta(target, 'records', done)
            put_meta(target, 'seconds', round(time.monotonic() - started, 3))
            put_meta(target, 'complete', True)
            target.commit()
        finally:
            for process in pool:
                process.close()
    return dict(records=done, states=dict(states), seconds=round(time.monotonic() - started, 3))


def compare(before_path, after_path, require_v4=False, ids=None):
    with readonly(before_path) as before, readonly(after_path) as after:
        left_meta, right_meta = meta(before), meta(after)
        if any(m.get('format') != FORMAT or m.get('complete') is not True for m in (left_meta, right_meta)):
            raise EvidenceError('incomplete_or_unknown_snapshot')
        if left_meta['source_sha256'] != right_meta['source_sha256']:
            raise EvidenceError('source_index_mismatch')
        for db, metadata in ((before, left_meta), (after, right_meta)):
            if db.execute('SELECT count(*) FROM records').fetchone()[0] != metadata['records']:
                raise EvidenceError('snapshot_record_count_mismatch')
        left_context, right_context = left_meta['context'], right_meta['context']
        audited = (left_meta.get('project_v4') is True
                   and left_context['algorithm_version'] == 'sql-normalization/3'
                   and left_context['parser_version'] == 'mpp-adapter-probe/8'
                   and right_context['algorithm_version'] == 'sql-normalization/4'
                   and right_context['parser_version'] == 'mpp-adapter/9'
                   and all(left_context[k] == right_context[k] for k in (
                       'dictionary_digest', 'profile', 'parser_dependency')))
        if require_v4 and not audited:
            raise EvidenceError('v4_audit_context_mismatch')
        old_groups, new_groups = defaultdict(set), defaultdict(set)
        old_members, new_members = defaultdict(list), defaultdict(list)
        transitions, reasons = Counter(), Counter()
        examples, mismatches = [], []
        status_count = reason_count = mismatch_count = checked = total = 0
        occurrences, eligible, reliable = {}, [0, 0], [0, 0]
        query = 'SELECT input_id,sql_sha256,state,reason,fingerprint,structure_sha256,v4_projection_sha256,in_lists,occurrences FROM records ORDER BY input_id'
        def rows(db):
            for row in db.execute(query):
                if ids is None or row[0] in ids:
                    yield row

        for left, right in zip_longest(rows(before), rows(after)):
            if left is None or right is None or left[:2] != right[:2] or left[8] != right[8]:
                raise EvidenceError('selected_inputs_mismatch')
            uid = left[0]
            total += 1
            occurrences[uid] = left[8]
            for side, row in enumerate((left, right)):
                reliable[side] += row[2] == 'reliable'
                eligible[side] += row[7] > 0
                if (row[2] == 'reliable') != bool(row[4] and row[5]):
                    raise EvidenceError('invalid_snapshot_record')
            if left[2] != right[2]:
                status_count += 1
                transitions[left[2] + ' -> ' + right[2]] += 1
                if len(examples) < 20:
                    examples.append(uid)
            if left[3] != right[3]:
                reason_count += 1
                reasons[str(left[3]) + ' -> ' + str(right[3])] += 1
            if left[2] == 'reliable':
                old_members[left[4]].append(uid)
            if right[2] == 'reliable':
                new_members[right[4]].append(uid)
            if left[2] == right[2] == 'reliable':
                old_groups[left[4]].add(right[4])
                new_groups[right[4]].add(left[4])
                if audited:
                    checked += 1
                    if not left[6] or left[6] != right[5]:
                        mismatch_count += 1
                        if len(mismatches) < 20:
                            mismatches.append(uid)
        if ids is not None and total != len(ids):
            raise EvidenceError('selected_ids_missing')
        merges = [key for key, groups in new_groups.items() if len(groups) > 1]
        splits = [key for key, groups in old_groups.items() if len(groups) > 1]
        affected = [uid for key in merges for uid in new_members[key]]
        result = dict(format=FORMAT, context_changed=left_context != right_context,
            comparison_tool_sha256=file_sha(Path(__file__)),
            selection=dict(kind='ids' if ids is not None else 'all_snapshot_records',
                           ids_sha256=digest(sorted(ids)) if ids is not None else None),
            contexts={'before': left_context, 'after': right_context},
            snapshot_sha256={'before': file_sha(before_path), 'after': file_sha(after_path)},
            source_sha256=left_meta['source_sha256'], inputs=total,
            reliable_inputs={'before': reliable[0], 'after': reliable[1]},
            eligible_in_inputs={'before': eligible[0], 'after': eligible[1]},
            groups={'before': len(old_members), 'after': len(new_members),
                    'delta': len(new_members) - len(old_members)},
            status_changes=status_count, status_transitions=dict(transitions),
            reason_changes=reason_count, reason_transitions=dict(reasons),
            status_change_examples=examples, merged_groups=len(merges), split_groups=len(splits),
            merged_inputs=len(affected), merged_input_occurrences=sum(occurrences[i] for i in affected),
            merge_examples=[new_members[key][:10] for key in merges[:10]],
            split_examples=[old_members[key][:10] for key in splits[:10]],
            v4_audit=dict(applied=audited, checked=checked, mismatches=mismatch_count,
                          mismatch_examples=mismatches))
        result['v4_acceptance_passed'] = (audited and checked == reliable[0] == reliable[1]
            and mismatch_count == status_count == reason_count == len(splits) == 0)
        return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='command', required=True)
    cap = commands.add_parser('capture', help='write a new local, redacted SQLite snapshot')
    cap.add_argument('--source', type=Path, default=ROOT / 'var/parser-probe/full-scan.sqlite')
    cap.add_argument('--output', type=Path, required=True)
    cap.add_argument('--workers', type=int, default=4)
    selection = cap.add_mutually_exclusive_group()
    selection.add_argument('--marker', action='append', help='UTF-8 byte marker; repeat for OR')
    selection.add_argument('--ids', type=Path, help='JSON array of distinct positive input IDs')
    cap.add_argument('--ignore-ascii-case', action='store_true')
    comp = commands.add_parser('compare', help='compare group memberships, never fingerprint strings')
    comp.add_argument('before', type=Path)
    comp.add_argument('after', type=Path)
    comp.add_argument('--output', type=Path)
    comp.add_argument('--ids', type=Path, help='compare only these IDs; every ID must exist in both complete snapshots')
    comp.add_argument('--require-v4', action='store_true', help='fail unless every v3-to-v4 change is explained')
    comp.add_argument('--text', action='store_true', help='print a compact text summary instead of JSON')
    args = ap.parse_args()
    try:
        ids = json.loads(args.ids.read_text()) if args.ids else None
        if ids is not None:
            if (not isinstance(ids, list) or not ids or any(type(i) is not int or i <= 0 for i in ids)
                    or len(set(ids)) != len(ids)):
                raise EvidenceError('invalid_id_selection')
            ids = set(ids)
        if args.command == 'capture':
            if (ROOT / 'var').resolve() not in args.output.resolve().parents:
                raise EvidenceError('snapshot_must_stay_in_local_var')
            result = capture(args.source, args.output, args.workers,
                             tuple(m.encode('utf-8') for m in (args.marker or [])), ids,
                             args.ignore_ascii_case)
        else:
            result = compare(args.before, args.after, args.require_v4, ids)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open('x') as stream:
                    stream.write(json.dumps(result, sort_keys=True, indent=2) + '\n')
            if args.text:
                print('inputs={inputs}; contexts_differ={context_changed}; '
                      'merges={merged_groups}; splits={split_groups}; '
                      'status_changes={status_changes}; v4_passed={v4_acceptance_passed}'.format(**result))
                return int(args.require_v4 and not result['v4_acceptance_passed'])
        print(json.dumps(result, sort_keys=True))
        return int(args.command == 'compare' and args.require_v4 and not result['v4_acceptance_passed'])
    except EvidenceError as error:
        print(json.dumps({'error': str(error)}))
    except Exception:
        print(json.dumps({'error': 'diagnostic_failed'}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
