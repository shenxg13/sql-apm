#!/usr/bin/env python3
"""Re-export a completed, stopped Issue #49 database without replaying tasks.

The original run/report.json stays unchanged. Use a new output directory for
each database, then compare these two directories with verify_python_runtime.
Product tables are only SELECTed; temporary comparison DDL is rolled back.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import psycopg2

from python_comparison import export, REPRESENTATIVE_CONTRACT
from verify_package import digest
from verify_python_runtime import save


def reexport(run, output, pg):
    run, output, pg = run.resolve(), output.resolve(), pg.resolve()
    source = run/'report.json'
    original_digest = digest(source)
    report = json.loads(source.read_text())
    expected = {cluster+'-'+str(step) for cluster, total in [('119', 5), ('120', 4)] for step in range(total)}
    if not report['complete'] or set(report['tasks']) != expected or len(report['contents']['tables']) != 57:
        raise ValueError('complete original nine-task report required')
    data = run/'pgdata'
    if (data/'postmaster.pid').exists():
        raise ValueError('database must be stopped; no concurrent owner permitted')
    # Only the retained, private replay instance format is supported.
    config = (data/'postgresql.conf').read_text()
    if report['postgres_configuration'] not in config or "listen_addresses=''" not in report['postgres_configuration']:
        raise ValueError('private replay PostgreSQL configuration differs')
    if "unix_socket_directories='"+str(run/'socket')+"'" not in config:
        raise ValueError('private replay socket differs')
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    env['PGCONNECT_TIMEOUT'] = '5'

    def command(label, words):
        with (output/(label+'.log')).open('x') as stream:
            subprocess.run([str(word) for word in words], env=env, check=True,
                           stdout=stream, stderr=subprocess.STDOUT)

    tick, started = time.monotonic(), False
    metadata = dict(original_report_sha256=original_digest, decision=REPRESENTATIVE_CONTRACT['decision'],
        comparison_sha256=digest(Path(__file__).with_name('python_comparison.py')),
        driver_sha256=digest(Path(__file__)),
        access='SELECT on product tables; temporary comparison DDL only, rolled back; autovacuum disabled',
        replayed_tasks=0, load_before=Path('/proc/loadavg').read_text().split()[:3])
    save(output/'start.json', metadata)
    try:
        command('pg-start', [pg/'pg_ctl', '-D', data, '-l', output/'server.log',
                            '-o', '-c autovacuum=off', '-w', 'start'])
        started = True
        with psycopg2.connect(host=str(run/'socket'), port=55473, dbname='sql_apm', user='sql_apm',
                              connect_timeout=5) as db:
            db.set_session(isolation_level='REPEATABLE READ')
            contents = export(db)
        # The accepted exception cannot change any other table's digest.
        unexpected = [name for name, value in report['contents']['tables'].items()
                      if name != 'mpp_observation_group' and contents['tables'].get(name) != value]
        metadata.update(seconds=round(time.monotonic()-tick, 3),
                        load_after=Path('/proc/loadavg').read_text().split()[:3],
                        original_other_56_tables_unchanged=not unexpected)
        if unexpected or digest(source) != original_digest:
            save(output/'failure.json', dict(metadata, unexpected_tables=unexpected))
            raise ValueError('retained evidence changed; stop and preserve new export')
        report['contents'] = contents
        report['reexport'] = metadata
        save(output/'report.json', report)
        print(json.dumps(dict(complete=True, tables=57, **metadata)), flush=True)
    finally:
        if started or (data/'postmaster.pid').exists():
            command('pg-stop', [pg/'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    args = parser.parse_args()
    reexport(args.run, args.output, args.pg_bin)


if __name__ == '__main__':
    main()
