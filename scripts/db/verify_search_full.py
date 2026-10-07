#!/usr/bin/env python3
"""Explicit full-data query audit. Private originals/results never enter reports."""
import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import psycopg2
from psycopg2 import sql
from database.retention import digest
from sql_apm.ingestion.config import identity
from sql_apm.sql.normalization import Normalizer
from sql_apm.cli.search import query


def command(args,log=None):
    p=subprocess.run([str(a) for a in args],capture_output=True,text=True)
    if log:log.write_text(p.stdout+p.stderr)
    if p.returncode:raise RuntimeError('validation_command_failed: '+str(args[0]))
    return p.stdout


def start(directory,pg_bin):
    command([pg_bin/'pg_ctl','-D',directory/'pgdata','-l',directory/'server.log','-o',
        "-p 55474 -k "+str(directory/'socket')+" -c listen_addresses=''",'-w','start'])


def stop(directory,pg_bin):
    command([pg_bin/'pg_ctl','-D',directory/'pgdata','-m','fast','-w','stop'])


def connection(directory):
    db=psycopg2.connect(host=str(directory/'socket'),port=55474,dbname='sql_apm',user='sql_apm')
    with db,db.cursor() as cur:
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
    return db


def tables(db):
    with db,db.cursor() as c:
        c.execute("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")
        return [r[0] for r in c]


def snapshot(db,after=False):
    result={}
    for table in tables(db):
        result[table]=digest(db,table,('search_text',) if after and table=='mpp_sql_text' else (),
            "WHERE version<>'1.10.0'" if after and table=='schema_version' else '')
        print('digest',table,result[table]['rows'],flush=True)
    with db,db.cursor() as cur:
        cur.execute("SELECT pg_total_relation_size('mpp_sql_text')")
        size=cur.fetchone()[0]
    return dict(tables=result,sql_text_bytes=size)


def prepare(args):
    directory=args.directory.resolve();source=args.source_data.resolve()
    state=command([args.pg_bin/'pg_controldata',source])
    if 'shut down' not in state or (source/'postmaster.pid').exists():raise ValueError('source_must_be_stopped')
    directory.mkdir(mode=0o700)
    command(['cp','-a','--reflink=auto',source,directory/'pgdata'])
    (directory/'socket').mkdir(mode=0o700)
    start(directory,args.pg_bin)
    try:
        with connection(directory) as db:before=snapshot(db)
        (directory/'before.json').write_text(json.dumps(before))
        began=time.monotonic()
        command([ROOT/'scripts/db/initialize.sh','upgrade','--host',directory/'socket','--port','55474',
                 '--pg-bin',args.pg_bin],directory/'upgrade.log')
        (directory/'upgrade-time.json').write_text(json.dumps(dict(seconds=time.monotonic()-began,exit_code=0)))
    finally:stop(directory,args.pg_bin)


