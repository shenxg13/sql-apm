#!/usr/bin/env python3
"""Deterministic full-tree oracles across native PG and adapted MPP grammar."""
import argparse
from collections import Counter
from itertools import product
import json
from pathlib import Path
import platform
import sys
import time

from pglast import parser

from sql_apm.sql.mpp_parser import parse, Unsupported, VERSION
from sql_apm.diagnostics.parser_fidelity import digest
from sql_apm.sql.pg_ast import pg_clean
from sql_apm.diagnostics.mpp_adapter_probe import probe, sha_file, MAX_BYTES

ROOT = Path(__file__).resolve().parents[2]


def pg(sql):
    nodes = json.loads(parser.parse_sql_json(sql))['stmts']
    assert len(nodes) == 1
    return pg_clean(nodes[0]['stmt'])


def native(sql):
    return {'base': pg(sql), 'extensions': []}


def const(value):
    return {'A_Const': {'sval': {'sval': value}}}


def policy(kind):
    if kind in ('random', 'replicated'):
        return {'kind': kind}
    keys = pg('CREATE INDEX i ON t (' + kind + ')')['IndexStmt']['indexParams']
    return {'kind': 'hash', 'keys': keys}


def formatted(sql, style):
    # pglast tokens retain quoted literals and identifiers exactly.
    tokens = parser.scan(sql)
    separator = '\n/* matrix gap */ ' if style else ' \t '
    pieces = [sql[t.start:t.end + 1] + ('\n' if t.name == 'SQL_COMMENT' else '') for t in tokens]
    return separator.join(pieces)


