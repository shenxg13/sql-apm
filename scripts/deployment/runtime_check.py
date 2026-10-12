#!/usr/bin/env python3
"""Stop on the first failed runtime/package check; never install or compile (Python >= 3.7)."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_archive import CHECKS, PREFIX, verify_installed
from verify_package import verify


def temporary_database(pg, run):
    directory = Path(tempfile.mkdtemp(prefix='sql-apm-runtime-', dir='/tmp'))
    data, socket = directory / 'data', directory / 'socket'
    socket.mkdir(mode=0o700)
    try:
        run('initdb-temporary', [pg / 'initdb', '-D', data, '-U', 'apm_runtime_check',
                                '--auth-local=trust', '--auth-host=reject', '--encoding=UTF8', '--locale=C'])
        with (data / 'postgresql.conf').open('a') as stream:
            stream.write("\nlisten_addresses=''\nunix_socket_directories='" + str(socket) + "'\nport=55472\n")
        run('start-temporary', [pg / 'pg_ctl', '-D', data, '-l', directory / 'server.log', '-w', '-t', '30', 'start'])
        value = run('query-temporary', [pg / 'psql', '-X', '-w', '-At', '-h', socket, '-p', '55472',
                    '-U', 'apm_runtime_check', '-d', 'postgres', '-c', 'SELECT 6 * 7;'])
        if value.strip() != '42':
            raise ValueError('temporary query result mismatch')
    finally:
        if (data / 'postmaster.pid').exists():
            # If stopping fails, preserve the instance instead of deleting a running server.
            run('stop-temporary', [pg / 'pg_ctl', '-D', data, '-m', 'fast', '-w', '-t', '30', 'stop'])
        shutil.rmtree(directory)


def check(root, verification, output, binary=False):
    if output.exists():
        raise ValueError('fresh selfcheck output directory required')
    output.mkdir(parents=True)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith('PG') or key in ('PYTHONPATH', 'PYTHONHOME', 'SQL_APM_DSN', 'SQL_APM_APP_ROOT'):
            env.pop(key)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PGCONNECT_TIMEOUT'] = '5'
    completed, step = [], 'runtime-manifest' if binary else 'python'
    report = dict(passed=False, checks=completed, program_commit=None)

    def run(name, command, timeout=1800):
        command = [str(value) for value in command]
        try:
            result = subprocess.run(command, cwd=str(root / 'app'), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            (output / (name + '.log')).write_text(str(error))
            raise ValueError(name + ': executable failed or timed out') from None
        (output / (name + '.log')).write_bytes(result.stdout)
        if result.returncode:
            raise ValueError(name + ': exit ' + str(result.returncode))
        return result.stdout.decode('utf-8', errors='replace')

    try:
        if binary:
            if str(root) != PREFIX or str(root.resolve()) != PREFIX:
                raise ValueError('binary installation only supports /data/sql-apm')
            verify_installed(root)
        python = root / 'app/.venv/bin/python'
        pg = root / 'postgresql-17.10/bin'
        for step in CHECKS:
            print('CHECK: ' + step, flush=True)
            if step == 'python':
                run(step, [python, root / 'app/scripts/deployment/check_environment.py'])
                run('pip-check', [python, '-m', 'pip', 'check'])
            elif step in ('postgres', 'initdb', 'psql'):
                version = run(step, [pg / step, '--version'])
                if ' 17.10' not in version:
                    raise ValueError('expected PostgreSQL 17.10')
            elif step == 'temporary-database':
                temporary_database(pg, run)
            elif step == 'package':
                result = verify(root / 'app', installed=True)
                report['program_commit'] = result['commit']
            else:
                run(step, [python, verification / 'scripts/deployment/run_verification.py',
                           '--app-root', root / 'app', '--pg-bin', pg, step])
            completed.append(step)
        report['passed'] = True
        print('OK: every runtime and packaged acceptance entry passed')
    except (ValueError, OSError, KeyError) as error:
        report['failed_check'] = step
        report['error'] = str(error)
        print('STOP: ' + step + ': ' + str(error) + '; inspect ' + str(output) +
              '; see INSTALL.html compilation appendix. No RPM installation or compilation was attempted.', flush=True)
    finally:
        (output / 'selfcheck.json').write_text(json.dumps(report, indent=2) + '\n')
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(PREFIX))
    parser.add_argument('--verification', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binary', action='store_true', help='require the immutable exported runtime manifest')
    args = parser.parse_args()
    try:
        passed = check(args.root, args.verification or args.root / 'verification', args.output, args.binary)
    except (ValueError, OSError) as error:
        parser.exit(1, 'STOP: ' + str(error) + '; see INSTALL.html compilation appendix\n')
    raise SystemExit(0 if passed else 1)
