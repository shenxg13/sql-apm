#!/usr/bin/env python3
"""Audit installed command entrypoints, with optional synthetic branch coverage."""
import argparse
import sys,json
from pathlib import Path
from perfkit import commands
from perfkit.core import Work,save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--dsn',required=True)
    parser.add_argument('--synthetic-branches',action='store_true')
    args=parser.parse_args()
    app,out=args.app.resolve(),args.work.resolve();out.mkdir(mode=0o700)
    work=Work(out,app.parent);trace=[];replay=[];results=[]
    selected=json.loads(args.cases.read_text());dsn=args.dsn
    for case in selected:
     for name,request in commands.operations(case):
      result=commands.command(work,app,case['id']+'/'+name,request,replay,trace,dsn);results.append(result)
     save(work.private/'trace.json',trace);save(work.public/'commands.json',results)
    # Cover conditional SQL sites using the fixture's known line-break, batch and partial-original inputs.
    sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'tests'),str(app)]
    from database import dashboard_data as data
    inputs=[('words','orders 2026-06'),('exact',selected[0]['fingerprint']),('exact','SELECT 1 AS one; SELECT 2 AS two; SELECT 3 AS three'),('exact',data.BREAK_CRLF.replace('\r\n','\n')),('exact',data.KEPT_CRLF.replace('\r\n','\n')),('exact',"INVALID\nSQL"),('exact',data.KEPT_TWO.replace('\r\n','\n').replace('\r','\n'))]
    for index,(mode,text) in enumerate(inputs if args.synthetic_branches else []):
     for kind in ('sqlfind','search','interactive'):
      if kind=='search':request=dict(kind=kind,argv=['exact','--sql',text] if mode=='exact' else ['find',text])
      elif kind=='sqlfind':request=dict(kind=kind,argv=['--mode',mode,text,'--show','s1' if 'SELECT 3 AS three' in text else '1','--view','history'])
      else:request=dict(kind=kind,argv=[],answers=[{'words':'1','exact':'3'}[mode],text,'s1' if 'SELECT 3 AS three' in text else '1','v matched','v','h','100','1','2','q'])
      results.append(commands.command(work,app,'extra-'+str(index)+'/'+kind,request,replay,trace,dsn))
     save(work.private/'trace.json',trace);save(work.public/'commands.json',results)
    work.close();print('commands',len(results),'statements',len(trace))


if __name__ == "__main__":
    main()
