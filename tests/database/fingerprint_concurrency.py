"""Exercise the real metadata writers with overlapping fingerprint pages."""
from contextlib import ExitStack
import json
import threading
import time
from unittest.mock import patch

from psycopg2.extras import execute_values

from ingestion.test_reader import configuration, row, write_csv
from sql_apm.ingestion.config import load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.ingestion.normalizing import normalize
from sql_apm.sql.normalization import Normalizer
from sql_apm.storage import ingestion, training
from sql_apm.training.config import validate


class LocalPool:
    """Real normalization without worker startup in the metadata-only probe."""
    def __init__(self):
        self.engine = Normalizer()

    def map(self, raws):
        return [normalize(self.engine, raw) for raw in raws]


def wait_for_blocker(db, blocked, blocker):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with db, db.cursor() as cur:
            cur.execute('SELECT %s=ANY(pg_blocking_pids(%s))', (blocker, blocked))
            if cur.fetchone()[0]:
                return
        time.sleep(0.01)  # Poll pacing; the server's wait edge is the evidence.
    raise AssertionError('fingerprint writers did not reach the intended overlap')


def interleave(leader, follower, leading_write, following_write, observer):
    """Hold the first real page until the other writer waits, then send page two.

    Before the fix, the follower owns the shared fingerprint's unique key and
    waits on the leader's counter row. Afterwards it waits before any insert.
    Both states have the same observable wait edge; neither is assumed here.
    """
    first_page, release = threading.Event(), threading.Event()
    outcomes = {}

    def paged(cur, statement, values, template=None, page_size=100, fetch=False):
        if cur.connection is not leader or not statement.startswith('INSERT INTO mpp_fingerprint'):
            return execute_values(cur, statement, values, template, page_size, fetch)
        assert len(values) > page_size, 'the leader must insert multiple real pages'
        result = []
        for offset in range(0, len(values), page_size):
            page = execute_values(cur, statement, values[offset:offset+page_size], template, page_size, fetch)
            if fetch:
                result.extend(page)
            if offset == 0:
                first_page.set()
                assert release.wait(15), 'coordinator did not release the first fingerprint page'
        return result if fetch else None

    def run(name, action):
        try:
            outcomes[name] = action()
        except Exception as error:
            outcomes[name] = error

    workers = [threading.Thread(target=run, args=('leader', leading_write)),
               threading.Thread(target=run, args=('follower', following_write))]
    with patch.object(ingestion, 'execute_values', paged), patch.object(training, 'execute_values', paged):
        try:
            workers[0].start()
            assert first_page.wait(15), ('leader did not finish page one', outcomes)
            workers[1].start()
            wait_for_blocker(observer, follower.get_backend_pid(), leader.get_backend_pid())
            release.set()
            for worker in workers:
                worker.join(15)
            assert all(not worker.is_alive() for worker in workers), 'writers did not finish'
        finally:
            release.set()
            for db, worker in zip((leader, follower), workers):
                if worker.is_alive():
                    db.cancel()
            for worker in workers:
                if worker.ident is not None:
                    worker.join(10)
    return outcomes


def verify(v, dsn, directory):
    """Import/prepare and prepare/prepare must match serial cache results."""
    with ExitStack() as stack:
        def connection():
            db = ingestion.connect(dsn, 'sql_apm')
            stack.callback(db.close)
            with db, db.cursor() as cur:
                cur.execute("SET statement_timeout='12s'")
            return db

        observer = connection()
        seed = ingestion.SqlWriter(observer, LocalPool())
        seed.resolve([f'SELECT {n} AS fingerprint_lock_probe'.encode() for n in range(300)])
        with observer, observer.cursor() as cur:
            cur.execute('SELECT sql_id,text FROM mpp_sql_text ORDER BY sql_id')
            texts = cur.fetchall()
        assert len(texts) == 300
        manifests, stores = [], []
        for index, selection in enumerate((texts[:200], texts[100:])):
            scope = 'FP' + str(index)
            path, cfg = directory / (scope+'.csv'), directory / (scope+'.json')
            write_csv(path, [row(text=text, message='duration: 1 ms', **{'10': 'cmd'+str(n)})
                             for n, (_, text) in enumerate(selection)])
            document = configuration(cfg, [path], scope)
            document['clusters'] = [scope]
            document['sources'] = {scope: dict(document['sources']['S1'], cluster=scope)}
            document['batches'][scope]['source'] = scope
            cfg.write_text(json.dumps(document))
            importer = Importer(dsn, workers=1, progress=lambda **kw: None)
            try:
                assert importer.run(load_config(cfg, scope, scope))['state'] == 'complete'
            finally:
                importer.close()
            store = training.TrainingStore(db=connection())
            config = validate(dict(version=1, clusters=[scope], window=dict(cutoff_date='2026-07-23')), scope)
            rule, templates = store._rule(config)
            manifests.append(store._select(scope, [scope], []))
            stores.append(store)
        leader_db = connection()
        norm = stores[0].normalization_id

        def reset():
            # This private fixture has no snapshots or builds. Remove only its
            # cache so both writers see texts missing the current rule, as after
            # a normalization change. The immutable cache forbids row DELETE.
            with observer, observer.cursor() as cur:
                cur.execute('TRUNCATE mpp_training_sql')
                cur.execute('DELETE FROM mpp_fingerprint')

        def contents():
            with observer, observer.cursor() as cur:
                cur.execute('SELECT * FROM mpp_fingerprint ORDER BY sql_id')
                fingerprints = cur.fetchall()
                cur.execute('SELECT * FROM mpp_training_sql ORDER BY sql_id,rule_id')
                cache = cur.fetchall()
                cur.execute('''SELECT mpp_query_missing_rules(%s),
                    (SELECT count(*) FROM mpp_sql_text t WHERE NOT EXISTS
                        (SELECT FROM mpp_fingerprint f WHERE f.sql_id=t.sql_id AND f.normalization_id=%s)),
                    (SELECT item_count FROM mpp_fingerprint_count WHERE normalization_id=%s),
                    (SELECT count(*) FROM mpp_fingerprint WHERE normalization_id=%s)''', (norm,)*4)
                missing, expected_missing, count, expected_count = cur.fetchone()
                assert missing == expected_missing == 0 and count == expected_count == 300
                return fingerprints, cache

        failures = {}
        for label in ('import-training', 'training-training'):
            def actions():
                follower = lambda: stores[1]._prepare(manifests[1], rule, templates)
                if label == 'import-training':
                    writer = ingestion.SqlWriter(leader_db, LocalPool())
                    leader = lambda: writer.resolve([text.encode() for _, text in texts[:200]])
                    return leader_db, leader, follower
                leader = lambda: stores[0]._prepare(manifests[0], rule, templates)
                return stores[0].db, leader, follower

            reset()
            _, first, second = actions()
            first(); second()
            expected = contents()
            reset()
            db, first, second = actions()
            outcomes = interleave(db, stores[1].db, first, second, observer)
            errors = {name: (getattr(value, 'pgcode', None), str(value))
                      for name, value in outcomes.items() if isinstance(value, Exception)}
            if errors:
                failures[label] = errors
                continue
            v.require(contents() == expected, label + ': both writers commit; fingerprints, training cache and exact counts match serial execution')
        assert not failures, failures
