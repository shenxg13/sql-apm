"""Exact per-table Python upgrade comparison, with explicit run-ID remapping.

Only private temporary tables are created. Product tables remain untouched;
original text, bytea, numeric, JSON, arrays and NULL values are hashed by PG17.
"""
import hashlib
from psycopg2 import sql

# These are execution timestamps/durations, never source-log or business times.
OMITTED = {
    'schema_version': {'applied_at': 'schema installation timestamp'},
    'import_attempt': {'started_at': 'import execution start', 'finished_at': 'import execution end'},
    'input_snapshot': {'frozen_at': 'snapshot execution timestamp'},
    'build': {'started_at': 'build execution start', 'finished_at': 'build execution end'},
    'publication': {'at': 'publication execution timestamp'},
    'current_version': {'last_success_at': 'publication execution timestamp'},
    'task': {'started_at': 'task execution start', 'finished_at': 'task execution end',
             'stage_seconds': 'measured separately for the performance gate'},
    'mpp_cleanup_month': {'started_at': 'cleanup execution start', 'finished_at': 'cleanup execution end',
                          'exclusive_seconds': 'cleanup execution duration', 'group_seconds': 'cleanup execution duration'},
}

MAPPINGS = {
    'task': ('''SELECT task_id id, scope_id||':'||row_number() OVER
                (PARTITION BY scope_id ORDER BY started_at,task_id) stable FROM task''',
             'cluster and serial task ordinal'),
    'build': ('''SELECT b.build_id id,t.stable FROM build b JOIN task_build tb USING(build_id)
                JOIN cmp_task t ON t.id=tb.task_id''', 'owning task'),
    'input': ('''SELECT b.input_id id,m.stable FROM build b JOIN cmp_build m ON m.id=b.build_id''', 'owning build'),
    'config': ('''SELECT b.config_id id,m.stable FROM build b JOIN cmp_build m ON m.id=b.build_id''', 'owning build'),
    'publication': ('''SELECT p.publication_id id,b.stable FROM publication p
                      JOIN cmp_build b ON b.id=p.build_id''', 'published build'),
    'attempt': ('''SELECT attempt_id id,batch_id||':'||file_id||':'||row_number() OVER
                  (PARTITION BY batch_id,file_id ORDER BY started_at,attempt_id) stable FROM import_attempt''',
                'batch, source file and serial attempt ordinal'),
    'sql': ("SELECT sql_id id,encode(content_sha256,'hex') stable FROM mpp_sql_text", 'original SQL byte checksum'),
    'fingerprint': ('''SELECT f.fingerprint_id id,s.stable||':'||f.normalization_id stable
                      FROM mpp_fingerprint f JOIN cmp_sql s ON s.id=f.sql_id''', 'original SQL and normalization identity'),
    'approximate_input': ("SELECT input_id id,encode(source_sha256,'hex') stable FROM mpp_approximate_input", 'original approximate input byte checksum'),
    'approximate_result': ('''SELECT r.result_id id,i.stable||':'||r.rule_id||':'||r.structural_reason stable
                             FROM mpp_approximate_result r JOIN cmp_approximate_input i ON i.id=r.input_id''',
                           'original approximate input, rule and structural reason'),
    'problem': ('''SELECT p.problem_id id,encode(sha256(convert_to(
                    ((to_jsonb(p)-'problem_id'-'build_id')||jsonb_build_object('build_id',b.stable,
                      'evidence',coalesce((SELECT jsonb_agg(e.record_id ORDER BY e.record_id)
                         FROM problem_evidence e WHERE e.problem_id=p.problem_id),'[]'::jsonb),
                      'attempts',coalesce((SELECT jsonb_agg(a.stable ORDER BY a.stable)
                         FROM attempt_problem ap JOIN cmp_attempt a ON a.id=ap.attempt_id
                         WHERE ap.problem_id=p.problem_id),'[]'::jsonb)))::text,'UTF8')),'hex') stable
                  FROM problem p LEFT JOIN cmp_build b ON b.id=p.build_id''', 'problem content and all source/attempt references'),
}

COMMON = {'task_id': 'task', 'busy_task_id': 'task', 'build_id': 'build', 'previous_build_id': 'build',
          'config_id': 'config', 'publication_id': 'publication', 'attempt_id': 'attempt',
          'final_attempt_id': 'attempt', 'duplicate_of': 'attempt', 'sql_id': 'sql',
          'fingerprint_id': 'fingerprint', 'result_id': 'approximate_result', 'problem_id': 'problem'}


def column_mapping(table, column):
    if column == 'input_id':
        return 'approximate_input' if table in ('mpp_approximate_input', 'mpp_approximate_result') else 'input'
    if column == 'retry_of':
        return 'build' if table == 'build' else 'attempt'
    return COMMON.get(column)


