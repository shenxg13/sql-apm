"""Full EOF coverage census with exact SQL deduplication; no product fingerprints.

Raw SQL and the disposable SQLite index must stay in ignored var/. Reports
contain only fixed diagnostics, counts, hashes and source locators.
"""
import argparse
from collections import Counter, OrderedDict
import csv
from datetime import date
import hashlib
import io
import json
import multiprocessing
from multiprocessing.connection import wait
from pathlib import Path
import platform
import resource
import sqlite3
import time
from importlib.metadata import version

from sql_apm.diagnostics.mpp_adapter_probe import EVIDENCE, MAX_BYTES, TIMEOUT, worker
from sql_apm.diagnostics.statement_census import DURATION, INLINE, DigestReader
from sql_apm.sql.lexical import diagnose
from sql_apm.sql.mpp_parser import VERSION

ROOT = Path(__file__).resolve().parents[2]
MEMORY_BYTES = 512 * 1024 * 1024
SOURCE_PATHS = ('sql_apm/sql/mpp_parser.py', 'sql_apm/sql/lexical.py',
                'sql_apm/sql/pg_ast.py', 'sql_apm/sql/structure.py', 'sql_apm/diagnostics/mpp_adapter_probe.py',
                'sql_apm/diagnostics/parser_fidelity.py',
                'sql_apm/diagnostics/statement_census.py',
                'sql_apm/diagnostics/mpp_full_scan.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def emit(**values):
    print(json.dumps(values, sort_keys=True), flush=True)


def context(evidence):
    return dict(prototype_version=VERSION, python=platform.python_version(),
                pglast=version('pglast'), evidence_sha256=sha(evidence),
                sources={p: sha(ROOT / p) for p in SOURCE_PATHS},
                limits=dict(max_sql_bytes=MAX_BYTES, timeout_seconds=TIMEOUT,
                            worker_address_space_bytes=MEMORY_BYTES))


def candidates(row):
    """Count each distinct field value per row, preserving exact text and batch."""
    seen = set()
    if row[24].strip():
        seen.add(row[24])
        yield 'sql', row[24]
    message = DURATION.sub('', row[18], count=1)
    match = INLINE.match(message)
    if match:
        sql = message[match.end():]
        if sql.strip() and sql not in seen:
            seen.add(sql)
            yield 'inline_distinct', sql
    if row[21].strip() and row[21] not in seen:
        yield 'internal', row[21]


def set_meta(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, json.dumps(value)))


def get_meta(db, key):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def initialize(db):
    db.executescript('''
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE inputs (id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL,
            sql BLOB NOT NULL, locator TEXT NOT NULL, result TEXT);
        CREATE TABLE occurrences (input_id INTEGER NOT NULL, file_key TEXT NOT NULL,
            field TEXT NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY (input_id, file_key, field));
        CREATE TABLE files (file_key TEXT PRIMARY KEY, evidence TEXT NOT NULL);
        CREATE TABLE occurrence_dates (input_id INTEGER NOT NULL, file_key TEXT NOT NULL,
            day TEXT NOT NULL, field TEXT NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY (input_id, file_key, day, field));
    ''')


class ExactIndex:
    """Bound hot-cache memory; byte comparison also detects digest collisions."""
    def __init__(self, db, cache_bytes=32 * 1024 * 1024):
        self.db, self.cache, self.used, self.limit = db, OrderedDict(), 0, cache_bytes

    def observe(self, raw, locator):
        key = hashlib.sha256(raw).hexdigest()
        cached = self.cache.get(key)
        if cached is None:
            cached = self.db.execute('SELECT id, sql FROM inputs WHERE sha256=?', (key,)).fetchone()
            if cached is None:
                cursor = self.db.execute('INSERT INTO inputs (sha256,sql,locator) VALUES (?,?,?)',
                                         (key, raw, json.dumps(locator)))
                cached = (cursor.lastrowid, raw)
            if len(raw) <= self.limit:
                # Charge metadata too, bounding millions of tiny inputs.
                cost = len(raw) + 256
                while self.cache and self.used + cost > self.limit:
                    _, (_, old) = self.cache.popitem(last=False)
                    self.used -= len(old) + 256
                if cost <= self.limit:
                    self.cache[key] = cached
                    self.used += cost
        else:
            self.cache.move_to_end(key)
        if cached[1] != raw:
            raise ValueError('SQL digest collision; refusing to merge distinct inputs')
        return cached[0]


