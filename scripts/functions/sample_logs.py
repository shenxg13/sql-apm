#!/usr/bin/env python3
"""Read bounded CSV prefixes; emit aggregate diagnostics without production SQL/names."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sql_apm.sql.function_dictionary import FunctionDictionary
from sql_apm.diagnostics.function_probe import call_candidates, ProbeError


class LimitReached(Exception):
    pass


class BoundedLines:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.used, self.lines = stream, limit, 0, 0
        self.sha = hashlib.sha256()

    def __iter__(self):
        return self

    def __next__(self):
        if self.used >= self.limit:
            raise LimitReached()
        raw = self.stream.readline(min(self.limit-self.used+1, 1024*1024+1))
        if not raw:
            raise StopIteration
        self.used += len(raw)
        self.sha.update(raw)
        self.lines += 1
        if self.used > self.limit or len(raw) > 1024*1024:
            raise LimitReached()
        return raw.decode('utf-8', errors='surrogateescape')


def sample(path, dictionary, record_limit, byte_limit):
    before = path.stat()
    counts, reasons, known, unknown = Counter(), Counter(), Counter(), Counter()
    replay = []
    with path.open('rb') as stream:
        lines = BoundedLines(stream, byte_limit)
        reader = csv.reader(lines, strict=True)
        try:
            for number, row in enumerate(reader, 1):
                counts['csv_records'] += 1
                if len(row) != 30:
                    reasons['unexpected_csv_columns'] += 1
                elif not row[24].strip():
                    counts['empty_sql_records'] += 1
                else:
                    counts['sql_records'] += 1
                    sql = row[24]
                    try:
                        calls, excluded = call_candidates(sql)
                        counts['syntax_or_uncertain_candidates'] += excluded
                        counts['lexically_readable_records'] += 1
                        for call in calls:
                            choice = dictionary.select(**{k:v for k,v in call.items() if k!='offset'})
                            # Aggregate/window identities require a SQL resolver;
                            # report them separately, never as scalar rule hits.
                            if choice['reason']=='no_matching_rule':
                                alternatives = [dictionary.select(**{k:v for k,v in call.items() if k!='offset'}, kind=k)
                                                for k in ('aggregate','window')]
                                if any(a['reason']=='matched' for a in alternatives):
                                    choice['reason']='aggregate_or_window_requires_context'
                            counts['call_candidates'] += 1
                            reasons[choice['reason']] += 1
                            if choice['reason']=='matched':
                                counts['matched_'+choice['decision']] += 1
                                for rule_id in choice['rule_ids']:
                                    known[rule_id] += 1
                            else:
                                # Do not publish custom function/schema identifiers.
                                unknown[(call['arity'],choice['reason'])] += 1
                            if len(replay)<8:
                                replay.append(dict(record=number, physical_line_end=reader.line_num,
                                                   offset=call['offset'], reason=choice['reason'],
                                                   rule_ids=choice['rule_ids']))
                    except ProbeError as error:
                        reasons[str(error)] += 1
                        counts['unresolved_sql_records'] += 1
                if number >= record_limit:
                    counts['record_limit_reached'] = 1
                    break
        except LimitReached:
            counts['byte_or_line_limit_reached'] = 1
        except csv.Error:
            counts['csv_parse_error'] = 1
        evidence=dict(bytes_read=lines.used, physical_lines_read=lines.lines, prefix_sha256=lines.sha.hexdigest())
    after = path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise RuntimeError('input changed while sampling')
    return dict(file=path.name, file_size=before.st_size, **evidence,
                counts=dict(sorted(counts.items())), reasons=dict(sorted(reasons.items())),
                known_rule_candidate_counts=dict(sorted(known.items())),
                unresolved_candidates=[dict(arity=k[0],reason=k[1],count=v) for k,v in sorted(unknown.items())],
                replay=replay)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--records',type=int,default=2000)
    parser.add_argument('--files',type=int,default=2)
    parser.add_argument('--bytes',type=int,default=8*1024*1024)
    args=parser.parse_args()
    if not 1<=args.files<=2 or not 1<=args.records<=2000 or not 1<=args.bytes<=8*1024*1024:
        parser.error('limits: 1..2 files, 1..2000 records, 1..8388608 bytes per file')
    if args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error('output must be outside the immutable input directory')
    csv.field_size_limit(1024*1024)
    dictionary=FunctionDictionary.load(ROOT/'rules/functions/v1.json')
    result=dict(rules_version=dictionary.rules_version, dictionary_sha256=dictionary.sha256,
                method='lexical candidates, not parsed SQL or execution counts', limits=vars(args).copy(), clusters={})
    result['limits']['root']=str(args.root)
    result['limits'].pop('output')
    for cluster in ('119','120'):
        paths=sorted((args.root/cluster).glob('*.csv*'))[:args.files]
        if not paths:
            parser.error('no input files for cluster '+cluster)
        result['clusters'][cluster]=[sample(p,dictionary,args.records,args.bytes) for p in paths]
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('Wrote bounded, SQL-free sample diagnostics to',args.output)


if __name__=='__main__':
    main()
