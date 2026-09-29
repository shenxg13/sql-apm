"""Issue #15 positions, protected v4 structures and independent projection."""
import hashlib
from itertools import product
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from sql_apm.diagnostics.normalization_v5_audit import project_v5
from sql_apm.sql.normalization import Normalizer
from sql_apm.sql.structure import dumps

FROZEN = '9a0f9f507a4e068f32c8704da28c761d6e65ca14'
# a/b are business values; no real SQL or object names are used in this fixture.
MERGE = (
    ("SELECT 'A', x*0.1 FROM t", "SELECT 'B', x*0.2 FROM t"),
    ('SELECT $1, x+$2 FROM t', "SELECT 9, x+'a' FROM t"),
    ('SELECT -1, +2.3', 'SELECT -9, +4.5'),
    ('SELECT CASE WHEN x=1 THEN 3 ELSE 4 END FROM t',
     'SELECT CASE WHEN x=2 THEN 3 ELSE 4 END FROM t'),
    ('SELECT * FROM a JOIN b ON a.id=b.id AND b.t=1',
     'SELECT * FROM a JOIN b ON a.id=b.id AND b.t=2'),
    ('SELECT * FROM a JOIN b ON b.t=$1', "SELECT * FROM a JOIN b ON b.t='value'"),
)
QUERY_WRAPPERS = (
    '{q}', 'WITH c AS ({q}) SELECT * FROM c', 'SELECT * FROM ({q}) c',
    'SELECT ({q}) FROM u', '{q} UNION {q}', '{q} INTERSECT {q}', '{q} EXCEPT {q}',
    'INSERT INTO u {q}', 'CREATE TABLE u AS {q} DISTRIBUTED RANDOMLY',
    'CREATE VIEW v AS {q}', 'COPY ({q}) TO STDOUT', 'EXPLAIN {q}',
    'PREPARE p AS {q}', 'DECLARE c CURSOR FOR {q}',
    'SELECT sum(x) FILTER (WHERE EXISTS ({q})) FROM u',
    'SELECT abs(({q}))', 'SELECT CASE WHEN true THEN ({q}) ELSE 0 END',
    'UPDATE u SET x=({q})', 'INSERT INTO u VALUES (({q}))',
)
PROTECTED = (
    ('SELECT CASE WHEN x=1 THEN 2 ELSE 3 END FROM t',
     'SELECT CASE WHEN x=1 THEN 4 ELSE 3 END FROM t'),
    ('SELECT CASE WHEN x=1 THEN 2 ELSE 3 END FROM t',
     'SELECT CASE WHEN x=1 THEN 2 ELSE 4 END FROM t'),
    ('SELECT true, NULL', 'SELECT false, NULL'),
    ('SELECT NULL', 'SELECT 1'),
    ("SELECT 't'::regclass", "SELECT 'u'::regclass"),
    ('SELECT 1::boolean', 'SELECT 2::boolean'),
    ("SELECT round(x::numeric, 1)", "SELECT round(x::numeric, 2)"),
    ("SELECT date_trunc('day', x::timestamp)", "SELECT date_trunc('month', x::timestamp)"),
    ("SELECT unknown_fn(1)", "SELECT unknown_fn(2)"),
    ("SELECT unknown_fn((SELECT 1))", "SELECT unknown_fn((SELECT 2))"),
    ('SELECT coalesce(1, x)', 'SELECT coalesce(2, x)'),
    ('SELECT nullif(1, x)', 'SELECT nullif(2, x)'),
    ('SELECT sum(x) FILTER (WHERE y IN (1,2)) FROM t',
     'SELECT sum(x) FILTER (WHERE y IN (1,2,3)) FROM t'),
    ('SELECT sum(x) OVER (ORDER BY 1) FROM t', 'SELECT sum(x) OVER (ORDER BY 2) FROM t'),
    ('SELECT sum(x) OVER (ROWS 1 PRECEDING) FROM t', 'SELECT sum(x) OVER (ROWS 2 PRECEDING) FROM t'),
    ('SELECT x,y FROM t ORDER BY 1', 'SELECT x,y FROM t ORDER BY 2'),
    ('SELECT x,y FROM t GROUP BY 1', 'SELECT x,y FROM t GROUP BY 2'),
    ('SELECT DISTINCT ON (1) x,y FROM t', 'SELECT DISTINCT ON (2) x,y FROM t'),
    ('SELECT x FROM t HAVING x=1', 'SELECT x FROM t HAVING x=2'),
    ('SELECT x FROM t LIMIT 1 OFFSET 2', 'SELECT x FROM t LIMIT 2 OFFSET 2'),
    ('SELECT x FROM t LIMIT 1 OFFSET 2', 'SELECT x FROM t LIMIT 1 OFFSET 3'),
    ('SELECT x FROM t LIMIT (SELECT 1)', 'SELECT x FROM t LIMIT (SELECT 2)'),
    ('SET work_mem=1', 'SET work_mem=2'),
    ('SELECT * FROM a JOIN b USING (x)', 'SELECT * FROM a JOIN b USING (y)'),
    ('SELECT * FROM a JOIN b ON CASE WHEN true THEN 1 ELSE 2 END = a.x',
     'SELECT * FROM a JOIN b ON CASE WHEN true THEN 3 ELSE 2 END = a.x'),
    ('UPDATE t SET x=1+2', 'UPDATE t SET x=1+3'),
    ('INSERT INTO t VALUES (1+2)', 'INSERT INTO t VALUES (1+3)'),
    ('UPDATE t SET x=1 RETURNING 2', 'UPDATE t SET x=1 RETURNING 3'),
    ('VALUES (1)', 'VALUES (2)'),
)
IN_TEMPLATES = ('SELECT x {op} ({v}) FROM t',
                'SELECT * FROM a JOIN b ON a.x {op} ({v})')
