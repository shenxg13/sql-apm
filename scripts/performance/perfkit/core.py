"""Private state, read-only serial sessions and numeric-only public measurements."""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import time

CANCEL_REQUESTED = False

class Refused(Exception):
    """A fixed reason code, never a driver message or a user value."""


def serializable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       default=serializable) + '\n').encode()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    """One work-directory-owned atomic replacement; no global temp directory."""
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def shape(value):
    if value is None:
        return 'null'
    if isinstance(value, (list, tuple)):
        return {'array': [shape(v) for v in value]}
    if isinstance(value, dict):
        return {'object_items': len(value)}
    if isinstance(value, str):
        return {'text_bytes': len(value.encode())}
    if isinstance(value, (bytes, memoryview)):
        return {'bytes': len(value)}
    if isinstance(value, (datetime, date)):
        return 'time'
    if isinstance(value, (float, int, Decimal)):
        return 'number'
    return 'adapted'


class Work:
    def __init__(self, path, root):
        self.path, root = Path(path).resolve(), Path(root).resolve()
        if self.path == root or self.path.is_relative_to(root):
            raise Refused('work_inside_product')
        if not self.path.is_dir():
            raise Refused('prepare_work_directory_first')
        self.lock = (self.path / 'run.lock').open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise Refused('another_toolkit_is_running') from None
        for name in ('private', 'results'):
            child = self.path / name
            if child.is_symlink():
                raise Refused('work_symlink')
            child.mkdir(mode=0o770, exist_ok=True)
        self.private = self.path / 'private'
        self.public = self.path / 'results'
        p = self.private / 'mapping.json'
        self.mapping = json.loads(p.read_text()) if p.exists() else {'objects': {}, 'sensitive': []}
        self.secrets = set(self.mapping['sensitive'])

    def close(self):
        self.lock.close()

    def secret(self, value):
        if isinstance(value, str) and value:
            self.secrets.add(value)

    def alias(self, kind, value):
        bucket = self.mapping['objects'].setdefault(kind, [])
        if value not in bucket:
            bucket.append(value)
        # Numeric cluster labels are mapping keys, not SQL/input/account secrets.
        # Treating them as substrings of counters would reject almost every run.
        if isinstance(value, str) and not (kind == 'cluster' and value.isdecimal()):
            self.secret(value)
        return kind + '-' + str(bucket.index(value) + 1).zfill(4)

    def flush(self):
        self.mapping['sensitive'] = sorted(self.secrets)
        save(self.private / 'mapping.json', self.mapping)

    @contextmanager
    def section(self, name):
        began = time.monotonic()
        record = {'part': name, 'started_at': datetime.now().astimezone(), 'state': 'running'}
        save(self.public / (name + '-state.json'), record)
        try:
            yield
        except BaseException as error:
            record['state'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
            record['reason'] = str(error) if isinstance(error, Refused) else type(error).__name__
            raise
        else:
            record['state'] = 'complete'
        finally:
            record['seconds'] = round(time.monotonic() - began, 6)
            record['ended_at'] = datetime.now().astimezone()
            self.flush()
            save(self.public / (name + '-state.json'), record)
            from .overview import terminal
            print(terminal(self.public,name), flush=True)


class Session:
    active = None

    def __init__(self, dsn, timeout=None):
        self.dsn, self.timeout, self.db = dsn, timeout, None

    def __enter__(self):
        import psycopg2
        from psycopg2.extensions import make_dsn, parse_dsn, set_wait_callback
        from psycopg2.extras import wait_select
        if Session.active is not None:
            raise Refused('concurrent_connection')
        options = parse_dsn(self.dsn).get('options','')+' -c default_transaction_read_only=on -c search_path=sql_apm,pg_catalog -c timezone=Asia/Shanghai'
        if self.timeout is not None:
            options += ' -c statement_timeout=' + str(int(self.timeout))
        set_wait_callback(wait_select)
        self.db = psycopg2.connect(make_dsn(self.dsn, options=options,
            application_name='sql-apm-perfcheck', connect_timeout=5))
        self.db.autocommit = True
        Session.active = self
        return self

    def __exit__(self, *unused):
        try:
            if self.db is not None:
                self.db.close()
        finally:
            Session.active = None

    def query(self, statement, params=None):
        import psycopg2
        with self.db.cursor() as cur:
            try:
                cur.execute(statement, params)
            except psycopg2.errors.QueryCanceled:
                if CANCEL_REQUESTED:
                    raise KeyboardInterrupt from None
                raise
            if cur.description:
                names = [d.name for d in cur.description]
                return [dict(zip(names, row)) for row in cur]
            return []


def measure(session, statement, params=None, *, label, target_ms=1000, replay=None,
            keep=False, upper_limit=False):
    """Consume all rows. Result values only survive when the caller needs them locally."""
    import psycopg2
    started, rows, count = time.monotonic(), [], 0
    record = dict(item=label, target_ms=target_ms, state='ok', parameter_shape=shape(params))
    try:
        with session.db.cursor() as cur:
            cur.execute(statement, params)
            if cur.description:
                names = [d.name for d in cur.description]
                for row in cur:
                    count += 1
                    if keep:
                        rows.append(dict(zip(names, row)))
    except psycopg2.errors.QueryCanceled:
        if CANCEL_REQUESTED:
            raise KeyboardInterrupt from None
        record['state'] = 'over_limit' if upper_limit else 'timeout'
    except psycopg2.Error as error:
        record.update(state='error', sqlstate=error.pgcode)
    record.update(ms=round((time.monotonic()-started)*1000, 3), rows=count)
    record['attention'] = record['state'] != 'ok' or record['ms'] > target_ms
    if replay is not None and (record['ms'] > min(target_ms, 1000) or record['state'] == 'timeout'):
        # Bind once locally, keeping the exact SQL out of public JSON.
        with session.db.cursor() as cur:
            replay.append(dict(item=label, sql=cur.mogrify(statement, params).decode(session.db.encoding)))
    return record, rows


def check_versions(app, session):
    release = json.loads((Path(app) / 'RELEASE.json').read_text())
    if release.get('version') not in ('1.2.0', 'v1.2.0'):
        raise Refused('program_v1_2_0_required')
    versions = {r['version'] for r in session.query('SELECT version FROM schema_version')}
    if '1.13.0' not in versions or any(tuple(map(int, v.split('.'))) > (1, 13, 0) for v in versions):
        raise Refused('schema_1_13_0_required')
    return {'version': 'v1.2.0', 'schema': '1.13.0', 'program_manifest_sha256': digest(Path(app)/'RELEASE.json')}


def idle(session):
    if session.query("SELECT 1 FROM task WHERE state='running' LIMIT 1") or session.query(
            "SELECT 1 FROM mpp_daily_run WHERE state='running' LIMIT 1"):
        raise Refused('daily_or_product_task_running')


def install_signals():
    global CANCEL_REQUESTED
    CANCEL_REQUESTED = False
    def stop(signum, frame):
        global CANCEL_REQUESTED
        CANCEL_REQUESTED = True
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
