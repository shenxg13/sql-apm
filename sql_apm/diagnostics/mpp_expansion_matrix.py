#!/usr/bin/env python3
"""Synthetic structural oracle for the expanded MPP probe."""
import argparse
import json
from pathlib import Path
import sys

from pglast import parser

from sql_apm.diagnostics.mpp_adapter_probe import probe, sha_file

ROOT = Path(__file__).resolve().parents[2]

FIXTURE = ROOT / 'tests/parser_probe/fixtures/mpp-expansion-cases.json'


def evaluate():
    fixture = json.loads(FIXTURE.read_text())
    records, by_id = [], {}
    for case in fixture['cases']:
        actual = probe(case['sql'], synthetic=True)
        tree = actual.pop('tree', None)
        passed = actual['state'] == ('prototype_parsed' if case['expect'] == 'structured' else 'unsupported')
        checks = []
        for check in case['checks']:
            value = tree
            try:
                for part in check['path']:
                    value = value[part]
                equal = value == check['value']
            except (KeyError, IndexError, TypeError):
                value, equal = None, False
            checks.append(dict(check, actual=value, passed=equal))
            passed = passed and equal
        formatting = None
        if actual['state'] == 'prototype_parsed':
            sql = case['sql']
            changed = '\n/* matrix spacing */ '.join(sql[t.start:t.end + 1] for t in parser.scan(sql))
            other = probe(changed)
            formatting = other['state'] == 'prototype_parsed' and other['comparison_digest'] == actual['comparison_digest']
            passed = passed and formatting
        records.append({'id': case['id'], 'classification': case['classification'],
                        'expected': case['expect'], 'result': actual, 'checks': checks,
                        'formatting_same': formatting, 'passed': passed})
        by_id[case['id']] = actual
    relations = []
    for pair in fixture['relations']:
        left, right = by_id[pair['left']], by_id[pair['right']]
        valid = left['state'] == right['state'] == 'prototype_parsed'
        same = left.get('comparison_digest') == right.get('comparison_digest')
        relations.append(dict(pair, passed=valid and same == pair['same']))
    return {'fixture_sha256': sha_file(FIXTURE),
            'source_sha256': {str(p.relative_to(ROOT)): sha_file(p) for p in
                              (Path(__file__).resolve(), ROOT / 'sql_apm/sql/mpp_parser.py',
                               ROOT / 'sql_apm/sql/pg_ast.py', ROOT / 'sql_apm/sql/structure.py', ROOT / 'sql_apm/sql/lexical.py',
                               ROOT / 'sql_apm/diagnostics/mpp_adapter_probe.py',
                               ROOT / 'sql_apm/diagnostics/parser_fidelity.py')},
            'cases': records, 'relations': relations,
            'passed': all(r['passed'] for r in records + relations)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'passed': result['passed'], 'case_count': len(result['cases']),
                      'failed_cases': [r['id'] for r in result['cases'] if not r['passed']],
                      'failed_relations': [r for r in result['relations'] if not r['passed']]}))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
