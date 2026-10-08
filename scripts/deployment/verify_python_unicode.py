#!/usr/bin/env python3
"""Compare runtime character properties, then audit the fixed CSV input read-only.

Snapshots and raw input stay private. The comparison contains only code points,
operation results, file checksums and counts, never SQL or identity fields.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OPERATIONS = ('lower', 'upper', 'isalpha', 'isalnum', 'isdigit', 'isspace',
              'regex_space', 'regex_word', 'regex_digit', 'regex_ascii_ignorecase')
PATTERNS = [re.compile(pattern) for pattern in (r'\s', r'\w', r'\d')]
ASCII_CASE = [re.compile(chr(code), re.I) for code in range(ord('a'), ord('z') + 1)]


def properties(char):
    return [char.lower(), char.upper(), char.isalpha(), char.isalnum(), char.isdigit(),
            char.isspace(), *(bool(p.fullmatch(char)) for p in PATTERNS),
            ''.join(chr(ord('a') + i) for i, p in enumerate(ASCII_CASE) if p.fullmatch(char))]


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write('\n')


def affected_pattern(codes):
    # Collapse adjacent points: thousands of literal alternatives make scanning
    # multi-GiB logs needlessly expensive, especially for astral characters.
    ranges = []
    for code in sorted(codes):
        if ranges and code == ranges[-1][1] + 1:
            ranges[-1][1] = code
        else:
            ranges.append([code, code])
    parts = [('\\U%08x' % start) + (('-\\U%08x' % end) if end != start else '')
             for start, end in ranges]
    return re.compile('[' + ''.join(parts) + ']') if parts else None


def snapshot(path):
    with gzip.open(path, 'xt', encoding='ascii') as stream:
        stream.write(json.dumps(dict(python=platform.python_version(),
                                    unicode=unicodedata.unidata_version,
                                    operations=OPERATIONS, codepoints=0x110000)) + '\n')
        for code in range(0x110000):
            stream.write(json.dumps(properties(chr(code)), ensure_ascii=True,
                                    separators=(',', ':')) + '\n')


def compare(old, new, output):
    differences = []
    with gzip.open(old, 'rt', encoding='ascii') as left, gzip.open(new, 'rt', encoding='ascii') as right:
        before, after = json.loads(next(left)), json.loads(next(right))
        assert before['operations'] == after['operations'] == list(OPERATIONS)
        assert before['codepoints'] == after['codepoints'] == 0x110000
        for code in range(0x110000):
            a, b = next(left), next(right)
            if a != b:
                av, bv = json.loads(a), json.loads(b)
                changed = {name: dict(old=x, new=y) for name, x, y in zip(OPERATIONS, av, bv) if x != y}
                if changed:
                    differences.append(dict(codepoint='U+%04X' % code, operations=changed))
        assert not left.read() and not right.read(), 'snapshot has trailing data'
    save(output, dict(old=before, new=after, differences=differences,
                      changed_codepoints=len(differences)))


def scan(delta, logs, manifest, output):
    from sql_apm.ingestion.mpp.reader import Records
    changes = json.loads(delta.read_text())
    affected = {int(row['codepoint'][2:], 16) for row in changes['differences']}
    pattern = affected_pattern(affected)
    fixed = json.loads(manifest.read_text())
    report = dict(manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                  delta_sha256=hashlib.sha256(delta.read_bytes()).hexdigest(),
                  files=[], sql_rows=0, sql_affected_rows=0, other_affected_rows=0,
                  sql_codepoints={}, other_codepoints={}, complete=False)
    for cluster, detail in sorted(fixed['clusters'].items()):
        for item in detail['files']:
            records = Records(logs / cluster / item['file'])
            counts = dict(sql_rows=0, sql_affected_rows=0, other_affected_rows=0)
            for _, _, _, row in records:
                if row[24]:
                    counts['sql_rows'] += 1
                for text, scope in [(row[24], 'sql'), ('\n'.join(row[:24] + row[25:]), 'other')]:
                    if pattern is None or text.isascii():
                        continue
                    found = set(pattern.findall(text))
                    if found:
                        counts[scope + '_affected_rows'] += 1
                        for char in found:
                            key = 'U+%04X' % ord(char)
                            totals = report[scope + '_codepoints']
                            totals[key] = totals.get(key, 0) + 1
            assert records.sha256 == item['sha256'], 'fixed input checksum mismatch'
            report['files'].append(dict(cluster=cluster, sha256=records.sha256,
                                        records=records.count, bytes=records.byte_count, **counts))
            for key, value in counts.items():
                report[key] += value
            print(json.dumps(dict(files=len(report['files']), **counts)), flush=True)
    report['complete'] = True
    report['passed'] = not report['sql_affected_rows'] and not report['other_affected_rows']
    save(output, report)
    if not report['passed']:
        raise SystemExit('affected Unicode code points found; preserve evidence and report before proceeding')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    snap = commands.add_parser('snapshot')
    snap.add_argument('--output', type=Path, required=True)
    diff = commands.add_parser('compare')
    diff.add_argument('--old', type=Path, required=True)
    diff.add_argument('--new', type=Path, required=True)
    diff.add_argument('--output', type=Path, required=True)
    audit = commands.add_parser('scan')
    audit.add_argument('--delta', type=Path, required=True)
    audit.add_argument('--logs', type=Path, required=True)
    audit.add_argument('--manifest', type=Path, default=ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json')
    audit.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'snapshot':
        snapshot(args.output)
    elif args.command == 'compare':
        compare(args.old, args.new, args.output)
    else:
        scan(args.delta, args.logs, args.manifest, args.output)


if __name__ == '__main__':
    main()
