#!/usr/bin/env python3
"""Bounded parser investigation; no SQL execution, normalization or fingerprint API.

Each candidate runs in a disposable process with a timeout. Only synthetic input
may return AST values; production output contains locations, hashes and fixed
diagnostic labels. A parsed AST is NOT a reliable product fingerprint.
"""
import argparse
from collections import Counter, defaultdict
import csv
from enum import Enum
import hashlib
import io
import json
import logging
from pathlib import Path
import platform
import subprocess
import sys
import time

from sql_apm.sql.lexical import diagnose
from sql_apm.sql.pg_ast import pg_clean
from sql_apm.sql.structure import dumps as structure_dumps
from sql_apm.diagnostics.statement_census import (
    DURATION, INLINE, DigestReader, origin,
)

ROOT = Path(__file__).resolve().parents[2]

PARSERS = ('pglast', 'sqlglot')
MAX_SQL_BYTES = 512 * 1024
TIMEOUT_SECONDS = 5
EXPECTED_VERSIONS = {'pglast': '7.18', 'sqlglot': '30.19.0'}
SAFE_ERROR_WORDS = frozenset(('DISTRIBUTED', 'EXTERNAL', 'READABLE', 'WRITABLE',
                             'PARTITION', 'EXECUTE', 'FORMAT', 'LOG', 'REJECT'))


def digest(value):
    return hashlib.sha256(structure_dumps(value).encode()).hexdigest()


