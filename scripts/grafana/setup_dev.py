#!/usr/bin/env python3
"""Set up Grafana, the fingerprint service and the read-only account on the development machine.

One command from an empty directory to a usable environment; running it again
leaves the same result. Everything it creates lives below --directory, which
must be an ignored location. It downloads the pinned archives (the only online
step), then uses the same installer, configuration files and unit files that an
offline installation uses.

  synthetic  a private PostgreSQL 17 instance with the committed synthetic logs
  existing   a private copy of an existing database directory (for example real
             data at structure 1.10.0, which is upgraded in place)
  stop       stop the processes started for --directory
  status     show what is running
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def run(arguments, env=None, quiet=True, check=True):
    done = subprocess.run([str(a) for a in arguments], cwd=ROOT, env=env, text=True, capture_output=True)
    if check and done.returncode:
        raise SystemExit('ERROR: ' + str(arguments[0]) + ' failed\n' + (done.stderr or done.stdout)[-2000:])
    if not quiet:
        for line in done.stdout.splitlines():
            if line.startswith(('OK', 'DOWNLOAD', 'START')):
                print(line)
    return done


def password(path, length=18):
    """Keep an existing secret; otherwise generate one. Never printed."""
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(secrets.token_urlsafe(length) + '\n')
    return path


def alive(pid_file):
    try:
        pid = int(pid_file.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def stop_process(pid_file):
    pid = alive(pid_file)
    if pid:
        os.killpg(pid, signal.SIGTERM)
        for _ in range(100):
            if not alive(pid_file):
                break
            time.sleep(0.1)
    pid_file.unlink(missing_ok=True)


def start_process(pid_file, log, arguments, env=None):
    stop_process(pid_file)
    with log.open('a') as stream:
        process = subprocess.Popen([str(a) for a in arguments], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    pid_file.write_text(str(process.pid))


def wait_http(url, seconds=90):
    for _ in range(seconds * 2):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise SystemExit('ERROR: not reachable: ' + url)


def postgres(args, directory, pgdata, admin):
    """Make the instance listen on 127.0.0.1 with password authentication for the read-only account."""
    socket = directory / 'socket'
    socket.mkdir(mode=0o700, exist_ok=True)
    (pgdata / 'pg_hba.conf').write_text('local all all trust\nhost %s %s 127.0.0.1/32 scram-sha-256\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n'
                                        % (args.database, args.readonly_role))
    pg_ctl = args.pg_bin / 'pg_ctl'
    if (pgdata / 'postmaster.pid').exists():
        run([pg_ctl, '-D', pgdata, '-m', 'fast', '-w', 'stop'], check=False)
    run([pg_ctl, '-D', pgdata, '-l', directory / 'postgres.log', '-w', 'start', '-o',
         "-p %d -k %s -c listen_addresses=127.0.0.1 -c password_encryption=scram-sha-256" % (args.pg_port, socket)])
    common = ['--host', socket, '--port', args.pg_port, '--pg-bin', args.pg_bin, '--database', args.database,
              '--schema', args.schema, '--readonly-role', args.readonly_role]
    initialize = ROOT / 'scripts/db/initialize.sh'
    run([initialize, 'bootstrap'] + common + ['--admin-user', admin, '--admin-database', 'postgres'], quiet=False)
    return socket, common, initialize


def synthetic(args, directory):
    pgdata = directory / 'pgdata'
    if not (pgdata / 'PG_VERSION').exists():
        run([args.pg_bin / 'initdb', '-D', pgdata, '-U', 'apm_admin', '--auth-local=trust', '--auth-host=scram-sha-256',
             '--encoding=UTF8', '--locale=C'])
    socket, common, initialize = postgres(args, directory, pgdata, 'apm_admin')
    run([initialize, 'all'] + common + ['--admin-user', 'apm_admin', '--admin-database', 'postgres'], quiet=False)
    dsn = 'host=%s port=%d dbname=%s user=sql_apm' % (socket, args.pg_port, args.database)
    from sql_apm.storage.ingestion import connect
    db = connect(dsn, args.schema)
    try:
        with db, db.cursor() as cur:
            cur.execute('SELECT count(*) FROM mpp_occurrence')
            loaded = cur.fetchone()[0]
    finally:
        db.close()
    if not loaded:
        from database import dashboard_data
        files = directory / 'synthetic'
        files.mkdir(exist_ok=True)
        dashboard_data.load(dsn, files, args.schema)
        print('OK: synthetic logs imported and published')
    else:
        print('OK: database already holds data; left as it is')
    return socket, 'apm_admin'


def existing(args, directory):
    pgdata = directory / 'pgdata'
    if not (pgdata / 'PG_VERSION').exists():
        source = args.pgdata.resolve()
        if (source / 'postmaster.pid').exists():
            raise SystemExit('ERROR: the source database directory must be stopped')
        run(['cp', '-a', '--reflink=auto', source, pgdata])
        print('OK: private copy of the database directory created')
    socket, common, initialize = postgres(args, directory, pgdata, args.admin_user)
    run([initialize, 'upgrade'] + common, quiet=False)
    run([initialize, 'check'] + common, quiet=False)
    return socket, args.admin_user


def setup(args):
    directory = args.directory.resolve()
    if ROOT in directory.parents and directory.relative_to(ROOT).parts[0] != 'var':
        raise SystemExit('ERROR: --directory must be an ignored location (var/ or outside the repository)')
    for name in ('private', 'run', 'downloads'):
        (directory / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    downloads = (args.downloads or directory / 'downloads').resolve()
    run([sys.executable, ROOT / 'scripts/grafana/fetch.py', '--directory', downloads], quiet=False)
    socket, admin = (synthetic if args.action == 'synthetic' else existing)(args, directory)
    private = directory / 'private'
    secrets_ = {name: password(private / (name + '-password'), 24 if name == 'db' else 18) for name in ('db', 'admin', 'viewer')}
    passfile = private / 'service-pgpass'
    run([sys.executable, ROOT / 'scripts/grafana/readonly_password.py', '--admin-dsn',
         'host=%s port=%d dbname=postgres user=%s' % (socket, args.pg_port, admin), '--role', args.readonly_role,
         '--database', args.database, '--port', args.pg_port, '--password-file', secrets_['db'], '--passfile', passfile], quiet=False)
    home = directory / 'grafana'
    installer = [sys.executable, ROOT / 'scripts/grafana/install.py']
    run(installer + ['files', '--home', home, '--files', downloads, '--db-password-file', secrets_['db'],
                     '--admin-password-file', secrets_['admin'], '--pg-port', args.pg_port, '--database', args.database,
                     '--schema', args.schema, '--readonly-role', args.readonly_role, '--listen', args.listen,
                     '--port', args.grafana_port, '--service-port', args.service_port, '--service-passfile', passfile], quiet=False)
    manifest = json.loads((ROOT / 'grafana/components.json').read_text())
    grafana = home / manifest['grafana']['directory']
    environment = dict(os.environ, PGPASSFILE=str(passfile),
                       SQL_APM_DSN='host=127.0.0.1 port=%d dbname=%s user=%s' % (args.pg_port, args.database, args.readonly_role))
    start_process(directory / 'run/fingerprint.pid', directory / 'fingerprint.log',
                  [sys.executable, '-m', 'sql_apm', 'fingerprint-service', '--port', args.service_port, '--schema', args.schema], environment)
    start_process(directory / 'run/grafana.pid', directory / 'grafana.out',
                  [grafana / 'bin/grafana', 'server', '--homepath', grafana, '--config', home / 'conf/grafana.ini'])
    wait_http('http://127.0.0.1:%d/v1/health' % args.service_port)
    wait_http('http://127.0.0.1:%d/api/health' % args.grafana_port)
    run(installer + ['accounts', '--admin-password-file', secrets_['admin'], '--viewer-password-file', secrets_['viewer'],
                     '--port', args.grafana_port], quiet=False)
    state = dict(directory=str(directory), home=str(home), grafana_port=args.grafana_port, service_port=args.service_port,
                 pg_port=args.pg_port, pg_socket=str(socket), database=args.database, schema=args.schema,
                 readonly_role=args.readonly_role, admin_password_file=str(secrets_['admin']),
                 viewer_password_file=str(secrets_['viewer']), db_password_file=str(secrets_['db']),
                 service_log=str(directory / 'fingerprint.log'), pg_bin=str(args.pg_bin), python=sys.executable, data=args.action)
    (directory / 'state.json').write_text(json.dumps(state, indent=1) + '\n')
    print('READY: http://127.0.0.1:%d/  logins: admin and viewer; their passwords are in %s (admin-password, viewer-password)'
          % (args.grafana_port, private))
    return 0


def stop(args):
    directory = args.directory.resolve()
    for name in ('grafana', 'fingerprint'):
        stop_process(directory / 'run' / (name + '.pid'))
    if (directory / 'pgdata/postmaster.pid').exists():
        run([args.pg_bin / 'pg_ctl', '-D', directory / 'pgdata', '-m', 'fast', '-w', 'stop'], check=False)
    print('OK: stopped')
    return 0


def status(args):
    directory = args.directory.resolve()
    for name in ('grafana', 'fingerprint'):
        print(name + ': ' + ('running' if alive(directory / 'run' / (name + '.pid')) else 'stopped'))
    print('postgresql: ' + ('running' if (directory / 'pgdata/postmaster.pid').exists() else 'stopped'))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['synthetic', 'existing', 'stop', 'status'])
    parser.add_argument('--directory', type=Path, required=True, help='本次环境的目录（Git 忽略的位置）')
    parser.add_argument('--downloads', type=Path, help='安装文件的下载目录，默认在 --directory 下')
    parser.add_argument('--pgdata', type=Path, help='existing：已停止的数据库目录，脚本只使用它的副本')
    parser.add_argument('--admin-user', default='postgres', help='existing：该实例的管理员账号')
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--pg-port', type=int, default=55432)
    parser.add_argument('--database', default='sql_apm')
    parser.add_argument('--schema', default='sql_apm')
    parser.add_argument('--readonly-role', default='sql_apm_ro')
    parser.add_argument('--listen', default='127.0.0.1', help='Grafana 监听地址')
    parser.add_argument('--grafana-port', type=int, default=3000)
    parser.add_argument('--service-port', type=int, default=3001)
    args = parser.parse_args()
    if args.action == 'existing' and not args.pgdata and not (args.directory / 'pgdata/PG_VERSION').exists():
        parser.error('existing requires --pgdata on the first run')
    return dict(synthetic=setup, existing=setup, stop=stop, status=status)[args.action](args)


if __name__ == '__main__':
    raise SystemExit(main())
