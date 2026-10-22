"""Deterministic per-cluster cases. All real values remain in private state."""
from contextlib import contextmanager
import hashlib
import json
import re
import time

from .core import Refused, save
from .vocabulary import fixed


KINDS = ('executions', 'texts', 'longest', 'history', 'slowest', 'few')
ORDERS = ('events DESC', 'texts DESC', 'chars DESC', 'events DESC',
          'slowest DESC NULLS LAST', 'events ASC')
BATCH = 8


class NoSafeInput(Exception):
    pass


def inputs(text, vocabulary=None, skipped=None):
    vocabulary = vocabulary or fixed()
    skipped = skipped if skipped is not None else []
    words = list(dict.fromkeys(re.findall(r'[A-Za-z_\u0080-\uffff][\w$]*', text)))
    quoted = re.findall(r"'[^'\r\n]{1,80}'|\"[^\"\r\n]{1,80}\"", text)
    common = next((word for word in words if word.lower() == 'select'), words[0] if words else text[:1])
    digest = hashlib.sha256(text.encode()).hexdigest()
    candidates = (
        ('words', 'words', [' '.join(words[:2]) or text[:80]] +
            [' '.join(words[i:i+2]) for i in range(1, len(words))] + words),
        ('quoted', 'passage', quoted + [text[:80], text]),
        ('common', 'words', [common] + words),
        ('absent', 'words', ['apm_perfcheck_absent_'+digest] +
            ['apm_perfcheck_absent_'+digest+'_'+str(i) for i in range(1, BATCH)]),
        ('exact', 'exact', [text]))
    result = []
    for kind, mode, values in candidates:
        for ordinal, value in enumerate(dict.fromkeys(values), 1):
            location = vocabulary.collision(value)
            if value and location is None:
                result.append(dict(kind=kind, mode=mode, text=value))
                break
            skipped.append(dict(input_kind=kind, input_candidate=ordinal,
                reason='fixed_name_collision' if value else 'empty_input',
                location=location or 'input'))
        else:
            raise NoSafeInput
    return result


@contextmanager
def selection_limit(session, seconds):
    """Only this connection/phase changes its limit; restore even after failure."""
    old = session.query('SHOW statement_timeout')[0]['statement_timeout']
    session.query("SELECT set_config('statement_timeout',%s,false)", (str(seconds*1000),))
    try:
        yield
    finally:
        session.query("SELECT set_config('statement_timeout',%s,false)", (old,))


class ClusterBudget:
    def __init__(self, session, seconds):
        self.session, self.deadline = session, time.monotonic()+seconds

    def query(self, sql, params=None):
        from psycopg2.errors import QueryCanceled
        remaining = int((self.deadline-time.monotonic())*1000)
        if remaining < 1:
            raise QueryCanceled
        self.session.query("SELECT set_config('statement_timeout',%s,false)", (str(remaining),))
        return self.session.query(sql, params)


def choose(session, work, extra=None, limit_seconds=600):
    if not 1 <= limit_seconds <= 3600:
        raise Refused('cases_limit_must_be_1_to_3600_seconds')
    coverage = []
    # A failed rerun must never leave an older runnable selection behind.
    (work.private/'cases.json').unlink(missing_ok=True)
    try:
        with selection_limit(session, limit_seconds):
            selected = select_cases(session, work, extra, limit_seconds, coverage)
        save(work.private/'cases.json', selected)
        return selected
    finally:
        work.flush()
        save(work.public/'cases.json', coverage)


