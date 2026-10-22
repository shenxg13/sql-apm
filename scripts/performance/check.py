#!/usr/bin/env python3
"""SQL APM v1.1.0 performance inspection; production data stays on the host."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from perfkit import VERSION
from perfkit.core import Work, Session, Refused, save, check_versions, idle, install_signals, digest, serializable
from perfkit import cases, collect, commands, dashboards, pack, plans


def parser():
    result=argparse.ArgumentParser(description='只读性能检查；main、system、pack 分别执行。')
    result.add_argument('part',choices=['main','host','inventory','cases','timing','system','plans','focus','pack'])
    result.add_argument('--work',type=Path,required=True)
    result.add_argument('--root',type=Path,default=Path('/data/sql-apm'))
    result.add_argument('--app',type=Path)
    result.add_argument('--pgdata',type=Path)
    result.add_argument('--dashboards',type=Path)
    result.add_argument('--dsn',help='测试时覆盖只读连接；密码仍放密码文件')
    result.add_argument('--admin-dsn',help='测试时覆盖本地管理员连接')
    result.add_argument('--extra-cases',type=Path)
    result.add_argument('--case-id',action='append',default=[],help='仅计时指定编号，可重复；默认全部')
    result.add_argument('--module-dir',type=Path,default=Path(__file__).resolve().parent/'module')
    result.add_argument('--fingerprint-url',default='http://127.0.0.1:3001')
    result.add_argument('--limit-seconds',type=int,default=600)
    result.add_argument('--cases-limit-seconds',type=int,default=600,
                        help='每集群选用例预算，1..3600 秒；不改变普通计时超时')
    result.add_argument('--at',help='固定 ISO 时间，用于两次测试／浏览器对照；默认当前时间')
    return result


def verify_files(app,directory):
    release=json.loads((app/'RELEASE.json').read_text())
    for name,expected in release['files'].items():
        if name.startswith(('sql_apm/','rules/','grafana/dashboards/')) and digest(app/name)!=expected:
            raise Refused('installed_product_file_mismatch')
    for name in ('mpp-search.json','mpp-detail.json','mpp-list.json','mpp-status.json'):
        if digest(directory/name)!=digest(app/'grafana/dashboards'/name):
            raise Refused('installed_dashboard_file_mismatch')


def timed(args,work,dsn):
    selected=json.loads((work.private/'cases.json').read_text())
    available={case['id'] for case in selected}
    requested=set(args.case_id)
    if requested-available:
        raise Refused('unknown_case_id')
    if requested:
        selected=[case for case in selected if case['id'] in requested]
    documents=dashboards.load(args.dashboards)
    replay,trace=[],[]
    output=dict(pages=[],commands=[],http=[],coverage=dict(
        mode='partial' if len(selected)<len(available) else 'all',
        available_cases=len(available),selected_cases=[case['id'] for case in selected],repetitions=3))
    now=datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc).replace(microsecond=0)
    if now.tzinfo is None:
        raise Refused('time_requires_timezone')
    written={'replay':0,'trace':0,'pages':0,'commands':0,'http':0}
    last_checkpoint=[0.0]
    for name in written:
        (work.private/(name+'.json')).unlink(missing_ok=True)
        (work.private/(name+'.jsonl')).write_bytes(b'')
    save(work.public/'timing.json',output)
    def checkpoint(force=False):
        # Append the newly completed records. Rewriting the whole SQL trace after
        # every command makes the inspector itself a substantial disk workload.
        for name,values in (('replay',replay),('trace',trace),*output.items()):
            if name not in written:
                continue
            fresh=values[written[name]:]
            if fresh:
                with (work.private/(name+'.jsonl')).open('a',encoding='utf-8') as stream:
                    for row in fresh:
                        stream.write(json.dumps(row,ensure_ascii=False,default=serializable,separators=(',',':'))+'\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                written[name]=len(values)
            if force and name in ('replay','trace'):
                save(work.private/(name+'.json'),values)
        if force:
            save(work.public/'timing.json',output)
        if force or time.monotonic()-last_checkpoint[0]>=15:
            save(work.private/'progress.json',dict(counts=written,updated_at=datetime.now().astimezone()))
            last_checkpoint[0]=time.monotonic()
    with Session(dsn) as session:
        idle(session)
        before=collect.health(session)
    started=time.monotonic()
    try:
        with collect.Sampler(work.public/'samples.json',work.private/'samples.jsonl'):
            for case in selected:
                for repeat in range(1,4):
                    stem=case['id']+'/run-'+str(repeat)
                    for item in case['inputs']:
                        response=None
                        if item['mode']=='exact':
                            record,response=commands.fingerprint(case,item,args.fingerprint_url)
                            record['item']=stem+'/http/'+item['kind']
                            output['http'].append(record)
                        targets=['mpp-search']
                        if item is case['inputs'][0]:
                            targets+=['mpp-detail','mpp-list','mpp-status']
                        for uid in targets:
                            document=documents[uid]
                            start,end=dashboards.bounds(document,now)
                            with Session(dsn) as session:
                                result,_=dashboards.page(session,document,dashboards.overrides(case,item,response),
                                    start,end,stem+'/'+uid+'/'+item['kind'],replay,trace)
                            output['pages'].append(result)
                            checkpoint()
                    for name,request in commands.operations(case):
                        output['commands'].append(commands.command(work,args.app,stem+'/'+name,request,replay,trace,dsn))
                        checkpoint()
    finally:
        output['seconds']=round(time.monotonic()-started,6)
        checkpoint(force=True)
        with Session(dsn) as session:
            after=collect.health(session)
        save(work.public/'deltas.json',dict(before=before,after=after,delta=collect.delta(before,after)))


def run(args):
    os.umask(0o007)
    install_signals()
    args.root=args.root.resolve()
    args.app=(args.app or args.root/'app').resolve()
    args.pgdata=args.pgdata or args.root/'pgdata'
    args.dashboards=args.dashboards or args.root/'grafana/dashboards/mpp'
    if not 1<=args.limit_seconds<=3600:
        raise Refused('limit_must_be_1_to_3600_seconds')
    if not 1<=args.cases_limit_seconds<=3600:
        raise Refused('cases_limit_must_be_1_to_3600_seconds')
    work=Work(args.work,args.root)
    try:
        if args.part=='pack':
            pack.package(work,Path(__file__).resolve().parent)
            return
        from psycopg2.extensions import make_dsn,parse_dsn
        dsn=args.dsn or make_dsn(host=str(args.root/'socket'),port=5432,dbname='sql_apm',user='sql_apm_ro',
                                passfile=os.environ.get('PGPASSFILE',str(args.root/'private/pgpass')))
        admin=args.admin_dsn or make_dsn(host=str(args.root/'socket'),port=5432,dbname='sql_apm',user='postgres')
        for value in (dsn,admin):
            if 'password' in parse_dsn(value):
                raise Refused('inline_password_forbidden')
        is_admin=args.part in ('system','plans','focus')
        if not is_admin and parse_dsn(dsn).get('user')!='sql_apm_ro':
            raise Refused('timing_requires_sql_apm_ro')
        verify_files(args.app,args.dashboards)
        with Session(admin if is_admin else dsn) as session:
            identity=check_versions(args.app,session)
        identity.update(toolkit_version=VERSION)
        save(work.public/'identity.json',identity)
        if args.part in ('main','host'):
            with work.section('host'):
                save(work.public/'host.json',collect.host_static(args.pgdata,work))
        if args.part in ('main','inventory'):
            with work.section('inventory'),Session(dsn) as session:
                save(work.public/'inventory.json',collect.inventory(session,work,args.pgdata))
        if args.part in ('main','cases'):
            with work.section('cases'),Session(dsn) as session:
                idle(session)
                cases.choose(session,work,args.extra_cases,args.cases_limit_seconds)
        if args.part in ('main','timing'):
            with work.section('timing'):
                timed(args,work,dsn)
        if args.part=='system':
            with work.section('system'),Session(admin) as session:
                save(work.public/'health.json',collect.health(session))
                save(work.public/'settings.json',collect.settings(session))
                # PGDATA normally belongs to postgres. Complete those size fields
                # in the account that can read them; do not require sudo for sfmon.
                target=work.public/'inventory.json'
                if target.exists():
                    inventory=json.loads(target.read_text())
                    inventory['directories']={name:collect.directory_bytes(args.pgdata/name)
                                              for name in inventory['directories']}
                    save(target,inventory)
        if args.part in ('system','plans'):
            with work.section('plans'),Session(admin,args.limit_seconds*1000) as session:
                idle(session)
                module=plans.enable(session,args.module_dir)
                result=dict(module=module,items=[])
                if module['state']=='loaded':
                    allowed=plans.names(session)
                    source=work.private/'replay.json'
                    unique={}
                    for item in json.loads(source.read_text()) if source.exists() else []:
                        unique.setdefault(item['sql'],[]).append(item['item'])
                    print(f'普通计划：{len(unique)} 条；每条上限 {args.limit_seconds} 秒；'
                          f'最坏查询预算 {len(unique)*args.limit_seconds} 秒', flush=True)
                    journal=work.private/'plans.jsonl'
                    journal.write_bytes(b'')
                    save(work.public/'plans.json',result)
                    try:
                        for sql,items in unique.items():
                            record=plans.actual(session,sql,items[0],allowed)
                            record['same_statement_items']=items
                            result['items'].append(record)
                            with journal.open('a',encoding='utf-8') as stream:
                                stream.write(json.dumps(record,ensure_ascii=False,default=serializable,separators=(',',':'))+'\n')
                                stream.flush()
                                os.fsync(stream.fileno())
                            save(work.private/'plans-progress.json',dict(completed=len(result['items']),total=len(unique)))
                    finally:
                        save(work.public/'plans.json',result)
                save(work.public/'plans.json',result)
        if args.part in ('system','focus'):
            from perfkit.focus import run as focus
            with work.section('focus'),Session(admin,args.limit_seconds*1000) as session:
                idle(session)
                focus(args,work,session)
    finally:
        work.close()


def main():
    try:
        run(parser().parse_args())
        return 0
    except KeyboardInterrupt:
        print('已停止；已完成结果保留，可按部分重跑。',file=sys.stderr)
        return 130
    except Refused as error:
        print('停止：'+str(error),file=sys.stderr)
        return 2
    except Exception as error:
        # Driver and filesystem error text can contain SQL, addresses or credentials.
        print('该部分未完成：'+type(error).__name__+'；检查配置及本地状态文件。',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
