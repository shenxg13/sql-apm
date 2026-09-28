"""Contract-oriented checks for the optional MPP parser experiment."""
import copy
import json
from pathlib import Path
import unittest
from pglast import parser

from sql_apm.sql.mpp_parser import parse, Unsupported

LOCATION = "CREATE EXTERNAL TABLE ext(id int, v text) LOCATION ('gpfdist://example.invalid/a') FORMAT 'CSV'"
WEB = "CREATE EXTERNAL WEB TABLE ext(id int) EXECUTE 'echo 1' ON ALL FORMAT 'TEXT'"
DIST = 'CREATE TABLE s.t(id int, v int) DISTRIBUTED BY (id, v)'


def spaced(sql):
    # Change only lexical gaps, never literal or Hint content.
    return ' \n /* ordinary */ '.join(sql[t.start:t.end + 1] for t in parser.scan(sql))


def extension(sql):
    return parse(sql)['statements'][0]['extensions'][0]


class AdapterTests(unittest.TestCase):
    def test_distribution_structure_and_opclass(self):
        value = extension(DIST)['policy']
        self.assertEqual(value['kind'], 'hash')
        self.assertEqual([x['IndexElem']['name'] for x in value['keys']], ['id', 'v'])
        other = extension('CREATE TABLE t("Key" int) DISTRIBUTED BY ("Key" pg_catalog.int4_ops)')
        key = other['policy']['keys'][0]['IndexElem']
        self.assertEqual(key['name'], 'Key')
        self.assertEqual([x['String']['sval'] for x in key['opclass']], ['pg_catalog', 'int4_ops'])

    def test_distribution_changes_stay_distinct(self):
        variants = [DIST, DIST.replace('(id, v)', '(v, id)'), DIST.replace('(id, v)', '(id)'),
                    DIST.replace('BY (id, v)', 'RANDOMLY'), DIST.replace('BY (id, v)', 'REPLICATED'),
                    DIST.replace('s.t', 'other.t'), DIST.replace('BY (id, v)', 'BY (id int4_ops, v)')]
        self.assertEqual(len({json.dumps(parse(s), sort_keys=True) for s in variants}), len(variants))

    def test_distribution_create_forms(self):
        for sql in ('CREATE TEMP TABLE t AS SELECT id FROM u DISTRIBUTED BY (id)',
                    'CREATE TABLE t AS (SELECT 1 AS id) WITH NO DATA DISTRIBUTED RANDOMLY',
                    'CREATE LOCAL TEMP TABLE t(id int) WITH (appendonly=true) ON COMMIT PRESERVE ROWS DISTRIBUTED BY (id)',
                    'CREATE TABLE distributed(id int)', 'SELECT distributed FROM t'):
            with self.subTest(sql=sql):
                result = parse(sql)
                self.assertEqual(len(result['statements']), 1)
        ctas = parse('CREATE TABLE t AS SELECT 1 AS id DISTRIBUTED RANDOMLY')['statements'][0]
        self.assertIn('CreateTableAsStmt', ctas['base'])
        self.assertEqual(ctas['extensions'][0]['policy']['kind'], 'random')

    def test_external_full_fields(self):
        sql = LOCATION + " (DELIMITER AS ',' NULL '' HEADER QUOTE 'x' ESCAPE 'y' NEWLINE 'LF' FILL MISSING FIELDS FORCE NOT NULL id,v) OPTIONS (formatter 'demo', \"Mode\" 'fast') ENCODING = 'UTF8' LOG ERRORS PERSISTENTLY SEGMENT REJECT LIMIT 10 ROWS DISTRIBUTED RANDOMLY"
        ext = extension(sql)
        self.assertEqual(ext['source']['kind'], 'location')
        self.assertEqual(ext['format']['A_Const']['sval']['sval'], 'CSV')
        self.assertEqual(len(ext['format_options']), 8)
        self.assertEqual(ext['format_options'][-1]['value'], ['id', 'v'])
        self.assertEqual(ext['options'][1]['name'], 'Mode')
        self.assertEqual(ext['encodings'][0]['A_Const']['sval']['sval'], 'UTF8')
        self.assertEqual(ext['errors'], {'log': 'persistent', 'limit': 10, 'unit': 'ROWS', 'explicit_unit': True})
        self.assertEqual(ext['distribution'], {'kind': 'random'})
        base = parse(sql)['statements'][0]['base']['CreateStmt']
        self.assertEqual([x['ColumnDef']['colname'] for x in base['tableElts']], ['id', 'v'])

    def test_external_values_and_flags_are_not_erased(self):
        variants = [LOCATION, LOCATION.replace('/a', '/b'), LOCATION.replace("'CSV'", "'TEXT'"),
                    LOCATION.replace('CREATE EXTERNAL', 'CREATE READABLE EXTERNAL'),
                    LOCATION.replace('CREATE EXTERNAL', 'CREATE WRITABLE EXTERNAL'),
                    LOCATION + " (DELIMITER ',')", LOCATION + " (DELIMITER '|')",
                    LOCATION + " ENCODING 'UTF8'", LOCATION + " ENCODING 'LATIN1'",
                    LOCATION + ' SEGMENT REJECT LIMIT 10 ROWS', LOCATION + ' SEGMENT REJECT LIMIT 11 ROWS',
                    LOCATION + ' SEGMENT REJECT LIMIT 10 PERCENT', LOCATION + ' LOG ERRORS SEGMENT REJECT LIMIT 10 ROWS']
        self.assertEqual(len({json.dumps(parse(s), sort_keys=True) for s in variants}), len(variants))

    def test_external_command_execution_and_location_order(self):
        variants = [WEB, WEB.replace('echo 1', 'echo 2'), WEB.replace('ON ALL', 'ON MASTER'),
                    WEB.replace('ON ALL', "ON HOST 'host-a'"), WEB.replace('ON ALL', 'ON HOST'),
                    WEB.replace('ON ALL', 'ON SEGMENT 1'), WEB.replace('ON ALL', 'ON 2'),
                    WEB.replace('ON ALL', ''), WEB.replace('ON ALL', 'ON ALL ON HOST')]
        self.assertEqual(len({json.dumps(parse(s), sort_keys=True) for s in variants}), len(variants))
        loc = LOCATION.replace("'gpfdist://example.invalid/a'", "'a', 'b'")
        self.assertNotEqual(parse(loc), parse(loc.replace("'a', 'b'", "'b', 'a'")))

    def test_external_custom_format_and_drop(self):
        sql = LOCATION + " (formatter='demo', key=1, enabled=true)"
        values = extension(sql)['format_options']
        self.assertEqual([v['name'] for v in values], ['formatter', 'key', 'enabled'])
        for sql in ('DROP EXTERNAL TABLE IF EXISTS s.ext CASCADE', 'DROP EXTERNAL WEB TABLE ext',
                    "CREATE EXTERNAL TEMP TABLE ext(LIKE src INCLUDING DEFAULTS) LOCATION ('a') FORMAT 'TEXT'"):
            self.assertTrue(parse(sql)['statements'][0]['extensions'])
        self.assertNotEqual(parse('DROP EXTERNAL TABLE ext'), parse('DROP TABLE ext'))

    def test_copy_segment_preserves_query_destination_and_options(self):
        sql = "COPY (SELECT id FROM t WHERE v=1) TO '/tmp/demo' WITH ON SEGMENT CSV HEADER"
        value = parse(sql)['statements'][0]
        self.assertEqual(value['extensions'], [{'kind': 'copy_on_segment', 'enabled': True}])
        self.assertEqual(value['base'], parse(sql.replace(' ON SEGMENT', ''))['statements'][0]['base'])
        self.assertNotEqual(parse(sql), parse(sql.replace(' ON SEGMENT', '')))
        self.assertNotEqual(parse(sql), parse(sql.replace('/tmp/demo', '/tmp/other')))
        self.assertNotEqual(parse(sql), parse(sql.replace('v=1', 'v=2')))

    def test_ordinary_format_does_not_change_structure(self):
        for sql in (DIST, LOCATION + " (DELIMITER ',')", WEB,
                    "COPY t TO '/tmp/demo' ON SEGMENT", 'DROP EXTERNAL TABLE ext'):
            with self.subTest(sql=sql):
                changed = '\n/* leading */ ' + spaced(sql) + ' ; -- trailing\n'
                self.assertEqual(parse(sql), parse(changed))
        self.assertEqual(parse(DIST), parse(DIST.replace('CREATE TABLE', 'create table').replace('DISTRIBUTED BY', 'distributed by')))

    def test_comments_between_lookahead_keywords(self):
        for sql in ('SELECT a NOT IN (1)', 'SELECT a NOT LIKE b',
                    'SELECT a NOT BETWEEN 1 AND 2', 'SELECT a NOT ILIKE b',
                    'SELECT a FROM t ORDER BY a NULLS FIRST',
                    'SELECT a FROM t ORDER BY a NULLS LAST'):
            with self.subTest(sql=sql):
                self.assertEqual(parse(sql), parse(spaced(sql)))
        sql = 'SELECT a NOT /*+ H */ IN (1)'
        self.assertEqual(parse(sql), parse(spaced(sql)))
        self.assertEqual({k: v for k, v in parse(sql)['hints'][0].items() if k != 'anchor'},
                         {'gap': 3, 'kind': 'C_COMMENT', 'raw': '/*+ H */'})
        self.assertEqual(parse(sql)['hints'][0]['anchor']['token_gap'], 3)
        self.assertEqual(parse(sql)['statements'], parse('SELECT a NOT IN (1)')['statements'])
        self.assertEqual(parse("SELECT 'a' /* ordinary */\n'b'"), parse("SELECT 'ab'"))
        self.assertEqual(parse('SELECT 1/*ordinary*/+2'), parse('SELECT 1 + 2'))
        self.assertEqual(parse('SELECT 1 -- ordinary\n + 2'), parse('SELECT 1 + 2'))
        with self.assertRaises(Unsupported):
            parse("SELECT 'a' /* no newline */ 'b'")

    def test_hints_have_stable_content_and_token_gap(self):
        sql = 'CREATE /*+ H1 */ TABLE t(id int) DISTRIBUTED /*+ H2 */ BY (id)'
        result = parse(sql)
        self.assertEqual([h['raw'] for h in result['hints']], ['/*+ H1 */', '/*+ H2 */'])
        self.assertEqual(result, parse(spaced(sql)))
        for changed in (sql.replace('H2', 'H3'), sql.replace(' /*+ H1 */', ''),
                        sql.replace('CREATE /*+ H1 */', '/*+ H1 */ CREATE')):
            self.assertNotEqual(result, parse(changed))
        a = "SELECT '中文'; /*+H*/ " + DIST
        self.assertEqual(parse(a), parse(a.replace('; ', ';\n /* ordinary */ ')))
        self.assertNotEqual(parse(a), parse(a.replace('/*+H*/ ', '') + ' /*+H*/'))
        self.assertEqual(parse("SELECT '/*+fake*/', $$--+fake$$ /* ordinary /*+fake*/ */")['hints'], [])
        self.assertEqual(parse('SELECT --+H\n1')['hints'][0]['raw'], '--+H')

    def test_hints_after_continued_strings_ignore_ordinary_comments(self):
        before = "SELECT 'a'\n'b' /*+ H */"
        self.assertEqual(parse(before), parse("SELECT 'a' /*ordinary*/\n'b' /*+ H */"))
        self.assertEqual(parse(before), parse("SELECT 'a' --ordinary\n'b' /*+ H */"))
        self.assertEqual(parse(before)['hints'][0]['gap'], 2)
        for sql in ("SELECT 'a' /*+ H */\n'b'", "SELECT 'a' --+ H\n'b'"):
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(Unsupported, '^hint_inside_combined_token$'):
                    parse(sql)

    def test_strings_comments_unicode_and_batch_boundaries(self):
        sql = WEB.replace('echo 1', "echo 中文; /*+fake*/ DISTRIBUTED RANDOMLY") + '; SELECT $$a;b$$;'
        result = parse(sql)
        self.assertEqual(len(result['statements']), 2)
        self.assertEqual(result['hints'], [])
        self.assertEqual(len(parse('DO $$ BEGIN PERFORM 1; END $$; ' + DIST)['statements']), 2)
        self.assertNotEqual(parse('SELECT 1; ' + DIST), parse(DIST + '; SELECT 1'))

    def test_invalid_or_unconsumed_extensions_fail_whole_batch(self):
        invalid = [DIST + ' GARBAGE', DIST + ' DISTRIBUTED RANDOMLY', DIST.replace('(id, v)', '()'),
                   DIST.replace('(id, v)', '(id, id)'), DIST.replace('(id, v)', '((id + 1))'),
                   DIST.replace('(id, v)', '(id DESC)'), 'CREATE VIEW v AS SELECT 1 DISTRIBUTED RANDOMLY',
                   LOCATION + ' GARBAGE', LOCATION.replace("('gpfdist://example.invalid/a')", '()'),
                   LOCATION + " (DELIMITER ',' GARBAGE)", LOCATION + " (key='x',)",
                   LOCATION + " OPTIONS (key 'x',)", LOCATION + " ENCODING 'UTF8' FORMAT 'TEXT'",
                   LOCATION + ' LOG ERRORS', LOCATION + ' SEGMENT REJECT LIMIT 1 ROWS',
                   LOCATION + ' SEGMENT REJECT LIMIT 101 PERCENT',
                   LOCATION + ' LOG ERRORS INTO err SEGMENT REJECT LIMIT 10 ROWS',
                   WEB.replace(' EXTERNAL WEB', ' EXTERNAL'),
                   WEB.replace('CREATE EXTERNAL', 'CREATE WRITABLE EXTERNAL'),
                   LOCATION.replace('CREATE EXTERNAL', 'CREATE WRITABLE EXTERNAL') + ' SEGMENT REJECT LIMIT 10 ROWS',
                   LOCATION.replace('id int', 'id int NOT NULL'),
                   "COPY t ON SEGMENT TO '/tmp/demo'", "COPY t TO '/tmp/demo' ON SEGMENT ON SEGMENT",
                   "COPY t TO '/tmp/demo' ON SEGMENT JUNK",
                   'CREATE TABLE t(d date) DISTRIBUTED RANDOMLY PARTITION BY RANGE(d) (START (1+2) END (3))']
        for sql in invalid:
            with self.subTest(sql=sql):
                with self.assertRaises(Unsupported):
                    parse('SELECT 1; ' + sql + '; SELECT 2;')

    def test_alter_distribution_and_reorganize(self):
        variants = ['ALTER TABLE ONLY s.t SET WITH (reorganize=true)',
                    'ALTER TABLE ONLY s.t SET WITH (reorganize=false)',
                    'ALTER TABLE ONLY s.t SET DISTRIBUTED BY (id)',
                    'ALTER TABLE ONLY s.t SET WITH (reorganize=true) DISTRIBUTED BY (id)',
                    'ALTER TABLE s.t SET WITH (reorganize=true)',
                    'ALTER TABLE ONLY s.t SET WITH (reorganize=true) DISTRIBUTED RANDOMLY']
        self.assertEqual(len({json.dumps(parse(s), sort_keys=True) for s in variants}), len(variants))
        node = parse(variants[0])['statements'][0]
        self.assertFalse(node['base']['AlterTableStmt']['relation'].get('inh', False))
        self.assertNotIn('cmds', node['base']['AlterTableStmt'])
        option = node['extensions'][0]['options'][0]['DefElem']
        self.assertEqual(option['defname'], 'reorganize')
        self.assertEqual(option['arg']['String']['sval'], 'true')
        self.assertEqual(parse(variants[0]), parse(spaced(variants[0])))
        self.assertNotEqual(parse(variants[0]), parse(variants[0].replace('SET WITH', 'SET')))
        mixed = parse(variants[0] + ', ADD COLUMN x int')['statements'][0]
        self.assertEqual([a['kind'] for a in mixed['extensions']], ['alter_distribution', 'postgres_alter'])
        for sql in (variants[0] + ' GARBAGE',
                    variants[0] + ' SET WITH (reorganize=false)',
                    'ALTER TABLE t SET WITH (reorganize=true,)'):
            with self.subTest(sql=sql):
                with self.assertRaises(Unsupported):
                    parse(sql)

    def test_uncertain_input_and_hint_rejection(self):
        for sql in ('', ' ; /*empty*/ ;', "SELECT 'abc", 'SELECT 1;\x00 SELECT 2',
                    'SELECT \udcff', "SELECT 'a\\b'", 'SELECT /*+ H /*nested*/ */ 1'):
            with self.subTest(sql=repr(sql)):
                with self.assertRaises(Unsupported):
                    parse(sql)

    def test_input_and_ordinary_ast_preserved(self):
        original = copy.deepcopy(LOCATION)
        parse(LOCATION)
        self.assertEqual(LOCATION, original)
        tree = parse("SELECT to_date('20260101','YYYYMMDD'), ('s.t'::regclass)::text")
        text = json.dumps(tree)
        for atom in ('to_date', 'YYYYMMDD', 'regclass', 'text'):
            self.assertIn(atom, text)
        self.assertNotEqual(tree, parse("SELECT to_date('20260101','yyyymmdd'), ('s.t'::regclass)::text"))


if __name__ == '__main__':
    unittest.main()
