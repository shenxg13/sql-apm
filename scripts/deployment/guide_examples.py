#!/usr/bin/env python3
"""Execute independent configuration examples after the fixed nine tasks."""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

from acceptance import (build_counts, command, connect, current_build, save,
                        selected, statistics, sufficiency, verify_baseline)
from verify_package import verify

CASES = ('window','threshold','template','exclusion','retention','workers','import')


def seed(args, db):
    path = args.records / 'seed.json'
    if path.exists():
        return json.loads(path.read_text())
    with db, db.cursor() as cur:
        cur.execute("SELECT scope_id,count(*) FROM publication WHERE result='published' GROUP BY scope_id ORDER BY scope_id")
        if cur.fetchall() != [('119',5),('120',4)]:
            raise ValueError('complete exactly nine tasks before guide examples')
    bid = current_build(db,'119')
    result = dict(build_id=bid, statistics=statistics(db,bid), sufficiency=sufficiency(db,bid))
    save(path,result)
    return result


def make_config(args, db, original):
    name = args.case
    document = json.loads((args.config / 'training-119.json').read_text())
    document.setdefault('window',{})['cutoff_date'] = '2026-07-31'
    values = deepcopy(document)
    fingerprint = None
    if name == 'window':
        values['window']['days'] = 2
    elif name == 'threshold':
        values['thresholds'] = dict(overall=dict(basic_count=1000000000))
    elif name == 'template':
        # Pick an actually included group deterministically without exporting source SQL.
        with db, db.cursor() as cur:
            cur.execute('''SELECT t.text,g.fingerprint_value FROM mpp_statistic s
                JOIN mpp_baseline_group g USING(group_id)
                JOIN mpp_fingerprint f ON f.fingerprint_id=g.fingerprint_id
                JOIN mpp_sql_text t USING(sql_id)
                WHERE s.build_id=%s AND s.layer='overall' AND s.included_count>0
                ORDER BY s.included_count DESC,g.group_id LIMIT 1''',(original['build_id'],))
            template, fingerprint = cur.fetchone()
        values['templates'] = [dict(id='guide-template',sql=template,cluster='119',description='演练模板')]
    elif name == 'exclusion':
        values['exclusions'] = [dict(id='guide-period',cluster='119',start='2026-07-31T00:00:00+08:00',
            end='2026-08-01T00:00:00+08:00',reason='演练排除时段')]
    elif name == 'retention':
        values['retention'] = dict(months=12)
    path = args.records / (name+'-training.json')
    save(path,values)
    path.chmod(0o600)
    return path, fingerprint


def run(args):
    args.records.mkdir(parents=True,exist_ok=True,mode=0o700)
    package = verify(args.app_root,installed=True)
    metadata = json.loads((args.app_root/'RELEASE.json').read_text())
    with closing(connect()) as db:
        original = seed(args,db)
        path, fingerprint = make_config(args,db,original)
        if args.case in ('workers','import'):
            cluster = '119' if args.case=='workers' else '120'
            words = ['import','--config',str(args.config/('import-'+cluster+'.json')),
                     '--source','daily-'+cluster,'--batch',cluster+'-3','--workers','4']
        elif args.case == 'retention':
            words = ['cleanup','--cluster','119','--training-config',str(path)]
        else:
            words = ['rebuild','--cluster','119','--training-config',str(path),'--cutoff-date','2026-07-31']
        events, resources = command(args.app_root,args.records/(args.case+'.log'),words)
        result = events[-1]
        if args.case in ('workers','import'):
            if result['state']!='complete' or any(f['state']!='duplicate_skipped' for f in result['files']):
                raise ValueError('guide import must skip all already completed files')
            comparable = dict(state=result['state'],files=len(result['files']),
                states=sorted(f['state'] for f in result['files']),workers=4)
        elif args.case=='retention':
            assert result['state']=='preview' and result['retention_months']==12
            comparable = dict(state=result['state'],retention_months=12,
                months=[dict(state=m['state'],published_builds=m['published_builds']) for m in result['months']])
        else:
            assert result['state']=='succeeded' and result['publication']['result']=='published'
            bid = result['build']['build_id']
            comparable = dict(selection=selected(events),build=build_counts(result),
                statistics=statistics(db,bid),sufficiency=sufficiency(db,bid),template_fingerprint=fingerprint)
            if args.case=='window':
                assert comparable['selection']['excluded_batches']>0
            elif args.case=='threshold':
                assert comparable['statistics']==original['statistics'], 'threshold changed metric values'
                assert comparable['sufficiency']!=original['sufficiency'], 'threshold must change insufficiency counts'
            else:
                reason = 'blacklist_template' if args.case=='template' else 'excluded_interval'
                assert sum(n for _,code,n in comparable['build']['reasons'] if code==reason)>0
        equal = None
        if args.baseline:
            baseline = json.loads(args.baseline.read_text())
            verify_baseline(metadata,baseline)
            equal = comparable==baseline['examples'][args.case]
        record = dict(case=args.case,program_commit=metadata['commit'],program_verification=package,
            resources=resources,comparable=comparable,baseline_equal=equal,passed=equal is not False)
        save(args.records/(args.case+'.json'),record)
        print(json.dumps(dict(case=args.case,passed=record['passed'],baseline_equal=equal)),flush=True)
        if equal is False:
            raise ValueError('guide baseline differs; preserve result and do not rerun completed rebuild')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--records',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('action',choices=['run'])
    parser.add_argument('case',choices=CASES)
    args = parser.parse_args()
    args.app_root=args.app_root.resolve();args.config=args.config.resolve();args.records=args.records.resolve()
    os.umask(0o077)
    run(args)


if __name__=='__main__':
    main()
