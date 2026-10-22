"""Deterministic per-cluster cases. All real values remain in private state."""
import hashlib
import json
import re

from .core import Refused, save


KINDS = ('executions', 'texts', 'longest', 'history', 'slowest', 'few')
ORDERS = ('events DESC', 'texts DESC', 'chars DESC', 'events DESC',
          'slowest DESC NULLS LAST', 'events ASC')


def inputs(text):
    tokens = re.findall(r'[A-Za-z_\u0080-\uffff][\w$]*', text)
    words = list(dict.fromkeys(tokens))
    quoted = re.search(r"'[^'\r\n]{1,80}'|\"[^\"\r\n]{1,80}\"", text)
    passage = quoted[0] if quoted else text[:80]
    common = next((word for word in words if word.lower() == 'select'), words[0] if words else text[:1])
    return [dict(kind='words', mode='words', text=' '.join(words[:2]) or text[:80]),
            dict(kind='quoted', mode='passage', text=passage),
            dict(kind='common', mode='words', text=common),
            dict(kind='absent', mode='words', text='apm_perfcheck_absent_'+hashlib.sha256(text.encode()).hexdigest()),
            dict(kind='exact', mode='exact', text=text)]


def choose(session, work, extra=None):
    norm = session.query('SELECT mpp_view_rules() value')[0]['value']
    clusters = [r['scope_id'] for r in session.query('SELECT scope_id FROM scope ORDER BY scope_id')]
    for row in session.query('SELECT DISTINCT database,execution_user FROM mpp_occurrence ORDER BY 1,2'):
        work.alias('database', row['database'])
        work.alias('user', row['execution_user'])
    cases, coverage = [], []
    common = """WITH identities AS MATERIALIZED (
        SELECT o.scope_id,o.database,o.execution_user,f.value fingerprint,
            count(*) events,count(DISTINCT f.sql_id) texts,max(length(t.text)) chars,
            max(o.duration_ms) slowest,max(o.end_at) last_at
        FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) JOIN mpp_sql_text t USING(sql_id)
        WHERE o.scope_id=%s AND f.normalization_id=%s AND f.state='reliable' AND f.profile='mpp-csv/1'
        GROUP BY o.scope_id,o.database,o.execution_user,f.value),
        structures AS (SELECT f.value fingerprint,count(DISTINCT f.sql_id) structure_texts
            FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
            WHERE o.scope_id=%s AND f.normalization_id=%s AND f.state='reliable'
            GROUP BY f.value)
    """
    for cluster in clusters:
        alias = work.alias('cluster', cluster)
        branches = ['(SELECT '+str(i)+' kind,i.*,s.structure_texts FROM identities i JOIN structures s USING(fingerprint) ORDER BY '+
                    ('structure_texts DESC,events DESC' if i==1 else order)+
                    ',fingerprint,database,execution_user LIMIT 1)' for i, order in enumerate(ORDERS)]
        chosen = session.query(common + ' UNION ALL '.join(branches), (cluster, norm,cluster,norm))
        for kind in range(len(KINDS)):
            row = next((row for row in chosen if row['kind'] == kind), None)
            if row is None:
                coverage.append(dict(cluster=alias, kind=KINDS[kind], state='no_reliable_identity'))
                continue
            originals = session.query("""SELECT t.sql_id,t.text FROM mpp_sql_text t
                JOIN mpp_fingerprint f USING(sql_id) WHERE f.normalization_id=%s AND f.value=%s
                AND EXISTS(SELECT FROM mpp_occurrence o WHERE o.sql_id=t.sql_id AND o.scope_id=%s
                    AND o.database IS NOT DISTINCT FROM %s AND o.execution_user IS NOT DISTINCT FROM %s)
                ORDER BY length(t.text) DESC,t.sql_id LIMIT 1""",
                (norm, row['fingerprint'], cluster, row['database'], row['execution_user']))
            item = dict(row, **originals[0], norm=norm, kind=KINDS[kind])
            item['identity'] = session.query('SELECT mpp_view_identity_token(%s,%s,%s) token',
                (cluster,row['database'],row['execution_user']))[0]['token']
            identity = [cluster, row['database'], row['execution_user'], row['fingerprint']]
            item['id'] = work.alias('case', identity)
            # Keep category provenance without timing the identical case twice.
            previous = next((c for c in cases if c['id'] == item['id']), None)
            if previous:
                previous['kinds'].append(KINDS[kind])
            else:
                item['kinds'] = [KINDS[kind]]
                item['inputs'] = inputs(item['text'])
                cases.append(item)
            coverage.append(dict(cluster=alias, kind=KINDS[kind], case=item['id'], state='available',
                                 events=row['events'], texts=row['texts'], structure_texts=row['structure_texts'],chars=row['chars']))
    if extra:
        additions = json.loads(extra.read_text())
        if not isinstance(additions, list):
            raise Refused('extra_cases_must_be_array')
        for entry in additions:
            if set(entry) - {'cluster', 'database', 'user', 'fingerprint', 'mode', 'input'}:
                raise Refused('unknown_extra_case_fields')
            reference = next((c for c in cases if c['scope_id'] == entry.get('cluster', c['scope_id'])), None)
            if reference is None:
                raise Refused('extra_case_cluster_unavailable')
            item = dict(reference, kinds=['operator'], inputs=[])
            for source, dest in (('cluster','scope_id'),('user','execution_user'),('database','database'),('fingerprint','fingerprint')):
                if source in entry:
                    item[dest] = entry[source]
            if any(k in entry for k in ('fingerprint','database','user')):
                found = session.query("""SELECT t.sql_id,t.text FROM mpp_fingerprint f JOIN mpp_sql_text t USING(sql_id)
                    WHERE f.normalization_id=%s AND f.value=%s AND EXISTS(SELECT FROM mpp_occurrence o
                        WHERE o.sql_id=t.sql_id AND o.scope_id=%s AND o.database IS NOT DISTINCT FROM %s
                        AND o.execution_user IS NOT DISTINCT FROM %s)
                    ORDER BY length(t.text) DESC,t.sql_id LIMIT 1""",
                    (norm,item['fingerprint'],item['scope_id'],item['database'],item['execution_user']))
                if not found:
                    raise Refused('extra_identity_unavailable')
                item.update(found[0])
                item['inputs'] = inputs(item['text'])
                item['identity']=session.query('SELECT mpp_view_identity_token(%s,%s,%s) token',
                    (item['scope_id'],item['database'],item['execution_user']))[0]['token']
            if 'input' in entry:
                mode = entry.get('mode','words')
                if mode not in ('words','passage','exact') or not isinstance(entry['input'], str) or not entry['input']:
                    raise Refused('invalid_extra_input')
                item['inputs'].append(dict(kind='operator',mode=mode,text=entry['input']))
            item['id'] = work.alias('case', ['operator',entry])
            if not item['inputs']:
                raise Refused('extra_case_requires_identity_or_input')
            cases.append(item)
            coverage.append(dict(cluster=work.alias('cluster',item['scope_id']),kind='operator',case=item['id'],state='available'))
    for case in cases:
        work.secret(case['text'])
        work.secret(case['fingerprint'])
        work.secret(case['sql_id'])
        for value in case['inputs']:
            work.secret(value['text'])
    work.flush()
    save(work.private/'cases.json', cases)
    save(work.public/'cases.json', coverage)
    return cases
