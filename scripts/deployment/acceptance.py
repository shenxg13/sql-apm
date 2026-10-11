"""Shared acceptance recording; no product code or raw SQL in public records."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from verify_package import product_file


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write('\n')


def memory():
    values = dict((name.rstrip(':'), int(number)*1024) for name, number, *_ in
                  (line.split() for line in Path('/proc/meminfo').read_text().splitlines()))
    vm = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
    return dict(used_bytes=values['MemTotal']-values['MemAvailable'],
                swap_used_bytes=values['SwapTotal']-values['SwapFree'],
                oom_kills=int(vm['oom_kill']))


def process_memory(pid):
    """Linux RSS sum of this CLI and its descendants; PostgreSQL is separate.

    Shared pages can be counted in multiple processes. A 250ms sample is a
    sampled peak, not a kernel high-water mark; both runtimes use this method.
    Exited children may disappear between reads and contribute zero then.
    """
    pending, visited, total = [pid], set(), 0
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            status = Path('/proc') / str(current) / 'status'
            for line in status.read_text().splitlines():
                if line.startswith('VmRSS:'):
                    total += int(line.split()[1]) * 1024
            children = Path('/proc') / str(current) / 'task' / str(current) / 'children'
            pending.extend(int(value) for value in children.read_text().split())
        except (FileNotFoundError, ProcessLookupError):
            pass
    return total


def command(app, log, words):
    """Sample host memory every 250ms while the actual delivered CLI runs."""
    initial = memory()
    peak, swap_peak, samples = initial['used_bytes'], initial['swap_used_bytes'], 1
    process_peak = 0
    tick = time.monotonic()
    with log.open('x') as stream:
        child = subprocess.Popen([sys.executable, '-m', 'sql_apm'] + words, cwd=app,
            env=dict(os.environ, PYTHONPATH=str(app)), stdout=stream, stderr=subprocess.STDOUT)
        try:
            while child.poll() is None:
                current = memory()
                peak = max(peak, current['used_bytes'])
                swap_peak = max(swap_peak, current['swap_used_bytes'])
                process_peak = max(process_peak, process_memory(child.pid))
                samples += 1
                time.sleep(0.25)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait()
    final = memory()
    resources = dict(seconds=round(time.monotonic()-tick, 3), returncode=child.returncode,
        memory_method='host MemTotal-MemAvailable, 250ms sampling; includes OS and PostgreSQL',
        process_peak_rss_bytes=process_peak,
        process_memory_method='sum VmRSS of CLI and recursive children, 250ms sampling; shared pages counted per process; excludes PostgreSQL',
        peak_used_bytes=max(peak,final['used_bytes']),peak_swap_used_bytes=max(swap_peak,final['swap_used_bytes']),
        samples=samples,oom_kills=final['oom_kills']-initial['oom_kills'])
    save(log.with_suffix('.resources.json'), resources)
    if child.returncode or resources['oom_kills']:
        raise ValueError('CLI failed or OOM observed; protected logs and resources preserved')
    events = [json.loads(line) for line in log.read_text().splitlines()]
    return events, resources


def selected(events):
    keys = ('completed_batches','selected_batches','excluded_batches','window_fallback')
    rows = [{key: event[key] for key in keys} for event in events if event.get('phase') == 'snapshot_finished']
    if len(rows) != 1:
        raise ValueError('exactly one snapshot_finished event required')
    return rows[0]


def connect():
    import psycopg2
    db = psycopg2.connect(os.environ['SQL_APM_DSN'])
    with db, db.cursor() as cur:
        cur.execute("SET search_path=sql_apm,pg_catalog; SET TIME ZONE 'Asia/Shanghai'")
    return db


def digest(db, table, omitted=(), where='', params=()):
    from psycopg2 import sql
    h, count = hashlib.sha256(), 0
    with db, db.cursor(name='acceptance_digest') as cur:
        cur.itersize = 10000
        cur.execute(sql.SQL("SELECT encode(sha256(convert_to((to_jsonb(t)-%s::text[])::text,'UTF8')),'hex') h FROM {} t {} ORDER BY h").format(
            sql.Identifier(table), sql.SQL(where)), (list(omitted),) + tuple(params))
        for value, in cur:
            h.update(value.encode('ascii'))
            count += 1
    return dict(rows=count, sha256=h.hexdigest())


def statistics(db, build, identical=False):
    return {table: digest(db, table, () if identical else ('build_id','partition_id'),
                         'WHERE build_id=%s', (build,))
            for table in ('mpp_statistic','mpp_observation_statistic')}


def current_build(db, cluster):
    with db, db.cursor() as cur:
        cur.execute('SELECT build_id FROM current_version WHERE scope_id=%s', (cluster,))
        return cur.fetchone()[0]


def sufficiency(db, build):
    with db, db.cursor() as cur:
        cur.execute('''SELECT s.layer,k,count(*) FILTER (WHERE NOT (v->>'met')::boolean)
            FROM mpp_statistic s JOIN build b USING(build_id)
            JOIN config_snapshot c USING(config_id)
            CROSS JOIN LATERAL jsonb_each(mpp_statistic_sufficiency(c.statistics_version,c.thresholds,
                s.layer,s.included_count,s.active_dates,s.active_week_starts)) AS flags(k,v)
            WHERE s.build_id=%s GROUP BY s.layer,k ORDER BY s.layer,k''', (build,))
        return [list(row) for row in cur]


def build_counts(result):
    keys = ('groups','layers','timings','states','count_scopes','reasons','observations','results_saved','state')
    return {key: result['build'][key] for key in keys}


def product_files(metadata):
    return {name: digest for name, digest in metadata['files'].items()
            if product_file(name)}


def verify_baseline(metadata, baseline):
    # Document-only candidates may inherit measured product evidence (Issue #45).
    # A different commit still requires every product path and byte to match.
    if baseline.get('product_sha256') is not None:
        if baseline['product_sha256'] != product_files(metadata):
            raise ValueError('baseline product files differ')
    elif baseline['program_commit'] != metadata['commit']:
        raise ValueError('baseline program differs; product hashes required for evidence inheritance')