def cases():
    result = []
    def add(name, family, sql, expected=None, classification='supported'):
        result.append(dict(id=name, family=family, sql=sql,
                           expected=expected, classification=classification))
    ordinary = {
        'select_recursive': 'WITH RECURSIVE r(n) AS (VALUES (1) UNION ALL SELECT n+1 FROM r WHERE n<8) SELECT * FROM r',
        'select_window': 'SELECT sum(v) FILTER (WHERE v>0) OVER (PARTITION BY id ORDER BY ts RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t',
        'select_cube': 'SELECT a,b,count(*) FROM t GROUP BY CUBE(a,b) HAVING count(*)>2',
        'select_sets': 'SELECT a,b,sum(v) FROM t GROUP BY GROUPING SETS ((a,b),(a),())',
        'select_lateral': 'SELECT t.id,x.v FROM t LEFT JOIN LATERAL (SELECT v FROM u WHERE u.id=t.id LIMIT 3) x ON true',
        'select_setops': '(SELECT a FROM t UNION ALL SELECT a FROM u) EXCEPT SELECT a FROM v',
        'select_sublink': 'SELECT a FROM t WHERE a=ANY(SELECT b FROM u) AND NOT EXISTS (SELECT 1 FROM v)',
        'select_case': "SELECT CASE WHEN a>0 THEN to_char(ts,'YYYY-MM') ELSE 'missing' END FROM t",
        'select_arrays': 'SELECT ARRAY[1,2,3][1:2]',
        'select_row': 'SELECT ROW(1,2), (a,b) IS DISTINCT FROM (c,d) FROM t',
        'select_json': "SELECT j->'a', j->>'b', jsonb_extract_path(j,'a','b') FROM t",
        'select_xml': "SELECT xmlelement(name foo, xmlattributes(1 AS bar), 'x')",
        'select_cast': "SELECT 's.t'::regclass, CAST('2026-01-01' AS date), interval '1 day', timestamp '2026-01-01'",
        'select_types': "SELECT B'101', X'ff', 1.2e3, -0.0, NULL, true, $1::numeric(10,2)",
        'select_collate': 'SELECT v COLLATE "C" FROM t ORDER BY v DESC NULLS FIRST LIMIT 4 OFFSET 2',
        'select_lock': 'SELECT * FROM t FOR UPDATE OF t NOWAIT',
        'select_quoted': 'SELECT "a,b", "a.b", "中文" FROM "A"."T"',
        'select_dollar': "SELECT $q$'; /*+fake*/ ON SEGMENT$q$, E'a\\nb'",
        'select_into': 'SELECT id INTO TEMP TABLE tmp FROM t',
        'insert_values': "INSERT INTO s.t(a,b) VALUES (1,'x'),(2,'y') RETURNING a",
        'insert_default': 'INSERT INTO t DEFAULT VALUES',
        'insert_query': 'WITH x AS (SELECT a FROM u) INSERT INTO t SELECT a FROM x RETURNING a',
        'update_cte': 'WITH x AS (SELECT id FROM u) UPDATE t SET a=a+1 FROM x WHERE t.id=x.id RETURNING t.*',
        'update_tuple': 'UPDATE t SET (a,b)=(SELECT a,b FROM u WHERE u.id=t.id)',
        'delete_using': 'DELETE FROM t USING u WHERE t.id=u.id RETURNING t.id',
        'create_table': "CREATE TABLE s.t(id int PRIMARY KEY, v numeric(12,3) DEFAULT 1.5 CHECK(v>0), x text[])",
        'create_like': 'CREATE TABLE t(LIKE u INCLUDING ALL)',
        'create_inherit': 'CREATE TABLE t(id int) INHERITS (u)',
        'create_view': 'CREATE OR REPLACE VIEW v(a) AS SELECT id FROM t WITH LOCAL CHECK OPTION',
        'create_matview': 'CREATE MATERIALIZED VIEW v AS SELECT id FROM t WITH NO DATA',
        'create_index': 'CREATE UNIQUE INDEX i ON t USING btree (a DESC NULLS LAST) WHERE b>0',
        'create_sequence': 'CREATE SEQUENCE s START 10 INCREMENT 2 MINVALUE 1 MAXVALUE 100 CYCLE',
        'create_schema': 'CREATE SCHEMA s AUTHORIZATION role1',
        'create_type': "CREATE TYPE mood AS ENUM ('ok','bad')",
        'create_function': 'CREATE FUNCTION f(int) RETURNS int LANGUAGE SQL IMMUTABLE AS $$ SELECT $1+1; $$',
        'alter_constraint': 'ALTER TABLE t ADD CONSTRAINT c CHECK(a>0) NOT VALID, VALIDATE CONSTRAINT c',
        'alter_rename': 'ALTER TABLE t RENAME COLUMN a TO b',
        'drop_objects': 'DROP TABLE IF EXISTS s.a,s.b CASCADE',
        'truncate': 'TRUNCATE t,u RESTART IDENTITY CASCADE',
        'comment': "COMMENT ON COLUMN s.t.v IS 'a; /*+ fake */'",
        'grant': 'GRANT SELECT,UPDATE ON t TO role1 WITH GRANT OPTION',
        'revoke': 'REVOKE SELECT ON t FROM role1 CASCADE',
        'set': "SET LOCAL search_path TO app,public",
        'reset': 'RESET ALL',
        'show': 'SHOW search_path',
        'begin': 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY',
        'commit': 'COMMIT',
        'rollback': 'ROLLBACK TO SAVEPOINT s',
        'savepoint': 'SAVEPOINT s',
        'prepare': 'PREPARE q(int) AS SELECT * FROM t WHERE id=$1',
        'execute': 'EXECUTE q(1)',
        'deallocate': 'DEALLOCATE q',
        'explain': 'EXPLAIN (ANALYZE false, VERBOSE true, FORMAT JSON) SELECT * FROM t',
        'vacuum': 'VACUUM (VERBOSE, ANALYZE) t',
        'analyze': 'ANALYZE t(a,b)',
        'do': 'DO $$ BEGIN PERFORM 1; PERFORM 2; END $$',
        'cursor': 'DECLARE c SCROLL CURSOR WITH HOLD FOR SELECT * FROM t',
        'fetch': 'FETCH FORWARD 2 FROM c',
        'close': 'CLOSE c',
        'copy_generic': "COPY t FROM '/tmp/a' WITH (format csv, header true, on_segment true)",
    }
    # Parentheses are required before subscripting a constructed array.
    ordinary['select_arrays'] = 'SELECT (ARRAY[1,2,3])[1:2]'
    for name, sql in ordinary.items():
        add(name, 'native_pg', sql, [native(sql)])

    policies = [('RANDOMLY', 'random'), ('REPLICATED', 'replicated'),
                ('BY (id)', 'id'), ('BY (v,id pg_catalog.int4_ops)', 'v,id pg_catalog.int4_ops')]
    creates = ['CREATE TABLE s.t(id int,v int)',
               'CREATE TEMP TABLE t(id int,v int) ON COMMIT DROP',
               'CREATE TABLE t(LIKE u INCLUDING DEFAULTS)',
               'CREATE TABLE t(id,v) AS SELECT 1,2 WITH NO DATA',
               'CREATE TABLE t AS WITH x AS (SELECT 1 id,2 v) SELECT * FROM x',
               'CREATE MATERIALIZED VIEW t AS SELECT 1 id,2 v']
    for i, (base, (suffix, kind)) in enumerate(product(creates, policies)):
        expected = {'base': pg(base), 'extensions': [{'kind': 'distribution', 'policy': policy(kind)}]}
        add('distribution_' + str(i), 'distribution', base + ' DISTRIBUTED ' + suffix, [expected])
    for i, row in enumerate(('row', "'row'")):
        sql = 'CREATE TABLE t WITH(appendonly=true,orientation=' + row + ') AS SELECT ROW(1,2) v DISTRIBUTED RANDOMLY'
        base = "CREATE TABLE t WITH(appendonly=true,orientation='row') AS SELECT ROW(1,2) v"
        add('row_compat_' + str(i), 'storage_options', sql,
            [{'base': pg(base), 'extensions': [{'kind': 'distribution', 'policy': policy('random')}]}])

    for i, (mode, web, location, encoding, kind) in enumerate(product(
            ('', 'READABLE ', 'WRITABLE '), ('', 'WEB '),
            (("'a'", ['a']), ("'a','b'", ['a','b'])), ('', " ENCODING 'UTF8'"), ('random','replicated'))):
        sql = 'CREATE ' + mode + 'EXTERNAL ' + web + "TABLE s.e(id int,v text) LOCATION (" + location[0] + ") FORMAT 'CSV' (HEADER DELIMITER '|')" + encoding + ' DISTRIBUTED ' + ('RANDOMLY' if kind == 'random' else 'REPLICATED')
        ext = dict(kind='external_create', mode=mode.strip().lower() or 'default', web=bool(web),
                   source={'kind':'location','values':[const(v) for v in location[1]]}, execution=[],
                   format=const('CSV'), format_options=[{'kind':'legacy','name':'HEADER','value':True},
                                                      {'kind':'legacy','name':'DELIMITER','value':const('|')}],
                   options=None, encodings=[const('UTF8')] if encoding else [], errors=None,
                   distribution=policy(kind))
        add('external_location_' + str(i), 'external_location', sql,
            [{'base': pg('CREATE TABLE s.e(id int,v text)'), 'extensions':[ext]}])
    for i, (clause, execution) in enumerate([
            ('', []), (' ON ALL', [{'kind':'all'}]), (' ON MASTER', [{'kind':'master'}]),
            (' ON HOST', [{'kind':'host','value':None}]),
            (" ON HOST 'host'", [{'kind':'host','value':const('host')}]),
            (' ON SEGMENT 0', [{'kind':'segment','value':0}]), (' ON 2', [{'kind':'count','value':2}]),
            (' ON ALL ON MASTER', [{'kind':'all'},{'kind':'master'}])]):
        sql = "CREATE EXTERNAL WEB TABLE e(id int) EXECUTE 'echo 中文;'" + clause + " FORMAT 'TEXT'"
        ext = dict(kind='external_create', mode='default', web=True,
                   source={'kind':'execute','command':const('echo 中文;')}, execution=execution,
                   format=const('TEXT'), format_options=None, options=None, encodings=[], errors=None, distribution=None)
        add('external_execute_' + str(i), 'external_execute', sql,
            [{'base':pg('CREATE TABLE e(id int)'), 'extensions':[ext]}])

    for i, (web, exists, names, behavior) in enumerate(product(
            ('', 'WEB '), ('', 'IF EXISTS '), ('e', 's.e,s.f'), ('', ' CASCADE'))):
        base = 'DROP TABLE ' + exists + names + behavior
        sql = 'DROP EXTERNAL ' + web + 'TABLE ' + exists + names + behavior
        add('external_drop_' + str(i), 'external_drop', sql,
            [{'base':pg(base), 'extensions':[{'kind':'external_drop','web':bool(web)}]}])
    for i, value in enumerate(('s.demo_in', 'other.demo_in', "'s.demo_in'", '0', '-2', '1.5', 'true', 'row')):
        # Full def_arg oracle from the independent PG reloptions production;
        # GP's explicit ROW -> String("row") compatibility is stated separately.
        arg = pg('CREATE TABLE probe() WITH (formatter=' + ("'row'" if value == 'row' else value) + ')')['CreateStmt']['options'][0]['DefElem']['arg']
        sql = "CREATE EXTERNAL TABLE e(id int) LOCATION ('a') FORMAT 'CUSTOM' (formatter=" + value + ")"
        ext = dict(kind='external_create', mode='default', web=False,
                   source={'kind':'location','values':[const('a')]}, execution=[], format=const('CUSTOM'),
                   format_options=[{'kind':'definition','name':'formatter','value':arg}],
                   options=None, encodings=[], errors=None, distribution=None)
        add('external_custom_' + str(i), 'external_custom', sql,
            [{'base':pg('CREATE TABLE e(id int)'), 'extensions':[ext]}])

    copies = ["COPY s.t(id,v) TO '/tmp/a'", "COPY s.t FROM '/tmp/a'", 'COPY t TO STDOUT',
              'COPY t FROM STDIN', "COPY t TO PROGRAM 'cat'", "COPY (SELECT id FROM t WHERE v=1) TO '/tmp/a'"]
    for i, (base, opts, position) in enumerate(product(copies, ('', ' CSV HEADER', " CSV DELIMITER '|'"), ('before','after'))):
        tail = ' ON SEGMENT' + opts if position == 'before' else opts + ' ON SEGMENT'
        add('copy_' + str(i), 'copy_segment', base + tail,
            [{'base':pg(base + opts), 'extensions':[{'kind':'copy_on_segment','enabled':True}]}])

    targets = ['t', 's.t', '"S"."T"', 'ONLY t', 'ONLY (s.t)', 'IF EXISTS s.t*']
    actions = ['ADD COLUMN x numeric(12,3) DEFAULT 1.5', 'DROP COLUMN IF EXISTS v CASCADE',
               'ALTER COLUMN id SET DEFAULT ARRAY[1,2][1]', 'SET (prototype_key=true)']
    actions[2] = 'ALTER COLUMN id SET DEFAULT (ARRAY[1,2])[1]'
    for i, (target, action, (suffix, kind), before) in enumerate(product(targets, actions, policies, (True, False))):
        prefix = 'ALTER TABLE ' + target + ' '
        ordinary_node = pg(prefix + action)['AlterTableStmt']
        command = ordinary_node.pop('cmds')[0]
        normal = {'kind':'postgres_alter','command':command}
        extension = {'kind':'alter_distribution','options':None,'policy':policy(kind)}
        ordered = [normal,extension] if before else [extension,normal]
        sql_actions = [action,'SET DISTRIBUTED ' + suffix] if before else ['SET DISTRIBUTED ' + suffix, action]
        add('mixed_alter_' + str(i), 'mixed_alter', prefix + ', '.join(sql_actions),
            [{'base':{'AlterTableStmt':ordinary_node}, 'extensions':ordered}])

    representatives = [next(c for c in result if c['family'] == f) for f in
                       ('native_pg','distribution','storage_options','external_location',
                        'external_execute','copy_segment','mixed_alter')]
    for i, (a,b) in enumerate(product(representatives, repeat=2)):
        add('batch_' + str(i), 'batch', a['sql'] + '; ' + b['sql'], a['expected'] + b['expected'])
    for i, case in enumerate(representatives):
        sql = case['sql']
        add('hint_' + str(i), 'hint', '/*+ keep */ ' + sql, case['expected'])
        result[-1]['hints'] = [{'kind':'C_COMMENT','raw':'/*+ keep */','gap':0}]
        add('line_hint_' + str(i), 'hint', '--+ keep\n' + sql, case['expected'])
        result[-1]['hints'] = [{'kind':'SQL_COMMENT','raw':'--+ keep','gap':0}]

    invalid = [
        'INSERT INTO t(a) SELECT 1,',
        "COPY t TO '/tmp/a' ON SEGMENT WITH CSV HEADER",
        "CREATE EXTERNAL TABLE e(id int ENCODING(compresstype=zlib)) LOCATION('a') FORMAT 'TEXT'",
        "COPY t TO PROGRAM ON SEGMENT 'cat'",
        "COPY t FROM PROGRAM ON SEGMENT 'cat'",
        "COPY t TO '/tmp/a' WITH (format csv) ON SEGMENT",
        "COPY t TO '/tmp/a' ON SEGMENT WITH (format csv)",
        "COPY t TO '/tmp/a' ON SEGMENT (format csv)",
        "COPY t TO '/tmp/a' WITH ON SEGMENT (format csv)",
        "COPY t TO '/tmp/a' CSV ON SEGMENT HEADER GARBAGE",
        'CREATE TABLE t(id int) DISTRIBUTED BY (id COLLATE "C")',
        'CREATE TABLE t(id int) DISTRIBUTED BY (id NULLS FIRST)',
        'CREATE TABLE t(id int) DISTRIBUTED BY (id opclass(foo=1))',
        'CREATE TABLE t(id int) DISTRIBUTED BY (id,id)',
        'CREATE TABLE t(id int) DISTRIBUTED BY ()',
        'CREATE TABLE t(id int) DISTRIBUTED RANDOMLY GARBAGE',
        'ALTER TABLE t ADD COLUMN a int,, SET DISTRIBUTED RANDOMLY',
        'ALTER TABLE t SET WITH (reorganize=true), GARBAGE',
        "CREATE EXTERNAL TABLE e(id int) EXECUTE 'echo 1' FORMAT 'TEXT'",
        "CREATE EXTERNAL TABLE e(id int) LOCATION () FORMAT 'TEXT'",
        "CREATE EXTERNAL TABLE e(id int) LOCATION ('a',) FORMAT 'TEXT'",
        "CREATE EXTERNAL TABLE e(id int) LOCATION ('a') FORMAT 'TEXT' OPTIONS (x 'a',)",
        "CREATE EXTERNAL TABLE e(id int) LOCATION ('a') FORMAT 'TEXT' (FORCE NOT NULL *)",
        "CREATE EXTERNAL TABLE e(id int) LOCATION ('a') FORMAT 'TEXT' (formatter=(id+1))",
        "CREATE EXTERNAL TABLE e(id int) LOCATION ('a') FORMAT 'TEXT' SEGMENT REJECT LIMIT 101 PERCENT",
        "CREATE WRITABLE EXTERNAL TABLE e(id int) LOCATION ('a') FORMAT 'TEXT' SEGMENT REJECT LIMIT 2 ROWS",
        'SELECT (1]', 'SELECT [1)', "SELECT 'truncated", 'SELECT 1 /* truncated',
        'SELECT "truncated', 'DO $tag$ truncated', 'SELECT 1\x00', 'SELECT \udcff',
    ]
    for i, sql in enumerate(invalid):
        for pos in range(3):
            parts = ['SELECT 10','SELECT 20']
            parts.insert(pos, sql)
            add('invalid_' + str(i) + '_' + str(pos), 'invalid_batch', '; '.join(parts), classification='invalid')
    gaps = [
        'ANALYZE ROOTPARTITION s.t',
        "CREATE EXTERNAL TABLE e(id int) LOCATION('a') FORMAT 'TEXT' OPTIONS (select 'x')",
        "CREATE TABLE p(id int) DISTRIBUTED BY(id) PARTITION BY RANGE(id) (START(1) END(10) EVERY(1))",
        "CREATE TABLE p(r text) DISTRIBUTED RANDOMLY PARTITION BY LIST(r) (PARTITION a VALUES('A'))",
        'ALTER TABLE p ADD PARTITION p1 START(1) END(2)',
        'ALTER TABLE p EXCHANGE PARTITION p1 WITH TABLE x WITHOUT VALIDATION',
        'ALTER TABLE p SPLIT PARTITION p1 AT(2) INTO (PARTITION p2,PARTITION p3)',
        'ALTER TABLE p TRUNCATE PARTITION p1',
        "COPY t FROM '/tmp/a' FILL MISSING FIELDS",
        "COPY t FROM '/tmp/a' NEWLINE 'LF'",
        "COPY t FROM '/tmp/a' LOG ERRORS SEGMENT REJECT LIMIT 2 ROWS",
        "COPY t TO '/tmp/a' IGNORE EXTERNAL PARTITIONS",
        "SELECT U&'d\\0061t'",
    ]
    for i, sql in enumerate(gaps):
        if i == 0:
            expected = {'base': pg('ANALYZE s.t'),
                        'extensions': [{'kind': 'analyze_rootpartition', 'all': False}]}
        elif i == 2:
            value = lambda n: {'A_Const': {'ival': {'ival': n}}}
            expected = {'base': pg('CREATE TABLE p(id int)'), 'extensions': [
                {'kind': 'distribution', 'policy': policy('id')},
                {'kind': 'range_partition', 'keys': ['id'], 'partitions': [
                    {'name': None, 'default': False, 'bounds': {
                        'start': {'values': [value(1)], 'inclusive': True},
                        'end': {'values': [value(10)], 'inclusive': False}, 'every': [value(1)]},
                     'options': None, 'tablespace': None}]}]}
        elif i == 7:
            base = pg('ALTER TABLE p ADD COLUMN x int')
            base['AlterTableStmt'].pop('cmds')
            expected = {'base': base, 'extensions': [{'kind': 'alter_truncate_partition',
                        'target': {'kind': 'name', 'name': 'p1'}, 'behavior': 'restrict'}]}
        else:
            add('gap_' + str(i), 'known_gap', sql, classification='known_gap')
            continue
        add('gap_' + str(i), 'full_scan_repair', sql, [expected])
    boundaries = ["SELECT 'a\\b'", "SELECT 'a' /*+ hint */\n'b'", 'SELECT /*+ A /*nested*/ */ 1', '', '; /*empty*/ ;', ';']
    for i, sql in enumerate(boundaries):
        add('boundary_' + str(i), 'conservative_boundary', sql, classification='conservative_boundary')
    return result


