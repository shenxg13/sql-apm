#!/usr/bin/env python3
"""Python upgrade: serial nine-task replay, preserved PG17 and exact comparison."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from verify_package import digest, verify
from python_comparison import export, compare

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, default=str)
        stream.write('\n')


def compare_runs(old, new, output):
    left, right = [json.loads((path/'report.json').read_text()) for path in (old, new)]
    if not left['complete'] or not right['complete']:
        raise ValueError('incomplete replay')
    expected_tasks = {cluster+'-'+str(step) for cluster, total in [('119', 5), ('120', 4)] for step in range(total)}
    if set(left['tasks']) != set(right['tasks']) or set(left['tasks']) != expected_tasks:
        raise ValueError('nine-task coverage differs')
    for key in ('postgres_configuration', 'manifest_sha256', 'verification_sha256'):
        if left[key] != right[key]:
            raise ValueError('measurement input differs: '+key)
    if 'reexport' in left or 'reexport' in right:
        for key in ('comparison_sha256', 'driver_sha256', 'decision'):
            if key not in left.get('reexport', {}) or left['reexport'][key] != right.get('reexport', {}).get(key):
                raise ValueError('re-export input differs: '+key)
    result = compare(left['contents'], right['contents'])
    tasks = {}
    for key in left['tasks']:
        a, b = left['tasks'][key], right['tasks'][key]
        peak_a, peak_b = a['resources']['process_peak_rss_bytes'], b['resources']['process_peak_rss_bytes']
        if peak_a <= 0 or peak_b <= 0:
            raise ValueError('missing process memory samples')
        tasks[key] = dict(old_seconds=a['resources']['seconds'], new_seconds=b['resources']['seconds'],
                          old_stages=a['stages'], new_stages=b['stages'],
                          old_peak_rss_bytes=peak_a, new_peak_rss_bytes=peak_b,
                          memory_ratio=peak_b/peak_a,
                          old_timeouts=a['normalization_timeouts'], new_timeouts=b['normalization_timeouts'])
    old_seconds = sum(t['old_seconds'] for t in tasks.values())
    new_seconds = sum(t['new_seconds'] for t in tasks.values())
    result.update(tasks=tasks, old_seconds=old_seconds, new_seconds=new_seconds,
                  elapsed_ratio=new_seconds/old_seconds,
                  memory_policy='record_only',
                  memory_over_legacy_threshold=[key for key, t in tasks.items() if t['memory_ratio'] > 1.10],
                  elapsed_gate=new_seconds <= old_seconds*1.10,
                  timeout_gate=all(t['old_timeouts'] == t['new_timeouts'] == 0 for t in tasks.values()))
    # The confirmed contract removed the memory stop, retaining every reading.
    # Keep the revised control code identifiable separately from the unchanged
    # measurement/comparison sources recorded when each replay was started.
    result['memory_decision'] = 'https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6054199090'
    result['comparison_driver_sha256'] = digest(Path(__file__))
    if 'reexport' in left:
        result['reexport'] = dict(old=left['reexport'], new=right['reexport'])
    result['passed'] = result['passed'] and result['elapsed_gate'] and result['timeout_gate']
    save(output, result)
    print(json.dumps({k: v for k, v in result.items() if k != 'tasks'}))
    if not result['passed']:
        raise SystemExit('Issue #49 stop condition; preserve evidence and report')


def run(args):
    import psycopg2
    app, out, pg = args.app_root.resolve(), args.output.resolve(), args.pg_bin.resolve()
    metadata = verify(app, installed=True)
    if Path(sys.prefix).resolve() != app/'.venv':
        raise ValueError('run with the selected package .venv interpreter')
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.umask(0o077)
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    env.update(SQL_APM_APP_ROOT=str(app), PYTHONPATH=str(app), PGCONNECT_TIMEOUT='5')
    data, socket = out/'pgdata', out/'socket'
    socket.mkdir(mode=0o700)
    config = """listen_addresses=''
