"""Complete manifests and atomic file imports into the 1.2.0 storage contract."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
import uuid

from sql_apm.ingestion.config import IngestionError, canonical, identity
from sql_apm.ingestion.hashdata.reader import Records, Interpreter, record_metrics
from sql_apm.ingestion.hashdata.persistence import write_records
from sql_apm.ingestion.normalizing import NormalizingPool
from sql_apm.storage.ingestion import connect, SqlWriter

PROFILE = 'hashdata-csv/1'
MAPPING = 'hashdata-3.13.13/1'
ASSOCIATION = 'execute-file-sequence/1'


def checksum(path):
    sha, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(chunk)
            sha.update(chunk)
    return sha.hexdigest(), size


def emit(**values):
    print(canonical(values), flush=True)


class Importer:
    def __init__(self, dsn, schema='sql_apm', workers=4, progress=emit, fault=None):
        self.db = connect(dsn, schema)
        self.sql_db = connect(dsn, schema)
        self.pool = NormalizingPool(workers)
        self.writer = SqlWriter(self.sql_db, self.pool)
        self.progress, self.fault = progress, fault
        self.config = None

    def close(self):
        self.db.close()
        self.sql_db.close()
        self.pool.close()

    def problem(self, cur, file_id, code, effect, attempt=None, record=None, occurrence=None):
        pid = 'P:' + uuid.uuid4().hex
        cur.execute('''INSERT INTO problem VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,1,%s,NULL)''',
                    (pid, 'record' if record else 'file' if file_id else 'batch',
                     self.config['batch_id'], file_id, self.analysis_id if occurrence else None,
                     occurrence, code, code, effect, 'log_record' if record else 'file' if file_id else 'problem',
                     'isolated' if record else 'open'))
        if record:
            cur.execute('INSERT INTO problem_evidence VALUES (%s,%s)', (pid, record))
        if attempt:
            cur.execute('INSERT INTO attempt_problem VALUES (%s,%s)', (attempt, pid))

    def register(self, config):
        self.config = config
        self.analysis_id = 'A:' + identity(config['batch_id'])
        self.task_id = 'T:' + uuid.uuid4().hex
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,1835101))', (config['scope_id'],))
            if not cur.fetchone()[0]:
                raise IngestionError('cluster_busy')
            source = config['source']
            scope_values = (config['scope_id'], 'hashdata', PROFILE, '1.0.0')
            cur.execute('INSERT INTO scope VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', scope_values)
            cur.execute('SELECT scope_id,system_kind,profile,contract_version FROM scope WHERE scope_id=%s', (config['scope_id'],))
            if cur.fetchone() != scope_values:
                raise IngestionError('scope_mapping_changed')
            values = (config['source_id'], config['scope_id'], 'sha256:' + identity(source), source['build'], source['timezone'], source['declaration'])
            cur.execute('INSERT INTO source VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', values)
            cur.execute('SELECT * FROM source WHERE source_id=%s', (config['source_id'],))
            if cur.fetchone() != values:
                raise IngestionError('source_mapping_changed')
            cur.execute("INSERT INTO import_batch VALUES (%s,%s,true,'pending') ON CONFLICT DO NOTHING", (config['batch_id'], config['scope_id']))
            cur.execute('SELECT scope_id FROM import_batch WHERE batch_id=%s', (config['batch_id'],))
            if cur.fetchone()[0] != config['scope_id']:
                raise IngestionError('batch_scope_changed')
            manifest = canonical(dict(digest=config['manifest_digest'], files=config['files'], dates=config['dates'], source=config['source_id']))
            cur.execute('''INSERT INTO analysis VALUES (%s,%s,%s,%s,'mpp-adapter/9',%s,NULL,%s) ON CONFLICT DO NOTHING''',
                        (self.analysis_id, config['scope_id'], PROFILE, MAPPING, ASSOCIATION, manifest))
            cur.execute('SELECT evidence_manifest FROM analysis WHERE analysis_id=%s', (self.analysis_id,))
            if cur.fetchone()[0] != manifest:
                raise IngestionError('batch_manifest_changed')
            for day in config['dates']:
                cur.execute('INSERT INTO batch_date VALUES (%s,%s) ON CONFLICT DO NOTHING', (config['batch_id'], day))
            # Lock admission proves prior task/attempt owner has exited, including SIGKILL.
            cur.execute("UPDATE task SET state='interrupted',reason='owner_exited' WHERE scope_id=%s AND state='running'", (config['scope_id'],))
            cur.execute("UPDATE import_attempt SET state='interrupted',finished_at=clock_timestamp() WHERE scope_id=%s AND state='running'", (config['scope_id'],))
            cur.execute("INSERT INTO task VALUES (%s,%s,'import_only','running','import',NULL,NULL)", (self.task_id, config['scope_id']))
            cur.execute('INSERT INTO task_batch VALUES (%s,%s,%s)', (self.task_id, config['batch_id'], config['scope_id']))
            cur.execute("UPDATE import_batch SET state='processing' WHERE batch_id=%s", (config['batch_id'],))

    def run(self, config):
        started = time.monotonic()
        registered = False
        try:
            self.register(config)
            registered = True
            results = []
            for index, entry in enumerate(config['files'], 1):
                result = self.file(entry)
                results.append(result)
                self.progress(phase='file_finished', index=index, total=len(config['files']), **result)
            state = 'conflict' if any(r['state'] == 'conflict' for r in results) else (
                'complete' if all(r['state'] in ('succeeded', 'duplicate_skipped') for r in results) else 'failed')
            with self.db, self.db.cursor() as cur:
                # Reconcile content identities only after every frozen manifest path
                # has an attempt; aliases may legitimately share a content entry.
                cur.execute('DELETE FROM batch_entry WHERE batch_id=%s AND NOT (file_id=ANY(%s))',
                            (config['batch_id'], [r['file_id'] for r in results]))
                cur.execute('''SELECT count(*) FROM batch_entry e LEFT JOIN import_attempt a
                    ON a.attempt_id=e.final_attempt_id WHERE e.batch_id=%s
                    AND (a.state IS NULL OR a.state NOT IN ('succeeded','duplicate_skipped'))''', (config['batch_id'],))
                if cur.fetchone()[0] and state == 'complete':
                    state = 'failed'
                cur.execute('UPDATE import_batch SET state=%s WHERE batch_id=%s', (state, config['batch_id']))
                cur.execute('UPDATE task SET state=%s,reason=%s WHERE task_id=%s',
                            ('succeeded' if state == 'complete' else 'failed', None if state == 'complete' else 'batch_incomplete', self.task_id))
            return dict(state=state, files=results, seconds=round(time.monotonic()-started, 3),
                        added_records=sum(r.get('added_records', 0) for r in results),
                        added_occurrences=sum(r.get('added_occurrences', 0) for r in results))
        except BaseException as error:
            self.db.rollback()
            if registered:
                with self.db, self.db.cursor() as cur:
                    cur.execute("UPDATE import_batch SET state='failed' WHERE batch_id=%s", (config['batch_id'],))
                    cur.execute('UPDATE task SET state=%s,reason=%s WHERE task_id=%s',
                                ('interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'failed',
                                 'import_interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'import_failed', self.task_id))
            raise
        finally:
            with self.db.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock_all()')
            self.db.commit()

    def file(self, entry):
        config = self.config
        path = Path(entry['path'])
        failure = None
        try:
            sha, size = checksum(path)
        except OSError:
            sha, size, failure = 'unreadable:' + identity(str(path)), 0, 'file_unreadable'
        fid = 'I:' + identity(config['source_id'], sha)
        attempt = 'AT:' + uuid.uuid4().hex
        declaration = dict(origin_key=entry['origin_key'], declaration=config['source']['declaration'],
                           input_observed=failure is None)
        with self.db, self.db.cursor() as cur:
            cur.execute('''INSERT INTO source_file VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s)
                ON CONFLICT DO NOTHING''', (fid, config['source_id'], config['scope_id'], sha, 'sha256' if failure is None else 'unavailable',
                                           sha, size, str(path), canonical(declaration)))
            cur.execute('SELECT attempt_id FROM import_attempt WHERE file_id=%s AND state=\'succeeded\' ORDER BY finished_at LIMIT 1', (fid,))
            success = cur.fetchone()
            cur.execute('SELECT attempt_id FROM import_attempt WHERE file_id=%s ORDER BY started_at DESC LIMIT 1', (fid,))
            previous = cur.fetchone()
            if entry['origin_key']:
                cur.execute('''SELECT f.declaration_evidence FROM source_file f WHERE source_id=%s AND file_id<>%s
                    AND EXISTS (SELECT FROM import_attempt a WHERE a.file_id=f.file_id AND a.state='succeeded')''', (config['source_id'], fid))
                if any(entry['origin_key'] in (json.loads(row[0]).get('origin_keys', []) +
                                             [json.loads(row[0]).get('origin_key')]) for row in cur):
                    failure = 'origin_content_changed'
            if failure:
                success = None
            cur.execute('''INSERT INTO import_attempt VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp(),
                CASE WHEN %s THEN clock_timestamp() ELSE NULL END,NULL)''',
                (attempt, config['batch_id'], fid, config['scope_id'], previous[0] if previous and not success else None,
                 success[0] if success else None, 'duplicate_skipped' if success else 'running', bool(success)))
            cur.execute('''INSERT INTO batch_entry VALUES (%s,%s,%s,%s) ON CONFLICT (batch_id,file_id)
                DO UPDATE SET final_attempt_id=excluded.final_attempt_id''', (config['batch_id'], fid, config['scope_id'], attempt))
            if success:
                if entry['origin_key']:
                    cur.execute('SELECT declaration_evidence FROM source_file WHERE file_id=%s', (fid,))
                    saved = json.loads(cur.fetchone()[0])
                    keys = saved.get('origin_keys', []) + [saved.get('origin_key')]
                    if entry['origin_key'] not in keys:
                        saved['origin_keys'] = sorted({key for key in keys if key} | {entry['origin_key']})
                        cur.execute('UPDATE source_file SET declaration_evidence=%s WHERE file_id=%s', (canonical(saved), fid))
                return dict(file_id=fid, state='duplicate_skipped', added_records=0, added_occurrences=0)
        if failure:
            return self.fail_file(fid, attempt, failure, 'conflict' if failure == 'origin_content_changed' else 'failed')
        try:
            counts = Counter()
            rows, parser = Records(path), Interpreter()
            buffer, buffered_bytes, last_progress = [], 0, time.monotonic()
            with self.db, self.db.cursor() as cur:
                cur.execute('INSERT INTO analysis_file VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (self.analysis_id, fid, config['scope_id']))
                for number, begin, end, row in rows:
                    rid = fid + ':' + str(number)
                    event = parser.interpret(row, rid)
                    metrics, duration = record_metrics(row)
                    counts.update(metrics)
                    buffer.append((rid, number, begin, end, row, event, duration))
                    buffered_bytes += sum(len(x) for x in row)
                    if len(buffer) >= 2000 or buffered_bytes >= 8 * 1024 * 1024:
                        write_records(self, cur, fid, buffer, counts)
                        buffer.clear()
                        buffered_bytes = 0
                        if self.fault:
                            self.fault(number)
                    if time.monotonic() - last_progress > 20:
                        self.progress(phase='file_read', file_id=fid, records=number, occurrences=counts['occurrences'])
                        last_progress = time.monotonic()
                write_records(self, cur, fid, buffer, counts)
                if self.fault:
                    self.fault(rows.count)
                if rows.sha256 != sha or rows.byte_count != size:
                    raise IngestionError('file_changed_during_read')
                edges = rows.edge_pairs()
                cur.execute('''SELECT f.declaration_evidence FROM source_file f WHERE source_id=%s AND file_id<>%s
                    AND EXISTS (SELECT FROM import_attempt a WHERE a.file_id=f.file_id AND a.state='succeeded')''', (config['source_id'], fid))
                if any(set(edges).intersection(json.loads(row[0]).get('edge_pairs', [])) for row in cur):
                    raise IngestionError('record_edge_overlap')
                declaration.update(edge_pairs=edges, counts=dict(counts), sha256=sha)
                cur.execute('UPDATE source_file SET declaration_evidence=%s WHERE file_id=%s', (canonical(declaration), fid))
                cur.execute("UPDATE import_attempt SET state='succeeded',finished_at=clock_timestamp(),reliable_record_count=%s WHERE attempt_id=%s", (rows.count, attempt))
            return dict(file_id=fid, state='succeeded', counts=dict(counts), added_records=rows.count, added_occurrences=counts['occurrences'])
        except (KeyboardInterrupt, SystemExit):
            self.db.rollback()
            self.fail_file(fid, attempt, 'import_interrupted', 'interrupted')
            raise
        except Exception as error:
            self.db.rollback()
            reason = str(error) if isinstance(error, IngestionError) else 'file_processing_failed'
            return self.fail_file(fid, attempt, reason, 'conflict' if reason == 'record_edge_overlap' else 'failed')

    def fail_file(self, fid, attempt, code, state):
        with self.db, self.db.cursor() as cur:
            cur.execute('UPDATE import_attempt SET state=%s,finished_at=clock_timestamp() WHERE attempt_id=%s', (state, attempt))
            self.problem(cur, fid, code, 'block_publication' if state == 'conflict' else 'fail_file', attempt=attempt)
        return dict(file_id=fid, state=state, reason=code, added_records=0, added_occurrences=0)
