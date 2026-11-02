#!/usr/bin/env python3
"""Compare real sqlfind terminal actions on an explicitly selected private copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts/tests')]
from sqlfind_terminal import Terminal


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--app',type=Path,default=ROOT)
    p.add_argument('--stage',required=True)
    p.add_argument('--port',type=int,default=55473)
    args=p.parse_args();directory=args.directory.resolve();app=args.app.resolve()
    assert (directory/'pgdata/PG_VERSION').read_text().strip()=='17'
    cases=json.loads((directory/'cases.json').read_text())
    out=directory/(args.stage+'-terminal');out.mkdir(mode=0o700)
    dsn=f'host={directory}/socket port={args.port} dbname=sql_apm user=sql_apm_ro'
    report=[]
    for item in [i for i in cases['identities'] if i['label'] in ('120-high','120-texts')]:
        for run in range(4):
            entry=[sys.executable,'-B','-m','sql_apm','sqlfind','--mode','words','--cluster','120','--width','200']
            term=Terminal(dsn,entry=entry,env={'PYTHONPATH':str(app)})
            listing,detail='Ctrl-C 返回输入。','Ctrl-C 返回列表。'
            steps=[]
            def action(name,command,prompt):
                tick=time.monotonic();term.send(command);text=term.until(prompt,timeout=600)
                steps.append(dict(action=name,seconds=time.monotonic()-tick,sha256=hashlib.sha256(text.encode()).hexdigest()))
                (out/f'{item["label"]}-{run}-{name}.out').write_text(text)
            try:
                term.until('之后 q 退出。',timeout=60)
                action('search',item['fingerprint']+'\n',listing)
                action('detail','1\n',detail)
                action('history-repeat','h\n','可选 1-1000：')
                # Keep the existing maximum, window and ordering.
                term.send('\n');term.until('时间范围：1 默认7天 / 2 指定起止时间 [1]：');term.send('\n');term.until('历史排序：1 最新在前 / 2 最慢在前 [1]：')
                action('history-cache','\n',detail)
                action('originals','v\n',detail)
                action('originals-repeat','v\n',detail)
                action('text','f\n',detail)
                action('text-repeat','f\n',detail)
                action('list','b\n',listing)
                term.send('o\n');term.until('取消。')
                action('candidate-order','2\n',listing)
                action('detail-return','1\n',detail)
                term.send('q\n');term.finish()
            finally:term.close()
            report.append(dict(case=item['label'],run=run,steps=steps))
            print(json.dumps(dict(case=item['label'],run=run,seconds={s['action']:s['seconds'] for s in steps})),flush=True)
    with (out/'report.json').open('x') as stream:json.dump(report,stream,indent=2)


if __name__=='__main__':main()
