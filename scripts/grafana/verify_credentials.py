#!/usr/bin/env python3
"""Exercise installation and password rotation with pinned Grafana and a private PG17.

Uses only synthetic credentials, a fresh output directory and random loopback
ports. Never connects to an existing database or changes installed services.
"""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--files', type=Path, required=True, help='Pinned Grafana and plugin archives')
    parser.add_argument('--output', type=Path, required=True, help='Fresh private evidence directory')
    args = parser.parse_args()
    root, pg, output = args.app_root.resolve(), args.pg_bin.resolve(), args.output.resolve()
    output.mkdir(mode=0o700)
    sys.path.insert(0, str(root / 'scripts/deployment'))
    from credentials import passfile_text, write_private
    from create_password import set_password
    spec = importlib.util.spec_from_file_location('grafana_installer', root / 'scripts/grafana/install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'GF_', 'SQL_APM_'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    pgport, gfport, fpport = free_port(), free_port(), free_port()
    while len({pgport, gfport, fpport}) != 3:
        pgport, gfport, fpport = free_port(), free_port(), free_port()
    data, sock, home = output / 'pgdata', output / 'socket', output / 'grafana'
    sock.mkdir(mode=0o700)
    admin_file, viewer_file = output / 'admin', output / 'viewer'
    database_file, passfile = output / 'readonly-password', output / 'pgpass'
    service_passfile = output / 'readonly.pgpass'
    children, handles, results = [], [], []
    all_passwords = []
    report = dict(passed=False, synthetic_only=True, cases=results)

    def run(label, command, input_text=None, extra_env=None, expected=0):
        result = subprocess.run([str(v) for v in command], input=input_text, text=True,
                                capture_output=True, env=dict(env, **(extra_env or {})), timeout=180)
        (output / (label + '.log')).write_text(result.stdout + result.stderr)
        if expected == 0 and result.returncode != 0:
            raise RuntimeError(label + ' failed; inspect private evidence')
        if expected != 0 and result.returncode == 0:
            raise RuntimeError(label + ' unexpectedly succeeded')
        return result

    def http(port, path, user=None, password=None, body=None, method=None):
        headers = {'Content-Type': 'application/json'}
        if user:
            headers['Authorization'] = 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()
        request = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
            headers=headers, data=None if body is None else json.dumps(body).encode(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, None

    def spawn(command, label, extra_env=None):
        handle = (output / (label + '.log')).open('wb')
        handles.append(handle)
        child = subprocess.Popen([str(v) for v in command], cwd=root, env=dict(env, **(extra_env or {})),
                                 stdout=handle, stderr=subprocess.STDOUT)
        children.append(child)
        return child

    def stop(child):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)

    def ready(child, port, path):
        for _ in range(150):
            if child.poll() is not None:
                raise RuntimeError('fixture exited before readiness; inspect private evidence')
            try:
                if http(port, path)[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(.2)
        raise RuntimeError('fixture readiness timed out')

    def psql(label, role, file, sql, expected=0):
        return run(label, [pg / 'psql', '-X', '-w', '-At', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1',
            '-p', pgport, '-U', role, '-d', 'sql_apm'], sql, {'PGPASSFILE': str(file)}, expected)

    # Real installation with both leading/trailing spaces and tabs, then rotations.
    # Provider-looking text must remain literal, too: a whitespace fix must not
    # introduce an environment/file interpolation bug in the same consumer chain.
    fixtures = [('space-tab', ' \tfixture-{}:\\ 	 '), ('tab', '\tfixture-{}\t'),
                ('literal', ' ${{NO_SUCH_VAR}}:$__file{{/never-read}}:%(x)s:{{}}:\\"\' '),
                ('short', '{}'), ('blank', ' \t \t')]
    previous_admin = None
    gf = None
    try:
        run('initdb', [pg / 'initdb', '-D', data, '-U', 'postgres', '--auth-local=trust',
                      '--auth-host=scram-sha-256', '--encoding=UTF8', '--locale=C'])
        with (data / 'postgresql.conf').open('a') as stream:
            stream.write("\nlisten_addresses='127.0.0.1'\nport=" + str(pgport) +
                "\nunix_socket_directories='" + str(sock) + "'\nlog_statement='none'\nlog_min_error_statement='panic'\n")
        run('start-pg', [pg / 'pg_ctl', '-D', data, '-l', output / 'postgres.log', '-w', 'start'])
        run('initialize', [root / 'scripts/db/initialize.sh', 'all', '--host', sock, '--port', pgport,
                          '--pg-bin', pg, '--admin-user', 'postgres', '--admin-database', 'postgres'])
        for index, (label, pattern) in enumerate(fixtures):
            stage = str(index) + '-' + label
            values = [pattern.format(role) for role in ('admin', 'viewer', 'project', 'readonly')]
            if label == 'short':
                values = ['adm!', 'view', 'x', ':']
            admin, viewer, project, readonly = values
            all_passwords.extend(values)
            for role, password in [('sql_apm', project), ('sql_apm_ro', readonly)]:
                set_password(str(sock), pgport, role, password)
                write_private(passfile, passfile_text(passfile, ['127.0.0.1'], pgport, 'sql_apm', role, password))
            write_private(service_passfile, passfile_text(service_passfile, ['127.0.0.1'], pgport, 'sql_apm', 'sql_apm_ro', readonly))
            for file, password in [(admin_file, admin), (viewer_file, viewer), (database_file, readonly)]:
                write_private(file, password)
            if gf is None:
                run('install-files', [sys.executable, root / 'scripts/grafana/install.py', 'files',
                    '--home', home, '--files', args.files.resolve(), '--pg-port', pgport, '--port', gfport,
                    '--listen', '127.0.0.1', '--service-port', fpport, '--service-passfile', service_passfile,
                    '--app-root', root, '--python', sys.executable, '--admin-password-file', admin_file,
                    '--db-password-file', database_file])
            else:
                api = installer.Api(gfport, 'admin', previous_admin)
                _, user = api.call('GET', '/api/users/lookup?loginOrEmail=viewer')
                api.call('PUT', '/api/admin/users/' + str(user['id']) + '/password', {'password': viewer})
                api.call('PUT', '/api/user/password', {'oldPassword': previous_admin, 'newPassword': admin, 'confirmNew': admin})
                stop(gf)
            command = next(line.removeprefix('ExecStart=') for line in
                (home / 'systemd/sql-apm-grafana.service').read_text().splitlines() if line.startswith('ExecStart='))
            gf = spawn(shlex.split(command), stage + '-grafana')
            ready(gf, gfport, '/api/health')
            if index == 0:
                run('accounts', [sys.executable, root / 'scripts/grafana/install.py', 'accounts', '--port', gfport,
                                '--admin-password-file', admin_file, '--viewer-password-file', viewer_file])
            for user, password in [('admin', admin), ('viewer', viewer)]:
                if http(gfport, '/api/user', user, password)[0] != 200:
                    raise RuntimeError(stage + ' exact Grafana authentication failed')
                if http(gfport, '/login', body={'user': user, 'password': password})[0] != 200:
                    raise RuntimeError(stage + ' Grafana form login failed')
                if password != password.strip() and http(gfport, '/api/user', user, password.strip())[0] == 200:
                    raise RuntimeError(stage + ' Grafana accepted trimmed password')
            status, health = http(gfport, '/api/datasources/uid/sql-apm-pg/health', 'admin', admin)
            if status != 200 or health.get('status') != 'OK':
                raise RuntimeError(stage + ' datasource authentication failed')
            for role in ('sql_apm', 'sql_apm_ro'):
                result = psql(stage + '-' + role, role, passfile, 'SELECT current_user;')
                if result.stdout.strip() != role:
                    raise RuntimeError(stage + ' unexpected database role')
            psql(stage + '-readonly-denied', 'sql_apm_ro', service_passfile,
                  'BEGIN; CREATE TABLE sql_apm.must_not_create(id int); ROLLBACK;', expected=1)
            if readonly != readonly.strip():
                wrong = output / 'wrong.pgpass'
                write_private(wrong, passfile_text(wrong, ['127.0.0.1'], pgport, 'sql_apm', 'sql_apm_ro', readonly.strip()))
                psql(stage + '-trimmed-denied', 'sql_apm_ro', wrong, 'SELECT 42;', expected=1)
            fp = spawn([sys.executable, '-m', 'sql_apm', 'fingerprint-service', '--port', fpport], stage + '-fingerprint',
                       {'SQL_APM_DSN': 'host=127.0.0.1 port=' + str(pgport) + ' dbname=sql_apm user=sql_apm_ro',
                        'PGPASSFILE': str(service_passfile)})
            ready(fp, fpport, '/v1/health')
            status, result = http(fpport, '/v1/exact', body={'sql_b64': base64.b64encode(b'SELECT 42;').decode()})
            stop(fp)
            if status != 200 or result.get('reason') == 'database_unavailable':
                raise RuntimeError(stage + ' fingerprint authentication failed')
            if any(file.stat().st_mode & 0o077 for file in [admin_file, viewer_file, database_file, passfile, service_passfile]):
                raise RuntimeError(stage + ' credential permissions changed')
            results.append(dict(case=label,installed=index == 0,rotated=index > 0,
                grafana_logins=True,form_logins=True,trimmed_grafana_rejected=True,datasource=True,psql_roles=2,
                readonly_write_rejected=True,fingerprint=True,credential_mode_0600=True))
            print('PASS: ' + stage, flush=True)
            previous_admin = admin
        api = installer.Api(gfport, 'admin', previous_admin)
        for length in (1, 2, 3):
            status, _ = api.call('POST', '/api/admin/users', {'login': 'too-short-' + str(length),
                                'password': 'x' * length}, expected=(400,))
            if status != 400:
                raise RuntimeError('Grafana upstream minimum length changed')
        report['upstream_short_password_rejected'] = True
        rendered = list((home / 'conf').rglob('*')) + list((home / 'systemd').glob('*'))
        for path in rendered:
            if path.is_file():
                content = path.read_text(errors='replace')
                if any(value.strip() and len(value) > 8 and value in content for value in all_passwords):
                    raise RuntimeError('synthetic credential appeared in rendered configuration')
        report['no_password_in_rendered_configuration'] = True
        report['passed'] = True
    finally:
        for child in reversed(children):
            stop(child)
        for handle in handles:
            handle.close()
        if (data / 'postmaster.pid').exists():
            run('stop-pg', [pg / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])
        report['all_processes_stopped'] = all(child.poll() is not None for child in children) and not (data / 'postmaster.pid').exists()
        for path in output.rglob('*.log'):
            content = path.read_text(errors='replace')
            if any(value.strip() and len(value) > 8 and value in content for value in all_passwords):
                report['passed'] = False
                raise RuntimeError('synthetic credential appeared in a private log')
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: protected credentials, fixed upstream limit, stopped fixtures')


if __name__ == '__main__':
    main()
