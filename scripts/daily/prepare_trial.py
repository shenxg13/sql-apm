#!/usr/bin/env python3
"""Prepare the environment for the user's look at Issue #54 (development machine only).

Works on an environment made by ``scripts/grafana/setup_dev.py existing`` from the
day-by-day replay database (clusters 119 and 120). It writes the daily-run
configuration with three empty receiving directories, adds a synthetic third cluster
121 by one daily run, and generates the synthetic log files the trial steps copy in.
Everything goes below the environment's directory; nothing real is read or changed.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts/db'), str(ROOT / 'scripts/daily')]
from ingestion.test_reader import write_csv
from verify_daily import lines
from verify_replay import SOURCE
from sql_apm.daily import inbox
from sql_apm.storage.ingestion import connect


def newest(db, cluster):
    with db, db.cursor() as cur:
        cur.execute('''SELECT max(d.declared_date) FROM batch_date d JOIN import_batch b USING(batch_id)
            WHERE b.scope_id=%s AND b.state='complete' ''', (cluster,))
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', type=Path, required=True, help='setup_dev.py existing 使用的目录')
    args = parser.parse_args()
    directory = args.directory.resolve()
    state = json.loads((directory / 'state.json').read_text())
    dsn = 'host=%s port=%d dbname=%s user=sql_apm' % (state['pg_socket'], state['pg_port'], state['database'])
    if (directory / 'daily').exists():
        raise SystemExit('ERROR: already prepared: ' + str(directory / 'daily'))
    db = connect(dsn, state['schema'])
    try:
        last = {cluster: newest(db, cluster) for cluster in ('119', '120')}
    finally:
        db.close()
    if None in last.values():
        raise SystemExit('ERROR: the environment does not hold clusters 119 and 120')
    clusters = ['119', '120', '121']
    for cluster in clusters:
        (directory / 'inbox' / cluster).mkdir(parents=True)
    work = directory / 'daily'
    work.mkdir()
    sources = {'S' + c: dict(SOURCE, cluster=c) for c in ('119', '120')}
    sources['S121'] = dict(SOURCE, cluster='121', declaration='synthetic trial cluster')
    (work / 'import.json').write_text(json.dumps(dict(version=1, clusters=clusters, sources=sources, batches={}), indent=1) + '\n')
    (work / 'training.json').write_text(json.dumps(dict(version=1, clusters=clusters, window=dict(cutoff_date='2026-01-01')), indent=1) + '\n')
    # 120's build takes about ten minutes on this data; a weekly interval keeps the hands-on steps short.
    (work / 'daily.json').write_text(json.dumps(dict(version=1, import_config='import.json', training_config='training.json',
        sources={'S' + c: dict(directory=str(directory / 'inbox' / c)) for c in clusters}, workers=4,
        build=dict(interval_days=1, clusters={'120': 7})), indent=1) + '\n')

    def put(target, cluster, day, base, suffix='_000000.csv', count=40):
        target.mkdir(parents=True, exist_ok=True)
        write_csv(target / ('gpdb-' + day.isoformat() + suffix), lines(day, count, base))

    today = date.today()
    first = {'119': last['119'] + timedelta(days=1), '120': last['120'] + timedelta(days=1), '121': today - timedelta(days=4)}
    prepared = directory / 'prepared'
    put(prepared / 'a/119', '119', first['119'], 1000)
    put(prepared / 'a/121', '121', first['121'] + timedelta(days=2), 3200)
    put(prepared / 'b/120', '120', first['120'], 2000)
    put(prepared / 'b/120', '120', first['120'], 2100, '_000000.csv.1')
    put(prepared / 'b/121', '121', first['121'] + timedelta(days=3), 3300)
    put(prepared / 'c/119', '119', first['119'] + timedelta(days=1), 1100)
    put(prepared / 'd/119', '119', first['119'] + timedelta(days=2), 1200, count=20000)
    # The synthetic cluster's first two days, by one daily run, so that the page starts with three clusters.
    for offset in (0, 1):
        day = first['121'] + timedelta(days=offset)
        put(directory / 'inbox/121', '121', day, 3000 + 100 * offset)
        (directory / 'inbox/121' / inbox.marker_name(day)).touch()
    done = subprocess.run([sys.executable, '-m', 'sql_apm', 'daily', 'run', '--config', str(work / 'daily.json')], cwd=ROOT,
                          env=dict(os.environ, SQL_APM_DSN=dsn), capture_output=True, text=True)
    if done.returncode:
        raise SystemExit('ERROR: the preparing daily run failed\n' + done.stdout[-1500:])
    print(json.dumps(dict(dsn=dsn, config=str(work / 'daily.json'),
                          dates={'119': [(first['119'] + timedelta(days=n)).isoformat() for n in range(3)],
                                 '120': [first['120'].isoformat()],
                                 '121': [(first['121'] + timedelta(days=n)).isoformat() for n in range(4)]}), indent=1))
    print('OK: prepared; follow docs/runbooks/daily-run-trial.md')


if __name__ == '__main__':
    raise SystemExit(main())
