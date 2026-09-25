import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sql_apm.function_dictionary import DictionaryError, FunctionDictionary, digest, read_json, validate
from sql_apm.function_probe import call_candidates, ProbeError

ROOT = Path(__file__).resolve().parents[1]
DATA = read_json(ROOT/'rules/functions/v1.json')


class DictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = FunctionDictionary(DATA)

    def test_reference_policies(self):
        cases = [
            ('to_date', ['text','text'], ['normalize','preserve']),
            ('to_timestamp', ['float8'], ['normalize']),
            ('to_timestamp', ['text','text'], ['normalize','preserve']),
            ('to_number', ['text','text'], ['normalize','preserve']),
            ('to_char', ['timestamp','text'], ['normalize','preserve']),
            ('round', ['numeric','int4'], ['normalize','preserve']),
            ('log', ['numeric','numeric'], ['preserve','normalize']),
            ('date_trunc', ['text','timestamp'], ['preserve','normalize']),
            ('substring', ['text','int4','int4'], ['normalize','preserve','preserve']),
            ('regexp_replace', ['text','text','text','text'], ['normalize','preserve','preserve','preserve']),
            ('generate_series', ['int4','int4'], ['preserve','preserve']),
            ('set_config', ['text','text','bool'], ['preserve']*3),
            ('pg_sleep', ['float8'], ['preserve']),
            ('lower', ['text'], ['normalize']),
            ('lower', ['anyrange'], ['preserve']),
            ('json_extract_path', ['json','_text'], ['preserve','preserve']),
        ]
        for name, types, expected in cases:
            with self.subTest(name=name, types=types):
                result=self.dictionary.select(name,len(types),types=types)
                self.assertEqual(result['actions'],expected)
                self.assertTrue(result['rule_ids'])

    def test_every_documented_signature_is_reachable(self):
        inventory=read_json(ROOT/'rules/functions/postgres-9.4.26-inventory.json')
        for proc in inventory['catalog']:
            if proc['name'] not in inventory['document_mentions']:
                continue
            with self.subTest(signature=proc['id']):
                result=self.dictionary.select(proc['name'],len(proc['types']),
                    schema=proc['schema'],types=proc['types'],kind=proc['kind'])
                self.assertIn(proc['id'],result['rule_ids'])

    def test_preserve_families_have_explicit_hits(self):
        cases=[('enum_first',['anyenum'],'function'),('area',['box'],'function'),
               ('network',['inet'],'function'),('ts_rank',['tsvector','tsquery'],'function'),
               ('xpath',['text','xml'],'function'),('json_array_length',['json'],'function'),
               ('nextval',['regclass'],'function'),('array_length',['anyarray','int4'],'function'),
               ('lower_inc',['anyrange'],'function'),('count',[],'aggregate'),
               ('ntile',['int4'],'window'),('current_database',[],'function'),
               ('pg_read_file',['text','int8','int8'],'function'),
               ('suppress_redundant_updates_trigger',[],'function'),
               ('pg_event_trigger_dropped_objects',[],'function')]
        for name,types,kind in cases:
            with self.subTest(name=name):
                result=self.dictionary.select(name,len(types),types=types,kind=kind)
                self.assertEqual(result['reason'],'matched')
                self.assertEqual(result['actions'],['preserve']*len(types))

    def test_unknown_types_consensus_and_ambiguity(self):
        self.assertEqual(self.dictionary.select('to_char',2)['actions'],['normalize','preserve'])
        self.assertEqual(self.dictionary.select('lower',1)['reason'],'ambiguous_overload')
        self.assertEqual(self.dictionary.select('to_date',2,types=['int4','int4'])['reason'],'no_matching_rule')
        self.assertEqual(self.dictionary.select('lower',1,types=['text'])['actions'],['normalize'])

    def test_schema_and_quoting(self):
        for name in ('TO_DATE','"to_date"'):
            self.assertEqual(self.dictionary.select(name,2)['reason'],'matched')
        for name in ('"TO_DATE"','x.to_date'):
            self.assertNotEqual(self.dictionary.select(name,2)['reason'],'matched')
        self.assertEqual(self.dictionary.select('to_date',2,schema='PG_CATALOG')['reason'],'matched')
        self.assertEqual(self.dictionary.select('to_date',2,schema='public')['reason'],'no_matching_rule')
        self.assertEqual(self.dictionary.select('__gp_aocsseg',1,schema='gp_toolkit')['reason'],'matched')
        self.assertEqual(self.dictionary.select('__gp_aocsseg',1)['reason'],'no_matching_rule')
        self.assertEqual(self.dictionary.select('gp_param_setting',1,schema='gp_toolkit')['reason'],'pending_review')

    def test_defaults_variadic_kinds_and_forms(self):
        self.assertEqual(self.dictionary.select('make_interval',0)['reason'],'matched')
        self.assertEqual(self.dictionary.select('pg_start_backup',1)['reason'],'matched')
        self.assertEqual(self.dictionary.select('concat',3)['reason'],'variadic_requires_resolver')
        self.assertEqual(self.dictionary.select('concat',0)['decision'],'preserve')
        self.assertEqual(self.dictionary.select('sum',1,kind='aggregate')['reason'],'matched')
        self.assertEqual(self.dictionary.select('lag',3,kind='window')['actions'],['preserve']*3)
        self.assertEqual(self.dictionary.select('sum',1)['reason'],'no_matching_rule')
        self.assertEqual(self.dictionary.select('to_date',2,form='named')['reason'],'unsupported_call_form')
        self.assertEqual(self.dictionary.select('to_date',2,protected=True)['reason'],'protected_context')

    def test_examples_and_nonmutation(self):
        for example in read_json(ROOT/'rules/functions/examples.json'):
            with self.subTest(case=example['id']):
                original=copy.deepcopy(example)
                a=self.dictionary.preview(example['left'],example['context'])
                b=self.dictionary.preview(example['right'],example['context'])
                self.assertEqual(a==b,example['expected_same'])
                self.assertEqual(example,original)

    def test_snapshot_and_digest(self):
        data=copy.deepcopy(DATA)
        dictionary=FunctionDictionary(data)
        old=dictionary.sha256
        data['rules'][0]['rationale']='changed after load'
        self.assertEqual(dictionary.sha256,old)
        self.assertNotEqual(digest(data),old)
        data=copy.deepcopy(DATA)
        data['rules'].reverse()
        for rule in data['rules']:
            rule['arguments'].reverse()
        self.assertEqual(digest(data),old)

    def test_disabled_rule(self):
        data=copy.deepcopy(DATA)
        for rule in data['rules']:
            if rule['name']=='to_date': rule['enabled']=False
        self.assertEqual(FunctionDictionary(data).select('to_date',2)['reason'],'disabled_rule')

    def test_invalid_configuration(self):
        rule=next(copy.deepcopy(r) for r in DATA['rules'] if r['name']=='to_date')
        base=dict(schema_version=1,rules_version='1.0.0',profile='hashdata-pg94',rules=[rule])
        mutations=[lambda d:d.pop('rules_version'),lambda d:d.update(schema_version=True),
                   lambda d:d.update(rules_version='v1'),lambda d:d.update(rules=[]),
                   lambda d:d['rules'].append(copy.deepcopy(d['rules'][0])),
                   lambda d:d['rules'][0]['arguments'][0].update(position=3),
                   lambda d:d['rules'][0]['arguments'][0].update(position=True),
                   lambda d:d['rules'][0]['arguments'][0].update(action='ignore'),
                   lambda d:d['rules'][0]['arguments'][0].update(position=2),
                   lambda d:d['rules'][0].update(defaults=3),
                   lambda d:d['rules'][0].update(decision='pending'),
                   lambda d:d['rules'][0].update(sources=[]),
                   lambda d:d['rules'][0].update(unknown_field=1)]
        for mutate in mutations:
            data=copy.deepcopy(base);mutate(data)
            with self.subTest(data=data),self.assertRaises(DictionaryError): validate(data)
        duplicate=copy.deepcopy(rule);duplicate['id']='another-id'
        base['rules'].append(duplicate)
        with self.assertRaisesRegex(DictionaryError,'conflicting signature'):validate(base)

    def test_bad_json_and_cli_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'bad.json'
            for text in ('{"x":1,"x":2}', '{"x":NaN}'):
                path.write_text(text)
                with self.assertRaises(DictionaryError):read_json(path)
            result=subprocess.run([sys.executable,'-m','sql_apm.function_dictionary','validate',str(path)],
                                  cwd=ROOT,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('ERROR:',result.stderr)


class ProbeTests(unittest.TestCase):
    def test_quotes_comments_nesting_and_schema(self):
        sql="SELECT pg_catalog.to_date('fake(1,2)','YYYYMMDD'), to_char(abs(2),'99'), $$fake(1)$$ /* a /* fake() */ b */;"
        calls, _=call_candidates(sql)
        self.assertEqual([(c['name'],c['arity']) for c in calls],[('to_date',2),('to_char',2),('abs',1)])
        self.assertEqual(calls[0]['schema'],'pg_catalog')
        calls,_=call_candidates('SELECT f(ARRAY[1,2],g(1,2)), "Mixed"(1); --fake()')
        self.assertEqual([(c['name'],c['arity']) for c in calls],[('f',2),('g',2),('"Mixed"',1)])

    def test_special_and_uncertain(self):
        for sql in ("SELECT f(x => 1)","SELECT substring('x' from 1 for 2)","SELECT count(*)"):
            calls,_=call_candidates(sql)
            self.assertEqual(calls[0]['form'],'special')
        calls, excluded=call_candidates('INSERT INTO orders(id) VALUES (1);')
        self.assertFalse(calls)
        self.assertGreater(excluded,0)
        for sql in ("SELECT f('x)",'SELECT f($$x)', 'SELECT f([1,2)',
                    "SELECT f('a\\b')",'CREATE TABLE f(a int)', 'SELECT f(\udcff)', "SELECT f('\udcff')", "SELECT f('\x00')"):
            with self.subTest(sql=repr(sql)),self.assertRaises(ProbeError):call_candidates(sql)

    def test_sample_bounds_and_privacy(self):
        spec=importlib.util.spec_from_file_location('sample_logs',ROOT/'scripts/functions/sample_logs.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        import csv
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.csv'
            with path.open('w',newline='') as f:
                writer=csv.writer(f)
                row=['']*30;row[24]="SELECT private_business_function('secret-value'), to_date('20260101','YYYYMMDD');"
                for _ in range(10):writer.writerow(row)
            result=module.sample(path,FunctionDictionary(DATA),2,4096)
            self.assertEqual(result['counts']['csv_records'],2)
            self.assertLessEqual(result['bytes_read'],4096)
            encoded=json.dumps(result)
            self.assertNotIn('secret-value',encoded)
            self.assertNotIn('private_business_function',encoded)
            self.assertEqual(result['counts']['matched_normalize'],2)
            self.assertEqual(result['prefix_sha256'],__import__('hashlib').sha256(path.read_bytes()[:result['bytes_read']]).hexdigest())
            limited=module.sample(path,FunctionDictionary(DATA),2000,10)
            self.assertEqual(limited['counts']['byte_or_line_limit_reached'],1)


if __name__=='__main__':unittest.main()