STABLE = (
    'SELECT x FROM t WHERE y IN (1,2)',
    'INSERT INTO t VALUES (1),(2)',
    'UPDATE t SET x=1 WHERE y=2',
    'SELECT sum(x) FILTER (WHERE y=1) FROM t',
    'SELECT * FROM t WHERE (x=1)::boolean',
    'CREATE TABLE t (x int DEFAULT 1) DISTRIBUTED BY (x)',
)


def fixture_sqls():
    sqls = set(STABLE)
    for pair in MERGE + PROTECTED:
        sqls.update(pair)
    for wrapper in QUERY_WRAPPERS:
        sqls.update(wrapper.format(q='SELECT ' + value) for value in ('1', '2'))
    for template, op, size in product(IN_TEMPLATES, ('IN', 'NOT IN'), (1,2,3,10,11,100,101)):
        sqls.add(template.format(op=op, v=','.join(str(i) for i in range(size))))
    for op in ('UNION', 'INTERSECT', 'EXCEPT'):
        for q in (
                "SELECT to_date('2020','YYYY'), x IN (1,2) FROM a JOIN b ON a.x=b.x AND b.y=1 WHERE a.z IN (1,2)",
                "SELECT sum(x) FILTER (WHERE x IN (1,2)), CASE WHEN x=1 THEN 2 ELSE 3 END FROM t HAVING x=1",
                "SELECT true, NULL, 1::boolean FROM t LIMIT 1",
                "SELECT 1 WHERE x=2 UNION SELECT 3 WHERE x=4"):
            sqls.add('(' + q + ') ' + op + ' (' + q + ')')
    # All v3 frozen trees must still conserve their old structures plus v5 changes.
    for name in ('normalization-v3-preserved.json', 'normalization-v3-r1.json'):
        fixture = json.loads((Path(__file__).parent/'fixtures'/name).read_text())
        sqls.update(c['sql'] for c in fixture['projection_cases'])
    return sorted(sqls)


