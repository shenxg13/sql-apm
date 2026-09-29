"""Issue #11: bucket boundaries, old protected behavior and anchor identity."""
import hashlib
from itertools import product
import json
from pathlib import Path
import unittest

from sql_apm.sql.normalization import Normalizer
from sql_apm.sql.structure import dumps

# These complete synthetic SQL inputs are also replayed on frozen v3 when
# recording fixtures. Only their structure digests are persisted in the fixture.
PRESERVED = (
    'SELECT * FROM t WHERE x IN ({v}, y)',
    'SELECT * FROM t WHERE x NOT IN ({v}, NULL)',
    'SELECT * FROM t WHERE x IN ({v}, true)',
    'SELECT * FROM t WHERE x IN ({v}, 1+2)',
    'SELECT * FROM t WHERE x IN ({v}, upper(\'a\'::text))',
    'SELECT * FROM t WHERE x IN ({v}, 1::int)',
    'SELECT * FROM t WHERE x IN ((SELECT {v}))',
    'SELECT x IN ({v}) FROM t',
    'SELECT * FROM t JOIN u ON t.x IN ({v})',
    'SELECT x FROM t GROUP BY x HAVING x IN ({v})',
    'SELECT * FROM t WHERE upper((x IN ({v}))::text)=\'TRUE\'',
    'SELECT * FROM t WHERE custom(x IN ({v}))',
    'SELECT * FROM t WHERE coalesce(x IN ({v}),false)',
    'SELECT * FROM t WHERE nullif(x IN ({v}),false)',
    'SELECT * FROM t WHERE (x IN ({v}))::boolean',
    'SELECT * FROM t LIMIT (SELECT count(*) FROM u WHERE x IN ({v}))',
    'SELECT sum(x) OVER (ORDER BY (x IN ({v}))) FROM t',
    'CREATE TABLE t (x boolean DEFAULT (1 IN ({v})))',
    'UPDATE t SET x=(y IN ({v}))',
    'INSERT INTO t VALUES (x IN ({v}))',
    'SELECT * FROM t WHERE x=ANY(ARRAY[{v}])',
    'SELECT * FROM t WHERE x=ANY($1)',
    'INSERT INTO t VALUES ({v})',
)


# v3 FILTER value normalization stays active, but list cardinality is retained.
R1_PRESERVED = (
    'SELECT sum(y) FILTER (WHERE x IN ({v})) FROM t',
    'SELECT x FROM t GROUP BY x HAVING count(*) FILTER (WHERE y NOT IN ({v}))>0',
    'SELECT x FROM t ORDER BY sum(y) FILTER (WHERE x IN ({v}))',
    'SELECT * FROM t WHERE count(*) FILTER (WHERE x IN ({v}))>0',
    'SELECT sum(y) FILTER (WHERE x IN ({v}) AND z=42) FROM t',
    'SELECT sum(y) FILTER (WHERE CASE WHEN x IN ({v}) THEN true ELSE false END) FROM t',
    'SELECT custom_fn(y) FILTER (WHERE x IN ({v})) FROM t',
    'INSERT INTO t VALUES (1) ON CONFLICT (x) WHERE x IN ({v}) DO UPDATE SET y=2',
)

R1_NESTED = (
    'SELECT sum(y) FILTER (WHERE EXISTS (SELECT 1 FROM u WHERE x IN ({v}))) FROM t',
    'SELECT x FROM t GROUP BY x HAVING count(*) FILTER (WHERE EXISTS (SELECT 1 FROM u WHERE y IN ({v})))>0',
    'INSERT INTO t VALUES (1) ON CONFLICT (x) DO UPDATE SET y=2 WHERE x IN ({v})',
    'SELECT sum(y) FILTER (WHERE x IN (1,2)) FROM t WHERE z IN ({v})',
)


def preservation_cases():
    for index, template in enumerate(PRESERVED):
        for variant, value in enumerate(('1,2', '1,2,3', '$1,$2')):
            yield '{}-{}'.format(index, variant), template.format(v=value)


