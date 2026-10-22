#!/usr/bin/env python3
"""Developer-only verification of private PG logs; output contains counts, never SQL."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re


def normalized(sql):
    return ' '.join(sql.split())


def statements(text):
    records=[]
    current=None
    header=re.compile(r'^\d{4}-\d\d-\d\d .*?\[(\d+)\] (.*?) (\S+) (?:LOG|日志):\s+statement: (.*)')
    any_header=re.compile(r'^\d{4}-\d\d-\d\d .*?\[\d+\] ')
    for line in text.splitlines():
        found=header.match(line)
        if found:
            current=dict(pid=int(found[1]),app=found[2],role=found[3],sql=found[4]);records.append(current)
        elif any_header.match(line):
            current=None
        elif current is not None:
            # PostgreSQL indents each continuation, including literal newlines.
            current['sql']+='\n'+line.removeprefix('\t')
    return records


def compare(trace,logs):
    recorded=Counter((r.get('backend_pid'),normalized(r['sql'])) for r in trace)
    available=Counter((r['pid'],normalized(r['sql'])) for r in logs)
    global_available=Counter(normalized(r['sql']) for r in logs)
    missing=[]
    for (pid,sql),count in recorded.items():
        actual=available[(pid,sql)] if pid is not None else global_available[sql]
        if actual<count:
            missing.append(dict(statement_sha256=__import__('hashlib').sha256(sql.encode()).hexdigest(),required=count,observed=actual))
    return dict(recorded=sum(recorded.values()),unique=len(recorded),missing=missing,matched=not missing)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log',type=Path,required=True)
    p.add_argument('--trace',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    logs=statements(args.log.read_text())
    trace=json.loads(args.trace.read_text())
    result=compare(trace,logs)
    result['forbidden_statement_heads']=sorted({r['sql'].split()[0] for r in logs
        if r['app'].startswith('sql-apm-perfcheck') and r['sql'].split()[0].upper() not in
        ('SELECT','WITH','SET','SHOW','EXPLAIN','LOAD','BEGIN','COMMIT','ROLLBACK','/*')})
    result['source_sites']=sorted({(s['file'],s['line'],s['function']) for r in trace for s in r.get('sites',[])})
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(matched=result['matched'],recorded=result['recorded'],sites=len(result['source_sites']),
                         forbidden=result['forbidden_statement_heads'])))
    return 0 if result['matched'] and not result['forbidden_statement_heads'] else 1


if __name__=='__main__':
    raise SystemExit(main())
