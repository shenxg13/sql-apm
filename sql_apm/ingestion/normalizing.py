"""Bounded parser processes with an external per-input watchdog."""
import multiprocessing
from multiprocessing.connection import wait
import resource
import time

from sql_apm.ingestion.config import IngestionError
from sql_apm.sql.normalization import Normalizer
from sql_apm.sql.lexical import diagnose


def normalize(engine, raw):
    result = engine.normalize(raw)
    categories, issues = diagnose(raw.decode('utf-8', 'surrogateescape'))
    sql_state = ('invalid_encoding' if 'invalid_encoding_or_nul' in issues else
                 'uncertain' if 'ambiguous_string_escape' in issues else
                 'incomplete' if issues else 'uncertain')
    if result['fingerprint']['state'] == 'reliable':
        sql_state = 'complete'
    return dict(sql_state=sql_state,
                shape='batch' if len(categories) > 1 else 'single' if categories else 'unknown',
                fingerprint=result['fingerprint'], approximate=result['approximate'])


def worker(connection):
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    engine = Normalizer()
    connection.send('ready')
    while True:
        try:
            raw = connection.recv_bytes()
        except EOFError:
            break
        try:
            result = normalize(engine, raw)
        except Exception:
            result = failure('normalization_worker_failed')
        connection.send(result)


def failure(reason):
    return dict(sql_state='uncertain', shape='unknown', approximate=None,
                fingerprint=dict(state='normalization_failed', value=None, reason=reason))


class ParserWorker:
    def __init__(self):
        self.process = self.connection = None
        self.calls = 0

    def start(self):
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=worker, args=(child,), daemon=True)
        self.process.start()
        child.close()
        if not self.connection.poll(30) or self.connection.recv() != 'ready':
            self.close()
            raise IngestionError('normalization_worker_start_failed')

    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
            self.connection.close()
        self.process = self.connection = None
        self.calls = 0


class NormalizingPool:
    def __init__(self, workers, timeout_seconds=5):
        if not 1 <= workers <= 8:
            raise IngestionError('worker_count_invalid')
        self.workers = [ParserWorker() for _ in range(workers)]
        self.timeout = timeout_seconds

    def map(self, inputs):
        results, pending, next_index = [None] * len(inputs), {}, 0
        while next_index < len(inputs) or pending:
            for child in self.workers:
                if child in pending or next_index == len(inputs):
                    continue
                if child.process is None:
                    child.start()
                child.connection.send_bytes(inputs[next_index])
                pending[child] = (next_index, time.monotonic())
                next_index += 1
            ready = wait([child.connection for child in pending], timeout=0.01)
            for child, (index, start) in list(pending.items()):
                expired = time.monotonic() - start >= self.timeout
                if child.connection not in ready and not expired:
                    continue
                if child.connection in ready:
                    try:
                        result = child.connection.recv()
                    except (EOFError, OSError):
                        result = failure('normalization_worker_failed')
                else:
                    result = failure('normalization_timeout')
                results[index] = result
                del pending[child]
                child.calls += 1
                if expired or result['fingerprint']['state'] == 'normalization_failed' or child.calls >= 1000:
                    child.close()
        return results

    def close(self):
        for child in self.workers:
            child.close()
