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
                with self.subTest(text=text):
                    result=classify(text)
                    self.assertEqual('pure',result['kind'])
                    self.assertEqual([category],result['categories'])

    def test_deferred_and_non_categories(self):
        for text in ['END','START TRANSACTION','ANALYSE t',"COMMIT PREPARED 'tx'",'SET ROLE NONE',
                     'SET SESSION AUTHORIZATION DEFAULT',"SET \"role\" TO 'example'",
                     "SET \"session_authorization\" TO 'example'",'SET TRANSACTION READ ONLY',
                     'SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY','SET CONSTRAINTS ALL DEFERRED',
                     'ROLLBACK','ABORT','SAVEPOINT s','RELEASE SAVEPOINT s','RESET ALL','DISCARD ALL',
                     'SHOW work_mem','PREPARE p AS SELECT 1','EXECUTE p','DEALLOCATE ALL',
                     'FETCH ALL FROM c','LOCK TABLE t','EXPLAIN ANALYZE SELECT 1','REINDEX TABLE t',
                     'CLUSTER t','CHECKPOINT','CREATE TABLE t AS SELECT 1','DROP TABLE t',
                     "SELECT set_config('work_mem','16MB',false)",'ALTER INDEX i RENAME TO j',
                     'ALTER VIEW v RENAME TO v2',"SELECT 'SET; COMMIT', $$ALTER TABLE t$$",'DO $$BEGIN PERFORM 1; END$$']:
            with self.subTest(text=text):self.assertNotEqual('pure',classify(text)['kind'])

    def test_whole_batches_and_incomplete_grammar(self):
        for text,kind in [("SET work_mem='16MB'; BEGIN; COMMIT",'pure'),
                          ("SET work_mem='16MB'; BEGIN; INSERT INTO t VALUES(1); COMMIT",'mixed'),
                          ("SET work_mem='16MB'; END",'mixed'),
                          ("/* SELECT */ sEt work_mem='16MB'; -- INSERT",'pure'),
                          ("SET work_mem='16MB'; SELECT 'cut",'unknown'),
                          ('; /* comment */ ;','unknown'),('SET broken','unknown'),
                          ('COMMIT gibberish','unknown'),('ALTER TABLE','unknown')]:
            with self.subTest(text=text):self.assertEqual(kind,classify(text)['kind'])
