"""Observation-only cluster/fingerprint threshold comparisons; never train SQL.

The source is a completed mpp_full_scan index. Public output contains aggregates
and version/digest evidence only. A private cache is reserved exclusively under
var/; interrupted runs are incomplete and must use a fresh cache on retry.
"""
import argparse
from collections import Counter
import hashlib
import json
from multiprocessing.connection import wait
from pathlib import Path
import platform
import sqlite3
import time

from sql_apm.diagnostics.log_supplement import file_sha, write_json
from sql_apm.diagnostics.mpp_full_scan import ParserProcess, ROOT, MAX_BYTES, TIMEOUT, MEMORY_BYTES
from sql_apm.diagnostics.normalization_diff import readonly, meta, code_hashes, digest
from sql_apm.sql import approximate
from sql_apm.sql.normalization import Normalizer

VERSION = 'threshold-coverage/1'
SCHEMES = ('v4', 'positions', 'unqualified_functions', 'positions_functions', 'tidb_lexical')
THRESHOLDS = (30, 200, 1000)
_ENGINE = None
_VARIANTS = {}


class Candidate(Normalizer):
    """Diagnostic traversal of an already normalized tree, with explicit guards.

    Reuse casts, unknown-node protection, object identity, extensions, Hint anchors,
    SET and LIMIT boundaries. F replaces values without adding IN-list bucketing.
    These are deliberately experimental identities, not product fingerprints.
    """
    def __init__(self, positions=False, functions=False):
        super().__init__()
        self.positions, self.functions = positions, functions

    def _fields(self, tag, body, mode, counters):
        fields = super()._fields(tag, body, mode, counters)
        if self.positions:
            if tag == 'SelectStmt':
                fields['targetList'] = 'F'
            elif tag == 'ResTarget' and mode == 'F':
                fields['val'] = 'F'
            elif tag == 'JoinExpr':
                fields['quals'] = 'F'
            elif tag == 'CaseExpr':
                fields.update(arg='F', args='F', defresult='F')
            elif tag == 'CaseWhen':
                fields.update(expr='F', result='F')
        if self.functions and tag == 'FuncCall' and len(body.get('funcname', [])) == 1:
            # Broad counterfactual: even unknown functions and control arguments.
            # Named, variadic and special syntax calls retain the product policy.
            if (body.get('funcformat') == 'COERCE_EXPLICIT_CALL' and
                    not body.get('func_variadic') and
                    not any('NamedArgExpr' in arg for arg in body.get('args', []))):
                fields['args'] = 'F'
        return fields

    def project(self, normalized):
        return dict(statements=[dict(base=self._walk(s['base'], Counter()), extensions=s['extensions'])
                               for s in normalized['statements']], hints=normalized['hints'])


def lexical_digest(sql):
    """TiDB-inspired PG token control, not a TiDB implementation/conformance claim.

    Fold unquoted words, replace literals/parameters everywhere and collapse bare
    IN value lists. Quoted identifiers and Hint tokens retain exact identity.
    Refuse uncertain lexical boundaries; successful tokenization is not parsing.
    """
    tokens, issues, _ = approximate._scan(sql)
    if issues:
        return dict(state='lexical_refused', reason=issues[0], fingerprint=None)
    values = []
    for token in tokens:
        if token.kind in ('number', 'string', 'protected_literal', 'parameter'):
            values.append(('value', '?'))
        else:
            values.append((token.kind, token.word))
    folded, i = [], 0
    while i < len(values):
        current = values[i]
        if current == ('word', 'in') and i + 2 < len(values) and values[i + 1] == ('punctuation', '('):
            end, expect_value = i + 2, True
            while end < len(values):
                value = values[end]
                if expect_value and value == ('value', '?'):
                    expect_value = False
                elif not expect_value and value == ('punctuation', ','):
                    expect_value = True
                else:
                    break
                end += 1
            if not expect_value and end < len(values) and values[end] == ('punctuation', ')'):
                folded.extend((current, ('punctuation', '('), ('values', '...'), ('punctuation', ')')))
                i = end + 1
                continue
        folded.append(current)
        i += 1
    if not folded or all(v == ('punctuation', ';') for v in folded):
        return dict(state='lexical_refused', reason='empty_tokens', fingerprint=None)
    return dict(state='lexical', reason=None, fingerprint=digest([VERSION, 'tidb_lexical', folded]))