class NormalizationV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Normalizer()
        cls.fixture = json.loads((Path(__file__).parent/'fixtures/normalization-v4.json').read_text())

    def result(self, sql):
        result = self.engine.normalize(sql)
        self.assertEqual(result['fingerprint']['state'], 'reliable', result['fingerprint'])
        return result

    def test_new_values_merge_in_each_query_context(self):
        pairs = list(MERGE)
        pairs.extend((wrapper.format(q='SELECT 1'), wrapper.format(q='SELECT 2'))
                     for wrapper in QUERY_WRAPPERS)
        for a, b in pairs:
            with self.subTest(a=a):
                self.assertEqual(self.result(a)['fingerprint'], self.result(b)['fingerprint'])

    def test_protected_values_remain_distinct(self):
        for a, b in PROTECTED:
            with self.subTest(a=a):
                self.assertNotEqual(self.result(a)['fingerprint'], self.result(b)['fingerprint'])

    def test_all_bucket_edges_and_mixed_parameters(self):
        for template, op in product(IN_TEMPLATES, ('IN', 'NOT IN')):
            fingerprints = []
            for size, bucket in ((1,0),(2,1),(3,1),(10,1),(11,2),(100,2),(101,3)):
                sql = template.format(op=op, v=','.join(str(i) for i in range(size)))
                alternate = template.format(op=op, v=','.join('$'+str(i+1) for i in range(size)))
                fp = self.result(sql)['fingerprint']
                self.assertEqual(fp, self.result(alternate)['fingerprint'])
                fingerprints.append((bucket, fp))
            for (a,x), (b,y) in product(fingerprints, repeat=2):
                self.assertEqual(x == y, a == b)
            for tail in ('NULL', 'true', 'y', '1+2', '1::int', '(SELECT 1)'):
                a = self.result(template.format(op=op, v='1,2,'+tail))
                b = self.result(template.format(op=op, v='1,2,3,'+tail))
                self.assertNotEqual(a['fingerprint'], b['fingerprint'])

    def test_frozen_v4_preservation_and_independent_projection(self):
        self.assertEqual(self.fixture['commit'], FROZEN)
        preserved = {sql for pair in PROTECTED for sql in pair} | set(STABLE)
        # These explicitly contain new business values; compare via projection.
        preserved -= {sql for sql in preserved if 'CASE WHEN x=1' in sql or sql == 'SELECT 1'}
        for case in self.fixture['cases']:
            with self.subTest(sql=case['sql']):
                actual = self.result(case['sql'])['normalized']
                self.assertEqual(hashlib.sha256(dumps(case['normalized']).encode('ascii')).hexdigest(),
                                 case['structure_sha256'])
                # Ensure audit does not call the implementation under test.
                with patch.object(Normalizer, '_walk', side_effect=AssertionError('product walker')):
                    projected, _ = project_v5(case['restored_v4'], self.engine, sets=True)
                self.assertEqual(projected, actual)
                if case['sql'] in preserved:
                    self.assertEqual(case['normalized'], actual)

    def test_set_branches_match_standalone_rules_in_every_container(self):
        left = ("SELECT 'A', x IN (1,2), to_date('2020','YYYY') "
                "FROM a JOIN b ON a.id=b.id AND b.kind='K' WHERE x IN (1,2)")
        right = ("SELECT 'B', x IN (3,4,5), to_date('2021','YYYY') "
                 "FROM a JOIN b ON a.id=b.id AND b.kind='L' WHERE x IN (3,4,5)")
        for op, wrapper in product(('UNION', 'INTERSECT', 'EXCEPT'), QUERY_WRAPPERS):
            with self.subTest(op=op, wrapper=wrapper):
                a = wrapper.format(q='(' + left + ') ' + op + ' (' + left + ')')
                b = wrapper.format(q='(' + right + ') ' + op + ' (' + right + ')')
                self.assertEqual(self.result(a)['fingerprint'], self.result(b)['fingerprint'])
        for op in ('UNION', 'INTERSECT', 'EXCEPT'):
            for a,b in PROTECTED:
                if not (a.startswith('SELECT') and b.startswith('SELECT')):
                    continue
                with self.subTest(op=op, a=a):
                    wrapped = '(' + a + ') ' + op + ' (' + b + ')'
                    branch = self.result(wrapped)['normalized']['statements'][0]['base']['SelectStmt']
                    expected = [self.result(q)['normalized']['statements'][0]['base']['SelectStmt'] for q in (a,b)]
                    self.assertEqual([branch['larg'],branch['rarg']], expected)
                    self.assertNotEqual(branch['larg'],branch['rarg'])
        # Query VALUES must not become INSERT's direct VALUES under a set branch.
        a = self.result('INSERT INTO t SELECT 1 UNION VALUES (2)')['normalized']
        b = self.result('INSERT INTO t SELECT 9 UNION VALUES (3)')['normalized']
        self.assertNotEqual(a,b)

    def test_deep_frozen_branch_protection_does_not_use_recursive_equality(self):
        from sql_apm.sql.structure import loads
        body={'leaf': 1}
        for _ in range(1500):
            body={'opaque':body}
        tree={'hints':[], 'statements':[{'base':{'SelectStmt':{'larg':{'targetList':body}}},'extensions':[]}]}
        class Frozen:
            context={'algorithm_version':'sql-normalization/4'}
            def _walk(self, node, counters):
                return loads(dumps(node))
        projected, counts=project_v5(tree,Frozen(),sets=True,restore=True)
        self.assertEqual(dumps(projected),dumps(tree))
        self.assertEqual(counts['set_branches_visited'],1)

    def test_restoration_refuses_v5_walker(self):
        with self.assertRaisesRegex(ValueError, 'frozen_v4_required'):
            project_v5(self.fixture['cases'][0]['normalized'], self.engine, sets=True, restore=True)

    def test_hint_anchor_exception_is_preserved_at_new_positions(self):
        for template, hint in product(IN_TEMPLATES, ('/*+ H */', '--+ H\n')):
            a, b = (self.result(template.format(op='IN', v=v) + ' ' + hint) for v in ('1,2','1,2,3'))
            self.assertEqual(a['normalized']['statements'], b['normalized']['statements'])
            self.assertNotEqual(a['fingerprint'], b['fingerprint'])
            self.assertEqual(a['normalized']['hints'], self.fixture_hint(template, hint))

    def fixture_hint(self, template, hint):
        # Anchor comes directly from the unchanged parser, independent of v5.
        from sql_apm.sql.mpp_parser import parse
        return [{k:v for k,v in h.items() if k != 'gap'}
                for h in parse(template.format(op='IN', v='1,2') + ' ' + hint)['hints']]


if __name__ == '__main__':
    unittest.main()
