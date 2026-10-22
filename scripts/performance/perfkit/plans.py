"""Client-only auto_explain notices, preserving plan structure and numeric metrics."""
import json
from pathlib import Path
import re
import shlex

from .core import digest, measure, Refused


TEXT_FIELDS = {'Node Type','Parent Relationship','Join Type','Strategy','Partial Mode',
               'Operation','Scan Direction','Sort Method','Sort Space Type','Cache Mode'}
RELATION_FIELDS = {'Relation Name','Index Name','Function Name','Schema'}


def redact_plan(value, names):
    if isinstance(value,list):
        return [redact_plan(v,names) for v in value]
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            if key in ('Query Text','Query Identifier','Triggers','Settings'):
                continue
            if isinstance(item,(dict,list)):
                result[key]=redact_plan(item,names)
            elif isinstance(item,(int,float,bool)) or item is None:
                result[key]=item
            elif key in TEXT_FIELDS and re.fullmatch(r'[A-Za-z0-9 _-]{1,80}',str(item)):
                result[key]=item
            elif key in RELATION_FIELDS and item in names:
                result[key]=item
            else:
                result[key]='<redacted>'
        return result
    return value if isinstance(value,(int,float,bool)) or value is None else '<redacted>'


class Notices:
    def __init__(self,names):
        self.names,self.plans,self.unparsed,self.sources=names,[],0,[]
    def append(self,text):
        if 'plan:' not in text:
            return
        raw=text[text.index('plan:')+5:].lstrip()
        try:
            value,_=json.JSONDecoder().raw_decode(raw)
        except ValueError:
            self.unparsed+=1
            return
        self.plans.append(redact_plan(value,self.names))
        if isinstance(value,dict) and value.get('Query Text'):
            self.sources.append(value['Query Text'])


def enable(session,directory):
    directory=Path(directory).resolve()
    manifest=json.loads((directory/'module.json').read_text())
    module=directory/'auto_explain.so'
    info={row['name']:row['setting'] for row in session.query('SELECT name,setting FROM pg_config')}
    if (info['VERSION'] != manifest['version'] or shlex.split(info['CONFIGURE']) != shlex.split(manifest['configure'])
            or digest(module) != manifest['module_sha256']):
        return dict(state='skipped',reason='module_server_mismatch')
    # NOTICE reaches the client but is below the WARNING server-log threshold.
    for statement in ("SET log_min_messages=warning","SET client_min_messages=notice"):
        session.query(statement)
    session.query('LOAD %s',(str(module),))
    for name,value in (('log_level','notice'),('log_format','json'),('log_analyze','on'),
                       ('log_buffers','on'),('log_nested_statements','on'),('log_timing','on'),
                       ('log_parameter_max_length','0'),('log_min_duration','0')):
        session.query('SET auto_explain.'+name+'=%s',(value,))
    return dict(state='loaded',module_sha256=digest(module),source_sha256=manifest['source_sha256'])


def names(session):
    rows=session.query("SELECT relname name FROM pg_class UNION SELECT proname FROM pg_proc UNION SELECT nspname FROM pg_namespace")
    return {row['name'] for row in rows}


def actual(session,sql,label,allowed_names):
    notice=Notices(allowed_names)
    session.db.notices=notice
    record,_=measure(session,sql,label=label,upper_limit=True)
    record.update(plans=notice.plans,unparsed_plans=notice.unparsed)
    return record


def shape(session,sql,parameters,label,allowed_names):
    # No ANALYZE: even a ten-minute canceled execution retains this record.
    record,rows=measure(session,'EXPLAIN (FORMAT JSON) '+sql,parameters,label=label,keep=True)
    record['plans']=[redact_plan(row['QUERY PLAN'],allowed_names) for row in rows]
    record['scan']=summary(record['plans'])
    return record


def summary(plans):
    nodes=[]
    def walk(value):
        if isinstance(value,list):
            for item in value:
                walk(item)
        elif isinstance(value,dict):
            if 'Node Type' in value:
                nodes.append(value)
            for item in value.values():
                if isinstance(item,(dict,list)):
                    walk(item)
    walk(plans)
    return dict(sequential_scan=any(n['Node Type'] in ('Seq Scan','Parallel Seq Scan') for n in nodes),
                largest_estimated_rows=max((n.get('Plan Rows',0) for n in nodes),default=0),
                largest_actual_rows=max((n.get('Actual Rows',0)*n.get('Actual Loops',1) for n in nodes),default=0))
