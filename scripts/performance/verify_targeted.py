#!/usr/bin/env python3
"""Exercise the delivered targeted workflow on synthetic data and a private PG17."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'scripts/db')]
from verify import instance,Verification
from database import dashboard_data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--toolkit',type=Path,required=True)
    parser.add_argument('--module',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    args=parser.parse_args()
    args.output=args.output.resolve();args.output.mkdir()
    (args.output/'run').mkdir()
    with instance(args.pg_bin) as (directory,env):
        v=Verification(args.pg_bin,directory,env);v.init()
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        dashboard_data.load(dsn,directory)
        common=[sys.executable,'-B',str(args.toolkit.resolve()/'check.py'),
                '--work',str(args.output/'run'),'--root',str(directory),
                '--app',str(args.app.resolve()),'--pgdata',str(directory/'data'),
                '--dashboards',str(args.app.resolve()/'grafana/dashboards'),
                '--dsn',dsn.replace('user=sql_apm','user=sql_apm_ro'),
                '--admin-dsn',dsn.replace('user=sql_apm','user=apm_test_admin'),
                '--module-dir',str(args.module.resolve()),'--at','2026-07-24T00:00:00+08:00',
                '--limit-seconds','60']
        for part in ('host','inventory','cases','first-read','focus','pack'):
            with (args.output/(part+'.log')).open('wb') as stream:
                result=subprocess.run(common+[part]+(['--targeted'] if part=='focus' else []),
                                      env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(part+' failed; inspect local log')
            print('PASS: targeted '+part,flush=True)
        work=args.output/'run'
        selected=json.loads((work/'private/cases.json').read_text())
        scopes=len({c['scope_id'] for c in selected})
        focus=json.loads((work/'results/focus.json').read_text())
        first=json.loads((work/'results/first-read.json').read_text())
        measurements=focus['measurements']
        assert sum('/range-' in r['item'] for r in measurements)==3*(scopes+1)
        assert sum(r['item'].endswith('/history-7/page') for r in measurements)==scopes
        assert sum(r['item'].endswith('/history-7/panel-29') for r in measurements)==scopes
        assert not any('/baseline/' in r['item'] for r in focus['shape']+measurements+focus['split'])
        assert all(r['state']=='ok' for r in measurements+focus['split'])
        assert all(r['state']=='ok' and r['first_plan_available']==1 for p in first['pages'] for r in p['statements'])
        assert not (work/'results/timing.json').exists()
        assert (work/'performance-results.tar.gz').is_file()
        result=dict(passed=True,scopes=scopes,measurements=len(measurements),
                    first_pages=len(first['pages']),full_timing_executed=False,disclosure_scan_passed=True)
        (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))


if __name__=='__main__':main()
