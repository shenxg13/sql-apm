"""Parser-dependent ingestion isolation checks in the pinned parser suite."""
import unittest


class ResourceTests(unittest.TestCase):
    def test_restart_does_not_timeout_an_already_returned_reply(self):
        from unittest.mock import patch
        from sql_apm.ingestion import normalizing

        clock = [0.0]
        good = dict(fingerprint=dict(state='reliable', reason=None, value='synthetic'), approximate=None)

        class Child:
            def __init__(self, replies):
                self.replies = replies
                self.process = self.connection = None
                self.calls = self.sent = self.received = 0

            def start(self):
                self.process = object()
                self.connection = self

            def send_bytes(self, raw):
                self.sent += 1
                if self.replies and self.sent == 1:
                    clock[0] = 2.0

            def poll(self):
                # This reply arrives at t=6, within its t=7 deadline, during
                # the first child's synchronous close/restart at t=5..7.
                return self.replies and clock[0] >= 6.0

            def recv(self):
                self.received += 1
                return good

            def close(self):
                clock[0] += 2.0
                self.process = self.connection = None
                self.calls = 0

        def wait(connections, timeout):
            clock[0] = max(clock[0], 5.0) if clock[0] < 5.0 else clock[0] + 5.0
            return []  # Snapshot before the synchronous restart, deliberately stale.

        pool = normalizing.NormalizingPool(2)
        slow, completed = Child(False), Child(True)
        pool.workers = [slow, completed]
        with patch.object(normalizing, 'wait', wait), patch.object(normalizing.time, 'monotonic', lambda: clock[0]):
            result = pool.map([b'slow', b'completed'])
        self.assertEqual('normalization_timeout', result[0]['fingerprint']['reason'])
        self.assertEqual(2, slow.sent)
        self.assertEqual(good, result[1])
        self.assertEqual(1, completed.sent)
        self.assertEqual(1, completed.received)

    def test_external_timeout_recovers_worker(self):
        from unittest.mock import patch
        from sql_apm.ingestion.normalizing import NormalizingPool
        pool = NormalizingPool(1, timeout_seconds=0.000001)
        try:
            with patch.object(pool.workers[0], 'start', wraps=pool.workers[0].start) as start:
                result = pool.map([b'SELECT ' + b'1+' * 100000 + b'1'])[0]
                self.assertEqual(2, start.call_count)
            self.assertEqual('normalization_timeout', result['fingerprint']['reason'])
            self.assertIsNone(result['approximate'])
            pool.timeout = 5
            self.assertEqual('reliable', pool.map([b'SELECT 1'])[0]['fingerprint']['state'])
        finally:
            pool.close()

    def test_startup_failure_discards_other_inflight_replies(self):
        from unittest.mock import patch
        from sql_apm.ingestion.config import IngestionError
        from sql_apm.ingestion.normalizing import NormalizingPool
        from sql_apm.sql.normalization import Normalizer
        pool = NormalizingPool(2)
        try:
            with patch.object(pool.workers[1], 'start', side_effect=IngestionError('normalization_worker_start_failed')):
                with self.assertRaisesRegex(IngestionError, '^normalization_worker_start_failed$'):
                    pool.map([b'SELECT 1', b'SELECT 2'])
            self.assertTrue(all(child.process is None for child in pool.workers))
            sql = b'CREATE TABLE cleanup_case(id bigint)'
            self.assertEqual(Normalizer().normalize(sql)['fingerprint'], pool.map([sql])[0]['fingerprint'])
        finally:
            pool.close()

    def test_send_failure_has_fixed_reason_and_discards_workers(self):
        from unittest.mock import patch
        from sql_apm.ingestion.config import IngestionError
        from sql_apm.ingestion.normalizing import NormalizingPool
        pool = NormalizingPool(1)
        try:
            pool.workers[0].start()
            with patch.object(pool.workers[0].connection, 'send_bytes', side_effect=BrokenPipeError()):
                with self.assertRaisesRegex(IngestionError, '^normalization_worker_failed$'):
                    pool.map([b'SELECT 1'])
            self.assertIsNone(pool.workers[0].process)
            self.assertEqual('reliable', pool.map([b'SELECT 2'])[0]['fingerprint']['state'])
        finally:
            pool.close()

    def test_deterministic_failure_preserves_concurrent_input_order(self):
        from sql_apm.ingestion.normalizing import NormalizingPool
        from sql_apm.sql.normalization import Normalizer
        pool = NormalizingPool(2)
        inputs = [b'SELECT ' + b'1+' * 100000 + b'1',
                  b'CREATE TABLE concurrent_case(id bigint)',
                  b'INSERT INTO concurrent_case VALUES (1)', b'SELECT * FROM concurrent_case']
        try:
            results = pool.map(inputs)
            self.assertEqual('normalization_failed', results[0]['fingerprint']['state'])
            self.assertEqual('normalization_worker_failed', results[0]['fingerprint']['reason'])
            self.assertIsNone(results[0]['approximate'])
            for raw, result in zip(inputs[1:], results[1:]):
                self.assertEqual(Normalizer().normalize(raw)['fingerprint'], result['fingerprint'])
            self.assertEqual('reliable', pool.map([b'SELECT 3'])[0]['fingerprint']['state'])
        finally:
            pool.close()
