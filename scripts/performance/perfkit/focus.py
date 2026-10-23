"""The agreed list/history investigation, deliberately separate from normal timing."""
from datetime import datetime, timedelta, timezone
import json
import os
import time
from pathlib import Path
import re

from . import dashboards, plans
from .core import measure, save, Refused, serializable


FUNCTIONS = ('mpp_view_ranking','mpp_view_baseline_ranking','mpp_query_hits','mpp_view_executions')


def extract(source):
    """Copy exact version-bound RETURN QUERY bodies; no handwritten query alternative."""
    result={}
    for name in FUNCTIONS:
        definition=source.split('CREATE OR REPLACE FUNCTION '+name+'(',1)[1].split('END $function$;',1)[0]
        body=definition.split('RETURN QUERY\n',1)[1].strip().removesuffix(';')
        result[name]=body
    return result


def bound(template,values,session):
    # PostgreSQL scanner distinguishes names from literals and comments.
    from pglast import scan
    replacements=[]
    tokens=list(scan(template))
    i=0
    while i<len(tokens):
        token=tokens[i]
        name=template[token.start:token.end+1]
        end=token.end+1
        if name=='base' and i+2<len(tokens) and template[tokens[i+1].start:tokens[i+1].end+1]=='.':
            name+='.'+template[tokens[i+2].start:tokens[i+2].end+1]
            end=tokens[i+2].end+1
            i+=2
        if name in values:
            value,cast=values[name]
            with session.db.cursor() as cur:
                rendered=cur.mogrify('%s::'+cast,(value,)).decode(session.db.encoding)
            replacements.append((token.start,end,rendered))
        i+=1
    for start,end,value in reversed(replacements):
        template=template[:start]+value+template[end:]
    return template


