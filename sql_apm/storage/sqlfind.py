"""Read-only adapter for the same query functions used by the search dashboards."""
import base64
from contextlib import contextmanager
from datetime import timedelta
import json

from psycopg2.extras import RealDictCursor

from sql_apm.service.fingerprint import Service
from sql_apm.storage.ingestion import connect


def rows(cur, function, values):
    # Function names are internal constants, never supplied by the operator.
    cur.execute('SELECT * FROM ' + function + '(' + ','.join(['%s'] * len(values)) + ')', values)
    return [dict(row) for row in cur.fetchall()]


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

    def search(self, raw, args):
        filters = [args.cluster, args.database, args.user]
        exact = self.service.exact(raw, filters) if args.mode == 'exact' else {}
        text = raw.decode('utf-8')
        fp, sid = exact.get('fingerprint'), exact.get('exact_sql_id')
        with self.cursor() as cur:
            notes = rows(cur, 'mpp_view_search_note', [self.norm, args.mode, text, fp,
                exact.get('state'), exact.get('reason'), sid] + filters)
            candidates = self.candidates(cur, args.mode, fp if args.mode == 'exact' else text, args, sid)
            hints = []
            if exact.get('statement_hints'):
                token = base64.urlsafe_b64encode(json.dumps(exact['statement_hints']).encode()).decode().rstrip('=')
                hints = rows(cur, 'mpp_view_hints', [self.norm, token] + filters)
        return dict(notes=notes, candidates=candidates, hints=hints, exact=exact, input=text, catalogs={})

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
        cur.execute('''SELECT f.sql_id,left(t.text,180) AS example,length(t.text) AS chars,
            (%s::text[] IS NULL OR (NOT EXISTS
                (SELECT FROM unnest(%s::text[]) term WHERE strpos(t.search_text,term)=0)
                AND EXISTS (SELECT FROM mpp_occurrence o WHERE o.sql_id=f.sql_id
                    AND (%s::text IS NULL OR o.scope_id=%s)
                    AND (%s::text IS NULL OR o.database=%s)
                    AND (%s::text IS NULL OR o.execution_user=%s)
                    AND (%s::timestamptz IS NULL OR o.end_at>=%s)
                    AND (%s::timestamptz IS NULL OR o.end_at<%s)))) AS matched
            FROM mpp_fingerprint f JOIN mpp_sql_text t USING(sql_id)
            WHERE f.normalization_id=%s AND f.profile='mpp-csv/1'
                AND f.state='reliable' AND f.value=%s ORDER BY f.sql_id''',
            (terms, terms, args.cluster, args.cluster, args.database, args.database, args.user, args.user,
             args.start, args.start, args.end, args.end, self.norm, fp))
        return [dict(row, number=n) for n, row in enumerate(cur.fetchall(), 1)]

    def executions(self, cur, fp, token, timing, build, start, end, ids, order, limit):
        common = [self.norm, fp, token, timing, build, start, end]
        if ids is None or len(ids) == 1:
            return rows(cur, 'mpp_view_executions', common +
                        [ids[0] if ids else None, None, None, None, order, limit])
        # The global page is selected before enrichment. Each text's rows in that
        # page form a prefix of its own ordered executions, so ask the existing
        # function only for that prefix, preserving its comparison/training rules.
        cur.execute('''WITH page AS MATERIALIZED (
                SELECT r.*,count(*) OVER () AS matching
                FROM mpp_view_records(%s,%s,%s,%s,%s,%s) r WHERE r.sql_id=ANY(%s::text[])
                ORDER BY CASE WHEN %s='slowest' THEN r.duration_ms END DESC NULLS LAST,
                    r.end_at DESC,r.analysis_id DESC,r.occurrence_id DESC LIMIT %s
            ), needed AS (SELECT sql_id,count(*)::integer n FROM page GROUP BY sql_id)
            SELECT e.*,p.matching AS scope_matching FROM needed n
            CROSS JOIN LATERAL mpp_view_executions(%s,%s,%s,%s,%s,%s,%s,
                n.sql_id,NULL,NULL,NULL,%s,n.n) e
            JOIN page p ON p.analysis_id=e.analysis_id AND p.occurrence_id=e.occurrence_id
            ORDER BY CASE WHEN %s='slowest' THEN e.duration_ms END DESC NULLS LAST,
                e.end_at DESC,e.analysis_id DESC,e.occurrence_id DESC''',
            [self.norm, fp, token, timing, start, end, ids, order, limit] + common + [order, order])
        result = [dict(row) for row in cur.fetchall()]
        for row in result:
            row['matching'] = row.pop('scope_matching')
        return result

    def originals(self, detail):
        """Compare full texts only when opening the catalog, including zero-count rows."""
        ids = [r['sql_id'] for r in detail['originals'] if r['in_scope']]
        if not ids:
            return detail
        with self.cursor() as cur:
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
        with self.cursor() as cur:
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
            versions = rows(cur, 'mpp_view_versions', [self.norm, token])
            version = next((v for v in versions if v['is_current']), None)
            build = version['build_id'] if version and version['selectable'] else None
            statistics = rows(cur, 'mpp_view_statistics', [self.norm, fp, token, build, 'overall'])
            # On entry, the dashboard link anchors the window to the matched identity's
            # last occurrence. Switching identity deliberately anchors its own window.
            last = chosen['top_last_at'] if selected['identity'] == chosen['top_identity'] else selected['last_at']
            start, end = window if window is not None else (last - timedelta(days=7), last + timedelta(milliseconds=1))
            scope_ids = {r['sql_id'] for r in scope}
            executions = self.executions(cur, fp, token, timing, build, start, end,
                None if len(scope) == len(catalog) else sorted(scope_ids), order, limit)
            cur.execute('''SELECT sql_id,outcome,mpp_view_label('outcome',outcome) AS label,
                count(*) AS records,max(end_at) AS last_at
                FROM mpp_view_records(%s,%s,%s,%s,%s,%s) GROUP BY sql_id,outcome''',
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
        return dict(candidate=chosen, text=text, identities=identities, identity=identity, version=version,
                    statistics=statistics, timings=timings, timing=timing, start=start, end=end,
                    limit=limit, custom_window=window is not None, order=order,
                    original=original, originals=list(originals.values()),
                    executions=executions, counts=counts, labels=labels)
