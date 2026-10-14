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
        return dict(notes=notes, candidates=candidates, hints=hints, exact=exact)

    def candidates(self, cur, mode, text, args, sid=None):
        values = [self.norm, mode, text, args.cluster, args.database, args.user,
                  args.start, args.end, args.order, sid]
        cur.execute('''SELECT r.*,t.sql_text AS example FROM mpp_view_search(
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s) r
            LEFT JOIN LATERAL mpp_query_text(r.example_sql_id) t ON true''', values)
        return [dict(row) for row in cur.fetchall()]

    def detail(self, found, mark, args, identity=None, timing=None, limit=20, window=None):
        with self.cursor() as cur:
            if mark.startswith('s'):
                hint = next((h for h in found['hints'] if 's' + str(h['statement']) == mark), None)
                if hint is None:
                    raise ValueError('invalid_mark')
                candidates = self.candidates(cur, 'exact', hint['fingerprint'], args)
                if not candidates:
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
            texts = rows(cur, 'mpp_view_sql_text', [self.norm, fp, chosen['only_sql_id']])
            text = texts[0] if texts else None
            sid = text['sql_id'] if text and text['selected'] else None
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
            executions = rows(cur, 'mpp_view_executions', [self.norm, fp, token, timing, build,
                start, end, sid, None, None, None, 'latest', limit])
            cur.execute('''SELECT outcome,mpp_view_label('outcome',outcome) AS label,count(*) AS records
                FROM mpp_view_records(%s,%s,%s,%s,%s,%s,%s) GROUP BY outcome ORDER BY outcome''',
                (self.norm, fp, token, timing, start, end, sid))
            counts = [dict(row) for row in cur.fetchall()]
            labels = {}
            for kind, codes in [('timing', ['request', 'execute_first', 'execute_fetch', 'parse', 'bind', 'unknown']),
                                ('shape', ['single', 'batch', 'unknown']),
                                ('outcome', ['success', 'failed', 'cancelled', 'timed_out', 'unknown'])]:
                for code in codes:
                    cur.execute('SELECT mpp_view_label(%s,%s) AS label', (kind, code))
                    labels[kind, code] = cur.fetchone()['label']
        return dict(candidate=chosen, text=text, identities=identities, identity=identity, version=version,
                    statistics=statistics, timings=timings, timing=timing, start=start, end=end,
                    limit=limit, custom_window=window is not None,
                    executions=executions, counts=counts, labels=labels)