def coverage_worker(request):
    global _ENGINE
    schemes = request['schemes']
    results = {}
    try:
        if any(s != 'tidb_lexical' for s in schemes):
            if _ENGINE is None:
                _ENGINE = Normalizer()
            base = _ENGINE.normalize(request['sql'].encode('utf-8', 'surrogateescape'))
            fp = base['fingerprint']
            for scheme in schemes:
                if scheme == 'tidb_lexical':
                    continue
                if fp['state'] != 'reliable' or scheme == 'v4':
                    results[scheme] = dict(state=fp['state'], reason=fp['reason'], fingerprint=fp['value'])
                else:
                    if scheme not in _VARIANTS:
                        _VARIANTS[scheme] = Candidate(positions=scheme in ('positions', 'positions_functions'),
                            functions=scheme in ('unqualified_functions', 'positions_functions'))
                    tree = _VARIANTS[scheme].project(base['normalized'])
                    results[scheme] = dict(state='candidate', reason=None,
                        fingerprint=digest([VERSION, scheme, _ENGINE.context, tree]))
        if 'tidb_lexical' in schemes:
            results['tidb_lexical'] = lexical_digest(request['sql'])
        return dict(state='computed', schemes=results)
    except MemoryError:
        return dict(state='probe_memory_limit')
    except Exception:
        # Raw exception text and normalized trees never cross the process boundary.
        return dict(state='coverage_failed')


def summarize(source, cache, schemes):
    """Distinct days, not files/fields; each cluster has its own denominator."""
    if meta(source).get('record_dates') is not True:
        raise ValueError('record_dates_required')
    cache.execute('CREATE TABLE days (file_key TEXT PRIMARY KEY, cluster TEXT)')
    cache.executemany('INSERT INTO days VALUES (?,?)',
                     ((key, key.split('/', 1)[0]) for key, in source.execute('SELECT file_key FROM files')))
    cache.execute('CREATE TABLE occurrences (input_id INTEGER, file_key TEXT, day TEXT, count INTEGER)')
    cache.executemany('INSERT INTO occurrences VALUES (?,?,?,?)', source.execute(
        'SELECT input_id,file_key,day,sum(count) FROM occurrence_dates GROUP BY input_id,file_key,day'))
    cache.execute('CREATE INDEX occurrence_input ON occurrences(input_id)')
    if cache.execute('SELECT count(*) FROM occurrences o LEFT JOIN days d USING(file_key) '
                     'WHERE d.file_key IS NULL OR o.count <= 0').fetchone()[0]:
        raise ValueError('invalid_source_occurrences')
    if source.execute('SELECT sum(count) FROM occurrences').fetchone()[0] != cache.execute(
            'SELECT sum(count) FROM occurrences').fetchone()[0]:
        raise ValueError('date_occurrence_mismatch')
    clusters = {}
    for cluster, count, days in cache.execute('''SELECT d.cluster,sum(o.count),count(DISTINCT o.day)
            FROM occurrences o JOIN days d USING(file_key) GROUP BY d.cluster'''):
        clusters[cluster] = dict(input_occurrences=count, active_dates=days, schemes={})
    for scheme in schemes:
        for cluster, summary in clusters.items():
            states = dict(cache.execute('''SELECT r.state,sum(o.count) FROM records r
                JOIN occurrences o ON r.input_id=o.input_id JOIN days d USING(file_key)
                WHERE r.scheme=? AND d.cluster=? GROUP BY r.state''', (scheme, cluster)))
            reasons = dict(cache.execute('''SELECT r.reason,sum(o.count) FROM records r
                JOIN occurrences o ON r.input_id=o.input_id JOIN days d USING(file_key)
                WHERE r.scheme=? AND d.cluster=? AND r.reason IS NOT NULL GROUP BY r.reason''',
                (scheme, cluster)))
            thresholds = {str(n): dict(groups=0, input_occurrences=0) for n in THRESHOLDS}
            groups = accepted = 0
            for count, days in cache.execute('''SELECT sum(o.count),count(DISTINCT o.day)
                    FROM records r JOIN occurrences o ON r.input_id=o.input_id
                    JOIN days d USING(file_key) WHERE r.scheme=? AND d.cluster=?
                    AND r.fingerprint IS NOT NULL GROUP BY r.fingerprint''', (scheme, cluster)):
                groups += 1
                accepted += count
                for threshold in THRESHOLDS:
                    if count >= threshold and days >= 7:
                        thresholds[str(threshold)]['groups'] += 1
                        thresholds[str(threshold)]['input_occurrences'] += count
            if sum(states.values()) != summary['input_occurrences']:
                raise ValueError('coverage_denominator_mismatch')
            for item in thresholds.values():
                item['fraction_all_input_occurrences'] = item['input_occurrences'] / summary['input_occurrences']
                item['fraction_fingerprinted_occurrences'] = item['input_occurrences'] / accepted if accepted else 0
            summary['schemes'][scheme] = dict(groups=groups, fingerprinted_occurrences=accepted,
                occurrence_states=states, occurrence_reasons=reasons, thresholds=thresholds)
    return clusters


