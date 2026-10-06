"""PostgreSQL 1.9.0 writer. Identifiers are quoted; values are bound or COPY encoded."""
from collections import OrderedDict
import hashlib
import io
import uuid

import psycopg2
from psycopg2 import sql

from sql_apm.ingestion.config import IngestionError, canonical, identity
from sql_apm.sql.normalization import Normalizer


def connect(dsn, schema):
    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.autocommit = False
    try:
        with connection.cursor() as cur:
            if not 170000 <= connection.server_version < 180000:
                raise IngestionError('postgresql_17_required')
            cur.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            cur.execute("SET TIME ZONE 'Asia/Shanghai'")
            # Consecutive migrations share transaction_timestamp(). Receipts form
            # a version history, so applied_at cannot identify the current version.
            cur.execute('SELECT version FROM schema_version')
            versions = {row[0] for row in cur}
            history = ['1.0.0', '1.1.0', '1.2.0', '1.3.0', '1.4.0', '1.5.0', '1.6.0', '1.7.0', '1.8.0', '1.9.0']
            if versions not in [set(history[i:]) for i in range(len(history))]:
                raise IngestionError('schema_1_9_0_required')
        connection.commit()
        return connection
    except BaseException:
        connection.close()
        raise


def copy_rows(cursor, table, columns, rows):
    if not rows:
        return
    def encode(value):
        if value is None:
            return '\\N'
        if isinstance(value, bool):
            return 't' if value else 'f'
        if isinstance(value, bytes):
            value = '\\x' + value.hex()
        elif hasattr(value, 'isoformat'):
            value = value.isoformat()
        else:
            value = str(value)
        return value.replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
    stream = io.StringIO(''.join('\t'.join(encode(x) for x in row) + '\n' for row in rows))
    command = sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier(table),
                  sql.SQL(',').join(sql.Identifier(c) for c in columns.split(',')))
    cursor.copy_expert(command.as_string(cursor), stream)


class SqlWriter:
    """Short cross-cluster text lock, exact equality and bounded hot result cache.

    Uses its own connection: immutable deduplicated SQL metadata can safely survive
    a file rollback, while every source/evidence/occurrence reference is atomic.
    """
    def __init__(self, connection, pool):
        self.connection, self.pool, self.cache = connection, pool, OrderedDict()
        self.cache_bytes = 0
        context = Normalizer().context
        self.normalization_id = 'N:' + identity(context)
        with connection, connection.cursor() as cur:
            cur.execute('INSERT INTO mpp_normalization VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                        (self.normalization_id, context['algorithm_version'], context['parser_version'],
                         context['dictionary_schema_version'], context['dictionary_rules_version'],
                         'sha256', context['dictionary_digest'], context['rules_ref']))

    def resolve(self, raws):
        unique = dict.fromkeys(raws)
        missing = [raw for raw in unique if raw not in self.cache]
        found = {}
        if missing:
            # Read transaction remains short; expensive parsing is outside the write lock.
            with self.connection, self.connection.cursor() as cur:
                digests = [hashlib.sha256(raw).digest() for raw in missing]
                cur.execute('''SELECT s.text,s.sql_id,f.state,f.value,f.reason FROM mpp_sql_text s
                    JOIN mpp_fingerprint f USING(sql_id) WHERE s.content_sha256=ANY(%s)
                    AND f.normalization_id=%s''', (digests, self.normalization_id))
                for text, sid, state, value, reason in cur:
                    raw = text.encode('utf-8')
                    if raw in unique:
                        found[raw] = dict(sql_id=sid, sql_state='complete', shape=None,
                                         fingerprint=dict(state=state, value=value, reason=reason), approximate_id=None, rule_id=None)
            fresh = [raw for raw in missing if raw not in found or found[raw]['fingerprint']['state'] != 'reliable']
            # Rejected inputs are rare; repeat normalization rebuilds complete approximate metadata.
            parsed = self.pool.map(fresh) if fresh else []
            # The pool retries accepted inputs before returning an isolated failure.
            # Startup/transport failures raise instead and roll back the whole file.
            with self.connection, self.connection.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(1835101, 1)')
                for raw, result in zip(fresh, parsed):
                    sid = None
                    fp = result['fingerprint']
                    if result['sql_state'] == 'complete':
                        text, digest = raw.decode('utf-8'), hashlib.sha256(raw).digest()
                        cur.execute('SELECT sql_id FROM mpp_sql_text WHERE content_sha256=%s AND text=%s', (digest, text))
                        row = cur.fetchone()
                        sid = row[0] if row else 'S:' + uuid.uuid4().hex
                        if not row:
                            cur.execute('INSERT INTO mpp_sql_text VALUES (%s,%s,%s)', (sid, text, digest))
                        cur.execute('''INSERT INTO mpp_fingerprint VALUES (%s,%s,%s,'mpp-csv/1',%s,%s,%s)
                            ON CONFLICT (sql_id,normalization_id,profile) DO NOTHING''',
                            ('F:' + identity(sid, self.normalization_id), sid, self.normalization_id, fp['state'], fp['value'], fp['reason']))
                    result.update(sql_id=sid, approximate_id=None, rule_id=None)
                    approx = result.pop('approximate')
                    if approx is not None:
                        result['approximate_id'], result['rule_id'] = self.approximate(cur, raw, approx)
                    found[raw] = result
            for raw, result in found.items():
                if result['shape'] is None:
                    from sql_apm.sql.lexical import diagnose
                    categories, _ = diagnose(raw.decode('utf-8'))
                    result['shape'] = 'batch' if len(categories) > 1 else 'single' if categories else 'unknown'
                self.cache[raw] = result
                self.cache_bytes += len(raw) + 1024
        result = {raw: self.cache[raw] for raw in unique}
        for raw in unique:
            self.cache.move_to_end(raw)
        while self.cache and self.cache_bytes > 64 * 1024 * 1024:
            raw, _ = self.cache.popitem(last=False)
            self.cache_bytes -= len(raw) + 1024
        return result

    @staticmethod
    def approximate(cur, raw, result):
        rule = result['algorithm_version'] + ':' + result['rules_digest']
        cur.execute('INSERT INTO mpp_approximate_rule VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (rule, result['algorithm_version'], result['profile'], result['rules_digest'], result['rules_ref'], canonical(result['rules'])))
        digest = hashlib.sha256(raw).digest()
        cur.execute('SELECT input_id FROM mpp_approximate_input WHERE source_sha256=%s AND raw_bytes=%s', (digest, raw))
        row = cur.fetchone()
        input_id = row[0] if row else 'AI:' + uuid.uuid4().hex
        if not row:
            cur.execute('INSERT INTO mpp_approximate_input VALUES (%s,%s,%s,%s)', (input_id, raw, len(raw), digest))
        rid = 'AR:' + identity(input_id, rule, result['structural_reason'])
        cur.execute('''INSERT INTO mpp_approximate_result VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unverified',%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (rid, input_id, rule, result['algorithm_version'], result['kind'], result['state'], result['value'],
             result['reason'], result['structural_reason'], result['observation_only'],
             result['source']['bytes_base64'] is not None,
             canonical(result['normalized']) if result['normalized'] is not None else None,
             result['diagnostics'], result['replacements']))
        return rid, rule