def collect(root, evidence, db, record_dates=False):
    manifest, started = json.loads(evidence.read_text()), time.monotonic()
    index = ExactIndex(db)
    csv.field_size_limit(128 * 1024 * 1024)
    set_meta(db, 'context', context(evidence))
    set_meta(db, 'collection_complete', False)
    set_meta(db, 'record_dates', record_dates)
    db.commit()
    for cluster, data in sorted(manifest['clusters'].items()):
        for entry in sorted(data['files'], key=lambda x: x['file']):
            path = (root / cluster / entry['file']).resolve()
            if root not in path.parents:
                raise ValueError('source outside root')
            before = path.stat()
            if before.st_size != entry['bytes']:
                raise ValueError('source size mismatch')
            key, counts, batch = cluster + '/' + entry['file'], Counter(), Counter()
            previous_line, last_progress = 0, time.monotonic()
            dated, records_by_date = Counter(), Counter()
            with path.open('rb', buffering=0) as raw:
                hashing = DigestReader(raw)
                with io.TextIOWrapper(io.BufferedReader(hashing, 1024 * 1024),
                                      encoding='utf-8', errors='surrogateescape', newline='') as stream:
                    reader = csv.reader(stream, strict=True)
                    for number, row in enumerate(reader, 1):
                        begin, previous_line = previous_line + 1, reader.line_num
                        if len(row) != 30:
                            raise ValueError('CSV column count mismatch')
                        counts['records'] += 1
                        if record_dates:
                            try:
                                day = date.fromisoformat(row[0][:10]).isoformat()
                            except ValueError:
                                raise ValueError('invalid_record_date') from None
                            records_by_date[day] += 1
                        counts['primary_empty'] += not bool(row[24].strip())
                        for field, sql in candidates(row):
                            counts['inputs'] += 1
                            counts[field] += 1
                            locator = dict(cluster=cluster, file=entry['file'], record=number,
                                           field=field, line_start=begin, line_end=reader.line_num)
                            uid = index.observe(sql.encode('utf-8', 'surrogateescape'), locator)
                            batch[(uid, key, field)] += 1
                            if record_dates:
                                dated[(uid, key, day, field)] += 1
                        if number % 10000 == 0:
                            flush_counts(db, batch)
                            flush_dates(db, dated)
                        if time.monotonic() - last_progress > 20:
                            emit(phase='collect', file=key, records=number)
                            last_progress = time.monotonic()
                actual_bytes, actual_sha = hashing.used, hashing.sha.hexdigest()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError('source changed during full scan')
            if (actual_bytes != entry['bytes'] or actual_sha != entry['sha256'] or
                    counts['records'] != entry['counts']['records']):
                raise ValueError('full source evidence mismatch')
            flush_counts(db, batch)
            flush_dates(db, dated)
            file_evidence = dict(cluster=cluster, file=entry['file'], bytes=actual_bytes,
                                 sha256=actual_sha, full_file_hash_rechecked=True,
                                 read_to_eof=True, stat_unchanged=True, counts=dict(counts))
            if record_dates:
                file_evidence['records_by_date'] = dict(records_by_date)
            db.execute('INSERT INTO files VALUES (?,?)', (key, json.dumps(file_evidence)))
            db.commit()
            emit(phase='file_complete', file=key, counts=dict(counts),
                 unique_inputs=db.execute('SELECT count(*) FROM inputs').fetchone()[0])
    set_meta(db, 'collection_complete', True)
    set_meta(db, 'collection_seconds', round(time.monotonic() - started, 3))
    db.commit()


def flush_counts(db, batch):
    db.executemany('''INSERT INTO occurrences VALUES (?,?,?,?)
        ON CONFLICT(input_id,file_key,field) DO UPDATE SET count=count+excluded.count''',
                   [(*key, count) for key, count in batch.items()])
    batch.clear()


def flush_dates(db, batch):
    db.executemany('''INSERT INTO occurrence_dates VALUES (?,?,?,?,?)
        ON CONFLICT(input_id,file_key,day,field) DO UPDATE SET count=count+excluded.count''',
                   [(*key, count) for key, count in batch.items()])
    batch.clear()


