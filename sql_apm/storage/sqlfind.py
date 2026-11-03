"""Read-only adapter for the same query functions used by the search dashboards."""
import base64
from contextlib import contextmanager, ExitStack
from collections import OrderedDict
from copy import deepcopy
import pickle
from datetime import timedelta
import json

from psycopg2.extras import RealDictCursor, Json

from sql_apm.service.fingerprint import Service
from sql_apm.storage.ingestion import connect


def rows(cur, function, values):
    # Function names are internal constants, never supplied by the operator.
    cur.execute('SELECT * FROM ' + function + '(' + ','.join(['%s'] * len(values)) + ')', values)
    return [dict(row) for row in cur.fetchall()]


class QueryCache:
    """Bounded, serialized query results; frozen text catalogs are held separately."""
    def __init__(self, budget=32 * 1024 * 1024):
        self.budget, self.size = budget, 0
        self.items = OrderedDict()

    def get(self, key):
        value = self.items.get(key)
        if value is None:
            return None
        self.items.move_to_end(key)
        return pickle.loads(value)

    def put(self, key, rows):
        value = pickle.dumps(rows)
        cost = len(key) + len(value)
        if cost > self.budget:
            return
        if key in self.items:
            self.size -= len(key) + len(self.items.pop(key))
        while self.size + cost > self.budget:
            old_key, old_value = self.items.popitem(last=False)
            self.size -= len(old_key) + len(old_value)
        self.items[key] = value
        self.size += cost


class CachedCursor:
    """Open one snapshot only on a miss, check revision before any business SQL."""
    def __init__(self, finder, found, stack):
        self.finder, self.found, self.stack = finder, found, stack
        self.pending, self.cur, self.result = {}, None, []

    def execute(self, sql, values=()):
        key = pickle.dumps((sql, tuple(v.adapted if isinstance(v, Json) else v for v in values)))
        result = self.pending.get(key)
        if result is None:
            result = self.found['cache'].get(key)
        if result is None:
            if self.cur is None:
                self.cur = self.stack.enter_context(self.finder.cursor())
                if self.found['check_revision']:
                    self.cur.execute('SELECT revision FROM mpp_query_state')
                    if self.cur.fetchone()['revision'] != self.found['revision']:
                        raise ValueError('records_changed')
            self.cur.execute(sql, values)
            result = [dict(row) for row in self.cur.fetchall()]
            self.pending[key] = result
        # Callers annotate rows. Never mutate either the staged or shared cache.
        self.result = deepcopy(result)

    def fetchall(self):
        result, self.result = self.result, []
        return result

    def fetchone(self):
        return self.result.pop(0) if self.result else None

    def commit(self):
        for key, result in self.pending.items():
            self.found['cache'].put(key, result)


