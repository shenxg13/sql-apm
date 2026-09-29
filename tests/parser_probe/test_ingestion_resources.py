"""Parser-dependent ingestion isolation checks in the pinned parser suite."""
import unittest


class ResourceTests(unittest.TestCase):
    def test_external_timeout_recovers_worker(self):
        from sql_apm.ingestion.normalizing import NormalizingPool
        pool = NormalizingPool(1, timeout_seconds=0.000001)
        try:
            result = pool.map([b'SELECT ' + b'1+' * 100000 + b'1'])[0]
            self.assertEqual('normalization_timeout', result['fingerprint']['reason'])
            self.assertIsNone(result['approximate'])
            pool.timeout = 5
            self.assertEqual('reliable', pool.map([b'SELECT 1'])[0]['fingerprint']['state'])
        finally:
            pool.close()