def table_columns(db):
    with db.cursor() as cur:
        cur.execute('''SELECT c.relname,a.attname FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
                       WHERE c.relnamespace='sql_apm'::regnamespace AND c.relkind IN ('r','p')
                       AND NOT c.relispartition AND a.attnum>0 AND NOT a.attisdropped
                       ORDER BY c.relname,a.attnum''')
        tables = {}
        for table, column in cur:
            tables.setdefault(table, []).append(column)
    return tables


def export(db):
    with db.cursor() as cur:
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
        cur.execute("SET work_mem='16MB'; SET max_parallel_workers_per_gather=0")
        for name, (query, _) in MAPPINGS.items():
            cur.execute(sql.SQL('CREATE TEMP TABLE {} ON COMMIT DROP AS ').format(sql.Identifier('cmp_'+name)) + sql.SQL(query))
            cur.execute(sql.SQL('CREATE UNIQUE INDEX ON {} (id)').format(sql.Identifier('cmp_'+name)))
            if name != 'problem':
                cur.execute(sql.SQL('CREATE UNIQUE INDEX ON {} (stable)').format(sql.Identifier('cmp_'+name)))
            cur.execute(sql.SQL('ANALYZE {}').format(sql.Identifier('cmp_'+name)))
        # Derived surrogate IDs must still follow the unchanged product formula.
        for table, identifier, arguments, prefix in (
                ('mpp_fingerprint', 'fingerprint_id', ['sql_id', 'normalization_id'], 'F:'),
                ('mpp_approximate_result', 'result_id', ['input_id', 'rule_id', 'structural_reason'], 'AR:')):
            encoded = sql.SQL("'['||{}||']'").format(sql.SQL("||','||").join(
                sql.SQL('to_json({})::text').format(sql.Identifier(c)) for c in arguments))
            cur.execute(sql.SQL("SELECT count(*) FROM {} WHERE {} <> %s||encode(sha256(convert_to({},'UTF8')),'hex')").format(
                sql.Identifier(table), sql.Identifier(identifier), encoded), (prefix,))
            if cur.fetchone()[0]:
                raise ValueError('derived identity formula differs: '+table)
    tables = table_columns(db)
    if len(tables) != 57:
        raise ValueError('unexpected table set; review comparison coverage')
    report = dict(tables={}, omitted=OMITTED, mapped_identifiers={},
                  method='PG17 JSONB complete rows, sorted per-row SHA-256; exact values, no numeric tolerance')
    for table, columns in tables.items():
        omitted = list(OMITTED.get(table, {}))
        if not set(omitted) <= set(columns):
            raise ValueError('unknown omitted column: '+table)
        base = sql.SQL('to_jsonb(t)-%s::text[]')
        joins, replacements, mappings, missing = [], [], {}, []
        for column in columns:
            mapping = column_mapping(table, column)
            if not mapping:
                continue
            alias = 'm'+str(len(joins))
            joins.append(sql.SQL(' LEFT JOIN {} {} ON {}.id=t.{}').format(
                sql.Identifier('cmp_'+mapping), sql.Identifier(alias), sql.Identifier(alias), sql.Identifier(column)))
            replacements.extend((sql.Literal(column), sql.SQL('{}.stable').format(sql.Identifier(alias))))
            mappings[column] = MAPPINGS[mapping][1]
            missing.append(sql.SQL('(t.{} IS NOT NULL AND {}.id IS NULL)').format(sql.Identifier(column),sql.Identifier(alias)))
        if replacements:
            base = sql.SQL('({})||jsonb_build_object({})').format(base,sql.SQL(',').join(replacements))
        source = sql.SQL(' FROM {} t').format(sql.Identifier(table)) + sql.SQL('').join(joins)
        if missing:
            with db.cursor() as cur:
                cur.execute(sql.SQL('SELECT count(*)')+source+sql.SQL(' WHERE ')+sql.SQL(' OR ').join(missing))
                if cur.fetchone()[0]:
                    raise ValueError('unmapped identity: '+table)
        digest, count = hashlib.sha256(), 0
        with db.cursor(name='python_comparison_rows') as cur:
            cur.itersize = 10000
            cur.execute(sql.SQL("SELECT encode(sha256(convert_to(({})::text,'UTF8')),'hex') h").format(base)
                        +source+sql.SQL(' ORDER BY h'), (omitted,))
            for value, in cur:
                digest.update(value.encode('ascii'))
                count += 1
        report['tables'][table] = dict(rows=count, sha256=digest.hexdigest(), columns=columns)
        if mappings:
            report['mapped_identifiers'][table] = mappings
        print('TABLE '+table+' '+str(count), flush=True)
    db.rollback()  # Drop comparison TEMP tables; never update product rows.
    return report


def compare(left, right):
    if left['omitted'] != right['omitted'] or left['mapped_identifiers'] != right['mapped_identifiers']:
        raise ValueError('comparison contract differs')
    names = sorted(set(left['tables']) | set(right['tables']))
    differences = [name for name in names if left['tables'].get(name) != right['tables'].get(name)]
    return dict(passed=not differences, compared_tables=len(names), different_tables=differences)