def run(args,work,session):
    selected=json.loads((work.private/'cases.json').read_text())
    if not selected:
        raise Refused('focus_requires_representative_identity')
    documents=dashboards.load(args.dashboards)
    source=(Path(__file__).resolve().parents[1]/'internal.json')
    templates=json.loads(source.read_text())
    # Bind the templates to exactly the installed v1.1.0 source.
    if templates!=extract((args.app/'sql_apm/storage/schema.sql').read_text()):
        raise Refused('internal_statement_source_mismatch')
    allowed=plans.names(session)
    module=plans.enable(session,args.module_dir)
    result=dict(module=module,shape=[],measurements=[],split=[],limit_seconds=args.limit_seconds)
    trace=[]
    now=datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc).replace(microsecond=0)
    written={name:0 for name in ('shape','measurements','split','trace')}
    last_checkpoint=[0.0]
    for name in written:
        (work.private/('focus-'+name+'.jsonl')).write_bytes(b'')
    save(work.public/'focus.json',result)
    def checkpoint(force=False):
        for name in written:
            values=trace if name=='trace' else result[name]
            fresh=values[written[name]:]
            if fresh:
                with (work.private/('focus-'+name+'.jsonl')).open('a',encoding='utf-8') as stream:
                    for row in fresh:
                        stream.write(json.dumps(row,ensure_ascii=False,default=serializable,separators=(',',':'))+'\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                written[name]=len(values)
        if force:
            save(work.public/'focus.json',result)
            save(work.private/'focus-trace.json',trace)
        if force or time.monotonic()-last_checkpoint[0]>=15:
            save(work.private/'focus-progress.json',dict(counts=written,updated_at=datetime.now().astimezone()))
            last_checkpoint[0]=time.monotonic()
    def planned(name,values,label):
        sql=bound(templates[name],values,session)
        session.query('SET auto_explain.log_min_duration=-1') if module['state']=='loaded' else None
        record=plans.shape(session,sql,None,label,allowed)
        result['shape'].append(record)
        trace.append(dict(item=label,function=name,sql=sql))
        checkpoint()
    def actual(sql,label,split=False,keep=False):
        if module['state']=='loaded':
            session.query('SET auto_explain.log_min_duration=0')
            notice=plans.Notices(allowed)
            session.db.notices=notice
        record,rows=measure(session,sql,label=label,keep=keep,upper_limit=True)
        record['plans']=notice.plans if module['state']=='loaded' else []
        record['scan']=plans.summary(record['plans'])
        result['split' if split else 'measurements'].append(record)
        trace.append(dict(item=label,sql=sql,nested_sources=notice.sources if module['state']=='loaded' else []))
        checkpoint()
        return rows
    def sql_params(sql,params):
        with session.db.cursor() as cur:
            return cur.mogrify(sql,params).decode(session.db.encoding)
    try:
        clusters=sorted({c['scope_id'] for c in selected})
        result['worst_seconds']=(80*(len(clusters)+1)+32)*args.limit_seconds
        result['budget_basis']='at_most_80_statements_per_scope_plus_32_setup_distribution; includes_EXPLAIN_and_lookups'
        for cluster in clusters+[None]:
            case=max((c for c in selected if cluster is None or c['scope_id']==cluster),key=lambda c:(c['events'],c['id']))
            label=work.alias('cluster',cluster) if cluster is not None else 'all'
            values=dashboards.defaults(documents['mpp-list'])
            values.update(norm=case['norm'],cluster=dashboards.token(cluster) if cluster else '*')
            common={name:(value,'text') for name,value in dict(p_normalization=case['norm'],p_scope=cluster,
                        p_database=None,p_user=None,p_fingerprint=case['fingerprint'],p_order='p95').items()}
            common.update(wanted=(['request','execute_first'],'text[]'),p_min_samples=(0,'bigint'))
            published=session.query("""SELECT b.build_id,b.partition_id FROM current_version v JOIN build b USING(build_id)
                JOIN mpp_result_partition p USING(partition_id) WHERE b.normalization_id=%s
                AND (%s::text IS NULL OR b.scope_id=%s) AND p.cleaned_at IS NULL ORDER BY b.build_id""",
                (case['norm'],cluster,cluster))
            common.update(builds=([r['build_id'] for r in published],'text[]'),partitions=([r['partition_id'] for r in published],'bigint[]'))
            for variable in ('database','user'):
                definition=next(v for v in documents['mpp-list']['templating']['list'] if v['name']==variable)
                actual(dashboards.interpolate(definition['query'],values,now-timedelta(days=1),now),
                       label+'/variable/'+variable,split=True)
            for limit in (50,100,200,500):
                stem=label+'/baseline/'+str(limit)
                parameters=dict(common,p_limit=(limit,'integer'))
                planned('mpp_view_baseline_ranking',parameters,stem+'/internal')
                core=sql_params('SELECT * FROM mpp_view_baseline_ranking(%s,%s,NULL,NULL,%s,%s,0,%s)',
                                (case['norm'],cluster,'request,execute_first','p95',limit))
                rows=actual(core,stem+'/core',split=True,keep=True)
                for i,row in enumerate(rows[:5],1):
                    hit_values=dict(common,p_fingerprint=(row['fingerprint'],'text'),p_scope=(None,'text'))
                    planned('mpp_query_hits',hit_values,stem+'/hit-'+str(i)+'/internal')
                    actual(sql_params('SELECT * FROM mpp_query_hits(%s,%s)',(case['norm'],row['fingerprint'])),
                           stem+'/hit-'+str(i),split=True)
                values['limit']=str(limit)
                panel=next(p for p in documents['mpp-list']['panels'] if p['id']==6)
                actual(dashboards.interpolate(panel['targets'][0]['rawSql'],values,now-timedelta(days=1),now),stem+'/panel-6')
            for days in (1,7,30):
                start,end=now-timedelta(days=days),now
                parameters=dict(common,p_order=('count','text'),p_start=(start,'timestamptz'),p_end=(end,'timestamptz'),p_limit=(100,'integer'))
                planned('mpp_view_ranking',parameters,label+'/range-'+str(days)+'/internal')
                values['limit']='100'
                panel=next(p for p in documents['mpp-list']['panels'] if p['id']==3)
                actual(dashboards.interpolate(panel['targets'][0]['rawSql'],values,start,end),label+'/range-'+str(days)+'/panel-3')
                detail=dashboards.defaults(documents['mpp-detail'])
                detail.update(norm=case['norm'],fp=case['fingerprint'],identity=case['identity'])
                chosen=session.query('SELECT timing FROM mpp_view_timings(%s,%s,%s) LIMIT 1',
                                     (case['norm'],case['fingerprint'],case['identity']))
                detail['timing']=chosen[0]['timing'] if chosen else 'request'
                current=session.query('SELECT build_id FROM mpp_view_versions(%s,%s) WHERE selectable LIMIT 1',
                                      (case['norm'],case['identity']))
                detail['version']=current[0]['build_id'] if current else ''
                base=session.query("""SELECT p50_ms,p95_ms,p99_ms,(sufficiency->'basic'->>'met')::boolean basic,
                    (sufficiency->'p95'->>'met')::boolean p95,(sufficiency->'p99'->>'met')::boolean p99
                    FROM mpp_view_statistics(%s,%s,%s,%s,'overall')
                    WHERE timing_type=%s AND sample_state='available'""",
                    (case['norm'],case['fingerprint'],case['identity'],detail['version'],detail['timing']))
                history=dict(parameters,p_identity=(case['identity'],'text'),p_timing=(detail['timing'],'text'),
                             p_sql_id=('', 'text'),wanted=(detail['status'] or None,'text[]'),
                             chosen=(detail['version'] or None,'text'),p_min_ms=(None,'numeric'),p_max_ms=(None,'numeric'),
                             p_order=('latest','text'),p_limit=(200,'integer'))
                history.update({'base.'+key:((base[0].get(key) if base else None),'boolean' if key in ('basic','p95','p99') else 'numeric')
                                for key in ('p50_ms','p95_ms','p99_ms','basic','p95','p99')})
                planned('mpp_view_executions',history,label+'/history-'+str(days)+'/internal')
                panel=next(p for p in documents['mpp-detail']['panels'] if p['id']==29)
                actual(dashboards.interpolate(panel['targets'][0]['rawSql'],detail,start,end),label+'/history-'+str(days)+'/panel-29')
        if module['state']=='loaded':
            session.query('SET auto_explain.log_min_duration=-1')
        distribution={}
        distribution['days']=session.query("""SELECT scope_id,(end_at AT TIME ZONE 'Asia/Shanghai')::date AS log_day,count(*) events
            FROM mpp_occurrence GROUP BY 1,2 ORDER BY 1,2""")
        distribution['fingerprints']=session.query("""SELECT scope_id,max(events) max_events,max(texts) max_texts,
            percentile_cont(ARRAY[0.5,0.9,0.95,0.99]) WITHIN GROUP(ORDER BY events) event_percentiles,
            percentile_cont(ARRAY[0.5,0.9,0.95,0.99]) WITHIN GROUP(ORDER BY texts) text_percentiles
            FROM (SELECT o.scope_id,f.value,count(*) events,count(DISTINCT o.sql_id) texts
            FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id)
            WHERE f.normalization_id=%s AND f.state='reliable' GROUP BY 1,2) x GROUP BY scope_id""",(selected[0]['norm'],))
        distribution['current_rows']=session.query("""SELECT v.scope_id,count(s.*) rows FROM current_version v
            JOIN build b USING(build_id) JOIN mpp_statistic s ON s.partition_id=b.partition_id AND s.build_id=b.build_id
            GROUP BY v.scope_id ORDER BY v.scope_id""")
        distribution['relations']=session.query("""SELECT c.relname,pg_total_relation_size(c.oid) bytes
            FROM pg_class c WHERE c.oid='mpp_occurrence'::regclass
            OR c.oid IN(SELECT indexrelid FROM pg_index WHERE indrelid='mpp_occurrence'::regclass) ORDER BY c.relname""")
        for rows in distribution.values():
            for row in rows:
                if 'scope_id' in row:
                    row['cluster']=work.alias('cluster',row.pop('scope_id'))
        save(work.public/'distribution.json',distribution)
        result['worst_statement_seconds']=(len(result['shape'])+len(result['measurements'])+len(result['split']))*args.limit_seconds
        checkpoint()
    finally:
        checkpoint(force=True)
