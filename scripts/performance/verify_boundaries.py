#!/usr/bin/env python3
"""Developer-only fault injection against an explicitly authorized private fixture."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import psycopg2
from perfkit import cases,collect,plans
from perfkit.core import Work,Session,Refused,check_versions,idle,measure,save,digest


def refused(operation,reason):
    try:
        operation()
    except Refused as e:
        assert str(e)==reason,(str(e),reason)
        return
    raise AssertionError('expected refusal: '+reason)


def child(spec):
    """Delay a real operation boundary so SIGINT reaches cleanup deterministically."""
    import check
    check.__file__=str(Path(spec['toolkit'])/'check.py')
    part=spec['part']; original=Work.section; active=[False]
    @contextmanager
    def section(self,name):
        with original(self,name):
            if name==part:
                active[0]=True
            yield
    Work.section=section
    query=Session.query
    def delayed(self,statement,params=None):
        if active[0]:
            active[0]=False
            Path(spec['ready']).touch()
            return query(self,'SELECT pg_sleep(30)')
        return query(self,statement,params)
    Session.query=delayed
    if part=='host':
        def host(*unused):
            Path(spec['ready']).touch();time.sleep(30)
        collect.host_static=host
    if part=='pack':
        import perfkit.pack
        original_scan=perfkit.pack.scan
        def scan(*args):
            Path(spec['ready']).touch();time.sleep(30);return original_scan(*args)
        perfkit.pack.scan=scan
    raise SystemExit(check.main())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dsn',required=True)
    p.add_argument('--admin-dsn',required=True)
    p.add_argument('--app',type=Path,required=True)
    p.add_argument('--pgdata',type=Path,required=True)
    p.add_argument('--fixture-work',type=Path,required=True)
    p.add_argument('--toolkit',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-private-fixture-writes',action='store_true',required=True)
    args=p.parse_args();args.output.mkdir(mode=0o700)
    # This verifier is deliberately not shipped in the production toolkit.
    assert args.pgdata.resolve().is_relative_to(Path.cwd()/'var/issue70')
    report={}; pg_files={name:digest(args.pgdata/name) for name in ('postgresql.conf','postgresql.auto.conf')}
    with Session(args.dsn) as s:
        before=collect.health(s)
        assert s.query('SHOW transaction_read_only')[0]['transaction_read_only']=='on'
        refused(lambda:Session(args.dsn).__enter__(),'concurrent_connection')
        work=Work(args.output,Path('/nonexistent-product'))
        selected=cases.choose(s,work);raw=(work.private/'cases.json').read_bytes()
        cases.choose(s,work);assert raw==(work.private/'cases.json').read_bytes()
        assert len({c['scope_id'] for c in selected})==3
        report['deterministic_three_clusters']=True
        extra=args.output/'extra.json'
        save(extra,[dict(cluster=selected[0]['scope_id'],database=selected[0]['database'],user=selected[0]['execution_user'],fingerprint=selected[0]['fingerprint']),dict(cluster=selected[0]['scope_id'],input='SentinelSearch70',mode='words')])
        chosen=cases.choose(s,work,extra);assert len(chosen)==len(selected)+2
        assert chosen[-2]['identity']==selected[0]['identity']
        assert chosen[-1]['inputs'][0]['text']=='SentinelSearch70'
        report['extra_cases']=True
        with tempfile.TemporaryDirectory(dir=args.output) as directory:
            marker=Path(directory)/'RELEASE.json';save(marker,dict(version='v0.0.0'))
            refused(lambda:check_versions(Path(directory),s),'program_v1_1_0_required')
        report['program_mismatch']=True
        mismatch=args.output/'mismatch';shutil.copytree(args.toolkit/'module',mismatch)
        manifest=json.loads((mismatch/'module.json').read_text());manifest['configure']='--different';save(mismatch/'module.json',manifest)
        work.close()
    with Session(args.admin_dsn) as s:
        assert plans.enable(s,mismatch)['state']=='skipped'
        report['module_mismatch']=True
    admin=psycopg2.connect(args.admin_dsn,application_name='perfcheck-test-setup')
    proxy=Session(args.admin_dsn);proxy.db=admin
    try:
        with admin.cursor() as cur:
            cur.execute('SET search_path=sql_apm,pg_catalog')
            cur.execute("UPDATE schema_version SET version='1.11.9' WHERE version='1.12.0'")
        refused(lambda:check_versions(args.app,proxy),'schema_1_12_0_required');admin.rollback()
        report['schema_mismatch']=True
        for mode,stage in [('full','import'),('rebuild','build'),('cleanup','cleanup')]:
            with admin.cursor() as cur:
                cur.execute('SET search_path=sql_apm,pg_catalog')
                cur.execute("INSERT INTO task(task_id,scope_id,mode,state,stage) SELECT 'perfcheck-refusal',scope_id,%s,'running',%s FROM scope LIMIT 1",(mode,stage))
            refused(lambda:idle(proxy),'daily_or_product_task_running');admin.rollback()
        report['active_tasks_refused']=True
        admin.autocommit=True
        with admin.cursor() as cur:cur.execute("ALTER ROLE sql_apm_ro SET statement_timeout='5ms'")
        try:
            with Session(args.dsn) as s:
                record,_=measure(s,'SELECT pg_sleep(0.1)',label='timeout')
                assert record['state']=='timeout'
                record,_=measure(s,'SELECT 1',label='continues');assert record['state']=='ok'
        finally:
            with admin.cursor() as cur:cur.execute("ALTER ROLE sql_apm_ro SET statement_timeout='120s'")
        report['role_timeout_continues']=True
    finally:
        admin.close()
    interrupted=[]
    for part in ('host','inventory','cases','timing','system','plans','focus','pack'):
        destination=args.output/('cancel-'+part)
        shutil.copytree(args.fixture_work,destination)
        ready=args.output/(part+'.ready');spec=args.output/(part+'.json')
        save(spec,dict(part=part,ready=str(ready),toolkit=str(args.toolkit.resolve())))
        cmd=[sys.executable,'-B',str(Path(__file__).resolve()),'child',str(spec),part,'--work',str(destination),
             '--root',str(args.app.parent),'--app',str(args.app),'--dashboards',str(args.app/'grafana/dashboards'),
             '--pgdata',str(args.pgdata),'--dsn',args.dsn,'--admin-dsn',args.admin_dsn,
             '--module-dir',str(args.toolkit/'module')]
        # plans can skip a mismatched module before the delay: first admin query
        # is delayed, before LOAD, so all database sections exercise cancellation.
        with (args.output/(part+'.log')).open('w') as log:
            child_process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+20
            while not ready.exists() and child_process.poll() is None and time.monotonic()<deadline:
                time.sleep(0.05)
            assert ready.exists(),part
            child_process.send_signal(signal.SIGINT)
            assert child_process.wait(timeout=8)==130,part
        assert not list(destination.rglob('*.tmp')),part
        assert (destination/'results/cases.json').exists()
        reopened=Work(destination,args.app.parent);reopened.close()
        with Session(args.admin_dsn) as s:
            assert not s.query("SELECT 1 FROM pg_stat_activity WHERE application_name LIKE 'sql-apm-perfcheck%' AND pid<>pg_backend_pid()"),part
        interrupted.append(part)
    report['cancelled_and_clean']=interrupted
    with Session(args.dsn) as s:after=collect.health(s)
    report['stats_reset_unchanged']=all(r['stats_reset_unchanged'] for rows in collect.delta(before,after).values() if isinstance(rows,list) for r in rows)
    report['config_unchanged']=all(digest(args.pgdata/name)==value for name,value in pg_files.items())
    assert report['stats_reset_unchanged'] and report['config_unchanged']
    save(args.output/'report.json',report);print(json.dumps(report))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='child':
        spec=json.loads(Path(sys.argv[2]).read_text());sys.argv=[sys.argv[0]]+sys.argv[3:];child(spec)
    else:
        main()