class Finder:
    def __init__(self, dsn, schema):
        self.dsn, self.schema = dsn, schema
        self.service = Service(dsn, schema)
        self.norm = self.service.rules['normalization_id']

    @contextmanager
    def cursor(self):
        db = connect(self.dsn, self.schema)
        try:
            db.set_session(readonly=True, isolation_level='REPEATABLE READ')
            with db, db.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
        finally:
            db.close()

    @contextmanager
    def cached_cursor(self, found):
        # ExitStack closes/commits the short transaction before publishing cache
        # entries. Cancellation or any query/commit failure discards all new data.
        with ExitStack() as stack:
            cur = CachedCursor(self, found, stack)
            yield cur
        cur.commit()

    def search(self, raw, args):
        filters = [args.cluster, args.database, args.user]
        text = raw.decode('utf-8')
        with self.cursor() as cur:
            cur.execute('SELECT revision FROM mpp_query_state')
            revision = cur.fetchone()['revision']
            # The service uses this same snapshot; its tuple cursor is local to
            # the shared connection, not a second connection or transaction.
            with cur.connection.cursor() as exact_cursor:
                exact = self.service.exact(raw, filters, cursor=exact_cursor) if args.mode == 'exact' else {}
            fp, sid = exact.get('fingerprint'), exact.get('exact_sql_id')
            notes = rows(cur, 'mpp_view_search_note', [self.norm, args.mode, text, fp,
                exact.get('state'), exact.get('reason'), sid] + filters)
            orders = self.candidate_orders(cur, args.mode, fp if args.mode == 'exact' else text, args, sid)
            hints = []
            if exact.get('statement_hints'):
                token = base64.urlsafe_b64encode(json.dumps(exact['statement_hints']).encode()).decode().rstrip('=')
                hints = rows(cur, 'mpp_view_hints', [self.norm, token] + filters)
            cur.execute("""SELECT c.scope_id,v.* FROM current_version c CROSS JOIN LATERAL
                mpp_view_versions(%s,mpp_view_identity_token(c.scope_id,NULL,NULL)) v WHERE v.is_current""", (self.norm,))
            versions = {r['scope_id']: dict(r) for r in cur.fetchall()}
        return dict(notes=notes, candidates=orders[args.order], orders=orders, hints=hints, exact=exact,
                    input=text, catalogs={}, cache=QueryCache(), revision=revision,
                    pinned_versions=versions, check_revision=not bool(getattr(args, 'show', None)))

    def reorder(self, found, order):
        return dict(found, candidates=found['orders'][order])

    def candidate_orders(self, cur, mode, text, args, sid):
        from sql_apm.cli.search import fingerprint_input
        if mode == 'exact' or fingerprint_input(text or ''):
            candidates = self.candidates(cur, mode, text, args, sid)
            return dict(count=candidates, recent=candidates)
        if not text or any(n['item']=='没有检索' for n in rows(cur,'mpp_view_search_note',
                [self.norm,mode,text]+[None]*7)):
            return dict(count=[],recent=[])
        cur.execute("""SELECT f.fingerprint,f.example_sql_id,f.matched_texts,f.structure_texts,
            f.record_count,f.identities,f.scopes,f.databases,f.execution_users,f.last_at,f.total_structures,
            mpp_view_identity_token(f.top_scope,f.top_database,f.top_user) top_identity,
            f.top_scope||' / '||coalesce(f.top_database,'（无数据库）')||' / '||coalesce(f.top_user,'（无用户）') top_label,
            f.top_records,f.top_last_at,CASE WHEN f.matched_texts=1 THEN f.example_sql_id END only_sql_id,
            t.sql_text example,f.count_rank,f.recent_rank
            FROM mpp_query_fuzzy_candidates(%s,%s,%s,%s,%s,%s,%s,%s,%s) f
            LEFT JOIN LATERAL mpp_query_text(f.example_sql_id) t ON true""",
            [self.norm,text,args.cluster,args.database,args.user,args.start,args.end,args.order,mode])
        candidates = [dict(row) for row in cur.fetchall()]
        return {order: [{k:v for k,v in row.items() if k not in ('count_rank','recent_rank')}
                        for row in sorted(candidates,key=lambda r:r[order+'_rank']) if row[order+'_rank']<=50]
                for order in ('count','recent')}

    def candidates(self, cur, mode, text, args, sid=None):
        values = [self.norm, mode, text, args.cluster, args.database, args.user,
                  args.start, args.end, args.order, sid]
        cur.execute('''SELECT r.*,t.sql_text AS example FROM mpp_view_search(
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s) r
            LEFT JOIN LATERAL mpp_query_text(r.example_sql_id) t ON true''', values)
        return [dict(row) for row in cur.fetchall()]

    def catalog(self, cur, found, candidate, args):
        """Freeze numbers and search membership for this structure in this search."""
        fp = candidate['fingerprint']
        if fp in found['catalogs']:
            return found['catalogs'][fp]
        terms = None
        if candidate['matched_texts'] is not None:
            function = 'mpp_search_terms' if args.mode == 'words' else 'mpp_search_passage'
            cur.execute('SELECT ' + function + '(%s) AS terms', (found['input'],))
            terms = cur.fetchone()['terms']
        cur.execute('''SELECT k.sql_id,left(t.text,180) AS example,length(t.text) AS chars,
            (%s::text[] IS NULL OR (NOT EXISTS
                (SELECT FROM unnest(%s::text[]) term WHERE strpos(t.search_text,term)=0)
                AND EXISTS (SELECT FROM mpp_query_text_total total JOIN mpp_query_identity i USING(identity_key)
                    JOIN scope c ON c.query_key=i.scope_key
                    WHERE total.rule_key=m.rule_key AND total.sql_key=m.sql_key
                        AND (%s::text IS NULL OR c.scope_id=%s)
                        AND (%s::text IS NULL OR i.database=%s)
                        AND (%s::text IS NULL OR i.execution_user=%s)
                        AND ((%s::timestamptz IS NULL AND %s::timestamptz IS NULL) OR EXISTS (
                            SELECT FROM mpp_query_record r WHERE r.rule_key=m.rule_key AND r.sql_key=m.sql_key
                                AND r.identity_key=i.identity_key AND (%s::timestamptz IS NULL OR r.end_at>=%s)
                                AND (%s::timestamptz IS NULL OR r.end_at<%s)))))) AS matched
            FROM mpp_query_structure s JOIN mpp_normalization n ON n.query_key=s.rule_key
            JOIN mpp_query_membership m USING(structure_key) JOIN mpp_query_text_key k USING(sql_key)
            JOIN mpp_sql_text t USING(sql_id)
            WHERE n.normalization_id=%s AND s.profile='mpp-csv/1' AND s.fingerprint_value=%s ORDER BY k.sql_id''',
            (terms,terms,args.cluster,args.cluster,args.database,args.database,args.user,args.user,
             args.start,args.end,args.start,args.start,args.end,args.end,self.norm,fp))
        return [dict(row, number=n) for n, row in enumerate(cur.fetchall(), 1)]

    def executions(self, cur, fp, token, timing, build, start, end, ids, order, limit, statistics):
        reference = next((s for s in statistics if s['timing_type']==timing and s['sample_state']=='available'), {})
        return rows(cur, 'mpp_view_execution_subset', [self.norm,fp,token,timing,build,start,end,
            ids,None,None,None,order,limit,Json(reference,dumps=lambda value:json.dumps(value,default=str))])

    def originals(self, detail):
        """Compare full texts only when opening the catalog, including zero-count rows."""
        ids = [r['sql_id'] for r in detail['originals'] if r['in_scope']]
        if not ids:
            return detail
        with self.cached_cursor(detail['_found']) as cur:
            # Reuse the dashboard's common-prefix primitive and overlap rule.
            # Work on the entire text in PostgreSQL; return only bounded snippets.
            cur.execute('''WITH texts AS MATERIALIZED (
                    SELECT sql_id,text FROM mpp_sql_text WHERE sql_id=ANY(%s::text[])
                ), shared AS (
                    SELECT mpp_view_common_prefix(min(text COLLATE "C"),max(text COLLATE "C")) head,
                        mpp_view_common_prefix(min(reverse(text) COLLATE "C"),max(reverse(text) COLLATE "C")) tail,
                        min(length(text)) shortest,count(*) n FROM texts
                ), parts AS (
                    SELECT t.sql_id,s.n,substr(t.text,s.head+1,
                        length(t.text)-s.head-least(s.tail,s.shortest-s.head)) part
                    FROM texts t CROSS JOIN shared s
                ) SELECT sql_id,CASE WHEN n=1 THEN '（只有这一份原文）'
                    WHEN part='' THEN '（此处无字符）'
                    WHEN length(part)>160 THEN left(part,100)||' ... '||right(part,50)
                    ELSE part END AS differing FROM parts''', (ids,))
            differences = {r['sql_id']: r['differing'] for r in cur.fetchall()}
        if len(differences) != len(ids):
            raise ValueError('records_changed')
        return dict(detail, originals=[dict(r, differing=differences.get(r['sql_id'])) for r in detail['originals']])

    def detail(self, found, mark, args, identity=None, timing=None, limit=20, window=None,
               order='latest', original=None):
        if order not in ('latest', 'slowest') or not 1 <= limit <= 1000:
            raise ValueError('invalid_history_settings')
        with self.cached_cursor(found) as cur:
            if mark.startswith('s'):
                hint = next((h for h in found['hints'] if 's' + str(h['statement']) == mark), None)
                if hint is None:
                    raise ValueError('invalid_mark')
                candidates = self.candidates(cur, 'exact', hint['fingerprint'], args)
                if not candidates:
                    if original is not None:
                        raise ValueError('no_originals')
                    return dict(hint=hint)
                chosen = candidates[0]
            else:
                try:
                    number = int(mark)
                except ValueError:
                    raise ValueError('invalid_mark') from None
                if number < 1 or number > len(found['candidates']):
                    raise ValueError('invalid_mark')
                chosen = found['candidates'][number - 1]
            fp = chosen['fingerprint']
            identities = rows(cur, 'mpp_view_identities', [self.norm, fp])
            hits = rows(cur, 'mpp_query_hits', [self.norm, fp])
            for item in identities:
                hit = next(h for h in hits if (h['scope_id'], h['database'], h['execution_user']) ==
                           (item['scope_id'], item['database'], item['execution_user']))
                item['first_at'] = hit['first_at']
            if not identities:
                raise ValueError('records_changed')
            if identity is None:
                identity = next((i for i, h in enumerate(identities, 1)
                                 if h['identity'] == chosen['top_identity']), 1)
            if not 1 <= identity <= len(identities):
                raise ValueError('invalid_identity')
            selected = identities[identity - 1]
            token = selected['identity']
            catalog = self.catalog(cur, found, chosen, args)
            if isinstance(original, int):
                selected_text = next((r for r in catalog if r['number'] == original), None)
                if selected_text is None:
                    raise ValueError('invalid_original')
                original = selected_text['sql_id']
            original = original or ('matched' if chosen['matched_texts'] is not None else chosen['only_sql_id'] or 'all')
            scope = [r for r in catalog if original == 'all' or original == 'matched' and r['matched']
                     or original == r['sql_id']]
            if original not in ('all', 'matched') and not scope:
                raise ValueError('invalid_original')
            sid = scope[0]['sql_id'] if scope else None
            texts = rows(cur, 'mpp_view_sql_text', [self.norm, fp, sid]) if sid else []
            text = texts[0] if texts else None
            if text:
                text['selected'] = original not in ('all', 'matched')
            timings = rows(cur, 'mpp_view_timings', [self.norm, fp, token])
            timing = timing or timings[0]['timing']
            if timing not in [t['timing'] for t in timings]:
                raise ValueError('invalid_timing')
            version = found['pinned_versions'].get(selected['scope_id'])
            if version:
                version = {k:v for k,v in version.items() if k!='scope_id'}
            build = version['build_id'] if version and version['selectable'] else None
            statistics = rows(cur, 'mpp_view_statistics', [self.norm, fp, token, build, 'overall'])
            # On entry, the dashboard link anchors the window to the matched identity's
            # last occurrence. Switching identity deliberately anchors its own window.
            last = chosen['top_last_at'] if selected['identity'] == chosen['top_identity'] else selected['last_at']
            start, end = window if window is not None else (last - timedelta(days=7), last + timedelta(milliseconds=1))
            scope_ids = {r['sql_id'] for r in scope}
            executions = self.executions(cur, fp, token, timing, build, start, end,
                None if len(scope) == len(catalog) else sorted(scope_ids), order, limit, statistics)
            cur.execute('''SELECT sql_id,outcome,mpp_view_label('outcome',outcome) AS label,
                count(*) AS records,max(end_at) AS last_at
                FROM mpp_view_record_facts(%s,%s,%s,%s,%s,%s) GROUP BY sql_id,outcome''',
                (self.norm, fp, token, timing, start, end))
            facts = [dict(row) for row in cur.fetchall()]
            originals = {r['sql_id']: dict(r, records=0, last_at=None, in_scope=r['sql_id'] in scope_ids) for r in catalog}
            totals = {}
            for fact in facts:
                item = originals.get(fact['sql_id'])
                if item is None:
                    if original == 'all':
                        raise ValueError('records_changed')
                    continue
                item['records'] += fact['records']
                item['last_at'] = max(item['last_at'], fact['last_at']) if item['last_at'] else fact['last_at']
                if item['in_scope']:
                    total = totals.setdefault(fact['outcome'], dict(outcome=fact['outcome'], label=fact['label'], records=0))
                    total['records'] += fact['records']
            counts = [totals[k] for k in sorted(totals)]
            for record in executions:
                if record['sql_id'] not in originals:
                    raise ValueError('records_changed')
                record['original_number'] = originals[record['sql_id']]['number']
            labels = {}
            for kind, codes in [('timing', ['request', 'execute_first', 'execute_fetch', 'parse', 'bind', 'unknown']),
                                ('shape', ['single', 'batch', 'unknown']),
                                ('outcome', ['success', 'failed', 'cancelled', 'timed_out', 'unknown'])]:
                for code in codes:
                    cur.execute('SELECT mpp_view_label(%s,%s) AS label', (kind, code))
                    labels[kind, code] = cur.fetchone()['label']
        found['catalogs'][fp] = catalog
        return dict(_found=found, candidate=chosen, text=text, identities=identities, identity=identity, version=version,
                    statistics=statistics, timings=timings, timing=timing, start=start, end=end,
                    limit=limit, custom_window=window is not None, order=order,
                    original=original, originals=list(originals.values()),
                    executions=executions, counts=counts, labels=labels)
