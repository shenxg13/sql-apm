import importlib.util
from pathlib import Path
import unittest

from sql_apm.sql.function_dictionary import read_json
from scripts.functions.coverage import coverage
from scripts.functions.import_postgres import functions

ROOT=Path(__file__).resolve().parents[1]


class InventoryTests(unittest.TestCase):
    def test_sgml_short_end_tags(self):
        text='<function>f(<type>text</>, <optional><parameter>x</></optional>)</function> and <function>g()</>'
        self.assertEqual(list(functions(text)),['f(text, x)','g()'])
        with self.assertRaises(ValueError):list(functions('<function>f(<type>text</function>'))

    def test_catalog_facts(self):
        inventory=read_json(ROOT/'rules/functions/postgres-9.4.26-inventory.json')
        lookup={(p['name'],tuple(p['types'])):p for p in inventory['catalog']}
        self.assertEqual(lookup['to_date',('text','text')]['defaults'],0)
        self.assertEqual(lookup['make_interval',('int4',)*6+('float8',)]['defaults'],7)
        self.assertEqual(lookup['pg_start_backup',('text','bool')]['defaults'],1)
        self.assertIn(('ts_debug',('text',)),lookup)
        self.assertIn(('ts_debug',('regconfig','text')),lookup)
        for name in ('enum_first','json_array_length','jsonb_array_length','json_build_object'):
            self.assertTrue(any(t['name']==name for t in inventory['document_signatures']))
        self.assertEqual(len(lookup),len(inventory['catalog']))

    def test_coverage_audit(self):
        result=coverage()
        self.assertEqual(result['documented_reviewed'],result['documented_catalog_signatures'])
        self.assertGreater(result['catalog_outside_document_scope'],0)
        self.assertEqual(result['extension_decisions']['pending'],5)
        self.assertEqual(sum(result['documented_decisions'].values()),result['documented_catalog_signatures'])


if __name__=='__main__':unittest.main()
