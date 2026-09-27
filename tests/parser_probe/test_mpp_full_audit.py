"""Auditing exports fixed grammar labels and distinguishes implementation limits."""
import hashlib
import json
import sqlite3
import unittest

from sql_apm.diagnostics import mpp_full_audit as audit
from sql_apm.diagnostics import mpp_full_scan as scan
from sql_apm.diagnostics.mpp_adapter_probe import probe
from pglast import parser


class FullAuditTests(unittest.TestCase):
    def test_signature_does_not_export_names_or_literals(self):
        raw = b"ALTER TABLE private_schema.private_table TRUNCATE PARTITION private_partition; SELECT 'secret_value', 987654321"
        result = audit.signature(raw)
        self.assertIn('alter_truncate_partition', result['features'])
        encoded = json.dumps(result)
        for text in ('private', 'secret_value', '987654321'):
            self.assertNotIn(text, encoded)
        self.assertIn('IDENT', encoded)
        self.assertIn('SCONST', encoded)

    def test_diagnostic_group_keeps_occurrence_denominator(self):
        raw = b'ALTER TABLE sample_table TRUNCATE PARTITION sample_partition'
        with sqlite3.connect(':memory:') as db:
            scan.initialize(db)
            scan.set_meta(db, 'collection_complete', True)
            # Retain the historical version-4 failure as audit input.
            result = {'state': 'unsupported', 'reason': 'base_parser_rejected'}
            db.execute('INSERT INTO inputs VALUES (1,?,?,?,?)',
                       (hashlib.sha256(raw).hexdigest(), raw, '{}', json.dumps(result)))
            db.execute("INSERT INTO occurrences VALUES (1,'119/synthetic.csv','sql',7)")
            result = audit.audit(db)
            group = result['failure_groups'][0]
            self.assertEqual((group['unique_inputs'], group['occurrences']), (1, 7))
            self.assertEqual(group['features'], ['alter_truncate_partition'])
            self.assertNotIn('sample_table', json.dumps(result))

    def test_observed_features_survive_repairs(self):
        cases = (
            ('ANALYZE ROOTPARTITION t;', 'base_parser_rejected', 'analyze_rootpartition'),
            ('ANALYSE ROOTPARTITION t;', 'base_parser_rejected', 'analyze_rootpartition'),
            ('ALTER TABLE t TRUNCATE PARTITION p;', 'base_parser_rejected', 'alter_truncate_partition'),
            ('CREATE TEMP TABLE t(id int) WITH OIDS ON COMMIT PRESERVE ROWS DISTRIBUTED BY(id);',
             'base_parser_rejected', 'legacy_with_oids'),
            ("CREATE TABLE t(d date) DISTRIBUTED BY(d) PARTITION BY RANGE(d) "
             "(PARTITION p START ('2020-01-01'::date) END ('2021-01-01'::date));",
             'unconsumed_extension', 'partition_by'))
        for sql, reason, feature in cases:
            with self.subTest(feature=feature, sql=sql):
                self.assertEqual(probe(sql)['state'], 'prototype_parsed')
                self.assertIn(feature, audit.signature(sql.encode())['features'])

    def test_missing_batch_separator_is_rejected_as_a_whole(self):
        sql = 'SELECT 1; DROP TABLE t CREATE TABLE t(id int) DISTRIBUTED BY(id); SELECT 2;'
        self.assertEqual(probe(sql), {'state': 'unsupported', 'reason': 'unsupported_distribution_statement'})

    def test_long_expression_repair_returns_structure(self):
        # The same reproducer that failed in version 4 now returns a structure.
        sql = 'SELECT ' + '+'.join('1' for _ in range(256))
        self.assertTrue(parser.parse_sql_json(sql))
        self.assertEqual(probe(sql)['state'], 'prototype_parsed')
        self.assertEqual(probe('SELECT 1')['state'], 'prototype_parsed')


if __name__ == '__main__':
    unittest.main()