class NormalizationV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Normalizer()

    def result(self, sql):
        result = self.engine.normalize(sql)
        self.assertEqual(result['fingerprint']['state'], 'reliable', result['fingerprint'])
        return result

    def fp(self, sql):
        return self.result(sql)['fingerprint']['value']

    def test_boundaries_and_mixed_parameters_in_and_not_in(self):
        lengths = (1, 2, 3, 10, 11, 20, 100, 101, 102, 150)
        buckets = (0, 1, 1, 1, 2, 2, 2, 3, 3, 3)
        for operator in ('IN', 'NOT IN'):
            values = []
            for length in lengths:
                literals = ','.join(str(i) for i in range(length))
                mixed = ','.join(('$'+str(i+1)) if i % 2 else "'value'" for i in range(length))
                sql = 'SELECT * FROM t WHERE x ' + operator + ' ({})'
                a, b = self.fp(sql.format(literals)), self.fp(sql.format(mixed))
                self.assertEqual(a, b)
                values.append(a)
            for a, b in product(range(len(lengths)), repeat=2):
                with self.subTest(operator=operator, a=lengths[a], b=lengths[b]):
                    self.assertEqual(values[a] == values[b], buckets[a] == buckets[b])
        self.assertNotEqual(self.fp('SELECT * FROM t WHERE x IN (1,2)'),
                            self.fp('SELECT * FROM t WHERE x NOT IN (1,2)'))

    def test_each_nested_where_has_its_own_business_context(self):
        for template in (
            'SELECT * FROM t WHERE x IN ({v})',
            'UPDATE t SET y=1 WHERE x NOT IN ({v})',
            'DELETE FROM t WHERE x IN ({v})',
            'WITH c AS (SELECT * FROM t WHERE x IN ({v})) SELECT * FROM c',
            'SELECT * FROM (SELECT * FROM t WHERE x IN ({v})) c',
            'SELECT * FROM u WHERE EXISTS (SELECT 1 FROM t WHERE x IN ({v}))',
            'SELECT * FROM u WHERE x IN (SELECT x FROM t WHERE y IN ({v}))',
            'SELECT (SELECT x FROM t WHERE y IN ({v})) FROM u',
            'COPY (SELECT * FROM t WHERE x IN ({v})) TO STDOUT',
            'CREATE TABLE u AS SELECT * FROM t WHERE x IN ({v}) DISTRIBUTED RANDOMLY',
        ):
            with self.subTest(template=template):
                self.assertEqual(self.fp(template.format(v='1,2')), self.fp(template.format(v='3,4,5')))
                self.assertNotEqual(self.fp(template.format(v='1')), self.fp(template.format(v='3,4')))

    def test_protected_positions_match_frozen_v3_structure(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/normalization-v3-preserved.json').read_text())
        self.assertEqual(fixture['commit'], 'bd62856921aa806e109490c199482d099d560557')
        for case, sql in preservation_cases():
            # Issue #15 explicitly replaces these old preservation expectations:
            # nested SELECT constants, SELECT IN and JOIN ON IN.
            if int(case.split('-')[0]) in (6, 7, 8):
                result = self.result(sql)
                index, variant = (int(v) for v in case.split('-'))
                self.assertNotEqual(hashlib.sha256(dumps(result['normalized']).encode('ascii')).hexdigest(),
                                    fixture['structure_sha256'][case])
                if index in (7, 8):
                    self.assertEqual(result['diagnostics']['in_lists_bucketed'], 1)
                    self.assertEqual(self.fp(sql), self.fp(PRESERVED[index].format(v='7,8')))
                else:
                    self.assertEqual(result['diagnostics'].get('in_lists_bucketed', 0), 0)
                    values = '7,8,9' if variant == 1 else '7,8'
                    self.assertEqual(self.fp(sql), self.fp(PRESERVED[index].format(v=values)))
                continue
            with self.subTest(case=case):
                result = self.result(sql)
                self.assertEqual(hashlib.sha256(dumps(result['normalized']).encode('ascii')).hexdigest(),
                                 fixture['structure_sha256'][case])
                self.assertEqual(result['diagnostics'].get('in_lists_bucketed', 0), 0)

    def test_mixed_lists_keep_length_and_expression_structure(self):
        for tail in ('y', 'NULL', 'true', '1+2', "upper('a'::text)", '1::int', '(SELECT 1)'):
            with self.subTest(tail=tail):
                self.assertNotEqual(self.fp('SELECT * FROM t WHERE x IN (1,2,{})'.format(tail)),
                                    self.fp('SELECT * FROM t WHERE x IN (1,2,3,{})'.format(tail)))

    def test_non_goals_and_in_operator_identity(self):
        for left, right in (
            ('INSERT INTO t VALUES (1),(2)', 'INSERT INTO t VALUES (1),(2),(3)'),
            ('SELECT * FROM t WHERE x=ANY(ARRAY[1,2])', 'SELECT * FROM t WHERE x=ANY(ARRAY[1,2,3])'),
            ('SELECT * FROM t WHERE x IN (1,2)', 'SELECT * FROM t WHERE y IN (1,2)'),
        ):
            with self.subTest(left=left):
                self.assertNotEqual(self.fp(left), self.fp(right))

        # Native parameters already share identity at this old WHERE position.
        self.assertEqual(self.fp('SELECT * FROM t WHERE x=ANY($1)'),
                         self.fp('SELECT * FROM t WHERE x=ANY($2)'))

    def test_global_gap_removed_without_changing_anchor(self):
        for hint in ('/*+ H */', '--+ H\n'):
            a = self.result('SELECT (a) FROM t; SELECT {} b FROM u'.format(hint))
            b = self.result('SELECT a FROM t; SELECT {} b FROM u'.format(hint))
            self.assertEqual(a['fingerprint'], b['fingerprint'])
            self.assertNotIn('gap', a['normalized']['hints'][0])
            self.assertIn('anchor', a['normalized']['hints'][0])
            self.assertNotEqual(self.fp('SELECT a FROM t; SELECT b {} FROM u'.format(hint)),
                                a['fingerprint']['value'])

    def test_r1_filter_and_conflict_inference_match_frozen_v3(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/normalization-v3-r1.json').read_text())
        self.assertEqual(fixture['commit'], 'bd62856921aa806e109490c199482d099d560557')
        for index, template in enumerate(R1_PRESERVED):
            fingerprints = []
            for variant, values in enumerate(('1,2', '1,2,3', '$1,$2')):
                with self.subTest(index=index, variant=variant):
                    result = self.result(template.format(v=values))
                    self.assertEqual(hashlib.sha256(dumps(result['normalized']).encode('ascii')).hexdigest(),
                                     fixture['structure_sha256']['{}-{}'.format(index, variant)])
                    self.assertEqual(result['diagnostics'].get('in_lists_bucketed', 0), 0)
                    fingerprints.append(result['fingerprint']['value'])
            self.assertNotEqual(fingerprints[0], fingerprints[1])

    def test_r1_nested_query_where_and_conflict_update_still_bucket(self):
        for template in R1_NESTED:
            with self.subTest(template=template):
                a, b = (self.result(template.format(v=v)) for v in ('1,2', '3,4,5'))
                self.assertEqual(a['fingerprint'], b['fingerprint'])
                self.assertEqual(a['diagnostics']['in_lists_bucketed'], 1)

    def test_confirmed_hint_exception_keeps_anchor_before_after_and_batch_boundary(self):
        for hint, placement, operator in product(('/*+ H */', '--+ H\n'),
                ('SELECT {h} * FROM t WHERE x {op} ({v})',
                 'SELECT * FROM t WHERE x {op} ({v}) {h}',
                 'SELECT * FROM t WHERE x {op} ({v}); {h}'), ('IN', 'NOT IN')):
            with self.subTest(hint=hint, placement=placement, operator=operator):
                a, b, same_length = (self.result(placement.format(h=hint, op=operator, v=v))
                                     for v in ('1,2', '1,2,3', '$1,42'))
                self.assertEqual(a['normalized']['statements'], b['normalized']['statements'])
                self.assertEqual(a['diagnostics']['in_lists_bucketed'], 1)
                self.assertNotEqual(a['normalized']['hints'][0]['anchor'], b['normalized']['hints'][0]['anchor'])
                self.assertNotEqual(a['fingerprint'], b['fingerprint'])
                self.assertEqual(a['fingerprint'], same_length['fingerprint'])
                for size in (1, 11, 101):
                    crossed = self.result(placement.format(h=hint, op=operator,
                                          v=','.join(str(i) for i in range(size))))
                    self.assertNotEqual(a['normalized']['statements'], crossed['normalized']['statements'])
                    self.assertNotEqual(a['fingerprint'], crossed['fingerprint'])

    def test_snapshot_records_new_rules_and_formal_versions(self):
        context = self.engine.context
        self.assertEqual(context['algorithm_version'], 'sql-normalization/5')
        self.assertEqual(context['parser_version'], 'mpp-adapter/9')
        rules = self.engine.rule_snapshot()['normalization']
        self.assertEqual(rules['where_in']['buckets'], ['1', '2-10', '11-100', '>100'])
        self.assertIn('exclude global gap', rules['hints'])
        self.assertIn('Hint anchors differ', rules['where_in']['hint_exception'])
        self.assertIn('FILTER keeps v3', rules['where_in']['eligibility'])
