#!/usr/bin/env python3
"""Generate the v0.2.0 developer baseline with an isolated installed candidate."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from acceptance import product_files


def collect_baseline(metadata, out):
    """Read exactly the nine task records, never adjacent resource sidecars."""
    baseline=dict(program_commit=metadata['commit'],product_sha256=product_files(metadata),selection={},examples={})
    for cluster,total in [('119',5),('120',4)]:
        for step in range(total):
            name=cluster+'-'+str(step)
            document=json.loads((out/'tasks'/(name+'.json')).read_text())
            if not document['passed'] or document['program_verification']['commit']!=metadata['commit']:
                raise ValueError('task evidence failed or belongs to another candidate: '+name)
            baseline['selection'][name]=document['selection']
    for case in ('window','threshold','template','exclusion','retention','workers','import'):
        document=json.loads((out/'guide'/(case+'.json')).read_text())
        if not document['passed'] or document['program_commit']!=metadata['commit']:
            raise ValueError('guide evidence failed or belongs to another candidate: '+case)
        baseline['examples'][case]=document['comparable']
    with (out/'v020-development-baseline.json').open('x') as file:
        file.write(json.dumps(baseline,sort_keys=True,indent=2)+'\n')
    return baseline


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,required=True)
    parser.add_argument('--verification-root',type=Path)
    parser.add_argument('--logs',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--collect-only',action='store_true',help='collect completed, passing records without replaying any task')
    args=parser.parse_args()
    for name in ('app_root','verification_root','logs','output','pg_bin'):
        if getattr(args,name) is not None:
            setattr(args,name,getattr(args,name).resolve())
    if args.collect_only:
        from verify_package import verify
        verify(args.app_root,installed=True)
        collect_baseline(json.loads((args.app_root/'RELEASE.json').read_text()),args.output)
        print(json.dumps(dict(collected=True,nine_tasks=9,examples=7)),flush=True)
        return
    if args.verification_root is None or args.logs is None:
        parser.error('--verification-root and --logs are required for execution')
    args.output.mkdir(parents=True,exist_ok=False,mode=0o700)
    os.umask(0o077)
    app,kit,out,pg=args.app_root,args.verification_root,args.output,args.pg_bin
    metadata=json.loads((app/'RELEASE.json').read_text())
    assert (kit/'PROGRAM_COMMIT').read_text().strip()==metadata['commit']
    python=app/'.venv/bin/python'
    env={key:value for key,value in os.environ.items() if not key.startswith('PG')}
    env.update(SQL_APM_APP_ROOT=str(app),PYTHONPATH=str(app),PGCONNECT_TIMEOUT='5')
    data,socket=out/'pgdata',out/'socket';socket.mkdir(mode=0o700)
    def run(label,words,cwd=app):
        tick=time.monotonic()
        with (out/(label+'.log')).open('x') as stream:
            result=subprocess.run([str(w) for w in words],cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(label+' failed; output and database preserved')
        print(json.dumps(dict(step=label,seconds=round(time.monotonic()-tick,3))),flush=True)
    started=False
    try:
        run('package',[python,app/'scripts/deployment/verify_package.py','--installed'])
        run('prepare',[python,kit/'scripts/deployment/rehearsal.py','prepare','--logs',args.logs,'--output',out/'config'])
        run('initdb',[pg/'initdb','-D',data,'-U','apm_test_admin','--auth-local=trust','--auth-host=reject',
                     '--encoding=UTF8','--locale=C'])
        with (data/'postgresql.conf').open('a') as file:
            file.write("\nlisten_addresses=''\nport=55473\nunix_socket_directories='"+str(socket)+"'\n"
                "shared_buffers='512MB'\nwork_mem='16MB'\nmaintenance_work_mem='128MB'\n"
                "max_connections=30\nmax_wal_size='2GB'\nmin_wal_size='256MB'\n"
                "timezone='Asia/Shanghai'\nlog_statement='none'\nlog_min_error_statement='panic'\n")
        run('pg-start',[pg/'pg_ctl','-D',data,'-l',out/'server.log','-w','start']);started=True
        env['SQL_APM_DSN']='host='+str(socket)+' port=55473 dbname=sql_apm user=sql_apm'
        init=[app/'scripts/db/initialize.sh','all','--host',socket,'--port','55473','--pg-bin',pg,
              '--admin-user','apm_test_admin','--admin-database','postgres']
        run('initialize',init)
        for cluster,total in [('119',5),('120',4)]:
            for step in range(total):
                run('task-'+cluster+'-'+str(step),[python,kit/'scripts/deployment/rehearsal.py','run',
                    '--cluster',cluster,'--step',step,'--config',out/'config','--records',out/'tasks',
                    '--data-root',out,'--workers','4','--program-commit',metadata['commit']])
        for case in ('window','threshold','template','exclusion','retention','workers','import'):
            run('guide-'+case,[python,kit/'scripts/deployment/guide_examples.py','--app-root',app,
                '--config',out/'config','--records',out/'guide','run',case])
        run('schema-check',[app/'scripts/db/initialize.sh','check','--host',socket,'--port','55473','--pg-bin',pg])
        collect_baseline(metadata,out)
        print(json.dumps(dict(passed=True,nine_tasks=9,examples=7)),flush=True)
    finally:
        if started or (data/'postmaster.pid').exists():
            run('pg-stop',[pg/'pg_ctl','-D',data,'-m','fast','-w','stop'])
        # Preserve private data and diagnostics on success and failure; no automatic replay.


if __name__=='__main__':main()
