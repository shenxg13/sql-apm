"""Calendar and operational configuration boundaries, independent of PostgreSQL."""
import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from sql_apm.baseline.retention import expired
from sql_apm.training.config import TrainingError, validate, retention_months, load_retention, load_config


class RetentionTests(unittest.TestCase):
    def document(self):
        return dict(version=1,clusters=['119','120'],window=dict(cutoff_date='2026-07-31'))

    def test_month_boundary(self):
        for current,months,last_expired,first_kept in [
            ('2027-01-01',2,'2026-10-01','2026-11-01'),
            ('2026-03-01',1,'2026-01-01','2026-02-01'),
            ('2026-10-01',12,'2025-09-01','2025-10-01')]:
            self.assertTrue(expired(date.fromisoformat(last_expired),date.fromisoformat(current),months))
            self.assertFalse(expired(date.fromisoformat(first_kept),date.fromisoformat(current),months))
        self.assertFalse(expired(date(2026,1,1),date(2027,1,1),10**100))

    def test_retention_not_in_training_snapshot_input(self):
        plain=self.document();doc=copy.deepcopy(plain)
        doc['retention']=dict(months=3,clusters={'119':12})
        self.assertEqual(validate(plain,'119'),validate(doc,'119'))
        self.assertEqual(retention_months(plain,'119'),2)
        self.assertEqual(retention_months(doc,'119'),12)
        self.assertEqual(retention_months(doc,'120'),3)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json';p.write_text(json.dumps(doc))
            self.assertEqual(load_config(p,'119',with_retention=True),(validate(plain,'119'),12))
            doc['window'].pop('cutoff_date');p.write_text(json.dumps(doc))
            before=p.read_bytes()
            self.assertEqual(load_retention(p,'119'),12)
            self.assertEqual(p.read_bytes(),before)

    def test_rejected_configuration(self):
        for value in (0,-1,1.5,True,False,'2',None):
            for retention in ({'months':value},{'clusters':{'119':value}}):
                doc=self.document();doc['retention']=retention
                with self.subTest(retention=retention),self.assertRaisesRegex(TrainingError,'^invalid_retention_months$'):
                    validate(doc,'119')
        for value in (None,[],{'days':2},{'clusters':[]}):
            doc=self.document();doc['retention']=value
            with self.subTest(value=value),self.assertRaisesRegex(TrainingError,'^invalid_retention$'):
                validate(doc,'119')
        doc=self.document();doc['retention']={'clusters':{'unknown':1}}
        with self.assertRaisesRegex(TrainingError,'^unknown_retention_cluster$'):validate(doc,'119')
