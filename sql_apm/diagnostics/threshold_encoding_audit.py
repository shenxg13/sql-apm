"""Audit encoding refusals against an existing threshold cache, without SQL export.

Scan bytes once, then replay all invalid inputs and a bounded valid selection
through the existing isolated capture. Keep historical evidence immutable.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from sql_apm.diagnostics import threshold_coverage as coverage
from sql_apm.diagnostics.mpp_full_scan import initialize, set_meta


def audit(source_path, baseline_path, evidence_path, output, work_dir):
    if output.exists() or work_dir.exists():
        raise ValueError('output_exists')
    before = {name: coverage.file_sha(path) for name, path in
              (('source', source_path), ('baseline', baseline_path), ('evidence', evidence_path))}
    evidence = json.loads(evidence_path.read_text())
    sources = coverage.code_hashes()
    for name in ('threshold_coverage', 'threshold_encoding_audit', 'log_supplement'):
        key = 'sql_apm/diagnostics/' + name + '.py'
        sources[key] = coverage.file_sha(coverage.ROOT / key)
    started = time.monotonic()
    work_dir.mkdir(parents=True)
    selected, invalid, byte_reasons, selected_kinds = set(), set(), Counter(), Counter()
    longest = (0, 0)
    old_success, affected_occurrences = 0, Counter()
    with coverage.readonly(source_path) as source, coverage.readonly(baseline_path) as baseline:
        metadata = coverage.meta(source)
        count = source.execute('SELECT count(*) FROM inputs').fetchone()[0]
        if (metadata.get('collection_complete') is not True or metadata.get('record_dates') is not True or
                coverage.meta(baseline).get('complete') is not True or
                evidence.get('complete') is not True or evidence['source_sha256'] != before['source'] or
                evidence['unique_inputs'] != count or evidence['schemes'] != list(coverage.SCHEMES)):
            raise ValueError('incompatible_source')
        uniform = {i * (count - 1) // 127 for i in range(128)} if count else set()
        for ordinal, (uid, sha, raw) in enumerate(source.execute('SELECT id,sha256,sql FROM inputs ORDER BY id')):
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError('source_digest_mismatch')
            bad_utf8 = False
            try:
                raw.decode('utf-8')
            except UnicodeDecodeError:
                bad_utf8 = True
            nul = b'\0' in raw
            if bad_utf8 or nul:
                invalid.add(uid)
                byte_reasons['invalid_utf8' if bad_utf8 else 'nul_only'] += 1
                byte_reasons['nul_present'] += int(nul)
                old = baseline.execute("SELECT state,reason,fingerprint FROM records WHERE "
                                       "scheme='tidb_lexical' AND input_id=?", (uid,)).fetchone()
                if old is None:
                    raise ValueError('baseline_missing_record')
                old_success += int(old[2] is not None)
                for cluster, total in source.execute('SELECT substr(file_key,1,instr(file_key,\'/\')-1),'
                        'sum(count) FROM occurrences WHERE input_id=? GROUP BY 1', (uid,)):
                    affected_occurrences[cluster] += total
            elif b'$$' in raw or re.search(rb'\$[A-Za-z_][A-Za-z_0-9]*\$', raw):
                kind = 'untagged_dollar_marker' if b'$$' in raw else 'tagged_dollar_marker'
                if selected_kinds[kind] < 8:
                    selected.add(uid)
                    selected_kinds[kind] += 1
            if ordinal in uniform:
                selected.add(uid)
            longest = max(longest, (len(raw), uid))
        selected.update(invalid)
        if count:
            selected.add(longest[1])
        scan_seconds = round(time.monotonic() - started, 3)
        subset_path = work_dir / 'selection.sqlite'
        selection_hash = hashlib.sha256()
        with sqlite3.connect(str(subset_path)) as subset:
            initialize(subset)
            set_meta(subset, 'collection_complete', True)
            set_meta(subset, 'record_dates', True)
            subset.executemany('INSERT INTO files VALUES (?,?)', source.execute('SELECT * FROM files'))
            for uid in sorted(selected):
                row = source.execute('SELECT * FROM inputs WHERE id=?', (uid,)).fetchone()
                selection_hash.update(('%s:%s\n' % (uid, row[1])).encode('ascii'))
                subset.execute('INSERT INTO inputs VALUES (?,?,?,?,?)', row)
                subset.executemany('INSERT INTO occurrences VALUES (?,?,?,?)',
                    source.execute('SELECT * FROM occurrences WHERE input_id=?', (uid,)))
                subset.executemany('INSERT INTO occurrence_dates VALUES (?,?,?,?,?)',
                    source.execute('SELECT * FROM occurrence_dates WHERE input_id=?', (uid,)))
        replay = coverage.capture(subset_path, work_dir/'replay.json', work_dir/'replay.sqlite', workers=2)
        if replay['context'] != evidence['context']:
            raise ValueError('normalization_context_changed')
        changes, occurrence_changes, comparisons = Counter(), Counter(), 0
        with coverage.readonly(work_dir/'replay.sqlite') as current:
            for uid, scheme, state, reason, fp in current.execute('SELECT * FROM records'):
                previous = baseline.execute('SELECT state,reason,fingerprint FROM records '
                                            'WHERE scheme=? AND input_id=?', (scheme, uid)).fetchone()
                now = (state, reason, fp)
                if previous is None:
                    raise ValueError('baseline_missing_record')
                comparisons += 1
                if uid in invalid and scheme == 'tidb_lexical':
                    if now != ('lexical_refused', 'invalid_encoding_or_nul', None):
                        raise ValueError('invalid_input_accepted')
                    if now != previous:
                        transition = str(previous[1]) + ' -> ' + reason
                        changes[transition] += 1
                        for cluster, total in source.execute('SELECT substr(file_key,1,instr(file_key,"/")-1),'
                                'sum(count) FROM occurrences WHERE input_id=? GROUP BY 1', (uid,)):
                            occurrence_changes[cluster + ': ' + transition] += total
                elif now != previous:
                    raise ValueError('unexpected_replay_change')
        if comparisons != len(selected) * len(coverage.SCHEMES):
            raise ValueError('incomplete_replay')
    after = {name: coverage.file_sha(path) for name, path in
             (('source', source_path), ('baseline', baseline_path), ('evidence', evidence_path))}
    if before != after or any(coverage.file_sha(coverage.ROOT / p) != sha for p, sha in sources.items()):
        raise ValueError('source_or_code_changed')
    result = dict(format='threshold-encoding-audit/1', complete=True, input_sha256=before,
        sources=sources, context=replay['context'], python=replay['python'], unique_inputs=count,
        invalid_inputs=len(invalid), byte_reasons=dict(byte_reasons), invalid_occurrences=dict(affected_occurrences),
        previously_fingerprinted_invalid_inputs=old_success,
        replay=dict(unique_inputs=len(selected), scheme_comparisons=comparisons,
            selection='all invalid; 128 uniformly spaced ordinals; first 8 valid inputs per dollar marker kind; longest',
            marker_counts=dict(selected_kinds), selection_sha256=selection_hash.hexdigest(),
            input_ids=sorted(selected), reason_changes=dict(changes),
            reason_occurrence_changes=dict(occurrence_changes), unexpected_changes=0,
            seconds=replay['seconds'], limits=replay['limits'], evidence_sha256=coverage.file_sha(work_dir/'replay.json')),
        threshold_metrics_unchanged_by_guard=old_success == 0,
        metrics_basis='invalid inputs replayed; valid-input token path unchanged; historical aggregates not recomputed',
        immutable_inputs_verified=True, scan_seconds=scan_seconds, seconds=round(time.monotonic()-started, 3))
    coverage.write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'baseline', 'evidence', 'output', 'work-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if (coverage.ROOT / 'var').resolve() not in args.work_dir.resolve().parents:
        parser.error('work_dir_must_stay_under_var')
    try:
        result = audit(args.source, args.baseline, args.evidence, args.output, args.work_dir)
    except Exception:
        print(json.dumps(dict(state='encoding_audit_failed')))
        return 1
    print(json.dumps(dict(state='complete', unique_inputs=result['unique_inputs'],
                         invalid_inputs=result['invalid_inputs'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