port=55473
shared_buffers='512MB'
work_mem='16MB'
maintenance_work_mem='128MB'
max_connections=30
max_wal_size='2GB'
min_wal_size='256MB'
timezone='Asia/Shanghai'
log_statement='none'
log_min_error_statement='panic'
"""
    report = dict(complete=False, python=platform.python_version(), package=metadata,
                  postgres_configuration=config,
                  manifest_sha256=digest(ROOT/'docs/reports/data/log-supplement-manifest-2026-09-28.json'),
                  verification_sha256={name: digest(Path(__file__).parent/name) for name in
                      ('verify_python_runtime.py', 'python_comparison.py', 'acceptance.py', 'rehearsal.py')},
                  load_before=Path('/proc/loadavg').read_text().split()[:3], tasks={})
    started = False

    def command(label, words, cwd=app):
        tick = time.monotonic()
        with (out/(label+'.log')).open('x') as stream:
            result = subprocess.run([str(w) for w in words], cwd=cwd, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(label+' failed; database and logs preserved')
        print(json.dumps(dict(step=label, seconds=round(time.monotonic()-tick, 3))), flush=True)

    try:
        command('prepare', [sys.executable, ROOT/'scripts/deployment/rehearsal.py', 'prepare',
                           '--logs', args.logs.resolve(), '--output', out/'config'])
        command('initdb', [pg/'initdb', '-D', data, '-U', 'apm_test_admin', '--auth-local=trust',
                          '--auth-host=reject', '--encoding=UTF8', '--locale=C'])
        with (data/'postgresql.conf').open('a') as stream:
            stream.write('\n'+config+"unix_socket_directories='"+str(socket)+"'\n")
        command('pg-start', [pg/'pg_ctl', '-D', data, '-l', out/'server.log', '-w', 'start'])
        started = True
        env['SQL_APM_DSN'] = 'host='+str(socket)+' port=55473 dbname=sql_apm user=sql_apm'
        command('initialize', [app/'scripts/db/initialize.sh', 'all', '--host', socket, '--port', '55473',
                              '--pg-bin', pg, '--admin-user', 'apm_test_admin', '--admin-database', 'postgres'])
        for cluster, total in [('119', 5), ('120', 4)]:
            for step in range(total):
                key = cluster+'-'+str(step)
                command('task-'+key, [sys.executable, ROOT/'scripts/deployment/rehearsal.py', 'run',
                        '--cluster', cluster, '--step', step, '--config', out/'config',
                        '--records', out/'tasks', '--data-root', out, '--workers', '4',
                        '--program-commit', metadata['commit']])
                report['tasks'][key] = json.loads((out/'tasks'/(key+'.json')).read_text())
                save(out/('checkpoint-'+key+'.json'), report)
        if args.reference:
            reference = json.loads((args.reference/'report.json').read_text())
            old_seconds = sum(t['resources']['seconds'] for t in reference['tasks'].values())
            new_seconds = sum(t['resources']['seconds'] for t in report['tasks'].values())
            if new_seconds > old_seconds*1.10:
                raise ValueError('Issue #49 total elapsed time exceeds 10%; stop and preserve evidence')
        with psycopg2.connect(env['SQL_APM_DSN']) as db:
            report['contents'] = export(db)
        report['load_after'] = Path('/proc/loadavg').read_text().split()[:3]
        report['complete'] = True
        save(out/'report.json', report)
        print(json.dumps(dict(complete=True, tasks=9, tables=len(report['contents']['tables']))), flush=True)
    finally:
        if started or (data/'postmaster.pid').exists():
            command('pg-stop', [pg/'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    replay = sub.add_parser('run')
    replay.add_argument('--app-root', type=Path, required=True)
    replay.add_argument('--logs', type=Path, required=True)
    replay.add_argument('--output', type=Path, required=True)
    replay.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    replay.add_argument('--reference', type=Path)
    diff = sub.add_parser('compare')
    diff.add_argument('--old', type=Path, required=True)
    diff.add_argument('--new', type=Path, required=True)
    diff.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'run':
        run(args)
    else:
        compare_runs(args.old, args.new, args.output)


if __name__ == '__main__':
    main()