def evaluate():
    started = time.monotonic()
    records, synthetic = [], cases()
    for case in synthetic:
        expected = case['expected']
        wanted = 'prototype_parsed' if expected is not None else 'unsupported'
        try:
            tree = parse(case['sql'])
            actual = {'state':'prototype_parsed', 'comparison_digest':digest(tree)}
        except Unsupported as exc:
            tree = None
            actual = {'state':'unsupported', 'reason':str(exc)}
        except Exception as exc:
            tree = None
            actual = {'state':'probe_exception', 'exception':type(exc).__name__}
        complete = tree is not None and tree['statements'] == expected and tree['hints'] == case.get('hints', [])
        formatting = []
        if tree:
            for style in range(2):
                try:
                    formatting.append(parse(formatted(case['sql'], style)) == tree)
                except Unsupported:
                    formatting.append(False)
        passed = actual['state'] == wanted and (wanted == 'unsupported' or complete and all(formatting))
        records.append(dict(id=case['id'], family=case['family'], classification=case['classification'],
                            sql=case['sql'], result=actual, expected_state=wanted,
                            full_tree_oracle_passed=complete if expected is not None else None,
                            formatting_checks=formatting, passed=passed))
    # Independent complete-oracle comparison also catches dropped fields; these
    # paired mutations separately demonstrate sensitivity to one changed field.
    pairs = [
        ('SELECT a FROM s.t WHERE v=1','SELECT b FROM s.t WHERE v=1'),
        ('SELECT a FROM s.t WHERE v=1','SELECT a FROM other.t WHERE v=1'),
        ('SELECT a FROM s.t WHERE v=1','SELECT a FROM s.t WHERE v=2'),
        ('SELECT a+1 FROM t','SELECT a-1 FROM t'),
        ("SELECT 'x'::text", "SELECT 'x'::varchar"),
        ('SELECT * FROM t LIMIT 1','SELECT * FROM t LIMIT 2'),
        ('SET work_mem=100','SET work_mem=200'),
        ('SELECT 1; SELECT 2','SELECT 2; SELECT 1'),
        ('SELECT /*+ H */ 1','SELECT /*+ J */ 1'),
        ('SELECT /*+ H */ 1','/*+ H */ SELECT 1'),
        ("COPY t TO '/tmp/a' ON SEGMENT", "COPY t TO '/tmp/b' ON SEGMENT"),
        ("CREATE EXTERNAL TABLE t(id int) LOCATION('a') FORMAT 'CSV'", "CREATE EXTERNAL TABLE t(id int) LOCATION('b') FORMAT 'CSV'"),
    ]
    relations = [dict(left=a, right=b, same=False, passed=parse(a) != parse(b)) for a,b in pairs]
    representatives = []
    for family in sorted({c['family'] for c in synthetic if c['expected'] is not None}):
        case = next(c for c in synthetic if c['family'] == family)
        one, two = probe(case['sql'], synthetic=True), probe(case['sql'], synthetic=True)
        representatives.append(dict(id=case['id'], family=family, passed=one == two and
                                    one.get('tree', {}).get('statements') == case['expected']))
    stress_sql = {
        'batch_128': ';'.join('SELECT ' + str(i) for i in range(128)),
        'columns_128': 'CREATE TABLE t(' + ','.join('c' + str(i) + ' int' for i in range(128)) + ') DISTRIBUTED RANDOMLY',
        'nested_64': 'SELECT ' + 'f(' * 64 + '1' + ')' * 64,
        'hints_64': ';'.join('SELECT /*+ H' + str(i) + ' */ 1' for i in range(64)),
    }
    stress = []
    for name, sql in stress_sql.items():
        one, two = probe(sql, synthetic=True), probe(sql, synthetic=True)
        tree = one.get('tree', {})
        if name == 'batch_128':
            structure = tree.get('statements') == [native('SELECT ' + str(i)) for i in range(128)]
        elif name == 'columns_128':
            structure = tree.get('statements', [{}])[0].get('base') == pg(sql.rsplit(' DISTRIBUTED',1)[0])
        elif name == 'nested_64':
            structure = tree.get('statements') == [native(sql)]
        else:
            structure = len(tree.get('hints', [])) == 64 and len(tree.get('statements', [])) == 64
        stress.append(dict(id=name, bytes=len(sql.encode()), state=one['state'], passed=one == two and structure))
    stress.append(dict(id='probe_size_limit', passed=probe(' ' * (MAX_BYTES + 1))['state'] == 'probe_size_limit'))
    from importlib.metadata import version
    paths = ['sql_apm/sql/pg_ast.py', 'sql_apm/sql/structure.py', 'sql_apm/sql/lexical.py',
             'sql_apm/diagnostics/mpp_broad_matrix.py', 'sql_apm/sql/mpp_parser.py',
             'sql_apm/diagnostics/parser_fidelity.py', 'sql_apm/diagnostics/mpp_adapter_probe.py',
             'sql_apm/diagnostics/statement_census.py']
    return dict(purpose='Overall structural parser oracle, not normalization or execution acceptance',
                python=platform.python_version(), pglast=version('pglast'), prototype=VERSION,
                source_sha256={p:sha_file(ROOT / p) for p in paths},
                cases=records, relations=relations, new_process=representatives, stress=stress,
                summary=dict(Counter(r['classification'] for r in records)),
                passed=all(r['passed'] for r in records + relations + representatives + stress),
                seconds=round(time.monotonic()-started,3))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'passed':result['passed'], 'cases':len(result['cases']), 'summary':result['summary'],
                      'failures':[r['id'] for r in result['cases'] if not r['passed']],
                      'seconds':result['seconds']}))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