def audit(args):
    directory=args.directory.resolve()
    if not (directory/'pgdata/postmaster.pid').exists():start(directory,args.pg_bin)
    db=None
    try:
        db=connection(directory)
        before=json.loads((directory/'before.json').read_text())
        after=snapshot(db,True)
        assert before['tables']==after['tables'],'upgrade_old_values_changed'
        report=dict(upgrade=json.loads((directory/'upgrade-time.json').read_text()),
                    before=before,after=after,old_values_equal=True)
        def rows(statement,params=()):
            with db,db.cursor() as c:c.execute(statement,params);return c.fetchall()
        assert rows('SELECT count(*) FROM mpp_sql_text WHERE search_text IS NULL OR search_text<>mpp_search_fold(text)')[0][0]==0
        report['generated_texts_complete']=True
        norm='N:'+identity(Normalizer().context)
        # Independent Python transformation of original text; no query-layer calls.
        cases=[('broad',['select']),('two_terms',['select','from']),('phrase',['select *']),
               ('symbol',['=']),('ordered',['order by']),('no_match',['zz_no_search_match_47']),
               ('reversed',['from','select']),('literal_wildcards',['%_'])]
        (directory/'fixed-inputs.json').write_text(json.dumps(cases))
        with db,db.cursor() as c:
            c.execute('CREATE TEMP TABLE oracle_match(case_id integer,sql_id text) ON COMMIT PRESERVE ROWS')
        trans=str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz',' \t\n\r\f\v')
        count=0;characters=Counter()
        with db,db.cursor(name='original_texts') as reader,db.cursor() as writer:
            reader.itersize=2000;reader.execute('SELECT sql_id,text FROM mpp_sql_text ORDER BY sql_id')
            buffer=io.StringIO()
            for sid,original in reader:
                folded=original.translate(trans)
                characters.update(set(folded))
                for i,(_,terms) in enumerate(cases):
                    if all(term.translate(trans) in folded for term in terms):buffer.write(str(i)+'\t'+sid+'\n')
                count+=1
                if count%2000==0:
                    buffer.seek(0);writer.copy_from(buffer,'oracle_match',columns=['case_id','sql_id']);buffer=io.StringIO()
            buffer.seek(0);writer.copy_from(buffer,'oracle_match',columns=['case_id','sql_id'])
        # Every nonempty literal substring is contained in each of its characters'
        # match sets. The most frequent folded character is therefore a global
        # worst case by matched-original count, including all possible phrases.
        broadest=max(characters,key=lambda ch:(characters[ch],ch))
        cases.append(('max_originals',[broadest]))
        with db,db.cursor(name='broadest_originals') as reader,db.cursor() as writer:
            reader.itersize=2000;reader.execute('SELECT sql_id,text FROM mpp_sql_text ORDER BY sql_id')
            buffer=io.StringIO();n=0
            for sid,original in reader:
                if broadest in original.translate(trans):buffer.write(str(len(cases)-1)+'\t'+sid+'\n')
                n+=1
                if n%2000==0:
                    buffer.seek(0);writer.copy_from(buffer,'oracle_match',columns=['case_id','sql_id']);buffer=io.StringIO()
            buffer.seek(0);writer.copy_from(buffer,'oracle_match',columns=['case_id','sql_id'])
        (directory/'fixed-inputs.json').write_text(json.dumps(cases))
        report['max_possible_matched_originals']=characters[broadest]
        report['independent_originals_scanned']=count
        with db,db.cursor() as c:c.execute('CREATE INDEX ON oracle_match(case_id); ANALYZE oracle_match')
        fuzz=[]
        for index,(label,terms) in enumerate(cases):
            # Empty/asymmetric scopes are also checked in synthetic acceptance.
            text=' '.join('"'+term+'"' if ' ' in term else term for term in terms)
            with db,db.cursor() as c:
                c.execute('''WITH all_groups AS (
                    SELECT f.value fingerprint,min(m.sql_id) example_sql_id,count(DISTINCT m.sql_id) matched_texts,
                        count(*) record_count,array_agg(DISTINCT o.scope_id ORDER BY o.scope_id) scopes,
                        array_agg(DISTINCT o.database ORDER BY o.database) databases,
                        array_agg(DISTINCT o.execution_user ORDER BY o.execution_user) execution_users,max(o.end_at) last_at
                    FROM oracle_match m JOIN mpp_fingerprint f ON f.sql_id=m.sql_id AND f.normalization_id=%s AND f.state='reliable' AND f.profile='mpp-csv/1'
                    JOIN mpp_occurrence o ON o.sql_id=m.sql_id WHERE m.case_id=%s GROUP BY f.value)
                    SELECT coalesce(jsonb_agg(to_jsonb(g)-'total_structures' ORDER BY record_count DESC,fingerprint),'[]'),coalesce(max(total_structures),0)
                    FROM (SELECT *,count(*) OVER() total_structures FROM all_groups ORDER BY record_count DESC,fingerprint LIMIT 50) g''',(norm,index))
                expected,total=c.fetchone()
                result=query(c,'mpp_query_search',[norm,text])
                assert result['rows']==expected and result['total_structures']==total,'fuzzy_oracle_'+label
                c.execute('SELECT count(*) FROM oracle_match WHERE case_id=%s',(index,));matched=c.fetchone()[0]
            fuzz.append(dict(case=label,matched_texts=matched,structures=total,equal=True))
            print('fuzzy_equal',label,total,flush=True)
        report['fuzzy_oracles']=fuzz
        # All candidate selection and independent counts read the base tables.
        with db,db.cursor() as c:
            c.execute('''CREATE TEMP TABLE oracle_structures AS
                SELECT f.value,count(*) records,count(DISTINCT f.sql_id) texts,
                       bool_and(o.outcome<>'success') failure_only,
                       bool_and(o.estimated_start_at<c.window_start OR o.estimated_start_at>=c.window_end) outside_window
                FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
                LEFT JOIN current_version v ON v.scope_id=o.scope_id LEFT JOIN build b ON b.build_id=v.build_id
                LEFT JOIN config_snapshot c ON c.config_id=b.config_id
                WHERE f.normalization_id=%s AND f.state='reliable' AND f.profile='mpp-csv/1' GROUP BY f.value''',(norm,))
            c.execute('''(SELECT 'most_records' label,value FROM oracle_structures ORDER BY records DESC,value LIMIT 1)
                UNION ALL (SELECT 'most_texts',value FROM oracle_structures ORDER BY texts DESC,value LIMIT 1)
                UNION ALL (SELECT 'failure_only',value FROM oracle_structures WHERE failure_only ORDER BY value LIMIT 1)
                UNION ALL (SELECT 'outside_window',value FROM oracle_structures WHERE outside_window ORDER BY value LIMIT 1)
                UNION ALL (SELECT 'sample',value FROM oracle_structures ORDER BY md5(value||'issue47-fixed-sample') LIMIT 50)''')
            selected=c.fetchall()
        precise=[];identities=[]
        for label,fingerprint in selected:
            direct=rows('''SELECT o.scope_id,o.database,o.execution_user,count(*),min(o.end_at),max(o.end_at)
                FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id)
                WHERE f.normalization_id=%s AND f.value=%s AND f.profile='mpp-csv/1'
                GROUP BY o.scope_id,o.database,o.execution_user ORDER BY count(*) DESC,o.scope_id,o.database,o.execution_user''',(norm,fingerprint))
            actual=rows('''SELECT scope_id,database,execution_user,record_count,first_at,last_at
                FROM mpp_query_hits(%s,%s) ORDER BY record_count DESC,scope_id,database,execution_user''',(norm,fingerprint))
            assert actual==direct,'precise_oracle'
            precise.append(dict(case=label,structure_sha256=hashlib.sha256(fingerprint.encode()).hexdigest(),identities=len(actual),records=sum(x[3] for x in actual),equal=True))
            if actual:identities.append((label,fingerprint,actual[0][:3]))
        report['precise_oracles']=precise
        baselines=0
        metrics='min_ms max_ms mean_ms p25_ms p50_ms p75_ms p90_ms p95_ms p99_ms stddev_ms cv mad_ms iqr_ms log_median log_mad p95_p50 p99_p50'.split()
        for label,fp,ident in identities:
            scope,database,user=ident
            if database is None or user is None:continue
            with db,db.cursor() as c:
                summary=query(c,'mpp_query_baseline',[norm,scope,database,user,fp])
                for item in summary['rows']:
                    c.execute('''SELECT to_jsonb(s) FROM mpp_baseline_group g CROSS JOIN LATERAL
                        mpp_read_statistics(%s,false,g.group_id) s WHERE g.scope_id=%s AND g.database=%s
                        AND g.execution_user=%s AND g.normalization_id=%s AND g.fingerprint_value=%s
                        AND g.timing_type=%s AND s.layer='overall' ''',(summary['version']['build_id'],scope,database,user,norm,fp,item['timing_type']))
                    direct=c.fetchone()
                    if direct:
                        for key in metrics+['included_count','excluded_count','exclusions_by_reason','metric_null_reasons']:
                            assert direct[0][key]==item[key],key
                        assert len(direct[0]['active_dates'])==item['active_days']
                    else:assert item['sample_state']=='no_samples'
                    baselines+=1
        report['baseline_rows_compared']=baselines
        # Cold = PostgreSQL restarted; the OS page cache is deliberately not cleared.
        # No base data are modified. Query results containing identities stay private.
        db.close();db=None
        scenarios=[]
        for label,terms in cases:
            text=' '.join('"'+term+'"' if ' ' in term else term for term in terms)
            scenarios.append(('fuzzy_'+label,'mpp_query_search',[norm,text]))
        for label,fp,ident in identities:
            scope,database,user=ident
            scenarios.append(('hits_'+label,'mpp_query_exact',[norm,fp]))
            if database is not None and user is not None:
                scenarios.extend([('baseline_'+label,'mpp_query_baseline',[norm,scope,database,user,fp]),
                    ('details_'+label,'mpp_query_history',[norm,fp,scope,database,user]),
                    ('timeline_'+label,'mpp_query_history',[norm,fp,scope,database,user,None,None,100,None,None,'hour'])])
        timings=[]
        for i,(label,function,params) in enumerate(scenarios):
            stop(directory,args.pg_bin);start(directory,args.pg_bin)
            db=connection(directory)
            seconds=[]
            for state in ('pg_cold','warm'):
                began=time.monotonic()
                with db,db.cursor() as c:
                    db.set_session(readonly=True)
                    result=query(c,function,params)
                seconds.append(round(time.monotonic()-began,6))
            timings.append(dict(case=label,function=function,pg_cold_seconds=seconds[0],warm_seconds=seconds[1]))
            db.close();db=None
            print('timed',i,label,seconds,flush=True)
            (directory/'timings-progress.json').write_text(json.dumps(timings))
        report['timings']=timings
        report['cold_boundary']='PostgreSQL shared buffers reset; OS page cache uncontrolled, not a cold-storage claim'
        (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print('RESULT: full search audit passed',flush=True)
    finally:
        if db is not None:db.close()
        if (directory/'pgdata/postmaster.pid').exists():stop(directory,args.pg_bin)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','audit'])
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--source-data',type=Path)
    p.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    a=p.parse_args()
    if a.command=='prepare' and a.source_data is None:p.error('--source-data required for prepare')
    (prepare if a.command=='prepare' else audit)(a)
