"""Serial command and HTTP operations; real values never appear in public records."""
import base64
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .core import Refused, save


def statement_target(sql,label):
    # Inspect the statement's leading query, before its bound business inputs.
    head=sql.split("'",1)[0]
    if re.search(r'\bmpp_(query_history|view_executions|view_records)\s*\(',head):
        return 5000
    if re.search(r'\bmpp_query_exact\s*\(',head):
        return 3000
    if re.search(r'\bmpp_(query_search|view_search|view_search_note)\s*\(',head):
        return 3000 if '/exact' in label else 10000
    return 1000


def operations(case):
    filters=['--cluster',case['scope_id']]
    identity=filters+['--database',case['database'],'--user',case['execution_user'],
                      '--fingerprint',case['fingerprint']]
    for item in case['inputs']:
        common=['--mode',item['mode'],item['text']]+filters
        yield 'sqlfind/'+item['kind']+'/candidates',dict(kind='sqlfind',argv=common)
        for view in ('detail','originals','text','history'):
            yield 'sqlfind/'+item['kind']+'/'+view,dict(kind='sqlfind',argv=common+['--show','1','--view',view])
        mode={'words':'1','passage':'2','exact':'3'}[item['mode']]
        # Real interaction state machine, with only terminal input replaced.
        answers=[mode,item['text'],'1','v','v 1','v all','i 1','t request','h','100','1','2','f','b','o','2','1','q']
        yield 'interactive/'+item['kind'],dict(kind='interactive',argv=filters,answers=answers)
        if item['mode']=='exact':
            argv=['exact','--sql',item['text']]+filters
        else:
            argv=['find',item['text'],'--mode',item['mode']]+filters
        yield 'search/'+item['kind'],dict(kind='search',argv=argv)
    for layer in ('overall','day','week','weekday','hour'):
        yield 'search/baseline/'+layer,dict(kind='search',argv=['baseline']+identity+['--layer',layer])
    for bucket in (None,'hour','day'):
        yield 'search/executions/'+str(bucket),dict(kind='search',argv=['executions']+identity+(['--bucket',bucket] if bucket else []))
    yield 'search/versions',dict(kind='search',argv=['versions']+filters)
    yield 'search/text',dict(kind='search',argv=['text','--sql-id',case['sql_id']])
    yield 'daily/status',dict(kind='daily',argv=['status'])


def command(work, app, label, request, replay, trace, default_dsn=None):
    request=dict(request,app=str(app))
    source=work.private/'command-request.json'
    destination=work.private/'command-result.json'
    save(source,request)
    destination.unlink(missing_ok=True)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    if default_dsn:
        env.setdefault('SQL_APM_DSN',default_dsn)
    started=time.monotonic()
    child=subprocess.Popen([sys.executable,'-B','-m','perfkit.command_worker',str(source),str(destination)],
                           env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        child.wait()
    except BaseException:
        child.send_signal(signal.SIGINT)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        raise
    if not destination.exists():
        raise Refused('command_worker_failed_without_record')
    result=json.loads(destination.read_text())
    result.update(item=label,wall_ms=round((time.monotonic()-started)*1000,3))
    for index, record in enumerate(result['statements']):
        sql=record.pop('sql')
        record['item']=label+'/statement/'+str(index+1)
        record['target_ms']=statement_target(sql,label)
        record['attention']=record['state']!='ok' or record['ms']>record['target_ms']
        trace.append(dict(item=record['item'],sql=sql,backend_pid=record.pop('backend_pid'),sites=record['sites']))
        if (record['ms']>min(record['target_ms'],1000) or record['state']=='timeout') and sql.lstrip().upper().startswith(('SELECT','WITH','/*')):
            replay.append(dict(item=record['item'],sql=sql))
    result['state']='timeout' if any(r['state']=='timeout' for r in result['statements']) else 'ok' if result['exit_code']==0 else 'command_error'
    result['target_ms']=5000 if '/history' in label or '/executions' in label else 1000 if any('/'+v in label for v in ('detail','baseline','daily')) else 3000 if '/exact' in label else 10000
    result['attention']=result['state']!='ok' or result['ms']>result['target_ms']
    return result


def fingerprint(case, item, url):
    body=json.dumps(dict(sql_b64=base64.b64encode(item['text'].encode()).decode(),
        cluster=case['scope_id'],database=case['database'],user=case['execution_user'])).encode()
    request=urllib.request.Request(url+'/v1/exact',data=body,headers={'Content-Type':'application/json'})
    began=time.monotonic()
    result={}
    try:
        with urllib.request.urlopen(request,timeout=125) as response:
            result=json.load(response)
            state=result.get('state','failed')
    except (OSError,ValueError,urllib.error.URLError):
        state='unavailable'
    allowed={'has_baseline','records_without_baseline','not_seen','unreliable_fingerprint','failed','unavailable','ok'}
    # Service states are fixed versioned enums, never copied unvalidated.
    if state not in allowed:
        state='other'
    ms=round((time.monotonic()-began)*1000,3)
    return dict(ms=ms,state=state,input_bytes=len(item['text'].encode()),target_ms=3000,
                attention=ms>3000 or state in ('failed','unavailable')),result
