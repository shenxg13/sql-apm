"""Regression checks for expansion findings and bounded evidence collection."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sql_apm.diagnostics import mpp_expansion_probe as sampling
from sql_apm.sql.mpp_parser import parse, Unsupported


class ExpansionTests(unittest.TestCase):
    def test_no_action_can_disappear_before_mpp_alter(self):
        for before in ('ADD COLUMN x int', 'ADD COLUMN y text', 'DROP COLUMN x',
                       'ADD COLUMN x numeric(10,2)', 'ALTER COLUMN id SET DEFAULT 7'):
            sql = 'ALTER TABLE t ' + before + ', SET WITH (reorganize=true)'
            with self.subTest(before=before):
                expected = parse('ALTER TABLE t ' + before)['statements'][0]['base']['AlterTableStmt']['cmds'][0]
                actions = parse(sql)['statements'][0]['extensions']
                self.assertEqual(len(actions), 2)
                self.assertEqual(actions[0], {'kind': 'postgres_alter', 'command': expected})
                self.assertEqual(actions[1]['kind'], 'alter_distribution')
                self.assertEqual(len(parse('SELECT 1; ' + sql + '; SELECT 2')['statements']), 3)

    def test_zero_segment_and_different_number_survive(self):
        template = "CREATE EXTERNAL WEB TABLE e(id int) EXECUTE 'echo 1' ON SEGMENT {} FORMAT 'TEXT'"
        zero, one = parse(template.format(0)), parse(template.format(1))
        self.assertEqual(zero['statements'][0]['extensions'][0]['execution'][0]['value'], 0)
        self.assertNotEqual(zero, one)

    def test_bare_formatter_retains_qualification_and_type(self):
        template = "CREATE EXTERNAL TABLE e(id int) LOCATION ('a') FORMAT 'CUSTOM' (formatter={})"
        tree = parse(template.format('schema.demo_in'))
        value = tree['statements'][0]['extensions'][0]['format_options'][0]['value']
        self.assertEqual([n['String']['sval'] for n in value['TypeName']['names']], ['schema', 'demo_in'])
        for other in ('other.demo_in', 'schema.other_in', "'schema.demo_in'"):
            with self.subTest(other=other):
                self.assertNotEqual(tree, parse(template.format(other)))
        for invalid in ('demo_in + 1', 'demo_in,', '(id + 1)'):
            with self.assertRaises(Unsupported):
                parse(template.format(invalid))

    def test_row_compatibility_is_confined_to_option_rhs(self):
        sql = "CREATE TABLE t WITH(orientation=row) AS SELECT ROW(1,2), 'orientation=row' DISTRIBUTED RANDOMLY"
        tree = parse(sql)
        self.assertEqual(tree, parse(sql.replace('WITH(orientation=row)', "WITH(orientation='row')")))
        self.assertNotEqual(tree, parse(sql.replace('WITH(orientation=row)', 'WITH(orientation=column)')))
        select = tree['statements'][0]['base']['CreateTableAsStmt']['query']['SelectStmt']
        self.assertEqual(select['targetList'][1]['ResTarget']['val']['A_Const']['sval']['sval'], 'orientation=row')
        with self.assertRaises(Unsupported):
            parse('CREATE TABLE t(id int) WITH(orientation=row+1)')

    def test_shape_sampling_ignores_values_but_not_operator_structure(self):
        one = sampling.shape("SELECT a FROM t WHERE b=1 AND c='private'")
        two = sampling.shape("SELECT x FROM y WHERE z=2 AND k='other'")
        self.assertEqual(one[0], two[0])
        self.assertNotEqual(one[0], sampling.shape('SELECT a FROM t WHERE b>1')[0])
        self.assertNotIn('hint', sampling.shape("SELECT '/*+fake*/'")[1])
        self.assertIn('hint', sampling.shape('SELECT /*+ real */ 1')[1])

    def test_sampling_respects_record_limit_and_validates_source_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root = directory / 'raw'
            (root / 'c').mkdir(parents=True)
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            for sql in ('SELECT a,b FROM t', 'SELECT a,b,c FROM t', 'SELECT a,b,c,d FROM t'):
                row = [''] * 30
                row[24], row[27], row[28] = sql, 'postgres.c', '1'
                writer.writerow(row)
            data = stream.getvalue().encode()
            source = root / 'c' / 'sample.csv'
            source.write_bytes(data)
            entry = {'file': source.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps({'clusters': {'c': {'files': [entry]}}}))
            base = directory / 'base.json'
            base.write_text(json.dumps({'rows': [{'target': {'cluster': 'c', 'file': 'prior.csv'}, 'sql': 'SELECT 1'}]}))
            result = directory / 'result.json'
            result.write_text(json.dumps({'cache_sha256': sampling.sha_file(base)}))
            output = directory / 'cache.json'
            with patch.multiple(sampling, EVIDENCE=manifest, BASE_CACHE=base, BASE_RESULT=result,
                                MAX_RECORDS=2, PER_FILE=6, PREFIX_BYTES=len(data) + 1):
                with contextlib.redirect_stdout(io.StringIO()):
                    sampling.prepare(root, output)
                cached = json.loads(output.read_text())
                self.assertEqual(len(cached['rows']), 2)
                self.assertEqual(cached['files'][0]['counts']['records'], 2)
                self.assertTrue(cached['files'][0]['full_file_hash_rechecked'])
                source.write_bytes(data + b'corruption')
                with self.assertRaisesRegex(ValueError, '^source size mismatch$'):
                    sampling.prepare(root, output)


if __name__ == '__main__':
    unittest.main()
