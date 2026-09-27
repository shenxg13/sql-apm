"""Complete structures, negative boundaries and differentiating full-scan repairs."""
import json
import sys
import unittest

from pglast import parser
from sql_apm.sql.mpp_parser import parse, Unsupported, VERSION
from sql_apm.sql.pg_ast import pg_clean
from sql_apm.sql.structure import dumps, loads
from sql_apm.diagnostics.mpp_adapter_probe import probe


def native(sql):
    return pg_clean(loads(parser.parse_sql_json(sql))['stmts'][0]['stmt'])


def tree(sql):
    return parse(sql)['statements'][0]


def constant(number):
    return {'A_Const': {'ival': {'ival': number}}} if number else {'A_Const': {'ival': {}}}


class FullRepairTests(unittest.TestCase):
    def test_analyze_complete_structure(self):
        for keyword in ('ANALYZE', 'ANALYSE'):
            self.assertEqual(tree(keyword + ' VERBOSE ROOTPARTITION "S".t(a,b)'),
                             {'base': native('ANALYZE VERBOSE "S".t(a,b)'),
                              'extensions': [{'kind': 'analyze_rootpartition', 'all': False}]})
            self.assertEqual(tree(keyword + ' ROOTPARTITION ALL'),
                             {'base': native('ANALYZE'),
                              'extensions': [{'kind': 'analyze_rootpartition', 'all': True}]})
        self.assertNotEqual(tree('ANALYZE ROOTPARTITION t'), tree('ANALYZE t'))

    def test_alter_complete_order_and_targets(self):
        base = native('ALTER TABLE ONLY s.t ADD COLUMN n int')['AlterTableStmt']
        native_action = base.pop('cmds')[0]
        actual = tree('ALTER TABLE ONLY s.t ADD COLUMN n int, TRUNCATE PARTITION "P" CASCADE, DROP COLUMN z')
        last = native('ALTER TABLE ONLY s.t DROP COLUMN z')['AlterTableStmt']['cmds'][0]
        self.assertEqual(actual, {'base': {'AlterTableStmt': base}, 'extensions': [
            {'kind': 'postgres_alter', 'command': native_action},
            {'kind': 'alter_truncate_partition', 'target': {'kind': 'name', 'name': 'P'}, 'behavior': 'cascade'},
            {'kind': 'postgres_alter', 'command': last}]})
        cases = (('DEFAULT PARTITION', {'kind': 'default'}),
                 ('PARTITION FOR (1)', {'kind': 'values', 'values': [constant(1)]}),
                 ('PARTITION FOR (rank(2))', {'kind': 'rank', 'value': constant(2)}))
        for text, expected in cases:
            self.assertEqual(tree('ALTER TABLE t TRUNCATE '+text)['extensions'][0]['target'], expected)

    def test_range_complete_structure(self):
        sql = ('CREATE TABLE t(id int) DISTRIBUTED RANDOMLY PARTITION BY RANGE(id) '
               '(PARTITION p START (0) INCLUSIVE END (10) EXCLUSIVE EVERY (2) '
               'WITH (appendonly=true,orientation=row) TABLESPACE ts, DEFAULT PARTITION other)')
        options = native("CREATE TABLE t() WITH (appendonly=true,orientation='row')")['CreateStmt']['options']
        self.assertEqual(tree(sql), {'base': native('CREATE TABLE t(id int)'), 'extensions': [
            {'kind': 'distribution', 'policy': {'kind': 'random'}},
            {'kind': 'range_partition', 'keys': ['id'], 'partitions': [
                {'name': 'p', 'default': False, 'bounds': {
                    'start': {'values': [constant(0)], 'inclusive': True},
                    'end': {'values': [constant(10)], 'inclusive': False}, 'every': [constant(2)]},
                 'options': options, 'tablespace': 'ts'},
                {'name': 'other', 'default': True, 'bounds': {'start': None, 'end': None, 'every': None},
                 'options': None, 'tablespace': None}]}]})

    def test_range_changes_cannot_merge(self):
        sql = ('CREATE TABLE t(id int) PARTITION BY RANGE(id) '
               '(PARTITION p START (1) END (10) WITH (appendonly=true) TABLESPACE ts)')
        changes = (('START (1)', 'START (2)'), ('END (10)', 'END (11)'),
                   ('END (10)', 'END (10) INCLUSIVE'), ('PARTITION p', 'PARTITION q'),
                   ('appendonly=true', 'appendonly=false'), ('TABLESPACE ts', 'TABLESPACE ts2'),
                   ('START (1)', 'START (1::bigint)'), ('END (10)', 'END (10) EVERY (2)'))
        results = [dumps(parse(sql))]
        results.extend(dumps(parse(sql.replace(a, b))) for a, b in changes)
        self.assertEqual(len(set(results)), len(results))
        multi = 'CREATE TABLE t(a int,b int) PARTITION BY RANGE(a,b) (START (1,2) END (3,4))'
        self.assertNotEqual(tree(multi), tree(multi.replace('RANGE(a,b)', 'RANGE(b,a)')))

    def test_legacy_oids_values_and_positions(self):
        for form, flag in (('WITH', 'true'), ('WITHOUT', 'false')):
            sql = 'CREATE TEMP TABLE t(id int) '+form+' OIDS ON COMMIT PRESERVE ROWS'
            self.assertEqual(tree(sql), {'base': native(sql.replace(form+' OIDS', 'WITH (oids='+flag+')')), 'extensions': []})
        self.assertNotEqual(tree('CREATE TABLE t(id int) WITHOUT OIDS'), tree('CREATE TABLE t(id int)'))
        self.assertEqual(tree("CREATE TABLE t AS SELECT 'WITH OIDS'"),
                         {'base': native("CREATE TABLE t AS SELECT 'WITH OIDS'"), 'extensions': []})

    def test_new_constructs_reject_invalid_batches(self):
        invalid = (
            'ANALYZE ROOTPARTITION', 'ANALYZE ROOTPARTITION ALL (a)',
            'ANALYZE ROOTPARTITION t,u', 'ANALYZE ROOTPARTITION VERBOSE t',
            'ALTER TABLE t TRUNCATE DEFAULT PARTITION p', 'ALTER TABLE t TRUNCATE PARTITION',
            'ALTER TABLE t TRUNCATE PARTITION FOR (rank(1,2))',
            'ALTER TABLE t TRUNCATE PARTITION FOR (random())',
            'CREATE TABLE t(id int) ON COMMIT DROP WITH OIDS',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) ()',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) (PARTITION p START (now()))',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) (PARTITION p START (1+2))',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) (PARTITION p START (1,2))',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) (DEFAULT PARTITION p START (1))',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) (PARTITION p EVERY (1))',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) (START (1) END (2),)',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) (START (1) END (2)) DISTRIBUTED RANDOMLY',
            'CREATE TABLE t(id int) PARTITION BY LIST(id) (PARTITION p VALUES (1,2))',
            'CREATE TABLE t(id int) PARTITION BY RANGE(id) SUBPARTITION BY RANGE(id) (START (1) END (2))',
        )
        for sql in invalid:
            for batch in (sql+'; SELECT 1', 'SELECT 1; '+sql, 'SELECT 1; '+sql+'; SELECT 2'):
                with self.subTest(sql=batch), self.assertRaises(Unsupported):
                    parse(batch)

    def test_format_and_hint_anchors(self):
        sql = ('ANALYZE /*+ keep */ ROOTPARTITION t; ALTER TABLE t TRUNCATE PARTITION p; '
               'CREATE TABLE x(id int) WITH OIDS DISTRIBUTED BY(id) PARTITION BY RANGE(id) '
               '(PARTITION p START (1) END (10))')
        changed = '\n/* spacing */ '.join(sql[t.start:t.end+1] for t in parser.scan(sql))
        self.assertEqual(parse(sql), parse(changed))
        self.assertNotEqual(parse(sql), parse(sql.replace('/*+ keep */', '/*+ other */')))
        self.assertNotEqual(parse(sql), parse(sql.replace('ANALYZE /*+ keep */ ROOTPARTITION', 'ANALYZE ROOTPARTITION /*+ keep */')))

    def test_deep_full_tree_survives_worker_transport(self):
        before = sys.getrecursionlimit()
        for kind in ('add', 'union'):
            sql = 'SELECT '+'+'.join(['1']*1024) if kind == 'add' else ' UNION ALL '.join(['SELECT 1']*1024)
            result = probe(sql, synthetic=True)
            self.assertEqual(result['state'], 'prototype_parsed')
            self.assertEqual(dumps(result['tree']['statements'][0]['base']), dumps(native(sql)))
            self.assertEqual(result['tree']['version'], VERSION)
            changed = probe(sql.replace('SELECT 1', 'SELECT 2', 1))
            self.assertNotEqual(result['comparison_digest'], changed['comparison_digest'])
        self.assertEqual(sys.getrecursionlimit(), before)
