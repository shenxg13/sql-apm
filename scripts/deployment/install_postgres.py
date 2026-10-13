#!/usr/bin/env python3
"""Render the PostgreSQL system service and login-only psql environments; install nothing."""
import argparse
from pathlib import Path
import re
import shlex


def word(value):
    value = str(value)
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value):
        raise ValueError('installation paths must be absolute without spaces or systemd specifiers')
    return value


def render(root, port):
    root = word(root)
    if not 1 <= port <= 65535:
        raise ValueError('invalid database port')
    pg = root + '/postgresql-17.10/bin'
    unit = f'''# Install as /etc/systemd/system/sql-apm-postgresql.service.
[Unit]
Description=SQL APM PostgreSQL 17
After=local-fs.target network.target
RequiresMountsFor={root}

[Service]
Type=forking
User=postgres
Group=postgres
PIDFile={root}/pgdata/postmaster.pid
ExecStart={pg}/pg_ctl -D {root}/pgdata -l {root}/pgdata/server.log -w -t 120 start
ExecStop={pg}/pg_ctl -D {root}/pgdata -m fast -w -t 120 stop
ExecReload={pg}/pg_ctl -D {root}/pgdata reload
TimeoutStartSec=130
TimeoutStopSec=130
KillMode=mixed

[Install]
WantedBy=multi-user.target
'''
    profiles = {}
    for user, database in [('sql_apm', 'sql_apm'), ('postgres', 'postgres')]:
        values = dict(PGHOST=root + '/socket', PGPORT=str(port), PGDATABASE=database, PGUSER=user)
        if user == 'sql_apm':
            values['PGPASSFILE'] = root + '/private/pgpass'
        text = '# Source only from this account\'s login profile; services have explicit connections.\n'
        text += 'export PATH=' + shlex.quote(pg) + ':"$PATH"\n'
        text += 'unset PGPASSWORD PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR\n'
        if user == 'postgres':
            text += 'unset PGPASSFILE\n'
        text += ''.join('export ' + key + '=' + shlex.quote(value) + '\n' for key, value in values.items())
        profiles[user] = text
    return {'sql-apm-postgresql.service': unit, 'sfmon-psql.sh': profiles['sql_apm'],
            'postgres-psql.sh': profiles['postgres']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/data/sql-apm'))
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    files = render(args.root, args.port)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (args.output / name).write_text(text)
    print('OK: service and two login profiles rendered; install using the production manual')
