"""The installed dashboards are the query source, including variable dependencies."""
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote

from .core import Refused, digest, measure


class AllValues(list):
    """SQL expands All; URL serialization preserves Grafana's All selection."""


def token(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')


def literal(value):
    return str(value).replace("'", "''")


def interpolate(statement, values, start, end, interval_ms=300000):
    def replace(match):
        name, fmt = match[1], match[2]
        if name == '__url_time_range':
            stamps = [v.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
                      for v in (start,end)]
            return 'from='+stamps[0]+'&to='+stamps[1]+'&timezone=Asia%2FShanghai'
        if name in ('__from','__to'):
            return str(int((start if name=='__from' else end).timestamp()*1000))
        if name not in values:
            raise Refused('unsupported_dashboard_variable')
        value = values[name]
        if fmt == 'queryparam':
            items = ['$__all'] if isinstance(value,AllValues) else value if isinstance(value,list) else [value]
            # Grafana urlUtil.toUrlParams uses its Angular-compatible encoder.
            return '&'.join('var-'+name+'='+quote(str(v), safe='~@:$,;') for v in items)
        if fmt == 'csv':
            return ','.join(map(str, value)) if isinstance(value, list) else str(value)
        if fmt:
            raise Refused('unsupported_dashboard_format')
        if isinstance(value, list):
            return ','.join("'"+literal(v)+"'" for v in value)
        return literal(value)
    result = re.sub(r'\$\{([A-Za-z_][A-Za-z_0-9]*)(?::([a-z]+))?\}', replace, statement)
    for name, value in (('timeFrom', start), ('timeTo', end)):
        # Grafana PostgreSQL macro uses UTC RFC3339Nano, omitting zero fractions.
        stamp = value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        stamp = stamp.replace('.000000Z', 'Z')
        result = result.replace('$__'+name+'()', "'"+stamp+"'")
    result = result.replace('$__interval_ms', str(interval_ms))
    if re.search(r'\$\{|\$__(?!all\b)[a-zA-Z]', result):
        raise Refused('unsupported_dashboard_macro')
    # The pinned PostgreSQL datasource strips leading SQL comments before sending.
    return re.sub(r'^/\*.*?\*/\s*','',result,flags=re.S)


def load(directory):
    result = {}
    for uid in ('mpp-search','mpp-detail','mpp-list','mpp-status'):
        path = Path(directory)/(uid+'.json')
        document = json.loads(path.read_text())
        if document['uid'] != uid:
            raise Refused('dashboard_identity_mismatch')
        result[uid] = document
    return result


def defaults(document):
    result = {}
    for variable in document['templating']['list']:
        value = variable.get('current', {}).get('value', '')
        if value == '$__all' or value == ['$__all']:
            value = variable.get('allValue') or AllValues(o['value'] for o in variable.get('options', []) if o['value'] != '$__all')
        result[variable['name']] = value
    return result


def bounds(document, now):
    match = re.fullmatch(r'now-(\d+)([dhm])', document['time']['from'])
    if not match or document['time']['to'] != 'now':
        raise Refused('unsupported_dashboard_time')
    seconds = int(match[1])*{'d':86400,'h':3600,'m':60}[match[2]]
    return now-timedelta(seconds=seconds), now


def overrides(case, item, exact=None):
    result = dict(fp=case['fingerprint'], identity=case['identity'],
                  cluster=token(case['scope_id']), mode=item['mode'], q=token(item['text']),
                  qd=item['text'], sent='1')
    if item['mode'] == 'exact' and exact:
        result.update(fp=exact.get('fingerprint') or '', xstate=exact['state'],
            xreason=exact.get('reason') or '', xsql=exact.get('exact_sql_id') or '',
            xhints=token(json.dumps(exact.get('statement_hints', []),ensure_ascii=False,separators=(',',':'))),
            xfor=token(item['text']), xbreak=exact.get('line_breaks') or '')
    return result


def target(uid, panel=None):
    if uid == 'mpp-search':
        return 10000
    if uid == 'mpp-detail' and panel in (18,20,21,22,24,29):
        return 5000
    return 1000


def page(session, document, values, start, end, label, replay, trace,
         panel_ids=None, interval_ms=300000):
    uid, all_values = document['uid'], defaults(document)
    all_values.update(values)
    timings = []
    # Textboxes/custom variables are available before query variable dependencies.
    variables = [v for v in document['templating']['list'] if v['type']=='query']
    pending, completed = list(variables), set(all_values)-{v['name'] for v in variables}
    while pending:
        ready = next((v for v in pending if set(re.findall(r'\$\{(\w+)',str(v['query']))) <= completed),None)
        if ready is None:
            raise Refused('cyclic_dashboard_variables')
        query = ready['query']
        if isinstance(query,dict):
            query = query['query']
        sql = interpolate(query,all_values,start,end,interval_ms)
        item = label+'/var/'+ready['name']
        record, rows = measure(session,sql,label=item,replay=replay,keep=True)
        record.update(kind='variable', variable=ready['name'])
        timings.append(record)
        trace.append(dict(item=item,sql=sql))
        if rows:
            first = rows[0]
            column = '__value' if '__value' in first else next(iter(first))
            available = [r[column] for r in rows]
            wanted = all_values[ready['name']]
            all_values[ready['name']] = wanted if wanted in available else available[0]
        else:
            all_values[ready['name']] = ''
        completed.add(ready['name'])
        pending.remove(ready)
    # Dashboard-data-source targets reuse another panel's result, issuing no SQL.
    # Enabled SQL annotations are separate database requests and must be timed too.
    annotations = [a['target'] for a in document.get('annotations',{}).get('list',[])
                   if a.get('enable') and a.get('target',{}).get('rawSql')]
    for query in annotations if panel_ids is None else []:
        sql=interpolate(query['rawSql'],all_values,start,end,interval_ms)
        item=label+'/annotation/'+query['refId']
        record,_=measure(session,sql,label=item,target_ms=5000,replay=replay)
        record.update(kind='annotation',panel='annotation',ref=query['refId'])
        timings.append(record)
        trace.append(dict(item=item,sql=sql))
    panels = document['panels']
    for panel in panels:
        if panel_ids is not None and panel['id'] not in panel_ids:
            continue
        for query in panel.get('targets',[]):
            if not query.get('rawSql') or query.get('hide'):
                continue
            sql=interpolate(query['rawSql'],all_values,start,end,interval_ms)
            item=label+'/panel/'+str(panel['id'])+'/'+query['refId']
            goal=3000 if uid=='mpp-search' and all_values.get('mode')=='exact' else target(uid,panel['id'])
            record,_=measure(session,sql,label=item,target_ms=goal,replay=replay)
            record.update(kind='panel',panel=panel['id'],ref=query['refId'])
            timings.append(record)
            trace.append(dict(item=item,sql=sql))
    chain=sum(r['ms'] for r in timings if r['kind']=='variable')
    # A panel with several targets waits for all of them. Sum those targets here;
    # panels themselves are independent in Grafana's browser request scheduling.
    grouped={}
    for r in timings:
        if r['kind'] in ('panel','annotation'):
            grouped[r['panel']]=grouped.get(r['panel'],0)+r['ms']
    slowest=max(grouped.values(),default=0)
    return dict(item=label,dashboard=uid,statements=timings,variables_ms=chain,
                panels_ms=sum(grouped.values()),slowest_panel_ms=slowest,page_estimate_ms=chain+slowest,
                incomplete=any(r['state']!='ok' for r in timings)), all_values