def worker_loop(connection, memory_bytes, worker_options=None, task=worker):
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    connection.send('ready')
    while True:
        try:
            raw = connection.recv_bytes()
        except EOFError:
            break
        sql = raw.decode('utf-8', 'surrogateescape')
        result = task(dict(worker_options or {}, sql=sql))
        if result.get('exception') == 'MemoryError':
            result = dict(state='probe_memory_limit')
        categories, issues = diagnose(sql)
        result.update(categories=list(categories), lexical_issues=list(issues))
        connection.send(result)


class ParserProcess:
    """A sequential child with a per-request watchdog; recycle for bounded RSS."""
    def __init__(self, timeout=TIMEOUT, memory_bytes=MEMORY_BYTES, worker_options=None, task=worker):
        self.timeout, self.memory_bytes = timeout, memory_bytes
        self.worker_options = worker_options
        self.task = task
        self.process = self.connection = None
        self.pending, self.calls = None, 0

    def start(self):
        ctx = multiprocessing.get_context('spawn')
        self.connection, child = ctx.Pipe()
        self.process = ctx.Process(target=worker_loop, args=(child, self.memory_bytes, self.worker_options, self.task))
        self.process.start()
        child.close()
        if not self.connection.poll(30) or self.connection.recv() != 'ready':
            self.close()
            raise RuntimeError('parser worker startup failed')

    def submit(self, uid, raw):
        if self.process is None:
            self.start()
        self.connection.send_bytes(raw)
        self.pending = (uid, time.monotonic())

    def receive(self):
        if self.connection.poll():
            try:
                result = self.connection.recv()
            except (EOFError, OSError):
                result = dict(state='worker_failed')
        elif time.monotonic() - self.pending[1] >= self.timeout:
            result = dict(state='probe_timeout')
        else:
            return None
        uid = self.pending[0]
        self.pending = None
        self.calls += 1
        if result['state'] in ('worker_failed', 'probe_timeout', 'probe_memory_limit') or self.calls >= 1000:
            self.close()
        return uid, result

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
        self.pending, self.calls = None, 0


def parse_all(db, workers=4):
    processes = [ParserProcess() for _ in range(workers)]
    started, last_progress, done = time.monotonic(), time.monotonic(), 0
    pending = iter(db.execute('SELECT id,sql FROM inputs WHERE result IS NULL ORDER BY id'))
    exhausted = False
    try:
        while True:
            for process in processes:
                while process.pending is None and not exhausted:
                    item = next(pending, None)
                    if item is None:
                        exhausted = True
                        break
                    uid, raw = item
                    if len(raw) > MAX_BYTES:
                        db.execute('UPDATE inputs SET result=? WHERE id=?',
                                   (json.dumps(dict(state='probe_size_limit')), uid))
                        done += 1
                    else:
                        process.submit(uid, raw)
                if process.pending is not None:
                    received = process.receive()
                    if received is not None:
                        uid, result = received
                        db.execute('UPDATE inputs SET result=? WHERE id=?', (json.dumps(result), uid))
                        done += 1
            active = [p for p in processes if p.pending is not None]
            if exhausted and not active:
                break
            if time.monotonic() - last_progress >= 20:
                db.commit()
                emit(phase='parse', completed_this_run=done,
                     seconds=round(time.monotonic() - started, 1))
                last_progress = time.monotonic()
            if active:
                wait([p.connection for p in active], timeout=0.05)
    finally:
        for process in processes:
            process.close()
        db.commit()
    set_meta(db, 'parse_seconds', round(time.monotonic() - started, 3))
    set_meta(db, 'workers', workers)
    db.commit()


