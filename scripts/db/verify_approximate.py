#!/usr/bin/env python3
"""Round-trip every v5 refusal through a private PG17; emit only safe summaries."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import signal
import sys
import time

from verify import ROOT, Verification, instance, run
from database.approximate import READ_SQL, canonical, insert_sql, rule_sql, unpack
from sql_apm.diagnostics.normalization_diff import code_hashes, meta, readonly
from sql_apm.diagnostics.mpp_full_audit import file_sha
from sql_apm.sql.normalization import Normalizer


class EvidenceError(ValueError):
    """Only fixed public codes; never include source text or database errors."""


def require(condition, code):
    if not condition:
        raise EvidenceError(code)


def verify(args):
    started = time.monotonic()
    require(args.source.resolve() != args.snapshot.resolve(), 'source_equals_snapshot')
    require(not args.output.exists(), 'output_already_exists')
    version = run([args.pg_bin / 'psql', '--version']).stdout.strip()
    require(' 17.' in version, 'postgresql_17_required')
    hashes = {'source': file_sha(args.source), 'snapshot': file_sha(args.snapshot)}
    sources = code_hashes()
    engine = Normalizer()
    with readonly(args.source) as source, readonly(args.snapshot) as snapshot:
        sm, vm = meta(source), meta(snapshot)
        require(sm.get('collection_complete') is True, 'incomplete_source')
        require(vm.get('complete') is True and vm.get('format') == 'normalization-diff/1', 'incomplete_snapshot')
        require(vm.get('selection', {}).get('kind') == 'all', 'snapshot_is_not_full')
        require(vm.get('source_sha256') == hashes['source'], 'source_digest_mismatch')
        require(vm.get('context') == engine.context and engine.context['algorithm_version'] == 'sql-normalization/5', 'v5_context_mismatch')
        require(vm.get('sources') == sources and vm.get('rule_snapshot') == engine.rule_snapshot(), 'snapshot_code_or_rules_mismatch')
        total = snapshot.execute('SELECT count(*) FROM records').fetchone()[0]
        require(total == vm.get('records') == source.execute('SELECT count(*) FROM inputs').fetchone()[0], 'snapshot_count_mismatch')
        counts = dict(snapshot.execute('SELECT state,count(*) FROM records GROUP BY state'))
        require(counts == vm.get('counts'), 'snapshot_distribution_mismatch')
        source.execute('ATTACH DATABASE ? AS snapshot', (args.snapshot.resolve().as_uri() + '?mode=ro',))
        require(source.execute('SELECT count(*) FROM inputs i LEFT JOIN snapshot.records r ON r.input_id=i.id WHERE r.input_id IS NULL OR i.sha256<>r.sql_sha256').fetchone()[0] == 0, 'snapshot_input_set_mismatch')
        selected = list(snapshot.execute("SELECT input_id,sql_sha256,reason,occurrences FROM records WHERE state='unsupported_syntax' ORDER BY input_id"))
        require(len(selected) == counts.get('unsupported_syntax') and len(selected) > 0, 'refusal_selection_mismatch')
        states, reasons, structural, diagnostics = Counter(), Counter(), Counter(), Counter()
        expected_digest, actual_digest, selected_digest = (hashlib.sha256() for _ in range(3))
        with instance(args.pg_bin) as (directory, env):
            v = Verification(args.pg_bin, directory, env)
            v.init()
            for offset in range(0, len(selected), 100):
                expected, statements = {}, []
                for uid, sha, reason, occurrences in selected[offset:offset + 100]:
                    raw = source.execute('SELECT sql FROM inputs WHERE id=?', (uid,)).fetchone()[0]
                    require(hashlib.sha256(raw).hexdigest() == sha, 'raw_digest_mismatch')
                    signal.setitimer(signal.ITIMER_REAL, 5)
                    try:
                        result = engine.normalize(raw)
                    finally:
                        signal.setitimer(signal.ITIMER_REAL, 0)
                    require(result['fingerprint']['state'] == 'unsupported_syntax' and result['fingerprint']['reason'] == reason,
                            'current_interface_refusal_mismatch')
                    near = result['approximate']
                    require(near is not None and near['structural_reason'] == reason, 'missing_approximate_result')
                    identity = str(uid).zfill(10)
                    expected[identity] = near
                    if offset == 0 and not statements:
                        v.sql(rule_sql(near))
                    statements.append(insert_sql(identity, raw, near))
                    states[near['state']] += 1
                    reasons[near['reason'] or '<none>'] += 1
                    structural[near['structural_reason']] += 1
                    diagnostics.update(near['diagnostics'])
                    selected_digest.update(canonical([uid, sha, reason, occurrences]).encode('ascii') + b'\n')
                # Both writes run serially, with exact-byte candidate reuse.
                batch = 'BEGIN;\n' + ''.join(statements) + 'COMMIT;'
                v.sql(batch)
                v.sql(batch)
                ids = ','.join("'" + identity + "'" for identity in expected)
                query = READ_SQL.replace('ORDER BY result_id', 'WHERE result_id IN (' + ids + ') ORDER BY result_id')
                rows = [json.loads(line) for line in v.sql(query).splitlines()]
                require(len(rows) == len(expected), 'roundtrip_count_mismatch')
                for row in rows:
                    before, after = expected[row['result_id']], unpack(row)
                    require(before == after, 'roundtrip_value_mismatch')
                    expected_digest.update(canonical([row['result_id'], before]).encode('ascii') + b'\n')
                    actual_digest.update(canonical([row['result_id'], after]).encode('ascii') + b'\n')
                print(json.dumps({'verified': offset + len(rows), 'total': len(selected)}), flush=True)
            sizes = json.loads(v.sql("SELECT json_build_object('inputs',(SELECT count(*) FROM mpp_approximate_input),'results',(SELECT count(*) FROM mpp_approximate_result),'rules',(SELECT count(*) FROM mpp_approximate_rule),'reliable_sql',(SELECT count(*) FROM mpp_sql_text),'groups',(SELECT count(*) FROM mpp_baseline_group),'decisions',(SELECT count(*) FROM mpp_decision))"))
            require(sizes == dict(inputs=len(selected), results=len(selected), rules=1, reliable_sql=0, groups=0, decisions=0), 'stored_counts_or_isolation_mismatch')
            stored = {}
            for column, expected_counts in [('state', states), ('reason', reasons), ('structural_reason', structural)]:
                actual = json.loads(v.sql("SELECT json_object_agg(key,n) FROM (SELECT coalesce(" + column + ",'<none>') key,count(*) n FROM mpp_approximate_result GROUP BY 1) t"))
                require(actual == dict(expected_counts), 'stored_distribution_mismatch')
                stored[column] = actual
            stored_diagnostics = json.loads(v.sql("SELECT coalesce(json_object_agg(key,n),'{}') FROM (SELECT unnest(diagnostics) key,count(*) n FROM mpp_approximate_result GROUP BY 1) t"))
            require(stored_diagnostics == dict(diagnostics), 'stored_diagnostics_mismatch')
            v.init('check')
            v.init('schema')
            require(int(v.sql('SELECT count(*) FROM mpp_approximate_result')) == len(selected), 'rerun_count_mismatch')
    require(hashes == {'source': file_sha(args.source), 'snapshot': file_sha(args.snapshot)} and sources == code_hashes(), 'input_or_code_changed')
    require(expected_digest.digest() == actual_digest.digest(), 'aggregate_roundtrip_mismatch')
    return dict(format='approximate-storage-verification/1', complete=True, schema_version='1.2.0',
                postgres=version, python=sys.version.split()[0], context=engine.context, sources=sources,
                source_sha256=hashes['source'], snapshot_sha256=hashes['snapshot'], selected_sha256=selected_digest.hexdigest(),
                interface_sha256=expected_digest.hexdigest(), roundtrip_sha256=actual_digest.hexdigest(),
                schema_sha256=file_sha(ROOT / 'sql_apm/storage/schema.sql'), selected=len(selected), total_inputs=total,
                log_occurrences=sum(row[3] for row in selected), stored=sizes, distributions=stored,
                diagnostics=dict(diagnostics), duplicate_passes=2, seconds=round(time.monotonic()-started,3))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise EvidenceError('interrupted_or_call_timeout')

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
        signal.signal(sig, interrupted)
    try:
        report = verify(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as file:
            json.dump(report, file, ensure_ascii=True, sort_keys=True, indent=2)
            file.write('\n')
    except Exception as error:
        # psql diagnostics can contain SQL fragments. Never export their text.
        print(json.dumps({'complete': False, 'error': str(error) if isinstance(error, EvidenceError) else type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps({'complete': True, 'selected': report['selected'], 'output': str(args.output)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
