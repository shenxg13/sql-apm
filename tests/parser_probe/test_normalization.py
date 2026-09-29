"""Behavioral merge/separation and failure contracts of the complete engine."""
import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sql_apm.sql import mpp_parser
from sql_apm.sql.function_dictionary import DictionaryError, FunctionDictionary
from sql_apm.sql.normalization import DEFAULT_DICTIONARY, MAX_BYTES, Normalizer
from sql_apm.sql.structure import dumps, loads


class NormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Normalizer()

    def result(self, sql):
        r = self.engine.normalize(sql)
        self.assertEqual(r['fingerprint']['state'], 'reliable', r['fingerprint'])
        self.assertIsNone(r['approximate'])
        return r

    def fp(self, sql):
        return self.result(sql)['fingerprint']['value']

    def pairs(self, cases, merge):
        for left, right in cases:
            with self.subTest(left=left, right=right):
                a, b = self.fp(left), self.fp(right)
                (self.assertEqual if merge else self.assertNotEqual)(a, b)

    def test_business_values_and_parameters(self):
        self.pairs([
            ('select * from t where x=1', 'select * from t where x=$2'),
            ("select * from t where x='a'", 'select * from t where x=-5'),
            ('select * from t where x between 1 and 2', 'select * from t where x between $1 and 5'),
            ('select * from t where x in(1,2)', 'select * from t where x in($9,3)'),
            ('select * from t where x=0.5 and y=0', 'select * from t where x=2 and y=-3'),
            ("insert into t(a,b) values(1,'a'),(2,'b')", 'insert into t(a,b) values($1,$2),($3,9)'),
            ("update t set a=1,b='x' where c=2", 'update t set a=$3,b=$1 where c=8'),
            ('update t set (a,b)=(1,2)', 'update t set (a,b)=($1,$2)'),
            ("select * from t where x='a'::text", "select * from t where x='b'::text"),
            ("select * from t where x=cast('a' as varchar(5))", "select * from t where x=cast('b' as varchar(5))"),
            ('delete from t where x=1', 'delete from t where x=2'),
        ], True)

    def test_v5_confirmed_projection_values(self):
        self.pairs([
            ('select 1', 'select 2'), ('select $1', 'select $2'),
            ('select x*.1 from t', 'select x*.2 from t'),
            ('insert into t select 1', 'insert into t select 2'),
            ('select * from t where exists(select 1 from u where y=1)',
             'select * from t where exists(select 2 from u where y=1)'),
        ], True)

    def test_unconfirmed_and_special_values_preserved(self):
        self.pairs([
            ('update t set x=x+1', 'update t set x=x+2'),
            ('insert into t values (1+2)', 'insert into t values (1+3)'),
            ('select * from t where x=true', 'select * from t where x=false'),
            ('select * from t where x=null', 'select * from t where x=$1'),
            ('update t set x=null', 'update t set x=1'),
            ('select case when x=1 then 1 else 2 end from t', 'select case when x=1 then 3 else 2 end from t'),
            ('select * from t where x=case when y=1 then 1 else 2 end', 'select * from t where x=case when y=1 then 3 else 2 end'),
            ('select * from t where x[1]=1', 'select * from t where x[2]=1'),
            ('update t set x[1]=1', 'update t set x[2]=1'),
            ('select * from t having count(*)>1', 'select * from t having count(*)>2'),
        ], False)

    def test_structure_preserved(self):
        self.pairs([
            ('select * from a where x=1', 'select * from b where x=1'),
            ('select * from s.a where x=1', 'select * from z.a where x=1'),
            ('select x from a where x=1', 'select y from a where x=1'),
            ('select * from a where x=1', 'select * from a where x>1'),
            ('select * from a where x=1', 'select * from a where x=1::int'),
            ("select * from a where x='x'::varchar(5)", "select * from a where x='x'::varchar(6)"),
            ('select * from t where x in(1)', 'select * from t where x in(1,2)'),
            ('select * from "T" where x=1', 'select * from t where x=1'),
            ('select * from t where x=$1 and y=$1', 'select * from t where y=$1 and x=$1'),
        ], False)

    def test_control_contexts_opaque(self):
        self.pairs([
            ("set application_name='a'", "set application_name='b'"),
            ('select * from t limit 1', 'select * from t limit 2'),
            ('select * from t limit $1', 'select * from t limit 1'),
            ('select * from t offset 1', 'select * from t offset 2'),
            ("select * from t limit length('a')", "select * from t limit length('b')"),
            ('select sum(x) over(order by y rows 1 preceding) from t', 'select sum(x) over(order by y rows 2 preceding) from t'),
            ('create table t(x int default 1)', 'create table t(x int default 2)'),
            ("create table t(x date default to_date('2020','YYYY'))", "create table t(x date default to_date('2021','YYYY'))"),
        ], False)

    def test_functions_business_control_nested(self):
        self.pairs([
            ("select to_date('2020','YYYY')", "select to_date('2021','YYYY')"),
            ("select to_date($1,'YYYY')", "select to_date('2021','YYYY')"),
            ("select upper('a'::text)", "select upper('b'::text)"),
            ("select to_date(upper('a'::text),'YYYY')", "select to_date(upper('b'::text),'YYYY')"),
        ], True)
        self.pairs([
            ('select sum(1)', 'select sum(2)'),
            ("select to_date('2020','YYYY')", "select to_date('2020','YYMM')"),
            ("select to_date('2020',upper('YYYY'::text))", "select to_date('2020',upper('YYMM'::text))"),
            ("select to_date('a'||'b','YYYY')", "select to_date('a'||'c','YYYY')"),
            ("select x.to_date('2020','YYYY')", "select x.to_date('2021','YYYY')"),
            ("select \"TO_DATE\"('2020','YYYY')", "select \"TO_DATE\"('2021','YYYY')"),
            ("select unknown(to_date('2020','YYYY'))", "select unknown(to_date('2021','YYYY'))"),
            ("select * from t where x=unknown(1)", "select * from t where x=unknown(2)"),
            ("select lower('a')", "select lower('b')"),
        ], False)

    def test_cast_protection_cannot_be_bypassed(self):
        for typename in ('bool','regclass','json','jsonb','uuid','int[]','custom_type','custom.text'):
            for template in ("select * from t where x='{}'::" + typename,
                             "select * from t where x=('{}'::" + typename + ')::text',
                             "select to_date(('{}'::" + typename + ")::text,'YYYY')"):
                self.pairs([(template.format('a'), template.format('b'))], False)

    def test_special_functions_preserve_subtrees(self):
        templates = ["select coalesce(to_date('{}','YYYY'),null)", "select nullif('{}','x')",
                     "select greatest('{}','x')", "select trim('{}')",
                     "select substring('{}' from 1 for 2)", "select extract(year from timestamp '{}')",
                     "select to_date(value => '{}',fmt => 'YYYY')",
                     "select format(variadic ARRAY['{}'])"]
        self.pairs([(t.format('2020'),t.format('2021')) for t in templates], False)

    def test_queries_nested_without_context_leaks(self):
        self.pairs([
            ('with c as (select * from t where x=1) select * from c where x=2',
             'with c as (select * from t where x=3) select * from c where x=4'),
            ('select * from (select * from t where x=1) a', 'select * from (select * from t where x=2) a'),
            ('select * from t where exists(select 1 from u where y=1)', 'select * from t where exists(select 1 from u where y=2)'),
            ('copy (select * from t where x=1) to stdout', 'copy (select * from t where x=2) to stdout'),
            ('create table u as select * from t where x=1 distributed randomly', 'create table u as select * from t where x=2 distributed randomly'),
            ('insert into t values(1) on conflict(a) do update set a=2 where t.a=3', 'insert into t values(4) on conflict(a) do update set a=5 where t.a=6'),
        ], True)
        self.pairs([
            ('select * from t where x=(select y from u limit 1)', 'select * from t where x=(select y from u limit 2)'),
            ('select * from t where x=unknown((select y from u where z=1))', 'select * from t where x=unknown((select y from u where z=2))'),
        ], False)

    def test_mpp_extensions_and_batches(self):
        self.pairs([
            ('create table t(x int) distributed by(x)', 'create table t(x int) distributed randomly'),
            ('alter table t add column x int, set distributed by(x)', 'alter table t add column y int, set distributed by(y)'),
            ('select 1 from a; select 2 from b', 'select 2 from b; select 1 from a'),
            ('copy t to stdout', 'copy t to stdout on segment'),
            ("create table t(x int) distributed by(x) partition by range(x)(start(1) end(9) every(1))", "create table t(x int) distributed by(x) partition by range(x)(start(1) end(8) every(1))"),
        ], False)
        self.pairs([('set work_mem=\'1MB\';select * from t where x=1', 'SET work_mem=\'1MB\'; SELECT * FROM t WHERE x=$1')], True)

    def test_format_and_hint_boundaries(self):
        self.pairs([
            ('select * from t where x=1', 'SELECT /* ordinary */ * FROM t\n WHERE x=2 -- note\n;'),
            ('select /*+ H */ * from t where x=1', 'SELECT /* note */ /*+ H */ * FROM t WHERE x=2'),
            ('select --+ H\n * from t where x=1', 'SELECT --+ H\n * FROM t WHERE x=2'),
            ("select 'a'\n'b' /*+ H */", "select 'a'\n/*note*/ 'b' /*+ H */"),
        ], True)
        self.pairs([
            ('select /*+ H */ 1', 'select /*+ J */ 1'),
            ('select /*+ H */ 1', 'select 1 /*+ H */'),
            ('select /*+ H */ 1', 'select /* H */ 1'),
            ('select /*+ H */ 1', 'select /*+  H */ 1'),
        ], False)

    def test_source_and_rule_artifacts_are_independent(self):
        raw = b'SELECT * FROM t WHERE x=1;\r\n'
        r = self.result(raw)
        self.assertEqual(base64.b64decode(r['source']['bytes_base64']), raw)
        r['context']['algorithm_version'] = 'bad'
        r['normalized'].clear()
        snapshot = self.engine.rule_snapshot()
        snapshot['dictionary']['rules'][0]['enabled'] = False
        self.assertEqual(self.result(raw)['context']['algorithm_version'], 'sql-normalization/5')
        self.assertEqual(self.result(raw), self.result(raw))

    def test_fixed_dictionary_changes_are_traceable(self):
        original = FunctionDictionary.load(DEFAULT_DICTIONARY)
        engine = Normalizer(original)
        original._data['rules'].clear()
        self.assertEqual(engine.context, self.engine.context)
        data = self.engine.rule_snapshot()['dictionary']
        for rule in data['rules']:
            if rule['name'] == 'to_date':
                rule['enabled'] = False
        changed = Normalizer(FunctionDictionary(data))
        a = changed.normalize("select to_date('2020','YYYY')")
        b = changed.normalize("select to_date('2021','YYYY')")
        self.assertNotEqual(a['fingerprint']['value'], b['fingerprint']['value'])
        self.assertNotEqual(changed.context['dictionary_digest'], self.engine.context['dictionary_digest'])
        self.assertNotEqual(changed.context['rules_ref'], self.engine.context['rules_ref'])
        self.assertEqual(a['diagnostics']['function_disabled_rule'], 1)
        # Same semantic artifact in a different order has the same identity.
        data = self.engine.rule_snapshot()['dictionary']
        data['rules'].reverse()
        self.assertEqual(Normalizer(FunctionDictionary(data)).context, self.engine.context)

    def test_pending_policy_preserves_whole_call(self):
        data = self.engine.rule_snapshot()['dictionary']
        for r in data['rules']:
            if r['name'] == 'to_date':
                r['decision'] = 'pending'
                for arg in r['arguments']:
                    arg['action'] = 'preserve'
        engine = Normalizer(FunctionDictionary(data))
        r = engine.normalize("select to_date(upper('a'::text),'YYYY')")
        self.assertEqual(r['diagnostics'], {'function_pending_review': 1})

    def test_failures_do_not_create_structural_identity(self):
        for sql in ('', '/*comment*/', 'select * from t where x in(1,', 'select 1; bad syntax', b'select \xff', 'select \x00'):
            with self.subTest(sql=sql):
                r = self.engine.normalize(sql)
                self.assertEqual(r['fingerprint']['state'], 'unsupported_syntax')
                self.assertIsNone(r['fingerprint']['value'])
                self.assertIsNone(r['normalized'])
                if r['approximate']['state'] == 'available':
                    self.assertTrue(r['approximate']['observation_only'])
                    self.assertTrue(r['approximate']['value'].startswith('approx:'))
        with self.assertRaises(TypeError):
            self.engine.normalize(None)

    def test_resource_and_internal_errors_do_not_fallback(self):
        r = self.engine.normalize('x' * (MAX_BYTES + 1))
        self.assertEqual(r['fingerprint']['reason'], 'input_size_limit')
        self.assertIsNone(r['approximate'])
        for target in ('_walk',):
            with patch.object(self.engine, target, side_effect=RuntimeError('sensitive SQL')):
                r = self.engine.normalize('select 1')
                self.assertEqual(r['fingerprint']['reason'], 'normalization_failed')
                self.assertIsNone(r['approximate'])
                self.assertNotIn('sensitive', dumps(r))
        with patch.object(mpp_parser, 'parse', side_effect=MemoryError()):
            r = self.engine.normalize('select 1')
            self.assertEqual(r['fingerprint']['reason'], 'parser_failed')
            self.assertIsNone(r['approximate'])
        with patch('sql_apm.sql.normalization.version', return_value='8.0'):
            with self.assertRaisesRegex(ValueError, 'unsupported_parser_version'):
                Normalizer()
        with self.assertRaises(DictionaryError):
            FunctionDictionary({'schema_version': 9})

    def test_deep_ast_without_recursion_limit_change(self):
        before = sys.getrecursionlimit()
        # Long left-associative expressions produced earlier real COPY failures.
        sql = 'copy (select ' + '+'.join(['1'] * 1600) + ' from t where x=1) to stdout'
        a = self.result(sql)
        b = self.result(sql[:-len('1) to stdout')] + '2) to stdout')
        self.assertEqual(a['fingerprint'], b['fingerprint'])
        self.assertEqual(sys.getrecursionlimit(), before)
        self.assertEqual(dumps(loads(dumps(a))), dumps(a))

    def test_new_process_and_text_file_cli_agree(self):
        sql = 'select * from t where x=3'
        cmd = [sys.executable, '-m', 'sql_apm.diagnostics.normalize_sql']
        a = subprocess.run(cmd + ['--sql',sql], capture_output=True, text=True, timeout=15)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(json.loads(a.stdout), self.result(sql))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.sql'
            path.write_bytes(sql.encode())
            b = subprocess.run(cmd + ['--file',str(path),'--include-rules'], capture_output=True, text=True, timeout=15)
            r = json.loads(b.stdout)
            self.assertEqual(r.pop('rules'), self.engine.rule_snapshot())
            self.assertEqual(r, json.loads(a.stdout))
            path.write_bytes(b'select * from t where x in(1,')
            near = subprocess.run(cmd + ['--file',str(path)], capture_output=True, text=True, timeout=15)
            self.assertEqual(near.returncode, 2)
            self.assertIsNone(json.loads(near.stdout)['fingerprint']['value'])
            invalid = Path(directory) / 'rules.json'
            invalid.write_text('{}')
            error = subprocess.run(cmd + ['--sql',sql,'--dictionary',str(invalid)], capture_output=True, text=True, timeout=15)
            self.assertEqual(error.returncode, 1)
            self.assertEqual(json.loads(error.stdout)['reason'], 'configuration_invalid')


if __name__ == '__main__':
    unittest.main()
