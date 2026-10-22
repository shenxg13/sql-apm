#!/usr/bin/env python3
"""Private developer-only browser SQL capture; never include its output in a result archive."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright
from perfkit import dashboards


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True)
    p.add_argument('--password-file',type=Path,required=True)
    p.add_argument('--user',default='admin')
    p.add_argument('--cases',type=Path,required=True)
    p.add_argument('--dashboards',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--at',required=True)
    args=p.parse_args()
    args.output.mkdir(mode=0o700)
    case=json.loads(args.cases.read_text())[0]
    boards=dashboards.load(args.dashboards)
    records=[]
    with sync_playwright() as play:
        browser=play.chromium.launch()
        context=browser.new_context(viewport={'width':1920,'height':920},locale='zh-CN',timezone_id='Asia/Shanghai')
        page=context.new_page()
        page.goto(args.base+'/login')
        page.fill('input[name=user]',args.user)
        page.fill('input[name=password]',args.password_file.read_text().strip())
        page.click('button[type=submit]')
        page.wait_for_url(lambda url:'/login' not in url)
        page.evaluate("window.localStorage.setItem('grafana.navigation.docked','false')")
        for uid,board in boards.items():
            start,end=dashboards.bounds(board,datetime.fromisoformat(args.at))
            values=dashboards.overrides(case,case['inputs'][0])
            query={**{'var-'+key:value for key,value in values.items() if key in {v['name'] for v in board['templating']['list']}},
                   'from':str(int(start.timestamp()*1000)),'to':str(int(end.timestamp()*1000))}
            captured=[]
            def response(r):
                if '/api/ds/query' not in r.url:
                    return
                try:
                    request=r.request.post_data_json
                    body=r.json()
                    for sent in request.get('queries',[]):
                        if not sent.get('rawSql','').startswith('/* '+uid):
                            continue
                        result=body.get('results',{}).get(sent['refId'],{})
                        executed=[f.get('schema',{}).get('meta',{}).get('executedQueryString') for f in result.get('frames',[])]
                        captured.append(dict(request=sent,request_from=request.get('from'),request_to=request.get('to'),
                                             executed=executed,error=result.get('error'),
                                             completed_ms=(time.monotonic()-began)*1000))
                except Exception:
                    pass
            page.on('response',response)
            began=time.monotonic()
            page.goto(args.base+'/d/'+uid+'/inspect?'+urlencode(query))
            page.wait_for_load_state('networkidle',timeout=120000)
            initial_elapsed_ms=(time.monotonic()-began)*1000
            initial_ms=max((r['completed_ms'] for r in captured),default=0)
            initial_requests=len(captured)
            # Lazy panels must also enter the viewport, while keeping 1920x920.
            for _ in range(25):
                page.mouse.wheel(0,850)
                page.wait_for_timeout(300)
            page.wait_for_load_state('networkidle',timeout=120000)
            page.remove_listener('response',response)
            record=dict(dashboard=uid,requests=captured,browser_ms=max((r['completed_ms'] for r in captured),default=0),
                        initial_ms=initial_ms,initial_requests=initial_requests,
                        initial_elapsed_ms=initial_elapsed_ms,
                        elapsed_ms=(time.monotonic()-began)*1000,viewport={'width':1920,'height':920})
            records.append(record)
            (args.output/(uid+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(dict(dashboard=uid,requests=len(captured),errors=sum(bool(r['error']) for r in captured))),flush=True)
        browser.close()
    (args.output/'capture.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
