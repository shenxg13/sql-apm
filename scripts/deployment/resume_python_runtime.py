#!/usr/bin/env python3
"""Resume Issue #49's preserved six-task checkpoint after the approved memory change.

This is the bounded continuation of the original run, not a retry facility.
The original checkpoint, logs and a stopped physical copy remain untouched.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from python_comparison import export
from verify_package import digest, verify
from verify_python_runtime import save

ROOT = Path(__file__).resolve().parents[2]
DECISION = 'https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6054199090'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app-root', 'run', 'reference', 'backup', 'logs'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    args = parser.parse_args()
    app, out, reference, backup, logs, pg = (
        p.resolve() for p in (args.app_root, args.run, args.reference,
                              args.backup, args.logs, args.pg_bin))
    os.umask(0o077)
    report = json.loads((out/'checkpoint-120-0.json').read_text())
    old = json.loads((reference/'report.json').read_text())
    expected = ['119-'+str(i) for i in range(5)] + ['120-0']
    if report['complete'] or list(report['tasks']) != expected or not old['complete']:
        raise ValueError('expected the original six-task stop and complete baseline')
    if Path(sys.prefix).resolve() != app/'.venv' or platform.python_version() != report['python']:
        raise ValueError('selected interpreter changed')
    if verify(app, installed=True) != report['package']:
        raise ValueError('selected package changed')
    for key in ('manifest_sha256', 'verification_sha256', 'postgres_configuration'):
        if report[key] != old[key]:
            raise ValueError('original measurement mismatch: '+key)
    for name, sha in report['verification_sha256'].items():
        if digest(Path(__file__).parent/name) != sha:
            raise ValueError('original verification source changed: '+name)
    if digest(ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json') != report['manifest_sha256']:
        raise ValueError('input manifest changed')
    for key, task in report['tasks'].items():
        if json.loads((out/'tasks'/(key+'.json')).read_text()) != task or not task['passed']:
            raise ValueError('original task evidence changed: '+key)
    for key in ('120-1', '120-2', '120-3'):
        if any((out/'tasks').glob(key+'*')) or (out/('checkpoint-'+key+'.json')).exists():
            raise ValueError('remaining task already attempted: '+key)
    data, socket = out/'pgdata', out/'socket'
    if (out/'report.json').exists() or backup.exists() or (data/'postmaster.pid').exists():
        raise ValueError('fresh continuation of a stopped database required')
    control = subprocess.check_output([pg/'pg_controldata', data], text=True,
                                     env=dict(os.environ, LC_ALL='C'))
    if 'Database cluster state:               shut down' not in control:
        raise ValueError('database was not cleanly stopped')
    config = report['postgres_configuration'] + "unix_socket_directories='"+str(socket)+"'\n"
    if not (data/'postgresql.conf').read_text().endswith(config):
        raise ValueError('database configuration changed')
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(SQL_APM_APP_ROOT=str(app), PYTHONPATH=str(app), PGCONNECT_TIMEOUT='5',
               SQL_APM_DSN='host='+str(socket)+' port=55473 dbname=sql_apm user=sql_apm')

    def command(label, words):
        tick = time.monotonic()
        with (out/(label+'.log')).open('x') as stream:
            result = subprocess.run([str(w) for w in words], cwd=app, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        print(json.dumps(dict(step=label, seconds=round(time.monotonic()-tick, 3),
                              returncode=result.returncode)), flush=True)
        if result.returncode:
            raise ValueError(label+' failed; preserve evidence and stop')

    # Copy the entire stopped run before any database mutation, including the
    # original failed attempt's logs and six checkpoints. Never overwrite it.
    command('resume-backup', ['cp', '-a', '--reflink=auto', out, backup])
    if digest(backup/'pgdata/global/pg_control') != digest(data/'global/pg_control'):
        raise ValueError('backup control file differs')
    command('resume-prepare', [sys.executable, ROOT/'scripts/deployment/rehearsal.py',
                              'prepare', '--logs', logs, '--output', out/'resume-config'])
    before = {p.name: digest(p) for p in (out/'config').iterdir()}
    after = {p.name: digest(p) for p in (out/'resume-config').iterdir()}
    if before != after:
        raise ValueError('rechecked inputs or configuration changed')
    report['continuation'] = dict(
        decision=DECISION, driver_sha256=digest(Path(__file__)),
        resumed_after='120-0', remaining_tasks=['120-1', '120-2', '120-3'],
        started_at=datetime.now(timezone.utc).isoformat(),
        load_before=Path('/proc/loadavg').read_text().split()[:3],
        original_checkpoint_sha256=digest(out/'checkpoint-120-0.json'),
        backup_control_sha256=digest(backup/'pgdata/global/pg_control'),
        backup_clean_shutdown=True, inputs_rechecked=True,
        memory_policy='record_only', interruption='PG restarted; caches not controlled; '
        'sum original six and remaining three CLI times, excluding the interruption')
    save(out/'resume-start.json', report['continuation'])
    started = False
    try:
        command('resume-pg-start', [pg/'pg_ctl', '-D', data, '-l', out/'resume-server.log', '-w', 'start'])
        started = True
        for step in (1, 2, 3):
            key = '120-'+str(step)
            command('task-'+key, [sys.executable, ROOT/'scripts/deployment/rehearsal.py', 'run',
                    '--cluster', '120', '--step', step, '--config', out/'config',
                    '--records', out/'tasks', '--data-root', out, '--workers', '4',
                    '--program-commit', report['package']['commit']])
            task = json.loads((out/'tasks'/(key+'.json')).read_text())
            report['tasks'][key] = task
            save(out/('checkpoint-'+key+'.json'), report)
            if not task['passed'] or task['normalization_timeouts'] != 0:
                raise ValueError('business or timeout check failed; stop')
        old_seconds = sum(t['resources']['seconds'] for t in old['tasks'].values())
        new_seconds = sum(t['resources']['seconds'] for t in report['tasks'].values())
        if new_seconds > old_seconds*1.10:
            raise ValueError('total elapsed time exceeds 10%; stop and preserve evidence')
        import psycopg2
        with psycopg2.connect(env['SQL_APM_DSN']) as db:
            report['contents'] = export(db)
        report['load_after'] = Path('/proc/loadavg').read_text().split()[:3]
        report['complete'] = True
        save(out/'report.json', report)
        print(json.dumps(dict(complete=True, tasks=9, tables=len(report['contents']['tables']),
                              old_seconds=old_seconds, new_seconds=new_seconds)), flush=True)
    finally:
        if started or (data/'postmaster.pid').exists():
            command('resume-pg-stop', [pg/'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])


if __name__ == '__main__':
    main()
