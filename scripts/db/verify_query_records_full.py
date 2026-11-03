#!/usr/bin/env python3
"""Explicit private-copy verification for #73; originals/rows/plans stay ignored."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import psycopg2
from database.retention import digest
from verify_query_performance_full import Notices, parse_notices, plan_nodes
from verify_query_records import reconcile


def save(path,value):
    with path.open('x') as out:
        json.dump(value,out,ensure_ascii=False,sort_keys=True,default=str,indent=2)


def connect(args):
    assert (args.directory/'pgdata/PG_VERSION').read_text().strip()=='17'
    db=psycopg2.connect(host=str(args.directory/'socket'),port=args.port,dbname='sql_apm',user='apm_test_admin')
    with db,db.cursor() as cur:
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'; SET statement_timeout='20min'")
    return db


def fetch(db,sql,values=()):
    with db,db.cursor() as cur:
        cur.execute(sql,values or None)
        return cur.fetchall() if cur.description else []


def select_cases(args,db):
    path=args.directory/'cases.json'
    if path.exists():return json.loads(path.read_text())
    norm=fetch(db,"SELECT normalization_id FROM build JOIN current_version USING(build_id) WHERE current_version.scope_id='120'")[0][0]
    result=dict(norm=norm,identities=[])
    for cluster in ('119','120'):
        groups=fetch(db,"""SELECT f.value,o.database,o.execution_user,count(*) n,count(DISTINCT o.sql_id) texts,
            max(o.end_at),mpp_view_identity_token(o.scope_id,o.database,o.execution_user)
            FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id)
            WHERE o.scope_id=%s AND f.normalization_id=%s AND f.state='reliable'
                AND o.database IS NOT NULL AND o.execution_user IS NOT NULL AND o.end_at IS NOT NULL
            GROUP BY f.value,o.scope_id,o.database,o.execution_user""",(cluster,norm))
        for kind,row in [('high',max(groups,key=lambda r:(r[3],r[0]))),('low',min(groups,key=lambda r:(r[3],r[0]))),
                         ('texts',max(groups,key=lambda r:(r[4],r[0])))]:
            fp,database,user,count,texts,last,token=row
            timing=fetch(db,'SELECT timing FROM mpp_view_timings(%s,%s,%s)',(norm,fp,token))[0][0]
            build=fetch(db,'SELECT build_id FROM current_version WHERE scope_id=%s',(cluster,))[0][0]
            result['identities'].append(dict(label=cluster+'-'+kind,cluster=cluster,fingerprint=fp,database=database,user=user,
                count=count,texts=texts,last=last.isoformat(),identity=token,timing=timing,build=build))
    result['inventory']=dict(fetch(db,"SELECT 'events',count(*) FROM mpp_occurrence UNION ALL SELECT 'texts',count(*) FROM mpp_sql_text UNION ALL SELECT 'fingerprints',count(*) FROM mpp_fingerprint"))
    save(path,result)
    return result


def call(name,values):
    return 'SELECT * FROM '+name+'('+','.join(['%s']*len(values))+')',values


def matrix(c):
    n=c['norm']
    for mode,term,scope,order in itertools.product(('words','passage'),('zz_unmatched_73','select','"'),(None,'120'),('count','recent')):
        yield f'search-{mode}-{term}-{scope}-{order}',*call('mpp_view_search',[n,mode,term,scope,None,None,None,None,order])
    for i in c['identities']:
        fp,token,build,timing=i['fingerprint'],i['identity'],i['build'],i['timing']
        label=i['label']
        for name,values in [('mpp_query_hits',[n,fp]),('mpp_view_identities',[n,fp]),('mpp_view_timings',[n,fp,token]),
                ('mpp_view_versions',[n,token]),('mpp_view_sql_text',[n,fp]),('mpp_query_exact',[n,fp])]:
            yield label+'-'+name,*call(name,values)
        for layer in ('overall','day','week','weekday','hour'):
            yield label+'-statistics-'+layer,*call('mpp_view_statistics',[n,fp,token,build,layer])
        for days in (1,7,30):
            end=datetime.fromisoformat(i['last'])+timedelta(milliseconds=1)
            start=end-timedelta(days=days)
            span=[start.isoformat(),end.isoformat()]
            cases=[('exact', 'mpp_view_search',[n,'exact',fp,i['cluster'],None,None,*span]),
                   ('counts','mpp_view_counts',[n,fp,token,timing,*span,3600000]),
                   ('points','mpp_view_points',[n,fp,token,timing,*span,3600000]),
                   ('compare','mpp_view_compare',[n,fp,token,timing,build,*span]),
                   ('timeline','mpp_query_timeline',[n,fp,i['cluster'],i['database'],i['user'],*span,'day'])]
            for order in ('count','median','slowest'):
                cases.append(('texts-'+order,'mpp_view_texts',[n,fp,token,timing,build,*span,order,'words','select']))
            for order in ('latest','slowest'):
                cases.append(('executions-'+order,'mpp_view_executions',[n,fp,token,timing,build,*span,None,None,None,None,order,200]))
            cases.append(('history','mpp_query_history',[n,fp,i['cluster'],i['database'],i['user'],*span,200,None,build]))
            for suffix,name,values in cases:yield f'{label}-{days}d-{suffix}',*call(name,values)
    anchor=max(datetime.fromisoformat(i['last']) for i in c['identities'])+timedelta(milliseconds=1)
    for days,scope,order,timings in itertools.product((1,7,30),(None,'120'),('count','total','mean','slowest','not_success'),('', 'request,execute_first')):
        yield f'range-{days}-{scope}-{order}-{timings}',*call('mpp_view_ranking',[n,(anchor-timedelta(days=days)).isoformat(),anchor.isoformat(),scope,None,None,timings,order,500])
    for scope,order in itertools.product((None,'120'),('samples','p50','p95','p99','max','mean')):
        yield f'baseline-{scope}-{order}',*call('mpp_view_baseline_ranking',[n,scope,None,None,'',order,0,500])


def render_panel(app,panel,c,identity=None,scope="*",order="p95"):
    dashboard='mpp-detail' if identity else 'mpp-list'
    doc=json.loads((app/'grafana/dashboards'/f'{dashboard}.json').read_text())
    target=next(p for p in doc['panels'] if p.get('id')==panel)['targets'][0]
    if 'panelId' in target:target=next(p for p in doc['panels'] if p.get('id')==target['panelId'])['targets'][0]
    sql=target['rawSql']
    i=identity or c['identities'][0]
    values=dict(norm=c['norm'],fp=i['fingerprint'],identity=i['identity'],timing=i['timing'],sid='',sqlid='',part='1',
                version=i['build'],mode='exact',q='',hit='',dmin='',dmax='',layer='overall',list_order='latest',
                text_order='count',status='',cluster=scope,database='*',user='*',timings='',base_order=order,min_samples='',limit='500',__url_time_range='')
    from urllib.parse import urlencode
    def replace(m):
        key,_,formatter=m[1].partition(':');value=values[key]
        if formatter=='queryparam':value=urlencode({'var-'+key:value})
        return value.replace("'","''")
    return re.sub(r'\$\{([^}]+)\}',replace,sql)


def cases(args,c):
    yield from matrix(c)
    for i in c['identities']:
        yield i['label']+'-panel2',render_panel(args.app,2,c,i),[]
    # Both displayed panels use the complete ranked query, including URL dates.
    for panel in (6,7):
        for scope,order in itertools.product(('*','120'),('samples','p50','p95','p99','max','mean')):
            import base64
            token='*' if scope=='*' else base64.b64encode(scope.encode()).decode()
            sql=render_panel(args.app,panel,c,scope=token,order=order)
            yield f'panel{panel}-{scope}-{order}',sql,[]


def rows(args):
    with closing(connect(args)) as db:
        c=select_cases(args,db)
        path=args.directory/(args.stage+'-rows.jsonl.gz')
        with gzip.open(path,'xt') as out:
            for label,sql,values in cases(args,c):
                if args.panels_only and not label.startswith('panel'):continue
                if args.case_pattern and not re.search(args.case_pattern,label):continue
                tick=time.monotonic();result=fetch(db,sql,values)
                out.write(json.dumps(dict(label=label,rows=result,seconds=time.monotonic()-tick),default=str,sort_keys=True)+'\n');out.flush()
                print(json.dumps(dict(case=label,rows=len(result),seconds=round(time.monotonic()-tick,3))),flush=True)


def timing(args):
    out=args.directory/args.stage;out.mkdir(mode=0o700)
    with closing(connect(args)) as db:
        c=select_cases(args,db)
        selected=[x for x in cases(args,c) if (
            x[0] in {'search-words-select-None-count','search-passage-select-None-count',
                     'search-words-zz_unmatched_73-None-count','120-high-mpp_query_exact',
                     '120-high-mpp_query_hits','120-high-mpp_view_identities','120-high-mpp_view_timings',
                     '120-high-statistics-overall','120-high-statistics-day'}
            or (x[0].startswith(('120-high-7d-','120-texts-7d-')))
            or re.match(r'range-(1|7)-(None|120)-(count|slowest)-$',x[0])
            or x[0].endswith('-panel2')
            or re.match(r'panel6-(\*|120)-(p95|samples)$',x[0]))]
        if args.case_pattern:
            selected=[x for x in cases(args,c) if re.search(args.case_pattern,x[0])]
        for label,sql,values in selected:
            durations=[]
            for _ in range(4):
                tick=time.monotonic();result=fetch(db,sql,values);durations.append(time.monotonic()-tick)
            safe=hashlib.sha256(label.encode()).hexdigest()[:16]
            with (out/(safe+'-plans.log')).open('x') as stream:
                db.notices=Notices(stream)
                fetch(db,"LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; SET auto_explain.log_analyze=on; "
                    "SET auto_explain.log_buffers=on; SET auto_explain.log_nested_statements=on; SET auto_explain.log_timing=off; "
                    "SET auto_explain.log_format=json; SET auto_explain.log_level=notice; SET client_min_messages=notice")
                if args.plan_timing:
                    fetch(db,'SET auto_explain.log_timing=on')
                fetch(db,sql,values)
                fetch(db,'SET auto_explain.log_min_duration=-1')
                db.notices=[]
            nodes=[node for node in plan_nodes(parse_notices((out/(safe+'-plans.log')).read_text()))
                   if node.get('Relation Name','').startswith(('mpp_occurrence','mpp_query_','mpp_sql_text'))]
            save(out/(safe+'.json'),dict(label=label,first=durations[0],warm=durations[1:],median=statistics.median(durations[1:]),rows=len(result),nodes=nodes))
            print(json.dumps(dict(case=label,seconds=durations)),flush=True)


def snapshot(args):
    with closing(connect(args)) as db:
        names=[r[0] for r in fetch(db,"SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")]
        result={}
        for name in names:
            if name.startswith('mpp_query_'):continue
            result[name]=digest(db,name,('query_key',) if name in ('scope','analysis','mpp_normalization') else (),
                                "WHERE version<>'1.14.0'" if name=='schema_version' else '')
            print(json.dumps(dict(table=name,rows=result[name]['rows'])),flush=True)
        save(args.directory/(args.stage+'-tables.json'),result)


def compare(args):
    with gzip.open(args.before,'rt') as a,gzip.open(args.after,'rt') as b:
        count=0
        filtered=lambda lines:(line for line in lines if not args.case_pattern or re.search(args.case_pattern,json.loads(line)['label']))
        for left,right in itertools.zip_longest(filtered(a),filtered(b)):
            assert left and right,'case count mismatch'
            x,y=json.loads(left),json.loads(right)
            assert x['label']==y['label'] and x['rows']==y['rows'],x['label']
            count+=1
    print(json.dumps(dict(equal=True,cases=count)))


def time_search(args):
    """Old frozen fuzzy SQL remains an independent oracle on unchanged sources."""
    old=(ROOT/'sql_apm/storage/versions/1.13.0.sql').read_text()
    a=old.index('CREATE OR REPLACE FUNCTION mpp_query_fuzzy(')
    b=old.index('END $function$;',a)+len('END $function$;')
    reference=old[a:b].replace('FUNCTION mpp_query_fuzzy(', 'FUNCTION issue73_reference_fuzzy(',1)
    with closing(connect(args)) as db:
        c=select_cases(args,db);i=next(x for x in c['identities'] if x['label']=='120-texts')
        end=datetime.fromisoformat(i['last'])+timedelta(milliseconds=1)
        start=end-timedelta(days=7)
        fetch(db,reference)
        report=[]
        try:
            for mode,term,scope,order,bound in itertools.product(('words','passage'),
                    ('zz_unmatched_73','select','"'),(None,'120'),('count','recent'),('start','end','both')):
                values=[c['norm'],term,scope,None,None,
                        start.isoformat() if bound!='end' else None,end.isoformat() if bound!='start' else None,order,mode]
                expected=fetch(db,*call('issue73_reference_fuzzy',values))
                actual=fetch(db,*call('mpp_query_fuzzy',values))
                assert actual==expected,(mode,term,scope,order,bound)
                report.append(dict(mode=mode,term=term,scope=scope,order=order,bound=bound,rows=len(actual),equal=True))
                print(json.dumps(dict(cases=len(report),equal=True)),flush=True)
        finally:
            fetch(db,'DROP FUNCTION issue73_reference_fuzzy')
        save(args.directory/(args.stage+'-time-search.json'),report)


def supplement(args):
    """Paired frozen fuzzy oracle, real CLI and HTTP warm timings on one corpus."""
    r = args.directory
    c = json.loads((r / 'cases.json').read_text())
    i = next((x for x in c['identities'] if x['label'] == '120-high'))
    end = datetime.fromisoformat(i['last']) + timedelta(milliseconds=1)
    start = end - timedelta(days=7)
    old = (ROOT / 'sql_apm/storage/versions/1.13.0.sql').read_text()
    a = old.index('CREATE OR REPLACE FUNCTION mpp_query_fuzzy(')
    b = old.index('END $function$;', a) + len('END $function$;')
    old = old[a:b].replace('FUNCTION mpp_query_fuzzy(', 'FUNCTION issue73_reference_fuzzy(', 1)
    out = r / args.stage
    out.mkdir(mode=0o700, exist_ok=args.before is not None)
    report = []
    if args.before is not None:
        report = [json.loads(line) for line in args.before.read_text().splitlines() if line.startswith('{"label":')]
        assert {x['label'] for x in report} == {'words-120-time-common', 'words-120-time-nohit', 'passage-all-common', 'words-120-common'}
        assert all(x['equal'] for x in report)
    with closing(connect(args)) as db:
        if args.before is None:
            fetch(db, old)
        try:
            cases = [('words-120-time-common', 'select', 'words', '120', start.isoformat(), end.isoformat()), ('words-120-time-nohit', 'zz_unmatched_73', 'words', '120', start.isoformat(), end.isoformat()), ('passage-all-common', 'select', 'passage', None, None, None), ('words-120-common', 'select', 'words', '120', None, None)]
            for label, term, mode, scope, lo, hi in cases:
                if any(x['label'] == label for x in report):
                    continue
                values = [c['norm'], term, scope, None, None, lo, hi, 'count', mode]
                result = {}
                reference = None
                for phase, name in [('before', 'issue73_reference_fuzzy'), ('after', 'mpp_query_fuzzy')]:
                    durations = []
                    for _ in range(4):
                        tick = time.monotonic()
                        rows = fetch(db, *call(name, values))
                        durations.append(time.monotonic() - tick)
                    if reference is None:
                        reference = rows
                    else:
                        assert rows == reference, label
                    result[phase] = dict(first=durations[0], warm=durations[1:], median=statistics.median(durations[1:]))
                with (out / (label + '-plans.log')).open('x') as stream:
                    db.notices = Notices(stream)
                    fetch(db, "LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; SET auto_explain.log_analyze=on; SET auto_explain.log_buffers=on; SET auto_explain.log_nested_statements=on; SET auto_explain.log_timing=on; SET auto_explain.log_format=json; SET auto_explain.log_level=notice; SET client_min_messages=notice")
                    fetch(db, *call('mpp_query_fuzzy', values))
                    fetch(db, 'SET auto_explain.log_min_duration=-1')
                    db.notices = []
                report.append(dict(label=label, equal=True, **result))
                print(json.dumps(report[-1]), flush=True)
        finally:
            if args.before is None:
                fetch(db, 'DROP FUNCTION issue73_reference_fuzzy')
        env = {**os.environ, 'PYTHONPATH': str(ROOT), 'SQL_APM_DSN': f'host={r}/socket port={args.port} dbname=sql_apm user=sql_apm_ro'}
        elapsed = []
        hashes = []
        for run in range(4):
            tick = time.monotonic()
            p = subprocess.run([sys.executable, '-m', 'sql_apm', 'sqlfind', '--mode', 'words', '--cluster', '120', '--show', '1', '--limit', '200'], input=b'select', env=env, cwd=ROOT, capture_output=True)
            elapsed.append(time.monotonic() - tick)
            (out / f'sqlfind-common-{run}.out').write_bytes(p.stdout)
            assert p.returncode == 0, (p.returncode, p.stderr[:300])
            hashes.append(hashlib.sha256(p.stdout).hexdigest())
        assert len(set(hashes)) == 1
        report.append(dict(label='sqlfind-common-120-detail', first=elapsed[0], warm=elapsed[1:], median=statistics.median(elapsed[1:]), equal=True))
        print(json.dumps(report[-1]), flush=True)
    from verify_views import Service, post, sql_b64
    http = Service(f'host={r}/socket port={args.port} dbname=sql_apm user=sql_apm_ro')
    try:
        with closing(connect(args)) as db:
            for item in c['identities']:
                raw = fetch(db, 'SELECT t.text FROM mpp_query_structure s JOIN mpp_normalization n ON n.query_key=s.rule_key JOIN mpp_sql_text t ON t.sql_id=s.example_sql_id WHERE n.normalization_id=%s AND s.fingerprint_value=%s', (c['norm'], item['fingerprint']))[0][0]
                elapsed = []
                answers = []
                for _ in range(4):
                    tick = time.monotonic()
                    status, answer = post(http.port, dict(sql_b64=sql_b64(raw), cluster=item['cluster']))
                    elapsed.append(time.monotonic() - tick)
                    assert status == 200 and answer['fingerprint'] == item['fingerprint']
                    answers.append(answer)
                assert all((x == answers[0] for x in answers))
                report.append(dict(label='http-' + item['label'], first=elapsed[0], warm=elapsed[1:], median=statistics.median(elapsed[1:]), equal=True))
                print(json.dumps(report[-1]), flush=True)
    finally:
        http.stop()
    # Attribute the service's direct calls separately from HTTP transport.
    # Each nested wrapper subtracts normalization already counted by its child.
    from unittest.mock import patch
    from contextlib import ExitStack
    from sql_apm.service import fingerprint as service_module
    service = service_module.Service(f'host={r}/socket port={args.port} dbname=sql_apm user=sql_apm_ro', 'sql_apm')
    with closing(connect(args)) as db:
        for label in ('120-high', '119-texts'):
            item = next(x for x in c['identities'] if x['label'] == label)
            raw = fetch(db, """SELECT t.text FROM mpp_fingerprint f JOIN mpp_sql_text t USING(sql_id)
                WHERE f.normalization_id=%s AND f.value=%s AND f.state='reliable'
                ORDER BY t.sql_id DESC LIMIT 1""", (c['norm'], item['fingerprint']))[0][0]
            forms = [('stored', raw)]
            if '\n' in raw or '\r' in raw:
                plain = service_module.LINE_BREAK.sub('\n', raw)
                changed = plain if plain != raw else plain.replace('\n', '\r\n')
                forms.append(('line-break', changed))
            for form, text in forms:
                measurements = []
                for _ in range(4):
                    measured = dict(normalization=0.0, exact_hits=0.0, same_text=0.0, other_form=0.0)
                    def timed(fn, key, subtract_normalization=False):
                        def wrapped(*pos, **kw):
                            before = measured['normalization']; tick = time.monotonic()
                            try:
                                return fn(*pos, **kw)
                            finally:
                                seconds = time.monotonic() - tick
                                measured[key] += seconds - (measured['normalization'] - before if subtract_normalization else 0)
                        return wrapped
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(service.normalizer, 'normalize', timed(service.normalizer.normalize, 'normalization')))
                        for name, key, subtract in [('exact', 'exact_hits', True), ('same_text_id', 'same_text', False), ('other_form', 'other_form', True)]:
                            stack.enter_context(patch.object(service_module, name, timed(getattr(service_module, name), key, subtract)))
                        tick = time.monotonic()
                        answer = service.exact(text.encode(), [item['cluster'], None, None])
                        measured['total'] = time.monotonic() - tick
                    assert answer['fingerprint'] == item['fingerprint'] and answer['exact_sql_id'] is not None
                    measurements.append(measured)
                report.append(dict(label='service-' + label + '-' + form, first=measurements[0],
                                   median={key: statistics.median(m[key] for m in measurements[1:]) for key in measurements[0]}))
                print(json.dumps(report[-1]), flush=True)
    with (out / 'report.json').open('x') as f:
        json.dump(report, f, indent=2)
    text_args = argparse.Namespace(**vars(args))
    text_args.stage = args.stage + '-texts'
    text_args.case_pattern = '^120-texts-7d-texts-count$'
    text_args.plan_timing = True
    timing(text_args)


def fallback(args):
    """Measure the preserved many-original fallback, including a nonmatching form."""
    from sql_apm.service import fingerprint as service_module
    from sql_apm.storage.ingestion import connect as application_connect
    c = json.loads((args.directory/'cases.json').read_text())
    item = next(x for x in c['identities'] if x['label']=='119-texts')
    with closing(connect(args)) as db:
        raw = fetch(db,"""SELECT t.text FROM mpp_fingerprint f JOIN mpp_sql_text t USING(sql_id)
            WHERE f.normalization_id=%s AND f.value=%s AND f.state='reliable'
            ORDER BY t.sql_id DESC LIMIT 1""",(c['norm'],item['fingerprint']))[0][0]
    assert ' ' in raw
    changed = raw.replace(' ','\n',1)
    dsn=f'host={args.directory}/socket port={args.port} dbname=sql_apm user=sql_apm_ro'
    service=service_module.Service(dsn,'sql_apm')
    totals=[];lookups=[]
    for _ in range(4):
        tick=time.monotonic();answer=service.exact(changed.encode(),[item['cluster'],None,None]);totals.append(time.monotonic()-tick)
        assert answer['fingerprint']==item['fingerprint'] and answer['exact_sql_id'] is None
    with closing(application_connect(dsn,'sql_apm')) as db:
        for _ in range(4):
            with db,db.cursor() as cur:
                tick=time.monotonic()
                assert service_module.same_text_id(cur,c['norm'],item['fingerprint'],changed) is None
                lookups.append(time.monotonic()-tick)
    save(args.directory/(args.stage+'.json'),dict(originals=item['texts'],whole=totals,same_text=lookups,
        median=statistics.median(totals[1:]),same_text_median=statistics.median(lookups[1:])))


def finder_timing(args):
    from sql_apm.cli.sqlfind import arguments, search
    from sql_apm.storage.sqlfind import Finder
    dsn=f'host={args.directory}/socket port={args.port} dbname=sql_apm user=sql_apm_ro'
    options=arguments(['--mode','words','--cluster','120'])
    finder=Finder(dsn,'sql_apm');runs=[]
    for _ in range(4):
        tick=time.monotonic();found=search(finder,b'select',options);search_seconds=time.monotonic()-tick
        tick=time.monotonic();finder.detail(found,'1',options,limit=200);detail_seconds=time.monotonic()-tick
        tick=time.monotonic();finder.detail(found,'1',options,limit=200);cache_seconds=time.monotonic()-tick
        runs.append(dict(search=search_seconds,detail=detail_seconds,cache=cache_seconds))
    save(args.directory/(args.stage+'.json'),dict(first=runs[0],warm=runs[1:],
        median={key:statistics.median(x[key] for x in runs[1:]) for key in runs[0]}))


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--panels-only',action='store_true')
    p.add_argument('--case-pattern')
    p.add_argument('--plan-timing',action='store_true',help='instrument nested node timings only in the separate plan collection')
    p.add_argument('mode',choices=('rows','timing','snapshot','reconcile','compare','time_search','supplement','fallback','finder_timing'))
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--port',type=int,default=55473)
    p.add_argument('--app',type=Path,default=ROOT)
    p.add_argument('--stage',default='after')
    p.add_argument('--before',type=Path);p.add_argument('--after',type=Path)
    args=p.parse_args();args.directory=args.directory.resolve();args.app=args.app.resolve()
    if args.mode=='reconcile':
        with closing(connect(args)) as db:save(args.directory/(args.stage+'-reconcile.json'),reconcile(db))
    else:globals()[args.mode](args)


if __name__=='__main__':main()
