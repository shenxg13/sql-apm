#!/usr/bin/env python3
"""Reconcile final adapter counts and omitted historical inputs, without SQL output."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sql_apm.ingestion.config import canonical, identity
from sql_apm.ingestion.hashdata.reader import Records, Interpreter, raw_bytes, record_metrics
from sql_apm.diagnostics.mpp_full_scan import candidates


def main(args):
    started = time.monotonic()
    evidence = json.loads(args.report.read_text())
    manifest = json.loads(args.manifest.read_text())
    expected = {entry['file_id']: entry['counts'] for run in evidence['first_runs'] for entry in run['files']}
    db = sqlite3.connect('file:' + str(args.audit.resolve()) + '?mode=ro', uri=True)
    db.execute('ATTACH DATABASE ? AS historical', (str(args.index.resolve()),))
    missing = {row[0] for row in db.execute('SELECT h.sha256 FROM historical.inputs h LEFT JOIN stored s ON s.sha=h.sha256 WHERE s.sha IS NULL')}
    snapshot_comparison = None
    if args.snapshot:
        db.execute('ATTACH DATABASE ? AS snapshot', (str(args.snapshot.resolve()),))
        snapshot_context = json.loads(db.execute("SELECT value FROM snapshot.meta WHERE key='context'").fetchone()[0])
        if snapshot_context != evidence['normalization_context']:
            raise RuntimeError('snapshot_context_mismatch')
        snapshot_comparison = dict(
            historical_states=dict(db.execute('SELECT state,count(*) FROM snapshot.records GROUP BY 1')),
            missing_states=dict(db.execute('SELECT r.state,count(*) FROM snapshot.records r LEFT JOIN stored s ON s.sha=r.sql_sha256 WHERE s.sha IS NULL GROUP BY 1')),
            stored_without_snapshot=db.execute('SELECT count(*) FROM stored s LEFT JOIN snapshot.records r ON r.sql_sha256=s.sha WHERE r.input_id IS NULL').fetchone()[0],
            changed_states_or_reasons=db.execute("SELECT count(*) FROM snapshot.records r JOIN stored s ON s.sha=r.sql_sha256 WHERE (s.kind='complete' AND r.state<>'reliable') OR (s.kind='fragment' AND (r.state<>'unsupported_syntax' OR s.state<>r.reason))").fetchone()[0])
        if snapshot_comparison['stored_without_snapshot'] or snapshot_comparison['changed_states_or_reasons']:
            raise RuntimeError('snapshot_state_mismatch')
    db.close()
    error_evidence = Counter()
    reasons, uses, results, eligible_missing = defaultdict(set), Counter(), [], set()
    for cluster, details in sorted(manifest['clusters'].items()):
        for entry in sorted(details['files'], key=lambda item: item['file']):
            path = args.root / cluster / entry['file']
            fid = 'I:' + identity('full-' + cluster, entry['sha256'])
            records, interpreter, counts = Records(path), Interpreter(), Counter()
            for number, begin, end, row in records:
                if row[16] == 'ERROR':
                    kind = 'other_error'
                    if row[17] == '57014':
                        kind = ('cancelled' if 'canceling statement due to user request' in row[18] else
                                'timed_out' if 'canceling statement due to statement timeout' in row[18] else 'other_57014')
                    error_evidence[cluster + ':' + kind + ':' + ('with_sql' if row[24].strip() else 'without_sql') + ':' + ('master' if row[11] == 'seg-1' else 'segment')] += 1
                event = interpreter.interpret(row, fid + ':' + str(number))
                metrics, duration = record_metrics(row)
                counts.update(metrics)
                if event:
                    counts['occurrences'] += 1
                    counts['outcome:' + event['outcome']] += 1
                    counts['timing:' + (event['timing'] or 'unknown')] += 1
                for field, value in candidates(row):
                    digest = hashlib.sha256(raw_bytes(value)).hexdigest()
                    if digest not in missing:
                        continue
                    reason = ('inline_diagnostic_only' if field == 'inline_distinct' else
                              'internal_diagnostic_only' if field == 'internal' else
                              'eligible_but_unstored' if event or duration else 'non_event_primary_sql')
                    reasons[digest].add(reason)
                    uses[reason] += 1
                    if reason == 'eligible_but_unstored':
                        eligible_missing.add(digest)
            if records.sha256 != entry['sha256'] or records.byte_count != entry['bytes']:
                raise RuntimeError('input_checksum_mismatch')
            previous = {key: val for key, val in expected[fid].items() if not key.startswith('problem:')}
            if dict(counts) != previous:
                raise RuntimeError('final_adapter_count_mismatch')
            results.append(dict(file_id=fid, records=counts['records'], matched=True))
            print(canonical(dict(phase='reconcile', files=len(results), missing_identities_seen=len(reasons))), flush=True)
    report = dict(files=results, record_count=sum(item['records'] for item in results),
                  historical_missing=len(missing), missing_seen=len(reasons),
                  eligible_missing=len(eligible_missing), occurrence_reasons=dict(uses),
                  distinct_input_reasons=dict(Counter('+'.join(sorted(value)) for value in reasons.values())),
                  snapshot_comparison=snapshot_comparison, error_evidence=dict(error_evidence),
                  final_adapter_counts_match=True, seconds=round(time.monotonic()-started, 3),
                  code_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                      sorted(Path('sql_apm/ingestion').rglob('*.py'))})
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    if eligible_missing or len(reasons) != len(missing):
        raise RuntimeError('unexplained_identity_difference')
    print(canonical(dict(state='reconciled', records=report['record_count'], historical_missing=len(missing))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, help='Optional matching v5 normalization snapshot')
    main(parser.parse_args())
