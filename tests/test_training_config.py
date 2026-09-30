import copy
import json
from pathlib import Path
import tempfile
import unittest

from sql_apm.training.config import TrainingError, validate, load_config


def config():
    return dict(version=1,clusters=['C1','C2'],window=dict(cutoff_date='2026-07-31'),templates=[],exclusions=[])


class TrainingConfigTests(unittest.TestCase):
    def test_window_and_defaults(self):
        result=validate(config(),'C1')
        self.assertEqual('2026-07-02T00:00:00+08:00',result['window_start'])
        self.assertEqual('2026-08-01T00:00:00+08:00',result['window_end'])
        self.assertEqual(4,result['thresholds']['weekday']['coverage_min'])
        doc=config();doc['window']=dict(cutoff_date='2024-02-29',days=1)
        self.assertEqual('2024-03-01T00:00:00+08:00',validate(doc,'C1')['window_end'])

    def test_invalid_windows_clusters_and_keys(self):
        cases=[('days',0),('days',-1),('days',True),('days','30'),('days',10**10),('cutoff_date','2026-02-30')]
        for key,value in cases:
            doc=config();doc['window'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(TrainingError):
                validate(doc,'C1')
        with self.assertRaisesRegex(TrainingError,'unknown_cluster'):
            validate(config(),'missing')
        doc=config();doc['category_rules']=['SELECT']
        with self.assertRaisesRegex(TrainingError,'unknown_config_key'):
            validate(doc,'C1')

    def test_intervals_templates_and_duplicate_ids(self):
        doc=config()
        doc['templates']=[dict(id='T1',sql='SELECT 1',description='synthetic',cluster='C2',database='db',execution_user='user')]
        interval=dict(id='E1',cluster='C1',start='2026-07-23T10:00:00+08:00',end='2026-07-23T10:30:00+08:00',reason='synthetic')
        doc['exclusions']=[interval]
        self.assertEqual('C1',validate(doc,'C1')['exclusions'][0]['scope_id'])
        for patch in [dict(end=interval['start']),dict(start=interval['end']),dict(start='2026-07-23T10:00:00'),dict(cluster='missing'),dict(id='T1')]:
            bad=copy.deepcopy(doc);bad['exclusions'][0].update(patch)
            with self.subTest(patch=patch),self.assertRaises(TrainingError):
                validate(bad,'C1')
        doc['templates'][0]['database']=''
        with self.assertRaises(TrainingError):validate(doc,'C1')

    def test_duplicate_json_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json';p.write_text('{"version":1,"version":1}')
            with self.assertRaisesRegex(TrainingError,'duplicate_config_key'):load_config(p,'C1')
