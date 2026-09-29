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
        try:
            self.process.start()
            child.close()
            if not self.connection.poll(30) or self.connection.recv() != 'ready':
                raise IngestionError('normalization_worker_start_failed')
        except (OSError, EOFError, IngestionError):
            child.close()
            self.close()
            raise IngestionError('normalization_worker_start_failed') from None

    def close(self):
        if self.process is not None and self.process.pid is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
        if self.connection is not None:
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
        try:
            return self._map(inputs)
        except BaseException:
            # An interrupted map must not leave replies for a later file's inputs.
            self.close()
            raise

    def _send(self, child, raw, index, attempt, pending):
        if child.process is None:
            child.start()
        try:
            child.connection.send_bytes(raw)
        except (OSError, EOFError):
            # No accepted input/reply pair: this is a file-level transport failure.
            raise IngestionError('normalization_worker_failed') from None
        pending[child] = (index, time.monotonic(), attempt)

    def _map(self, inputs):
        results, pending, next_index = [None] * len(inputs), {}, 0
        while next_index < len(inputs) or pending:
            for child in self.workers:
                if child in pending or next_index == len(inputs):
                    continue
                self._send(child, inputs[next_index], next_index, 1, pending)
                next_index += 1
            ready = wait([child.connection for child in pending], timeout=0.01)
            for child, (index, start, attempt) in list(pending.items()):
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
                del pending[child]
                child.calls += 1
                if result['fingerprint']['reason'] in ('normalization_timeout', 'normalization_worker_failed'):
                    # An accepted input gets at most one retry in a fresh process.
                    # Two failures isolate this input; startup/send failures still raise.
                    child.close()
                    if attempt == 1:
                        self._send(child, inputs[index], index, 2, pending)
                        continue
                results[index] = result
                if expired or result['fingerprint']['state'] == 'normalization_failed' or child.calls >= 1000:
                    child.close()
        return results

    def close(self):
        for child in self.workers:
            child.close()