def select_cases(session, work, extra, limit_seconds, coverage):
    from psycopg2.errors import QueryCanceled
    vocabulary = fixed(session)

    def required(value, category, location):
        hit = vocabulary.collision(value)
        if hit:
            coverage.append(dict(state='refused', category=category,
                location=location, fixed_location=hit, reason='fixed_name_collision'))
            raise Refused('fixed_name_collision:'+category+':'+location)

    additions = json.loads(extra.read_text()) if extra else []
    if not isinstance(additions, list):
        raise Refused('extra_cases_must_be_array')
    for ordinal, entry in enumerate(additions, 1):
        if not isinstance(entry, dict) or set(entry) - {'cluster','database','user','fingerprint','mode','input'}:
            raise Refused('unknown_extra_case_fields')
        for field in ('cluster','database','user','fingerprint','input'):
            if field in entry:
                required(entry[field], 'operator_'+field, 'extra-'+str(ordinal))
        if 'input' in entry and (entry.get('mode','words') not in ('words','passage','exact')
                or not isinstance(entry['input'],str) or not entry['input']):
            raise Refused('invalid_extra_input')
    norm = session.query('SELECT mpp_view_rules() value')[0]['value']
    clusters = [r['scope_id'] for r in session.query('SELECT scope_id FROM scope ORDER BY scope_id')]
    cases = []
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
    originals_sql = """SELECT t.sql_id,t.text FROM mpp_sql_text t
        JOIN mpp_fingerprint f USING(sql_id) WHERE f.normalization_id=%s AND f.value=%s
        AND EXISTS(SELECT FROM mpp_occurrence o WHERE o.sql_id=t.sql_id AND o.scope_id=%s
            AND o.database IS NOT DISTINCT FROM %s AND o.execution_user IS NOT DISTINCT FROM %s)
        ORDER BY length(t.text) DESC,t.sql_id LIMIT %s OFFSET %s"""

    def original(query, row, cluster, kind, rank):
        offset = 0
        while True:
            originals = query.query(originals_sql, (norm,row['fingerprint'],cluster,
                row['database'],row['execution_user'],BATCH,offset))
            for number, source in enumerate(originals, offset+1):
                skipped = []
                try:
                    generated = inputs(source['text'], vocabulary, skipped)
                except NoSafeInput:
                    generated = None
                for skip in skipped:
                    coverage.append(dict(cluster=work.alias('cluster',cluster), kind=kind,
                        identity_candidate=rank, original_candidate=number, state='skipped', **skip))
                if generated:
                    return dict(row, **source, norm=norm, kind=kind, inputs=generated)
            if len(originals) < BATCH:
                return None
            offset += BATCH

    for cluster in clusters:
        alias = work.alias('cluster', cluster)
        query = ClusterBudget(session, limit_seconds)
        cluster_cases, available = [], []
        pending, offset = set(range(len(KINDS))), 0
        try:
            identities = query.query('SELECT DISTINCT database,execution_user FROM mpp_occurrence WHERE scope_id=%s ORDER BY 1,2', (cluster,))
            for ordinal, row in enumerate(identities, 1):
                for field, category in (('database','database'),('execution_user','user')):
                    required(row[field], category, alias+'/identity-'+str(ordinal))
                    work.alias(category, row[field])
            while pending:
                branches = []
                for kind in sorted(pending):
                    order = ('structure_texts DESC,events DESC' if kind==1 else ORDERS[kind])+',fingerprint,database,execution_user'
                    branches.append('(SELECT '+str(kind)+' kind,i.*,s.structure_texts,'+
                        'row_number() OVER (ORDER BY '+order+') candidate_rank '+
                        'FROM identities i JOIN structures s USING(fingerprint) ORDER BY '+order+
                        ' LIMIT '+str(BATCH)+' OFFSET '+str(offset)+')')
                chosen = query.query(common+' UNION ALL '.join(branches)+' ORDER BY kind,candidate_rank',
                                     (cluster,norm,cluster,norm))
                for kind in sorted(pending):
                    rows = [r for r in chosen if r['kind']==kind]
                    for row in rows:
                        item = original(query, row, cluster, KINDS[kind], row['candidate_rank'])
                        if item is None:
                            continue
                        item['identity'] = query.query('SELECT mpp_view_identity_token(%s,%s,%s) token',
                            (cluster,row['database'],row['execution_user']))[0]['token']
                        item['id'] = work.alias('case', [cluster,row['database'],row['execution_user'],row['fingerprint']])
                        previous = next((c for c in cluster_cases if c['id']==item['id']), None)
                        if previous:
                            previous['kinds'].append(KINDS[kind])
                        else:
                            item['kinds'] = [KINDS[kind]]
                            cluster_cases.append(item)
                        available.append(dict(cluster=alias,kind=KINDS[kind],case=item['id'],state='available',
                            identity_candidate=row['candidate_rank'],events=row['events'],texts=row['texts'],
                            structure_texts=row['structure_texts'],chars=row['chars']))
                        pending.remove(kind)
                        break
                    else:
                        if len(rows) < BATCH:
                            available.append(dict(cluster=alias,kind=KINDS[kind],state='no_safe_reliable_identity'))
                            pending.remove(kind)
                offset += BATCH
        except QueryCanceled:
            # Discard the partial cluster so no category claims full coverage.
            coverage.extend(dict(cluster=alias,kind=kind,state='skipped',reason='selection_timeout',
                                 limit_seconds=limit_seconds) for kind in KINDS)
        else:
            cases.extend(cluster_cases)
            coverage.extend(available)
        finally:
            session.query("SELECT set_config('statement_timeout',%s,false)", (str(limit_seconds*1000),))

    for ordinal, entry in enumerate(additions, 1):
        reference = next((c for c in cases if c['scope_id']==entry.get('cluster',c['scope_id'])), None)
        if reference is None:
            raise Refused('extra_case_cluster_unavailable')
        item = dict(reference, kinds=['operator'], inputs=[])
        for source, dest in (('cluster','scope_id'),('user','execution_user'),('database','database'),('fingerprint','fingerprint')):
            if source in entry:
                item[dest] = entry[source]
        if any(k in entry for k in ('fingerprint','database','user')):
            query = ClusterBudget(session, limit_seconds)
            try:
                found = original(query, item, item['scope_id'], 'operator', ordinal)
                if not found:
                    raise Refused('extra_identity_has_no_safe_original')
                item.update(found)
                item['identity'] = query.query('SELECT mpp_view_identity_token(%s,%s,%s) token',
                    (item['scope_id'],item['database'],item['execution_user']))[0]['token']
            except QueryCanceled:
                coverage.append(dict(cluster=work.alias('cluster',item['scope_id']),kind='operator',
                    state='skipped',reason='selection_timeout',operator_candidate=ordinal,limit_seconds=limit_seconds))
                continue
            finally:
                session.query("SELECT set_config('statement_timeout',%s,false)", (str(limit_seconds*1000),))
        if 'input' in entry:
            item['inputs'].append(dict(kind='operator',mode=entry.get('mode','words'),text=entry['input']))
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
    return cases
