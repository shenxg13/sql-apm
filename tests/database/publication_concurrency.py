"""Real subprocess/session failures, entrypoint admission and nonblocking DDL."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time

from sql_apm.storage.ingestion import connect
from sql_apm.storage.tasks import Task
from sql_apm.storage.statistics import StatisticsStore
from sql_apm.storage.publication import PublicationStore,version_status


def verify_concurrency(v,dsn,root,config_path,config,snapshot):
    repo=Path(__file__).resolve().parents[2]
    training_path=root/'training.json'
    training_path.write_text(json.dumps(dict(version=1,clusters=['C1','C2'],window=dict(cutoff_date='2026-07-31'))))
    child_env=dict(os.environ,SQL_APM_DSN=dsn,INPUT=snapshot['input_id'],CONFIG=snapshot['config_id'])
    db=connect(dsn,'sql_apm')
    # A real owner holds admission while each public mutation entrypoint tries.
    with Task(db,'C1','rebuild') as task:
        commands=[['full','--config',str(config_path),'--source','S1','--batch','B1','--training-config',str(training_path)],
            ['import','--config',str(config_path),'--source','S1','--batch','B1'],
            ['rebuild','--cluster','C1','--training-config',str(training_path),'--cutoff-date','2026-07-31'],
            ['training','snapshot','--cluster','C1','--config',str(training_path),'--batch','B1'],
            ['statistics','--cluster','C1','--input',snapshot['input_id'],'--config-id',snapshot['config_id']]]
        for args in commands:
            result=subprocess.run([sys.executable,'-m','sql_apm']+args,cwd=repo,env=child_env,capture_output=True,text=True,timeout=20)
            assert result.returncode==1 and json.loads(result.stdout.splitlines()[-1])['reason']=='cluster_busy',result.stdout
            assert v.sql("SELECT state FROM task WHERE task_id='"+task.task_id+"'")=='running'
        other=connect(dsn,'sql_apm')
        with Task(other,'C2','snapshot'):
            assert v.sql("SELECT count(*) FROM task WHERE state='running'")=='2'
        other.close()
    assert v.sql("SELECT count(*) FROM task WHERE state='busy_rejected'")=='5'
    v.require(True,'all five command entrypoints record busy rejection; other cluster enters concurrently')
    # No cutoff is inferred for rebuild, even when the configuration contains one.
    bad=subprocess.run([sys.executable,'-m','sql_apm','rebuild','--cluster','C1','--training-config',str(training_path)],
                       cwd=repo,env=child_env,capture_output=True,text=True)
    assert bad.returncode==2
    v.require(True,'rebuild requires explicit cutoff argument')
    for override,expected in [(None,'2026-07-23'),('2026-07-31','2026-07-31')]:
        args=commands[0]+(['--cutoff-date',override] if override else [])
        result=subprocess.run([sys.executable,'-m','sql_apm']+args,cwd=repo,env=child_env,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stdout
        payload=json.loads(result.stdout.splitlines()[-1]);assert payload['cutoff_date']==expected
        assert list(payload['stage_seconds'])==['build','check','import','publish','snapshot']
    v.require(True,'full derives cutoff from declared dates and accepts explicit override')
    current=version_status(db,'C1')['current']['build_id']
    for stage in ('import','build','check','publish'):
        code='''import json,os,time
from sql_apm.storage.ingestion import connect
from sql_apm.storage.tasks import Task
from sql_apm.storage.statistics import StatisticsStore
from sql_apm.storage.publication import PublicationStore
from sql_apm.ingestion.importer import Importer
from sql_apm.ingestion.config import load_config
stage=os.environ['STAGE']
db=connect(os.environ['SQL_APM_DSN'],'sql_apm')
def stop(**values):
 print(json.dumps(values),flush=True)
 time.sleep(300)
with Task(db,'C1','full') as task:
 if stage=='import':
  importer=Importer(os.environ['SQL_APM_DSN'],workers=1,db=db,progress=lambda **r:None,fault=lambda n:stop(task_id=task.task_id))
  importer.run(load_config(os.environ['IMPORT_CONFIG'],'S1','KILL'),task)
 else:
  store=StatisticsStore(os.environ['SQL_APM_DSN'],db=db)
  result=store.calculate('C1',os.environ['INPUT'],os.environ['CONFIG'],task=task,
   progress=lambda r:stop(task_id=task.task_id,build_id=r['build_id']) if stage=='build' and r['phase']=='results_written' else None)
  pub=PublicationStore(db,task)
  if stage=='check':
   task.set_stage('check')
   stop(task_id=task.task_id,build_id=result['build_id'])
  pub.check(result['build_id'])
  pub.publish(result['build_id'],fault=lambda:stop(task_id=task.task_id,build_id=result['build_id']))
'''
        # A distinct raw file ensures interruption happens in a real new attempt.
        from ingestion.test_reader import row,write_csv,configuration
        source=root/('kill-'+stage+'.csv');write_csv(source,[row(text='SELECT kill_'+stage,message='duration: 1 ms')])
        cfg=root/('kill-'+stage+'.json');cfg.write_text(json.dumps(configuration(cfg,[source],'KILL')))
        child=subprocess.Popen([sys.executable,'-c',code],cwd=repo,env=dict(child_env,STAGE=stage,IMPORT_CONFIG=str(cfg)),
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            if not select.select([child.stdout],[],[],30)[0]:raise AssertionError('child_not_ready_'+stage)
            message=child.stdout.readline().strip()
            assert message,child.stderr.read()
            evidence=json.loads(message)
            child.kill();child.wait(timeout=10)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=10)
            child.stdout.close();child.stderr.close()
        with Task(db,'C1','snapshot'):
            assert v.sql("SELECT state||':'||reason FROM task WHERE task_id='"+evidence['task_id']+"'")=='interrupted:owner_exited'
            if 'build_id' in evidence:
                assert v.sql("SELECT state FROM build WHERE build_id='"+evidence['build_id']+"'")=='interrupted'
                assert v.sql("SELECT count(*) FROM publication WHERE build_id='"+evidence['build_id']+"' AND result='published'")=='0'
            else:
                assert v.sql("SELECT count(*) FROM import_attempt WHERE batch_id='KILL' AND state='interrupted'")=='1'
                assert v.sql("SELECT count(*) FROM attempt_problem ap JOIN problem p USING(problem_id) WHERE p.code='owner_exited'")=='1'
        assert version_status(db,'C1')['current']['build_id']==current
        v.require(True,'SIGKILL at '+stage+' recovers task and artifacts without changing current')
    # Terminate the database session while the worker process remains alive.
    code='''import os,json,time
from sql_apm.storage.ingestion import connect
from sql_apm.storage.tasks import Task
from sql_apm.storage.statistics import StatisticsStore
db=connect(os.environ['SQL_APM_DSN'],'sql_apm')
with Task(db,'C1','statistics') as task:
 def progress(r):
  if r['phase']=='results_written':
   print(json.dumps(dict(task_id=task.task_id,build_id=r['build_id'],backend=db.get_backend_pid())),flush=True)
   input()
 StatisticsStore(os.environ['SQL_APM_DSN'],db=db).calculate('C1',os.environ['INPUT'],os.environ['CONFIG'],task=task,progress=progress)
'''
    child=subprocess.Popen([sys.executable,'-c',code],cwd=repo,env=child_env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert select.select([child.stdout],[],[],30)[0]
        evidence=json.loads(child.stdout.readline())
        # Wait for backend exit, not just delivery of its termination signal.
        assert v.sql('SELECT pg_terminate_backend('+str(evidence['backend'])+',5000)',
                     admin=True,database='sql_apm')=='t'
        assert child.poll() is None
        with Task(db,'C1','snapshot'):
            child.communicate('\n',timeout=15)
            assert child.returncode!=0
            assert v.sql("SELECT count(*) FROM mpp_statistic WHERE build_id='"+evidence['build_id']+"'")=='0'
            assert v.sql("SELECT state FROM build WHERE build_id='"+evidence['build_id']+"'")=='interrupted'
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
    v.require(True,'lost lease session prevents surviving process from committing results')
    # Keep a reader transaction open while a new month's partition is attached.
    reader=connect(dsn,'sql_apm');writer=connect(dsn,'sql_apm')
    with reader.cursor() as cur:
        cur.execute('SELECT count(*) FROM mpp_statistic');cur.fetchone()
    started=time.monotonic()
    with writer,writer.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout='3s'")
        cur.execute("SELECT mpp_ensure_result_partition('C1','2030-01-01')")
    elapsed=time.monotonic()-started
    reader.rollback();reader.close();writer.close()
    v.require(elapsed<3,'new month ATTACH completes under a held ordinary reader; seconds='+str(round(elapsed,3)))
    # Repeated joined reads see only fully committed current versions during switch.
    observed=[];done=threading.Event()
    def read_versions():
        connection=connect(dsn,'sql_apm')
        try:
            while not done.is_set():
                with connection,connection.cursor() as cur:
                    cur.execute('''SELECT v.build_id,p.result,b.state,b.results_saved,
                        (SELECT count(*) FROM build_check c WHERE c.build_id=b.build_id AND c.state='passed')
                        FROM current_version v JOIN publication p USING(publication_id)
                        JOIN build b ON b.build_id=v.build_id WHERE v.scope_id='C1' ''')
                    observed.append(cur.fetchone())
        finally:connection.close()
    thread=threading.Thread(target=read_versions);thread.start()
    from sql_apm.baseline.workflow import run
    try:new=run(dsn,'sql_apm',config)['build']['build_id']
    finally:done.set();thread.join(timeout=10)
    assert observed and all(r[0] in (current,new) and r[1:]==('published','calculated',True,6) for r in observed)
    v.require(True,'concurrent current-version joins see only complete old or new publication')
    db.close()
