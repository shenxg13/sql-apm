#!/usr/bin/env python3
"""Bounded, real CLI rehearsal of documentation examples against a delivered app."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts/db'),str(ROOT/'tests'),str(ROOT)]
from verify import instance,Verification
from ingestion.test_reader import row,write_csv
from check_documents import examples, GUIDE
from statistic_comparison import FORMAT, compare_statistics
from acceptance import product_files


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,help='compare every helper to an earlier synthetic run')
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    args=parser.parse_args();args.app_root=args.app_root.resolve();args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    report=[]
    with instance(args.pg_bin) as (directory,env):
        v=Verification(args.pg_bin,directory,env);v.init(root=args.app_root)
        env.update(SQL_APM_DSN='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm',
                   PYTHONPATH=str(args.app_root),SQL_APM_APP_ROOT=str(args.app_root))
        def cli(label,words):
            log=args.output/(label+'.log')
            with log.open('x') as stream:
                result=subprocess.run([str(args.app_root/'.venv/bin/python'),'-m','sql_apm']+words,cwd=args.app_root,
                    env=env,stdout=stream,stderr=subprocess.STDOUT)
            assert result.returncode==0,label
            return json.loads(log.read_text().splitlines()[-1])
        for cluster,month,days in [('119','2026-07',[28,29,30,31]),('120','2026-09',[16,17,18,19])]:
            config=dict(version=1,clusters=[cluster],sources={'daily-'+cluster:dict(cluster=cluster,
                build='HashData Warehouse 3.13.13',timezone='UTC+08:00',declaration='synthetic guide replay')},batches={})
            training=args.output/('training-'+cluster+'.json');training.write_text(json.dumps(dict(version=1,clusters=[cluster],window=dict(days=30))))
            import_path=args.output/('import-'+cluster+'.json')
            for i,day in enumerate(days):
                csv=args.output/(cluster+'-'+str(i)+'.csv')
                dates=range(day-6,day+1) if i==0 else [day]
                write_csv(csv,[row(text=sql,message='duration: 2 ms',**{'0':month+'-'+str(d).zfill(2)+' 12:00:00 CST'})
                    for d in dates for sql in ('SELECT 1','SELECT x FROM guide_table') for _ in range(10)])
                batch=cluster+'-'+str(i)
                config['batches'][batch]=dict(source='daily-'+cluster,files_confirmed_complete=True,
                    dates=[month+'-'+str(d).zfill(2) for d in dates],files=[dict(path=str(csv),closed_and_copied=True)])
                import_path.write_text(json.dumps(config))
                result=cli(batch,['full','--config',str(import_path),'--source','daily-'+cluster,'--batch',batch,
                    '--training-config',str(training)])
                assert result['publication']['result']=='published'
            if cluster=='119':
                cli('119-rebuild',['rebuild','--cluster',cluster,'--training-config',str(training),'--cutoff-date','2026-07-31'])
        # Exercise the same helper and checks later used with the real nine-task database.
        for case in ('window','threshold','template','exclusion','retention','workers','import'):
            baseline_args=['--baseline',str(args.baseline.resolve())] if args.baseline else []
            with (args.output/('helper-'+case+'.log')).open('x') as stream:
                result=subprocess.run([str(args.app_root/'.venv/bin/python'),str(ROOT/'scripts/deployment/guide_examples.py'),
                    '--app-root',str(args.app_root),'--config',str(args.output),'--records',str(args.output/'guide'),
                    *baseline_args,'run',case],env=env,stdout=stream,stderr=subprocess.STDOUT)
            assert result.returncode==0,'helper_'+case
            report.append(dict(example=case,helper_passed=True))
            if case in ('window','threshold','template','exclusion'):
                record=json.loads((args.output/'guide'/(case+'.json')).read_text())
                comparison=compare_statistics(record['statistics_values'],args.output/'guide',
                                              record['statistics_values'],args.output/'guide')
                assert comparison['passed']
                report[-1]['statistics_comparison']=comparison
        metadata=json.loads((args.app_root/'RELEASE.json').read_text())
        baseline=dict(program_commit=metadata['commit'],product_sha256=product_files(metadata),
                      statistics_comparison=FORMAT,examples={},statistics_values={})
        for case in ('window','threshold','template','exclusion','retention','workers','import'):
            record=json.loads((args.output/'guide'/(case+'.json')).read_text())
            baseline['examples'][case]=record['comparable']
            if record['statistics_values'] is not None:
                baseline['statistics_values'][case]=record['statistics_values']
        (args.output/'guide/v020-development-baseline.json').write_text(json.dumps(baseline,indent=2)+'\n')
        # At this point the private fixture has the same 9+4 publication shape
        # required by the target's natural-date cleanup step. Exercise its real
        # CLI and row/audit comparisons before adding the literal examples.
        with (args.output/'cleanup-natural.log').open('x') as stream:
            result=subprocess.run([str(args.app_root/'.venv/bin/python'),str(ROOT/'scripts/deployment/cleanup_rehearsal.py'),
                '--app-root',str(args.app_root),'--config',str(args.output),'--records',str(args.output/'cleanup'),
                'natural'],env=env,stdout=stream,stderr=subprocess.STDOUT)
        assert result.returncode==0,'cleanup_natural'
        # Execute every literal JSON sample too, including the synthetic import manifest.
        cases=examples((ROOT/GUIDE).read_text())
        for name,kind,document in cases:
            path=args.output/('literal-'+name+'.json')
            path.write_text(json.dumps(document))
            if kind=='import':
                write_csv(args.output/'sample.csv',[row(text='SELECT 1',message='duration: 2 ms',**{'0':'2026-07-31 12:00:00 CST'})])
                words=['import','--config',str(path),'--source','guide-source','--batch','guide-batch']
            elif name=='retention':
                words=['cleanup','--cluster','119','--training-config',str(path)]
            else:
                words=['rebuild','--cluster','119','--training-config',str(path),'--cutoff-date','2026-07-31']
            result=cli('literal-'+name,words)
            assert result['state'] in ('complete','succeeded','preview')
            report.append(dict(example=name,literal_executed=True,state=result['state']))
        v.init('check',root=args.app_root)
    (args.output/'report.json').write_text(json.dumps(dict(passed=True,examples=report,natural_cleanup_passed=True),indent=2)+'\n')
    print(json.dumps(dict(passed=True,examples=len(report),natural_cleanup_passed=True)),flush=True)


if __name__=='__main__':main()
