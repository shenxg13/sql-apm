"""Inspect parser refusal and observation-only fingerprints of text or files."""
import argparse
import json
from pathlib import Path
import sys

from sql_apm.sql.approximate import analyze, MAX_BYTES


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--sql', help='SQL text; output contains the supplied input')
    source.add_argument('--file', type=Path, help='SQL file; exact bytes are preserved')
    args = ap.parse_args()
    try:
        if args.file is not None:
            with args.file.open('rb') as stream:
                sql = stream.read(MAX_BYTES + 1)
        else:
            sql = args.sql
        result = analyze(sql)
    except (OSError, ValueError, TypeError, UnicodeError):
        print(json.dumps({'state': 'failed', 'reason': 'input_unavailable'}))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    if result['parser_state'] == 'parsed':
        return 0
    approx = result['approximate']
    return 2 if approx and approx['state'] == 'available' else 1


if __name__ == '__main__':
    sys.exit(main())
