#!/usr/bin/env python3
"""Explicit real-clock acceptance on the dedicated rehearsal database only."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import time

from acceptance import command, connect, current_build, digest, save, statistics
from verify_package import verify

CLUSTERS = ('119','120')
CUTOFFS = {'119':'2026-07-31','120':'2026-09-19'}
RESULTS = ('mpp_statistic','mpp_observation_statistic','mpp_build_group','mpp_build_observation_group')


def size(db):
    with db, db.cursor() as cur:
        cur.execute('SELECT pg_database_size(current_database())')
        return cur.fetchone()[0]


def preview_audit(db):
    return {table:digest(db,table) for table in ('task','mpp_cleanup_month','mpp_result_partition')}


def all_results(db):
    # Only hashes leave the server. This full pass is explicit acceptance work,
    # never a product command or daily precondition.
    return {table:digest(db,table) for table in RESULTS}


def cli(args, label, words):
    events, resources = command(args.app_root,args.records/(label+'.log'),words)
    return events[-1], resources


def cleanup(args, cluster, label, execute=False, training=None):
    words = ['cleanup','--cluster',cluster,'--training-config',str(training or args.config/('training-'+cluster+'.json'))]
    if execute:
        words.append('--execute')
    return cli(args,label,words)


def history(args,cluster,label):
    return cli(args,label,['history','--cluster',cluster,'--limit','1000'])[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--records',type=Path,required=True)
    parser.add_argument('phase',choices=['natural','protected','execute'])
    args=parser.parse_args()
    for name in ('app_root','config','records'):
        setattr(args,name,getattr(args,name).resolve())
    os.umask(0o077)
    args.records.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (args.records/(args.phase+'.json')).exists():
        raise ValueError('fresh phase record required; do not repeat completed rebuilds')
    package=verify(args.app_root,installed=True)
    with closing(connect()) as db:
        with db,db.cursor() as cur:
            cur.execute("SELECT date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date")
            month=cur.fetchone()[0].isoformat()
            cur.execute("SELECT count(*) FROM task WHERE state='running'")
            assert cur.fetchone()[0]==0, 'active task blocks clock rehearsal'
        report=dict(phase=args.phase,program_verification=package,current_month=month,
                    simulated_date=args.phase!='natural',before_database_bytes=size(db),clusters={})
        tick=time.monotonic()
        if args.phase=='natural':
            with db,db.cursor() as cur:
                cur.execute("SELECT scope_id,count(*) FROM publication WHERE result='published' GROUP BY scope_id ORDER BY scope_id")
                assert cur.fetchall()==[('119',9),('120',4)], 'run four guide rebuilds first'
                cur.execute('SELECT DISTINCT build_month FROM mpp_result_partition p JOIN build b USING(partition_id)')
                assert [r[0].isoformat() for r in cur]==[month], 'expected one natural construction month'
            report['original_month']=month
        else:
            previous=json.loads((args.records/'natural.json').read_text())
            assert month=='2027-01-01' and previous['original_month']<'2026-11-01', 'simulated clock or original month differs'
            assert package['commit']==previous['program_verification']['commit']
            report['original_month']=previous['original_month']
        original_month=report['original_month']
        if args.phase in ('natural','protected'):
            before=all_results(db)
            for cluster in CLUSTERS:
                audit=preview_audit(db)
                preview,_=cleanup(args,cluster,args.phase+'-'+cluster+'-preview')
                assert preview_audit(db)==audit, 'preview wrote audit data'
                old=[m for m in preview['months'] if m['build_month']==original_month]
                assert len(old)==1 and old[0]['state']==('retained' if args.phase=='natural' else 'protected')
                result,resources=cleanup(args,cluster,args.phase+'-'+cluster+'-execute',True)
                assert result['state']=='succeeded'
                assert all(m['released_bytes']==0 and m['formal_groups_deleted']==0 and m['observation_groups_deleted']==0 for m in result['months'])
                report['clusters'][cluster]=dict(preview=preview,result=result,resources=resources)
            after=all_results(db)
            assert before==after, 'empty cleanup changed result rows'
            report['result_rows_before']=before;report['result_rows_after']=after
        else:
            protected=json.loads((args.records/'protected.json').read_text())
            assert protected['current_month']==month
            for cluster in CLUSTERS:
                result,resources=cli(args,'new-month-'+cluster,['rebuild','--cluster',cluster,
                    '--training-config',str(args.config/('training-'+cluster+'.json')),'--cutoff-date',CUTOFFS[cluster]])
                assert result['state']=='succeeded' and result['publication']['result']=='published'
                with db,db.cursor() as cur:
                    cur.execute('SELECT build_month FROM build JOIN mpp_result_partition USING(partition_id) WHERE build_id=%s',(result['build']['build_id'],))
                    assert cur.fetchone()[0].isoformat()==month
                report['clusters'][cluster]=dict(new_build_id=result['build']['build_id'],rebuild_resources=resources)
            current={c:current_build(db,c) for c in CLUSTERS}
            intact={c:statistics(db,b,identical=True) for c,b in current.items()}
            for cluster in CLUSTERS:
                policy=json.loads((args.config/('training-'+cluster+'.json')).read_text())
                policy['retention']=dict(months=12)
                training=args.records/('retention-12-'+cluster+'.json');save(training,policy)
                retained,_=cleanup(args,cluster,'retained-'+cluster,training=training)
                expired,_=cleanup(args,cluster,'expired-'+cluster)
                old=lambda result:next(m for m in result['months'] if m['build_month']==original_month)
                assert old(retained)['state']=='retained' and old(expired)['state']=='expired'
                pid=old(expired)['partition_id']
                before=size(db)
                result,resources=cleanup(args,cluster,'remove-'+cluster,True)
                assert result['state']=='succeeded'
                assert old(result)['state']=='succeeded' and old(result)['released_bytes']>0
                with db,db.cursor() as cur:
                    for table in RESULTS[:2]:
                        cur.execute('SELECT to_regclass(%s)',(table+'_p'+str(pid),));assert cur.fetchone()[0] is None
                    for table in RESULTS[2:]:
                        cur.execute('SELECT count(*) FROM '+table+' WHERE partition_id=%s',(pid,));assert cur.fetchone()[0]==0
                    cur.execute('SELECT cleaned_at IS NOT NULL AND groups_cleaned_at IS NOT NULL FROM mpp_result_partition WHERE partition_id=%s',(pid,))
                    assert cur.fetchone()[0]
                versions=history(args,cluster,'history-'+cluster)
                assert all(v['results_cleaned'] for v in versions['versions'] if v['build_id']!=current[cluster])
                assert not next(v for v in versions['versions'] if v['build_id']==current[cluster])['results_cleaned']
                report['clusters'][cluster].update(retained=retained,expired=expired,result=result,resources=resources,
                    before_database_bytes=before,after_database_bytes=size(db),history=versions)
            after={c:statistics(db,b,identical=True) for c,b in current.items()}
            assert intact==after
            report['current_rows_before']=intact;report['current_rows_after']=after
        report.update(after_database_bytes=size(db),seconds=round(time.monotonic()-tick,3),passed=True)
        save(args.records/(args.phase+'.json'),report)
        print(json.dumps(dict(phase=args.phase,passed=True,simulated_date=report['simulated_date'])),flush=True)


if __name__=='__main__':
    main()