def capture(source_path, output, cache_path, schemes=SCHEMES, workers=8):
    if not schemes or len(set(schemes)) != len(schemes) or set(schemes) - set(SCHEMES):
        raise ValueError('invalid_schemes')
    if not 1 <= workers <= 8:
        raise ValueError('invalid_workers')
    if len({p.resolve() for p in (source_path, output, cache_path)}) != 3:
        raise ValueError('path_conflict')
    if output.exists() or cache_path.exists():
        raise ValueError('output_exists')
    before = file_sha(source_path)
    sources = code_hashes()
    for path in ('sql_apm/diagnostics/threshold_coverage.py', 'sql_apm/diagnostics/log_supplement.py'):
        sources[path] = file_sha(ROOT / path)
    engine = Normalizer()
    started = last_progress = time.monotonic()
    done = 0
    pool = [ParserProcess(task=coverage_worker, worker_options=dict(schemes=schemes)) for _ in range(workers)]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open('xb'):
        pass
    with readonly(source_path) as source, sqlite3.connect(str(cache_path)) as cache:
        metadata = meta(source)
        if metadata.get('collection_complete') is not True or metadata.get('record_dates') is not True or source.execute(
                'SELECT count(*) FROM inputs WHERE result IS NULL').fetchone()[0]:
            raise ValueError('incomplete_source')
        count = source.execute('SELECT count(*) FROM inputs').fetchone()[0]
        cache.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE records(input_id INTEGER,scheme TEXT,state TEXT,reason TEXT,fingerprint TEXT,
                PRIMARY KEY(scheme,input_id));''')
        cache.execute('INSERT INTO meta VALUES (?,?)', ('complete', 'false'))
        cache.commit()
        rows, exhausted = iter(source.execute('SELECT id,sha256,sql FROM inputs ORDER BY id')), False

        def record(uid, result):
            nonlocal done
            for scheme in schemes:
                item = result.get('schemes', {}).get(scheme)
                if item is None:
                    item = dict(state=result['state'], reason=result['state'], fingerprint=None)
                cache.execute('INSERT INTO records VALUES (?,?,?,?,?)',
                    (uid, scheme, item['state'], item['reason'], item['fingerprint']))
            done += 1

        try:
            while True:
                for process in pool:
                    if process.pending is None and not exhausted:
                        row = next(rows, None)
                        if row is None:
                            exhausted = True
                        else:
                            uid, sha, raw = row
                            if hashlib.sha256(raw).hexdigest() != sha:
                                raise ValueError('source_digest_mismatch')
                            if len(raw) > MAX_BYTES:
                                record(uid, dict(state='input_size_limit'))
                            else:
                                process.submit(uid, raw)
                    if process.pending is not None:
                        received = process.receive()
                        if received is not None:
                            record(*received)
                active = [p.connection for p in pool if p.pending is not None]
                if exhausted and not active:
                    break
                if active:
                    wait(active, timeout=.05)
                if time.monotonic() - last_progress >= 20:
                    cache.commit()
                    print(json.dumps(dict(completed=done, total=count)), flush=True)
                    last_progress = time.monotonic()
            if done != count:
                raise ValueError('incomplete_coverage')
            cache.commit()
            clusters = summarize(source, cache, schemes)
            if before != file_sha(source_path) or any(file_sha(ROOT / p) != sha for p, sha in sources.items()):
                raise ValueError('source_or_code_changed')
            result = dict(format=VERSION, complete=True, source_sha256=before,
                source_context=metadata.get('context'), context=engine.context, sources=sources,
                python=platform.python_version(), schemes=list(schemes), unique_inputs=count,
                date_basis='CSV record timestamp date; not inferred execution start date',
                thresholds=dict(min_active_dates=7, min_occurrences=list(THRESHOLDS)),
                limits=dict(workers=workers, max_sql_bytes=MAX_BYTES, timeout_seconds=TIMEOUT,
                            worker_address_space_bytes=MEMORY_BYTES), clusters=clusters,
                seconds=round(time.monotonic() - started, 3))
            write_json(output, result)
            cache.execute("UPDATE meta SET value='true' WHERE key='complete'")
            cache.commit()
        finally:
            for process in pool:
                process.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, help='new private SQLite cache under var/')
    parser.add_argument('--schemes', nargs='+', choices=SCHEMES, default=list(SCHEMES))
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    cache = args.cache or args.output.with_suffix('.sqlite')
    if (ROOT / 'var').resolve() not in cache.resolve().parents:
        parser.error('cache_must_stay_under_var')
    try:
        result = capture(args.source, args.output, cache, tuple(args.schemes), args.workers)
    except Exception:
        # No arbitrary path, exception, object identifier or SQL is printed.
        print(json.dumps(dict(state='coverage_failed', format=VERSION)))
        return 1
    print(json.dumps(dict(state='complete', unique_inputs=result['unique_inputs'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
