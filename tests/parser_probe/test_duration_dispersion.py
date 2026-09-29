"""Duration diagnostic grouping, exact thresholds, ratios and redaction."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sql_apm.diagnostics import duration_dispersion as duration
from sql_apm.diagnostics.mpp_full_scan import initialize, set_meta
from sql_apm.diagnostics.normalization_diff import put_meta


class DurationTests(unittest.TestCase):
    def row(self, message='duration: 2.5 ms', sql="SELECT 'private_sql_493'"):
        row = [''] * 30
        row[0], row[1], row[2] = '2026-09-01 01:02:03+08', 'private_user_493', 'private_db_493'
        row[18], row[24], row[27], row[28] = message, sql, 'postgres.c', '123'
        return row

    def test_one_duration_one_sql_source_precedence_and_hashes(self):
        row = self.row("duration: 2.5 ms statement: SELECT 'inline_secret'")
        item = duration.duration_record(row)
        self.assertEqual(item['raw'], b"SELECT 'private_sql_493'")
        self.assertEqual(item['field'], 'sql')
        self.assertEqual(item['duration_ms'], 2.5)
        self.assertEqual(item['day'], '2026-09-01')
        self.assertEqual(item['site'], 'postgres.c:123')
        self.assertEqual(item['database_hash'], duration.private_hash('private_db_493'))
        self.assertEqual(item['user_hash'], duration.private_hash('private_user_493'))
        row[24] = ''
        self.assertEqual(duration.duration_record(row)['field'], 'inline')
        row[18], row[21] = 'duration: 0 ms', 'SELECT internal'
        self.assertEqual(duration.duration_record(row)['field'], 'internal')
        row[21] = ''
        self.assertEqual(duration.duration_record(row), {'empty': True})
        row[18] = 'ordinary record'
        self.assertIsNone(duration.duration_record(row))
        with self.assertRaisesRegex(ValueError, 'invalid_csv_columns'):
            duration.duration_record([])
        with self.assertRaises(ValueError):
            bad = self.row()
            bad[0] = 'bad date'
            duration.duration_record(bad)

    def db(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE joined(cluster,database_hash,user_hash,site,day,duration_ms,old_fp,new_fp,input_id,select_fp,join_fp)')
        return db

    def add(self, db, count, old='a', new='x', cluster='119', database='db', user='user',
            site='postgres.c:123', days=7, value=1, input_id=None, select_fp=None, join_fp=None):
        db.executemany('INSERT INTO joined VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ((cluster, database, user, site, '2026-09-%02d' % (i % days + 1),
              value, old, new, input_id or old, select_fp or new, join_fp or new) for i in range(count)))

    def test_threshold_edges_rejections_and_date_union(self):
        db = self.db()
        for n in (29,30,199,200,999,1000):
            self.add(db,n,old=str(n),new=str(n))
        # Same new group combines six-day subgroups into seven distinct dates.
        self.add(db,15,old='left',new='merged',days=6)
        self.add(db,15,old='right',new='merged',days=7)
        self.add(db,1000,old='six-days',new='six-days',days=6)
        self.add(db,9,old=None,new=None)
        result = duration.summarize(db)['119']
        self.assertEqual(result['duration_samples'], 3496)
        self.assertEqual([result['versions']['v4']['thresholds'][str(n)]['groups']
                          for n in duration.THRESHOLDS], [5,3,1])
        self.assertEqual([result['versions']['v5']['thresholds'][str(n)]['groups']
                          for n in duration.THRESHOLDS], [6,3,1])
        self.assertEqual(result['versions']['v4']['fingerprinted_samples'], 3487)
        self.assertAlmostEqual(result['versions']['v5']['thresholds']['1000']['fraction_all_duration_samples'],
                               1000/3496)

    def test_all_dimensions_isolate_counts_days_and_merge_subgroups(self):
        db = self.db()
        self.add(db,15,old='a')
        for change in (dict(cluster='120'), dict(database='other'), dict(user='other'), dict(site='postgres.c:124')):
            self.add(db,15,old='b',**change)
        result = duration.summarize(db)
        self.assertEqual(result['119']['versions']['v5']['groups'], 4)
        self.assertEqual(result['120']['versions']['v5']['groups'], 1)
        for cluster in result.values():
            self.assertEqual(cluster['versions']['v5']['thresholds']['30']['groups'], 0)
            self.assertEqual(cluster['merges']['new_merged_groups'], 0)

    def test_ratio_edges_zero_and_sample_weighting(self):
        for low, high, bucket in ((1,2,'<=2'),(1,2.01,'2-5'),(1,5,'2-5'),
                                  (1,5.01,'5-10'),(1,10,'5-10'),(1,10.01,'>10'),
                                  (0,0,'<=2'),(0,1,'>10')):
            self.assertEqual(duration.ratio_bucket(low,high),bucket)
        db = self.db()
        for i, ratio in enumerate((2,5,10,11),1):
            self.add(db,5*i,old='a',new=str(i),value=1)
            self.add(db,5*i,old='b',new=str(i),value=ratio)
            self.add(db,4,old='too-small',new=str(i),value=10000)
        self.add(db,5,old='only-one-large',new='incomparable')
        self.add(db,4,old='small',new='incomparable')
        result = duration.summarize(db)['119']['merges']
        self.assertEqual(result['new_merged_groups'],5)
        self.assertEqual(result['comparable_groups'],4)
        self.assertEqual(result['comparable_samples'],100)
        for label, samples in zip(duration.BUCKETS,(10,20,30,40)):
            self.assertEqual(result['median_ratio_buckets'][label],
                             dict(groups=1,samples=samples,sample_fraction=samples/100))

    def test_category_attribution_and_raw_v4_control(self):
        db = self.db()
        for kind in ('select','join','sets'):
            for old in ('a','b'):
                self.add(db,10,old=kind+old,new=kind,
                    select_fp=kind if kind == 'select' else old,
                    join_fp=kind if kind != 'sets' else old)
        # Reference: two raw SQL identities in one old group, with a 10x ratio.
        self.add(db,5,old='ref',new='ref',input_id='raw1',value=1)
        self.add(db,5,old='ref',new='ref',input_id='raw2',value=10)
        result=duration.summarize(db)['119']
        for category in ('select_list','join_on','set_branches'):
            self.assertEqual(result['merges_by_category'][category]['new_merged_groups'],1)
            self.assertEqual(result['merges_by_category'][category]['comparable_samples'],20)
        self.assertEqual(result['merges']['comparable_samples'],60)
        ref=result['v4_raw_reference']
        self.assertEqual(ref['new_merged_groups'],1)
        self.assertEqual(ref['median_ratio_buckets']['5-10']['samples'],10)

    def source(self, root):
        logs = root/'logs'
        (logs/'119').mkdir(parents=True)
        path = logs/'119/sample.csv'
        rows = [self.row() for _ in range(7)]
        for i,row in enumerate(rows):
            row[0] = '2026-09-%02d 01:02:03+08' % (i+1)
        rows += [self.row('ordinary record'), self.row('duration: 1 ms','')]
        with path.open('w',newline='') as stream:
            csv.writer(stream).writerows(rows)
        source = root/'source.sqlite'
        with sqlite3.connect(str(source)) as db:
            initialize(db)
            set_meta(db,'collection_complete',True)
            set_meta(db,'record_dates',True)
            raw = b"SELECT 'private_sql_493'"
            db.execute('INSERT INTO inputs VALUES (?,?,?,?,?)',
                       (1,hashlib.sha256(raw).hexdigest(),raw,'{}','{}'))
            db.execute('INSERT INTO files VALUES (?,?)',('119/sample.csv',json.dumps(dict(
                bytes=path.stat().st_size,sha256=duration.file_sha(path),counts=dict(records=len(rows))))))
        return source,logs

    def test_isolated_extraction_exact_source_redaction_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source,logs=self.source(root)
            before=duration.file_sha(source)
            with contextlib.redirect_stdout(io.StringIO()):
                result=duration.extract(source,logs,root/'cache.sqlite',workers=1)
            self.assertEqual(result['counts']['samples'],7)
            self.assertEqual(result['counts']['duration_records'],8)
            self.assertEqual(result['counts']['empty_sql'],1)
            self.assertEqual(before,duration.file_sha(source))
            with duration.readonly(root/'cache.sqlite') as db:
                safe='\n'.join(db.iterdump())+json.dumps(result)
                self.assertTrue(duration.meta(db)['complete'])
                self.assertEqual(db.execute('SELECT count(DISTINCT day) FROM samples').fetchone()[0],7)
            for secret in ('private_sql_493','private_user_493','private_db_493','SELECT'):
                self.assertNotIn(secret,safe)
            with self.assertRaisesRegex(ValueError,'output_exists'):
                duration.extract(source,logs,root/'cache.sqlite',workers=1)
            (logs/'119/sample.csv').write_text('corrupt source')
            with self.assertRaisesRegex(ValueError,'extraction_failed'):
                duration.extract(source,logs,root/'failed.sqlite',workers=1)
            with duration.readonly(root/'failed.sqlite') as db:
                self.assertFalse(duration.meta(db)['complete'])

    def test_report_requires_complete_audited_snapshots(self):
        from sql_apm.sql.normalization import Normalizer
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source,logs=self.source(root)
            with contextlib.redirect_stdout(io.StringIO()):
                duration.extract(source,logs,root/'cache.sqlite',workers=1)
            for version in ('v4','v5'):
                with sqlite3.connect(str(root/(version+'.sqlite'))) as db:
                    db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);'
                        'CREATE TABLE records(input_id,sql_sha256,state,reason,fingerprint,structure_sha256,'
                        'v5_projection_sha256,in_lists,occurrences,select_projection_sha256,join_projection_sha256);')
                    context=Normalizer().context
                    context['algorithm_version']='sql-normalization/'+version[1:]
                    for k,v in dict(format='normalization-diff/1',complete=True,records=1,context=context,
                                    source_sha256=duration.file_sha(source),project_v5=version=='v4').items():
                        put_meta(db,k,v)
                    db.execute('INSERT INTO records VALUES (1,?,"reliable",NULL,?,?,?,0,7,?,?)',
                               ('a'*64,version,'b'*64,'b'*64,'b'*64,'b'*64))
            result=duration.report(root/'cache.sqlite',root/'v4.sqlite',root/'v5.sqlite',root/'report.json')
            self.assertEqual(result['clusters']['119']['duration_samples'],7)
            for secret in ('private_sql_493','private_user_493','private_db_493'):
                self.assertNotIn(secret,(root/'report.json').read_text())
            with sqlite3.connect(str(root/'v5.sqlite')) as db:
                db.execute('UPDATE records SET structure_sha256=?',('c'*64,))
            with self.assertRaisesRegex(ValueError,'v5_audit_failed'):
                duration.report(root/'cache.sqlite',root/'v4.sqlite',root/'v5.sqlite',root/'bad.json')
            self.assertFalse((root/'bad.json').exists())


if __name__ == '__main__':
    unittest.main()
