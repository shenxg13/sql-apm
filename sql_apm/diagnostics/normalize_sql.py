"""Normalize SQL text/files and emit separate structural/approximate results."""
import argparse
from pathlib import Path
import sys

from sql_apm.sql.function_dictionary import DictionaryError, FunctionDictionary
from sql_apm.sql.normalization import DEFAULT_DICTIONARY, MAX_BYTES, Normalizer
from sql_apm.sql.structure import dumps


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--sql', help='SQL text; output includes the exact source')
    source.add_argument('--file', type=Path)
    ap.add_argument('--dictionary', type=Path, default=DEFAULT_DICTIONARY)
    ap.add_argument('--include-rules', action='store_true', help='include immutable rule snapshot for persistence')
    args = ap.parse_args()
    try:
        engine = Normalizer(FunctionDictionary.load(args.dictionary))
    except (DictionaryError, OSError, ValueError, TypeError):
        print(dumps({'state': 'normalization_failed', 'reason': 'configuration_invalid', 'value': None}))
        return 1
    try:
        if args.file is not None:
            with args.file.open('rb') as stream:
                sql = stream.read(MAX_BYTES + 1)
        else:
            sql = args.sql
        result = engine.normalize(sql)
    except (OSError, ValueError, TypeError, UnicodeError):
        print(dumps({'state': 'normalization_failed', 'reason': 'input_unavailable', 'value': None}))
        return 1
    if args.include_rules:
        result['rules'] = engine.rule_snapshot()
    print(dumps(result))
    if result['fingerprint']['state'] == 'reliable':
        return 0
    near = result['approximate']
    return 2 if near and near['state'] == 'available' else 1


if __name__ == '__main__':
    sys.exit(main())
