"""Regressions for broad parser evidence and COPY placement safety."""
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sql_apm.sql.mpp_parser import parse, Unsupported
from sql_apm.diagnostics import mpp_broad_replay as sampling
from sql_apm.diagnostics.mpp_broad_matrix import cases, formatted


class BroadTests(unittest.TestCase):
    def test_copy_option_must_be_in_legacy_option_position(self):
        invalid = ["COPY t TO '/tmp/a' ON SEGMENT WITH CSV HEADER",
                   "COPY t TO PROGRAM ON SEGMENT 'cat'",
                   "COPY t FROM PROGRAM ON SEGMENT 'cat'",
                   "COPY t TO '/tmp/a' WITH (format csv) ON SEGMENT",
                   "COPY t TO '/tmp/a' ON SEGMENT WITH (format csv)",
                   "COPY t TO '/tmp/a' ON SEGMENT (format csv)",
                   "COPY t TO '/tmp/a' WITH ON SEGMENT (format csv)"]
        for sql in invalid:
            for index in range(3):
                batch = ['SELECT 1','SELECT 2']
                batch.insert(index,sql)
                with self.subTest(sql=sql, index=index):
                    with self.assertRaises(Unsupported):
                        parse(';'.join(batch))

    def test_copy_validation_preserves_real_freeze_and_all_options(self):
        for tail in (' FREEZE ON SEGMENT CSV HEADER', ' ON SEGMENT FREEZE CSV HEADER',
                     ' WITH FREEZE CSV ON SEGMENT HEADER', ' WITH ON SEGMENT',
                     " CSV FORCE QUOTE a,b ON SEGMENT NULL ''"):
            sql = "COPY t TO '/tmp/a'" + tail
            with self.subTest(tail=tail):
                current = parse(sql)['statements'][0]
                expected = parse(sql.replace(' ON SEGMENT',''))['statements'][0]
                self.assertEqual(current['base'],expected['base'])
                self.assertEqual(current['extensions'],[{'kind':'copy_on_segment','enabled':True}])
                self.assertEqual(parse(sql),parse(formatted(sql,1)))

    def test_all_synthetic_complete_oracles_and_rejections(self):
        for case in cases():
            with self.subTest(case=case['id']):
                if case['expected'] is None:
                    with self.assertRaises(Unsupported):
                        parse(case['sql'])
                else:
                    tree = parse(case['sql'])
                    self.assertEqual(tree['statements'],case['expected'])
                    self.assertEqual(tree['hints'],case.get('hints',[]))
                    for style in range(2):
                        self.assertEqual(tree,parse(formatted(case['sql'],style)))

    def test_sampling_distinguishes_internal_and_deduplicates_same_input(self):
        row=['']*30
        row[24],row[21]='SELECT 1','SELECT 2'
        self.assertEqual(list(sampling.candidates_from_row(row)),[('sql','SELECT 1'),('internal','SELECT 2')])
        row[21]='SELECT 1'
        self.assertEqual(list(sampling.candidates_from_row(row)),[('sql','SELECT 1')])

    def test_sampling_handles_crlf_and_bounded_partial_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            root=directory/'raw'
            (root/'c').mkdir(parents=True)
            stream=io.StringIO(newline='')
            writer=csv.writer(stream)
            for sql in ('SELECT 1', "SELECT 'line1\rline2\nline3'"):
                row=['']*30
                row[24],row[27],row[28]=sql,'postgres.c','1'
                writer.writerow(row)
            data=stream.getvalue().encode()
            source=root/'c'/'data.csv'
            source.write_bytes(data)
            manifest=directory/'manifest.json'
            manifest.write_text(json.dumps({'clusters':{'c':{'files':[{'file':'data.csv','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}}}))
            base=directory/'base.json'
            base.write_text(json.dumps({'rows':[]}))
            cache=directory/'cache.json'
            # Bound inside the second CSV record after both a bare CR and LF.
            boundary=data.index(b'line3')
            with patch.multiple(sampling,ROOT=directory,EVIDENCE=manifest,BASE_CACHES=[base],PREFIX_BYTES=boundary,MAX_RECORDS=10,PER_FILE=16):
                sampling.prepare(root,cache)
                result=json.loads(cache.read_text())
                self.assertEqual(len(result['rows']),1)
                self.assertEqual(result['files'][0]['counts']['bounded_partial_record_discarded'],1)
            with patch.multiple(sampling,ROOT=directory,EVIDENCE=manifest,BASE_CACHES=[base],PREFIX_BYTES=len(data)+1,MAX_RECORDS=1,PER_FILE=16):
                sampling.prepare(root,cache)
                result=json.loads(cache.read_text())
                self.assertEqual(result['files'][0]['counts']['records'],1)
                self.assertTrue(result['files'][0]['full_file_hash_rechecked'])
            source.write_bytes(data+b'x')
            with patch.multiple(sampling,ROOT=directory,EVIDENCE=manifest,BASE_CACHES=[base]):
                with self.assertRaisesRegex(ValueError,'source size mismatch'):
                    sampling.prepare(root,cache)


if __name__ == '__main__':
    unittest.main()