def glot_clean(value, comments=False):
    from sqlglot import exp
    if isinstance(value, exp.Expression):
        result = {'node': type(value).__name__, 'args': glot_clean(value.args, comments)}
        if comments and value.comments:
            result['comments'] = value.comments
        return result
    if isinstance(value, dict):
        return {k: glot_clean(v, comments) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [glot_clean(v, comments) for v in value]
    if isinstance(value, Enum):
        return value.name
    return value


def atoms(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from atoms(v)
    elif isinstance(value, list):
        for v in value:
            yield from atoms(v)
    else:
        yield value


def pg_hint_sidecar(sql):
    """Experimental token-gap anchoring, not a complete Hint recognizer."""
    from pglast import parser
    result, gap = [], 0
    for token in parser.scan(sql):
        if token.name in ('C_COMMENT', 'SQL_COMMENT'):
            raw = sql[token.start:token.end + 1]
            if raw.startswith(('/*+', '--+')):
                result.append({'gap': gap, 'raw': raw})
        else:
            gap += 1
    return result


def worker(request):
    # SQLGlot warning/error messages may contain complete production SQL.
    logging.disable(logging.CRITICAL)
    name, sql = request['parser'], request['sql']
    try:
        if name == 'pglast':
            from pglast import parser
            raw = json.loads(parser.parse_sql_json(sql))
            tree = pg_clean(raw)
            roots = [next(iter(s['stmt'])) for s in raw['stmts']]
            opaque = False
            with_comments = tree
        else:
            import sqlglot
            from sqlglot import exp
            from sqlglot.errors import ErrorLevel
            raw = sqlglot.parse(sql, read='postgres', error_level=ErrorLevel.RAISE)
            roots = [type(s).__name__ for s in raw if s is not None]
            opaque = any(isinstance(n, exp.Command) for s in raw if s is not None
                         for n in s.walk())
            tree = glot_clean([s for s in raw if s is not None])
            with_comments = glot_clean([s for s in raw if s is not None], True)
        result = {'state': 'opaque_command' if opaque else 'parsed' if roots else 'empty',
                  'roots': roots, 'statement_count': len(roots),
                  'ast_digest': digest(tree), 'comments_ast_digest': digest(with_comments)}
        if request.get('synthetic'):
            result['tree'] = tree
            if name == 'pglast':
                sidecar = pg_hint_sidecar(sql)
                result['hint_sidecar'] = sidecar
                result['ast_with_hint_digest'] = digest([tree, sidecar])
        return result
    except Exception as exc:
        # No exception text, SQL fragments or identifiers leave this process.
        word = None
        if name == 'pglast':
            # v7.18 error indices are unreliable after non-ASCII text; match
            # only fixed grammar words in the message, never export the message.
            for candidate in SAFE_ERROR_WORDS:
                if exc.args and exc.args[0] == 'syntax error at or near "' + candidate + '"':
                    word = candidate
                    break
        state = ('parse_error' if type(exc).__name__ in ('ParseError', 'TokenError')
                 else 'input_encoding_error' if isinstance(exc, UnicodeError)
                 else 'probe_exception')
        return {'state': state, 'exception': type(exc).__name__,
                'syntax_marker': word}


def probe(name, sql, synthetic=False):
    if len(sql.encode('utf-8', 'surrogateescape')) > MAX_SQL_BYTES:
        return {'state': 'probe_size_limit'}
    try:
        run = subprocess.run([sys.executable, '-m', 'sql_apm.diagnostics.parser_fidelity', '--worker'],
                             input=json.dumps({'parser': name, 'sql': sql,
                                               'synthetic': synthetic}), text=True,
                             capture_output=True, cwd=ROOT, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return {'state': 'probe_timeout'}
    if run.returncode:
        return {'state': 'worker_failed', 'returncode': run.returncode}
    return json.loads(run.stdout)


def synthetic_suite(path):
    spec = json.loads(path.read_text())
    results, by_id, trees = [], {}, {}
    for case in spec['cases']:
        outcomes = {}
        for name in PARSERS:
            result = probe(name, case['sql'], True)
            tree = result.pop('tree', None)
            trees[(case['id'], name)] = tree
            if tree is not None:
                values = list(atoms(tree))
                result['required_atoms_preserved'] = {
                    str(v): v in values for v in case.get('required_atoms', [])}
            outcomes[name] = result
        row = {'id': case['id'], 'family': case['family'], 'results': outcomes}
        results.append(row)
        by_id[case['id']] = row
    relations = []
    for pair in spec['relations']:
        results_by_parser = {}
        for name in PARSERS:
            left = by_id[pair['left']]['results'][name]
            right = by_id[pair['right']]['results'][name]
            result = {}
            for field in ('ast_digest', 'comments_ast_digest', 'ast_with_hint_digest'):
                if left.get('state') == right.get('state') == 'parsed' and field in left and field in right:
                    same = left[field] == right[field]
                    result[field] = {'same': same, 'meets_expectation': same == pair['same']}
            results_by_parser[name] = result
        relations.append(dict(pair, results=results_by_parser))
    checks = []
    for check in spec.get('structural_checks', []):
        value = trees[(check['case'], check['parser'])]
        try:
            for part in check['path']:
                value = value[part]
            matched = value == check['expected']
        except (TypeError, KeyError, IndexError):
            matched = False
        checks.append(dict(check, passed=matched))
    repeats = []
    for case_id in ('function', 'aggregate', 'hint_unicode', 'quoted_semicolon'):
        case = next(c for c in spec['cases'] if c['id'] == case_id)
        for name in PARSERS:
            second = probe(name, case['sql'], True)
            first = by_id[case_id]['results'][name]
            repeats.append({'case': case_id, 'parser': name,
                            'same': first['state'] == second['state'] == 'parsed'
                            and first['ast_digest'] == second.get('ast_digest')
                            and first.get('ast_with_hint_digest') == second.get('ast_with_hint_digest')})
    return {'fixture_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'cases': results, 'relations': relations, 'structural_checks': checks,
            'fresh_process_repeats': repeats}


def replay(root, evidence_path, progress):
    evidence = json.loads(evidence_path.read_text())
    selected = defaultdict(list)
    for t in evidence['replay_targets']:
        selected[(t['cluster'], t['file'])].append(t)
    manifests = {(cluster, f['file']): f for cluster, data in evidence['clusters'].items()
                 for f in data['files']}
    results, files = [], []
    csv.field_size_limit(128 * 1024 * 1024)
    for key, targets in sorted(selected.items()):
        path = (root / key[0] / key[1]).resolve()
        if root not in path.parents:
            raise ValueError('input outside root')
        before = path.stat()
        if before.st_size != manifests[key]['bytes']:
            raise ValueError('source size mismatch')
        by_number = defaultdict(list)
        for t in targets:
            by_number[t['record']].append(t)
        last_record, previous_line = max(by_number), 0
        with path.open('rb') as raw:
            hashing = DigestReader(raw)
            with io.TextIOWrapper(io.BufferedReader(hashing), encoding='utf-8',
                                  errors='surrogateescape', newline='') as text:
                reader = csv.reader(text, strict=True)
                for number, row in enumerate(reader, 1):
                    begin, previous_line = previous_line + 1, reader.line_num
                    if number in by_number:
                        if len(row) != 30:
                            raise ValueError('column mismatch')
                        for t in by_number.pop(number):
                            if t['field'] == 'inline_distinct':
                                message = DURATION.sub('', row[18], count=1)
                                match = INLINE.match(message)
                                if not match:
                                    raise ValueError('inline mismatch')
                                sql = message[match.end():]
                            else:
                                sql = row[{'sql': 24, 'internal': 21}[t['field']]]
                            sha = hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest()
                            categories, issues = diagnose(sql)
                            if (sha != t['sql_sha256'] or list(categories) != t['categories']
                                    or list(issues) != t['issues'] or origin(row) != t['origin']
                                    or begin != t['line_start'] or reader.line_num != t['line_end']):
                                raise ValueError('source location/hash mismatch')
                            outcomes = {name: probe(name, sql) for name in PARSERS}
                            results.append(dict(t, source_verified=True,
                                                sql_bytes=len(sql.encode('utf-8', 'surrogateescape')),
                                                results=outcomes))
                    if number == last_record:
                        break
                prefix_digest, prefix_bytes = hashing.sha.hexdigest(), hashing.used
        after = path.stat()
        if by_number or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('missing target or changed source')
        files.append({'cluster': key[0], 'file': key[1], 'bytes': before.st_size,
                      'historical_file_sha256': manifests[key]['sha256'],
                      'historical_full_hash_rechecked': False,
                      'read_prefix_bytes': prefix_bytes, 'read_prefix_sha256': prefix_digest,
                      'stop_record': last_record, 'source_stat_unchanged': True})
        progress.write_text(json.dumps({'completed_files': len(files), 'records': len(results)}))
    summary = {}
    for name in PARSERS:
        summary[name] = dict(Counter(r['results'][name]['state'] for r in results))
    return {'selection': 'all 140 existing census replay targets, no new full scan',
            'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            'files': files, 'records': results, 'summary': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.load(sys.stdin))))
        return
    if args.output is None:
        parser.error('--output required')
    output = args.output.resolve()
    root = args.root.resolve() if args.root else None
    if root and (root == output or root in output.parents):
        parser.error('output must be outside immutable input')
    output.mkdir(parents=True, exist_ok=True)
    from importlib.metadata import version
    versions = {name: version(name) for name in PARSERS}
    if versions != EXPECTED_VERSIONS or platform.python_version() != '3.13.16':
        raise ValueError('probe version contract mismatch')
    from pglast import parser as pg_parser
    started = time.monotonic()
    result = {'purpose': 'parser fidelity investigation; not product fingerprint acceptance',
              'python': platform.python_version(), 'versions': versions,
              'pglast_postgresql': pg_parser.get_postgresql_version(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(__file__), ROOT / 'sql_apm/sql/pg_ast.py', ROOT / 'sql_apm/sql/structure.py',
                                          ROOT / 'sql_apm/sql/lexical.py',
                                          ROOT / 'sql_apm/diagnostics/statement_census.py')},
              'limits': {'max_sql_bytes': MAX_SQL_BYTES, 'worker_timeout_seconds': TIMEOUT_SECONDS}}
    fixture = ROOT / 'tests/parser_probe/fixtures/parser-fidelity-cases.json'
    result['synthetic'] = synthetic_suite(fixture)
    (output / 'synthetic.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    if root:
        result['replay'] = replay(root, ROOT / 'docs/reports/data/statement-census-2026-09-26.json',
                                  output / 'progress.json')
    result['seconds'] = round(time.monotonic() - started, 3)
    (output / 'results.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'synthetic_cases': len(result['synthetic']['cases']),
                      'replay': result.get('replay', {}).get('summary'), 'seconds': result['seconds']}))


if __name__ == '__main__':
    main()
