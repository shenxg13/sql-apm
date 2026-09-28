#!/usr/bin/env python3
"""Read-only SQL category evidence, not a parser or training filter.

Only fixed category labels, counts, hashes and CSV locations leave this probe.
The caller supplies local immutable CSVs; SQL is never executed or exported.
"""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import time

from sql_apm.sql.lexical import diagnose

DURATION = re.compile(r'^duration:\s*[0-9]+(?:\.[0-9]+)?\s+ms\b\s*', re.I)
INLINE = re.compile(r'^(?:statement:|(?:parse|bind|execute|execute fetch from)\s+[^:]*:)\s*', re.I)


class DigestReader(io.RawIOBase):
    def __init__(self, stream):
        self.stream, self.sha, self.used = stream, hashlib.sha256(), 0

    def readable(self):
        return True

    def readinto(self, buffer):
        size = self.stream.readinto(buffer)
        if size:
            self.sha.update(memoryview(buffer)[:size])
            self.used += size
        return size


def origin(row):
    name = row[27].rsplit('/', 1)[-1]
    if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.(?:c|cc|cpp)', name) and re.fullmatch(r'[0-9]{1,9}', row[28]):
        return name + ':' + row[28]
    return 'OTHER'


def scan(path):
    before, started = path.stat(), time.monotonic()
    counts, groups, examples, cache = Counter(), {}, {}, {}
    previous_line = 0

    def observe(sql, field, site, number, line_start, line_end):
        key = field + '/' + site
        group = groups.setdefault(key, dict(records=0, empty=0, readable=0,
                                           categories=Counter(), shapes=Counter(), issues=Counter()))
        group['records'] += 1
        if not sql.strip():
            group['empty'] += 1
            return
        digest = hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest()
        if digest not in cache:
            if len(cache) >= 100000:
                cache.clear()
            cache[digest] = diagnose(sql)
        seq, issues = cache[digest]
        group['issues'].update(issues)
        if not issues:
            group['readable'] += 1
            group['categories'].update(seq)
            shape = 'empty_tokens' if not seq else 'single' if len(seq) == 1 else 'batch'
            group['shapes'][shape] += 1
        tags = ['category:' + x for x in set(seq)] + ['issue:' + x for x in issues]
        if len(seq) > 1:
            tags.append('batch:' + ' > '.join(seq[:8]) + (' > ...' if len(seq) > 8 else ''))
        for tag in tags:
            example_key = field + '/' + tag
            if example_key not in examples:
                examples[example_key] = dict(record=number, line_start=line_start, line_end=line_end,
                                            field=field, origin=site, sql_sha256=digest,
                                            categories=seq, issues=issues)

    with path.open('rb', buffering=0) as raw:
        digest = DigestReader(raw)
        stream = io.TextIOWrapper(io.BufferedReader(digest, 1024*1024), encoding='utf-8', errors='surrogateescape', newline='')
        reader = csv.reader(stream, strict=True)
        for number, row in enumerate(reader, 1):
            begin, previous_line = previous_line + 1, reader.line_num
            counts['records'] += 1
            if len(row) != 30:
                counts['unexpected_columns'] += 1
                continue
            site = origin(row)
            counts['duration_records'] += bool(DURATION.match(row[18]))
            observe(row[24], 'sql', site, number, begin, reader.line_num)
            if row[21].strip():
                observe(row[21], 'internal', site, number, begin, reader.line_num)
            message = DURATION.sub('', row[18], count=1)
            inline = INLINE.match(message)
            if inline:
                text = message[inline.end():]
                if text == row[24]:
                    counts['inline_equals_sql'] += 1
                else:
                    counts['inline_differs_sql'] += 1
                    observe(text, 'inline_distinct', site, number, begin, reader.line_num)
        read_bytes, sha = digest.used, digest.sha.hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or read_bytes != before.st_size:
        raise RuntimeError('input changed or scan incomplete')
    return dict(file=path.name, bytes=read_bytes, sha256=sha, counts=counts, groups=groups,
                examples=examples, seconds=round(time.monotonic()-started, 3))



def replay(root, evidence):
    """Replay only recorded locations, stopping at each file's last target."""
    targets = {}
    for target in evidence['replay_targets']:
        relative = Path(target['cluster']) / target['file']
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError('replay input outside root')
        targets.setdefault(path, []).append(target)
    results = []
    for path, selected in targets.items():
        by_number = {}
        for target in selected:
            by_number.setdefault(target['record'], []).append(target)
        before, previous_line = path.stat(), 0
        with path.open(encoding='utf-8', errors='surrogateescape', newline='') as stream:
            reader = csv.reader(stream, strict=True)
            for number, row in enumerate(reader, 1):
                begin, previous_line = previous_line + 1, reader.line_num
                if number in by_number:
                    if len(row) != 30:
                        raise ValueError('replay column mismatch')
                    for target in by_number.pop(number):
                        field = target['field']
                        if field == 'inline_distinct':
                            message = DURATION.sub('', row[18], count=1)
                            match = INLINE.match(message)
                            if not match:
                                raise ValueError('replay inline mismatch')
                            sql = message[match.end():]
                        else:
                            sql = row[{'sql': 24, 'internal': 21}[field]]
                        digest = hashlib.sha256(sql.encode('utf-8', 'surrogateescape')).hexdigest()
                        seq, issues = diagnose(sql)
                        if (digest != target['sql_sha256'] or list(seq) != target['categories']
                                or list(issues) != target['issues'] or origin(row) != target['origin']
                                or begin != target['line_start'] or reader.line_num != target['line_end']):
                            raise ValueError('replay evidence mismatch')
                        results.append(dict(cluster=target['cluster'], file=target['file'],
                                            record=number, field=field, verified=True))
                if not by_number:
                    break
        after = path.stat()
        if by_number or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('replay missing target or changed input')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--replay', type=Path, help='aggregate evidence JSON with replay_targets')
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if output == root or root in output.parents:
        parser.error('output must be outside immutable input directory')
    csv.field_size_limit(128*1024*1024)
    if args.replay:
        results = replay(root, json.loads(args.replay.read_text()))
        output.mkdir(parents=True, exist_ok=True)
        (output/'replay.json').write_text(json.dumps(results, indent=2)+'\n')
        print(json.dumps(dict(replayed=len(results), verified=True)))
        return
    paths = sorted(root.glob('*.csv*'))
    if not paths:
        parser.error('no CSV inputs')
    csv.field_size_limit(128*1024*1024)
    output.mkdir(parents=True, exist_ok=True)
    for path in paths:
        result = scan(path)
        (output/(path.name+'.json')).write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
        print(json.dumps(dict(file=path.name, records=result['counts']['records'], seconds=result['seconds'])), flush=True)


if __name__ == '__main__':
    main()