def report(db):
    if not get_meta(db, 'collection_complete'):
        raise ValueError('incomplete collection')
    if db.execute('SELECT count(*) FROM inputs WHERE result IS NULL').fetchone()[0]:
        raise ValueError('incomplete parsing')
    states, reasons, roots, extensions = Counter(), Counter(), Counter(), Counter()
    weighted_states, weighted_reasons, categories = Counter(), Counter(), Counter()
    clusters, fields, examples = {}, {}, {}
    total_occurrences, largest, batch_count = 0, 0, 0
    for uid, key, size, locator, encoded in db.execute(
            'SELECT id,sha256,length(sql),locator,result FROM inputs ORDER BY id'):
        result, location = json.loads(encoded), json.loads(locator)
        state, reason = result['state'], result.get('reason', result.get('exception', result['state']))
        occurrences = list(db.execute('SELECT file_key,field,count FROM occurrences WHERE input_id=?', (uid,)))
        count = sum(o[2] for o in occurrences)
        total_occurrences += count
        largest = max(largest, size)
        states[state] += 1
        reasons[reason] += 1
        weighted_states[state] += count
        weighted_reasons[reason] += count
        roots.update(result.get('roots', []))
        extensions.update(result.get('extensions', {}))
        categories.update(set(result.get('categories', [])))
        batch_count += len(result.get('roots', [])) > 1
        for file_key, field, frequency in occurrences:
            cluster = file_key.split('/')[0]
            clusters.setdefault(cluster, Counter())[state] += frequency
            fields.setdefault(field, Counter())[state] += frequency
        if state != 'prototype_parsed':
            bucket = examples.setdefault(reason, [])
            if len(bucket) < 5:
                bucket.append(dict(sql_sha256=key, bytes=size, locator=location,
                                   occurrences=count, result=result))
    files = [json.loads(row[0]) for row in db.execute('SELECT evidence FROM files ORDER BY file_key')]
    return dict(purpose='Full parser coverage census; AST return is not structural fidelity or fingerprint acceptance',
                context=get_meta(db, 'context'), files=files,
                totals=dict(files=len(files), bytes=sum(f['bytes'] for f in files),
                            records=sum(f['counts']['records'] for f in files),
                            unique_inputs=sum(states.values()), input_occurrences=total_occurrences,
                            largest_sql_bytes=largest, parsed_unique_batches=batch_count),
                unique_states=states, occurrence_states=weighted_states,
                unique_reasons=reasons, occurrence_reasons=weighted_reasons,
                cluster_occurrence_states=clusters, field_occurrence_states=fields,
                unique_input_categories=categories, statement_roots=roots, extensions=extensions,
                failure_examples=examples, collection_seconds=get_meta(db, 'collection_seconds'),
                parse_seconds=get_meta(db, 'parse_seconds'), workers=get_meta(db, 'workers'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=ROOT / 'raw/inbox/hashdata')
    ap.add_argument('--evidence', type=Path, default=EVIDENCE)
    ap.add_argument('--database', type=Path, default=ROOT / 'var/parser-probe/full-scan.sqlite')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--phase', choices=('collect', 'parse', 'report', 'all'), default='all')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--record-dates', action='store_true', help='index actual CSV record dates')
    args = ap.parse_args()
    if platform.python_version() != '3.9.5' or version('pglast') != '7.18':
        ap.error('requires Python 3.9.5 and pglast 7.18')
    database, output, root = args.database.resolve(), args.output.resolve(), args.root.resolve()
    if not 1 <= args.workers <= 8:
        ap.error('workers must be between 1 and 8')
    if (ROOT / 'var').resolve() not in database.parents:
        ap.error('raw database must stay under ignored repository var/')
    if output == database or output == root or root in output.parents:
        ap.error('output conflicts with input')
    if args.phase in ('collect', 'all') and database.exists():
        ap.error('collection requires a new disposable database; existing data preserved')
    if args.phase in ('parse', 'report') and not database.is_file():
        ap.error('collection database missing')
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(database)) as db:
        db.execute('PRAGMA cache_size=-32768')
        if args.phase in ('collect', 'all'):
            initialize(db)
            collect(root, args.evidence, db, args.record_dates)
        if get_meta(db, 'context') != context(args.evidence):
            raise ValueError('parser/runtime/source evidence context changed')
        if not get_meta(db, 'collection_complete'):
            raise ValueError('incomplete collection; use a new database')
        if args.phase in ('parse', 'all'):
            parse_all(db, args.workers)
        if args.phase in ('parse', 'report', 'all'):
            if get_meta(db, 'context') != context(args.evidence):
                raise ValueError('parser/runtime/source evidence changed during parsing')
            result = report(db)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
            emit(totals=result['totals'], unique_states=result['unique_states'])


if __name__ == '__main__':
    main()
