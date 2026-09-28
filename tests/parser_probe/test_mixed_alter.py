"""Complete-action, order and atomicity regressions for mixed MPP ALTER."""
import itertools
import json
import unittest
from pglast import parser

from sql_apm.sql.mpp_parser import parse, Unsupported, pg_one


MPP = 'SET WITH (reorganize=true) DISTRIBUTED BY (id)'
PREFIX = 'ALTER TABLE IF EXISTS ONLY ("S"."T") '


def actions(sql):
    return parse(sql)['statements'][0]['extensions']


def spaced(sql):
    return '\n/* ordinary */ '.join(sql[t.start:t.end + 1] for t in parser.scan(sql))


class MixedAlterTests(unittest.TestCase):
    def test_original_collision_is_distinguished_by_column_and_type(self):
        variants = ['ADD COLUMN x int', 'ADD COLUMN y int', 'ADD COLUMN x text']
        trees = [parse('ALTER TABLE t ' + v + ', ' + MPP) for v in variants]
        self.assertEqual(len({json.dumps(t, sort_keys=True) for t in trees}), 3)
        for column, type_name, tree in zip(('x', 'y', 'x'), ('int4', 'int4', 'text'), trees):
            node = tree['statements'][0]['extensions'][0]['command']['AlterTableCmd']['def']['ColumnDef']
            self.assertEqual(node['colname'], column)
            self.assertEqual(node['typeName']['names'][-1]['String']['sval'], type_name)
            self.assertEqual(len(tree['statements'][0]['extensions']), 2)

    def test_ordinary_actions_match_complete_pg_nodes(self):
        ordinary = [
            'ADD COLUMN amount numeric(10,2) DEFAULT round(1.25,1) NOT NULL',
            'ALTER COLUMN amount TYPE numeric(12,3) USING round(amount,3)',
            'DROP COLUMN IF EXISTS old_column CASCADE',
            'ADD CONSTRAINT ck CHECK (amount IN (1,2,3)) NOT VALID',
            'ADD CONSTRAINT fk FOREIGN KEY (id,code) REFERENCES r(id,code) ON DELETE CASCADE',
            'SET (fillfactor=70, autovacuum_enabled=false)',
            'RESET (fillfactor, autovacuum_enabled)',
            'ALTER COLUMN amount SET STATISTICS 100',
        ]
        for action in ordinary:
            expected = pg_one(PREFIX + action)['AlterTableStmt']['cmds'][0]
            for parts in ((action, MPP), (MPP, action), (action, MPP, action)):
                with self.subTest(parts=parts):
                    result = actions(PREFIX + ', '.join(parts))
                    self.assertEqual(len(result), len(parts))
                    for index, part in enumerate(parts):
                        if part == action:
                            self.assertEqual(result[index], {'kind': 'postgres_alter', 'command': expected})
                        else:
                            self.assertEqual(result[index], actions(PREFIX + MPP)[0])

    def test_all_action_permutations_have_distinct_ordered_structures(self):
        parts = ('ADD COLUMN x int', MPP, 'ALTER COLUMN id SET DEFAULT 7')
        references = {part: (actions(PREFIX + part)[0] if part == MPP else
                            {'kind': 'postgres_alter', 'command': pg_one(PREFIX + part)['AlterTableStmt']['cmds'][0]})
                      for part in parts}
        outputs = []
        for order in itertools.permutations(parts):
            result = actions(PREFIX + ', '.join(order))
            self.assertEqual(result, [references[part] for part in order])
            outputs.append(json.dumps(result, sort_keys=True))
        self.assertEqual(len(set(outputs)), 6)

    def test_multiple_mpp_actions_and_option_changes_remain_distinct(self):
        sql = 'ALTER TABLE t SET WITH (reorganize=true), ADD COLUMN x int, SET DISTRIBUTED RANDOMLY'
        result = actions(sql)
        self.assertEqual([a['kind'] for a in result], ['alter_distribution', 'postgres_alter', 'alter_distribution'])
        self.assertEqual(result[0]['options'][0]['DefElem']['arg']['String']['sval'], 'true')
        self.assertEqual(result[2]['policy'], {'kind': 'random'})
        for changed in (sql.replace('true', 'false'), sql.replace('RANDOMLY', 'REPLICATED'),
                        sql.replace('RANDOMLY', 'BY (id)'), sql.replace('x int', 'x bigint')):
            self.assertNotEqual(parse(sql), parse(changed))
        duplicate = actions('ALTER TABLE t SET WITH (reorganize=true), SET WITH (reorganize=true)')
        self.assertEqual(len(duplicate), 2)
        self.assertEqual(duplicate[0], duplicate[1])

    def test_shared_target_flags_and_names_are_preserved(self):
        tail = 'ADD COLUMN x int, ' + MPP
        base = parse(PREFIX + tail)['statements'][0]['base']['AlterTableStmt']
        self.assertTrue(base['missing_ok'])
        self.assertEqual(base['relation']['schemaname'], 'S')
        self.assertEqual(base['relation']['relname'], 'T')
        self.assertFalse(base['relation'].get('inh', False))
        for head in ('ALTER TABLE "S"."T" ', 'ALTER TABLE ONLY "S"."T" ',
                     'ALTER TABLE IF EXISTS ONLY "s"."T" ', 'ALTER TABLE IF EXISTS ONLY "S"."t" '):
            self.assertNotEqual(parse(PREFIX + tail), parse(head + tail))
        self.assertEqual(parse(PREFIX + tail), parse(PREFIX.replace('("S"."T")', '"S"."T"') + tail))
        inherited = parse('ALTER TABLE catalog.s.t * ' + tail)['statements'][0]['base']['AlterTableStmt']
        self.assertEqual(inherited['relation']['catalogname'], 'catalog')
        self.assertTrue(inherited['relation']['inh'])

    def test_nested_commas_strings_quoted_names_and_hints(self):
        sql = ('ALTER TABLE "表,一" ADD COLUMN "x,y" text DEFAULT concat($$a,b;$$, \'/*fake*/,b\'), '
               '/*+ H1 */ SET WITH (reorganize=true), '
               'ADD COLUMN z int[] DEFAULT ARRAY[1,2], '
               '/*+ H2 */ ALTER COLUMN "x,y" SET DEFAULT \'SET WITH, SET DISTRIBUTED\'')
        tree = parse(sql)
        self.assertEqual(len(tree['statements']), 1)
        self.assertEqual(len(tree['statements'][0]['extensions']), 4)
        self.assertEqual([h['raw'] for h in tree['hints']], ['/*+ H1 */', '/*+ H2 */'])
        self.assertEqual(tree, parse(spaced(sql)))
        self.assertNotEqual(tree, parse(sql.replace('/*+ H1 */ SET', 'SET /*+ H1 */')))
        self.assertNotEqual(tree, parse(sql.replace('H2', 'H3')))

    def test_user_action_identical_to_helper_is_not_removed(self):
        ordinary = 'SET (prototype_key=true)'
        sql = 'ALTER TABLE t ' + ordinary + ', ' + MPP + ', ' + ordinary
        result = actions(sql)
        reference = pg_one('ALTER TABLE t ' + ordinary)['AlterTableStmt']['cmds'][0]
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], {'kind': 'postgres_alter', 'command': reference})
        self.assertEqual(result[2], result[0])
        self.assertNotIn('prototype_key', json.dumps(actions('ALTER TABLE t ADD COLUMN x int, ' + MPP)))

    def test_invalid_action_at_any_position_fails_whole_batch(self):
        invalid = [
            'ADD COLUMN', 'ADD COLUMN x', 'ALTER COLUMN x TYPE', 'UNKNOWN ACTION',
            'SET WITH ()', 'SET WITH (reorganize=)', 'SET DISTRIBUTED BY ()',
            'SET DISTRIBUTED BY (id,id)', 'SET DISTRIBUTED BY (id DESC)',
            'SET WITH (reorganize=true) GARBAGE', 'SPLIT PARTITION p AT (5)',
        ]
        for bad in invalid:
            for parts in ((bad, MPP), (MPP, bad), ('ADD COLUMN x int', bad, MPP)):
                with self.subTest(parts=parts):
                    with self.assertRaises(Unsupported):
                        parse('SELECT 1; ALTER TABLE t ' + ', '.join(parts) + '; SELECT 2')
        for tail in (', ' + MPP, MPP + ',', MPP + ',, ADD COLUMN x int',
                     MPP + ' ADD COLUMN x int', 'RENAME TO u, ' + MPP):
            with self.subTest(tail=tail):
                with self.assertRaises(Unsupported):
                    parse('ALTER TABLE t ' + tail)
        for head in ('ALTER TABLE ONLY (s.t, u) ', 'ALTER TABLE ONLY s.t * ', 'ALTER TABLE t alias ',
                     'ALTER TABLE s..t ', 'ALTER TABLE IF t '):
            with self.subTest(head=head):
                with self.assertRaises(Unsupported):
                    parse(head + 'ADD COLUMN x int, ' + MPP)

    def test_regular_pg_actions_and_lookalikes_keep_the_pg_path(self):
        for sql in ("ALTER TABLE t ADD COLUMN x text DEFAULT 'SET WITH (x=1), SET DISTRIBUTED BY (id)'",
                    'ALTER TABLE t ADD COLUMN distributed int, ADD COLUMN x numeric(10,2)',
                    'ALTER TABLE t SET (prototype_key=true), RESET (fillfactor)'):
            result = parse(sql)['statements'][0]
            self.assertEqual(result, {'base': pg_one(sql), 'extensions': []})
        self.assertEqual(len(actions('ALTER TABLE set ADD COLUMN x int, ' + MPP)), 2)

    def test_batch_statement_order_and_format_are_preserved(self):
        sql = 'BEGIN; ALTER TABLE t ADD COLUMN x int, ' + MPP + '; ANALYZE t; COMMIT;'
        tree = parse(sql)
        self.assertEqual([next(iter(s['base'])) for s in tree['statements']],
                         ['TransactionStmt', 'AlterTableStmt', 'VacuumStmt', 'TransactionStmt'])
        self.assertEqual(len(tree['statements'][1]['extensions']), 2)
        self.assertEqual(tree, parse(spaced(sql)))
        self.assertNotEqual(tree, parse(sql.replace('ADD COLUMN x int, ' + MPP, MPP + ', ADD COLUMN x int')))


if __name__ == '__main__':
    unittest.main()
