import unittest
from sql_apm.training.categories import classify


class TrainingCategoryTests(unittest.TestCase):
    def test_confirmed_variants(self):
        examples={
            'SET':["SET work_mem='16MB'","SET SESSION work_mem='16MB'","SET LOCAL work_mem='16MB'",
                   "SET \"work_mem\" TO '16MB'","SET TIME ZONE 'UTC'","SET NAMES 'UTF8'","SET SCHEMA 'public'","SET SEED TO 0.2"],
            'BEGIN':['BEGIN','BEGIN WORK','BEGIN TRANSACTION READ ONLY'],
            'COMMIT':['COMMIT','COMMIT WORK','COMMIT TRANSACTION'],
            'VACUUM':['VACUUM','VACUUM FULL FREEZE VERBOSE t','VACUUM ANALYZE t'],
            'ANALYZE':['ANALYZE','ANALYZE VERBOSE t(id)'],
            'CREATE INDEX':['CREATE INDEX i ON t(id)','CREATE UNIQUE INDEX i ON t(id)'],
            'ALTER TABLE':['ALTER TABLE t ADD COLUMN flag int','ALTER TABLE ONLY t DROP COLUMN flag',
                           'ALTER TABLE IF EXISTS t RENAME TO t2','ALTER TABLE t TRUNCATE PARTITION p']}
        for category,texts in examples.items():
            for text in texts:
                for fast in (False,True):
                    with self.subTest(text=text,grammar_verified=fast):
                        result=classify(text,grammar_verified=fast)
                        self.assertEqual('pure',result['kind'])
                        self.assertEqual([category],result['categories'])

    def test_deferred_and_non_categories(self):
        for text in ["COMMIT PREPARED 'tx'",'SET ROLE NONE',
                     'SET SESSION AUTHORIZATION DEFAULT',"SET \"role\" TO 'example'",
                     "SET \"session_authorization\" TO 'example'",'SET TRANSACTION READ ONLY',
                     'SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY','SET CONSTRAINTS ALL DEFERRED',
                     'ROLLBACK','ABORT','SAVEPOINT s','RELEASE SAVEPOINT s','RESET ALL','DISCARD ALL',
                     'SHOW work_mem','PREPARE p AS SELECT 1','EXECUTE p','DEALLOCATE ALL',
                     'FETCH ALL FROM c','LOCK TABLE t','EXPLAIN ANALYZE SELECT 1','REINDEX TABLE t',
                     'CLUSTER t','CHECKPOINT','CREATE TABLE t AS SELECT 1','DROP TABLE t',
                     "SELECT set_config('work_mem','16MB',false)",'ALTER INDEX i RENAME TO j',
                     'ALTER VIEW v RENAME TO v2',"SELECT 'SET; COMMIT', $$ALTER TABLE t$$",'DO $$BEGIN PERFORM 1; END$$']:
            for fast in (False,True):
                with self.subTest(text=text,grammar_verified=fast):
                    self.assertNotEqual('pure',classify(text,grammar_verified=fast)['kind'])

    def test_whole_batches_and_incomplete_grammar(self):
        for text,kind in [("SET work_mem='16MB'; BEGIN; COMMIT",'pure'),
                          ("SET work_mem='16MB'; BEGIN; INSERT INTO t VALUES(1); COMMIT",'mixed'),
                          ("SET work_mem='16MB'; END",'pure'),
                          ("/* SELECT */ sEt work_mem='16MB'; -- INSERT",'pure'),
                          ("SET work_mem='16MB'; SELECT 'cut",'unknown'),
                          ('; /* comment */ ;','unknown'),('SET broken','unknown'),
                          ('COMMIT gibberish','unknown'),('ALTER TABLE','unknown')]:
            with self.subTest(text=text):self.assertEqual(kind,classify(text)['kind'])

    def test_prefixed_transaction_special_forms(self):
        for prefix in ('', 'LOCAL ', 'SESSION '):
            for mode in ('READ ONLY', 'READ WRITE', 'ISOLATION LEVEL SERIALIZABLE', 'DEFERRABLE'):
                text='SET '+prefix+'SESSION CHARACTERISTICS AS TRANSACTION '+mode
                for fast in (False,True):
                    with self.subTest(text=text,grammar_verified=fast):
                        self.assertEqual(dict(kind='none',categories=[]),classify(text,grammar_verified=fast))
        for text in ['SET LOCAL TRANSACTION READ ONLY','SET SESSION TRANSACTION READ ONLY',
                     "SET TRANSACTION SNAPSHOT '00000001-1'",'SET LOCAL SESSION AUTHORIZATION DEFAULT',
                     'SET SESSION ROLE NONE','SET LOCAL ROLE NONE',
                     r'SET U&"\0074ransaction_read_only" TO on',
                     'SET LOCAL U&"!0074ransaction_read_only" UESCAPE \'!\' = on']:
            for fast in (False,True):
                with self.subTest(text=text,grammar_verified=fast):
                    self.assertEqual(dict(kind='none',categories=[]),classify(text,grammar_verified=fast))

    def test_transaction_parameter_spellings(self):
        for stem,value in [('isolation',"'serializable'"),('read_only','on'),('deferrable','on')]:
            for default in ('','default_'):
                parameter=default+'transaction_'+stem
                for name in (parameter,parameter.upper(),'"'+parameter.upper()+'"'):
                    for prefix in ('','SESSION ','LOCAL '):
                        for assignment in ('TO','='):
                            text='SET '+prefix+name+' '+assignment+' '+value
                            for fast in (False,True):
                                with self.subTest(text=text,grammar_verified=fast):
                                    self.assertEqual(dict(kind='none',categories=[]),classify(text,grammar_verified=fast))

    def test_transaction_batches_and_keyword_literals(self):
        cases=[
            ("BEGIN; SET transaction_isolation TO 'serializable'; COMMIT", 'mixed', ['BEGIN', 'COMMIT']),
            ('SET LOCAL SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; COMMIT', 'mixed', ['COMMIT']),
            ("SET default_transaction_read_only=on; SET work_mem='16MB'", 'mixed', ['SET']),
            ("/* transaction_isolation */ SET work_mem='16MB'", 'pure', ['SET']),
            ("SET application_name='transaction_read_only; SESSION CHARACTERISTICS'", 'pure', ['SET']),
        ]
        for text,kind,categories in cases:
            for fast in (False,True):
                with self.subTest(text=text,grammar_verified=fast):
                    self.assertEqual(dict(kind=kind,categories=categories),classify(text,grammar_verified=fast))

    def test_confirmed_aliases_on_both_paths(self):
        from sql_apm.sql.normalization import Normalizer
        engine = Normalizer()
        variants = {
            'COMMIT': ['END', 'END WORK', 'END TRANSACTION', '/* before */ eNd /* after */ WORK;'],
            'BEGIN': ['START TRANSACTION', 'start /* mode */ transaction READ ONLY',
                      'START TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ WRITE, DEFERRABLE'],
            'ANALYZE': ['ANALYSE', 'ANALYSE VERBOSE t(col)',
                        'analyse /* target */ "schema"."table"("column")'],
        }
        for category, texts in variants.items():
            for text in texts:
                with self.subTest(text=text):
                    self.assertEqual('reliable', engine.normalize(text)['fingerprint']['state'])
                    expected = dict(kind='pure', categories=[category])
                    self.assertEqual(expected, classify(text))
                    self.assertEqual(expected, classify(text, grammar_verified=True))

    def test_alias_negative_boundaries_and_batches(self):
        from sql_apm.sql.normalization import Normalizer
        engine = Normalizer()
        cases = [
            ('EXPLAIN ANALYSE SELECT 1', 'none', []),
            ('VACUUM ANALYSE t', 'pure', ['VACUUM']),
            ('DO $$BEGIN PERFORM 1; END$$', 'none', []),
            ("SELECT 'END; START TRANSACTION; ANALYSE'", 'none', []),
            ('SELECT "END", "ANALYSE", "START TRANSACTION" FROM t', 'none', []),
            ("SET work_mem='16MB'; END", 'pure', ['COMMIT', 'SET']),
            ('START TRANSACTION; INSERT INTO t VALUES (1); END', 'mixed', ['BEGIN', 'COMMIT']),
            ('BEGIN; SELECT 1; END', 'mixed', ['BEGIN', 'COMMIT']),
            ('START TRANSACTION; ANALYSE t; END WORK', 'pure', ['ANALYZE', 'BEGIN', 'COMMIT']),
            ("SET transaction_read_only=on; END", 'mixed', ['COMMIT']),
        ]
        for text, kind, categories in cases:
            with self.subTest(text=text):
                self.assertEqual('reliable', engine.normalize(text)['fingerprint']['state'])
                expected = dict(kind=kind, categories=categories)
                self.assertEqual(expected, classify(text))
                self.assertEqual(expected, classify(text, grammar_verified=True))
        # Invalid text cannot enter the trusted fast path: normalization must refuse it.
        for text in ['END nonsense', 'START', 'START TRANSACTION ISOLATION LEVEL',
                     'ANALYSE VERBOSE t(', 'END; SELECT FROM', "ANALYSE 'unfinished"]:
            with self.subTest(text=text):
                self.assertNotEqual('reliable', engine.normalize(text)['fingerprint']['state'])
                self.assertEqual(dict(kind='unknown', categories=[]), classify(text))
