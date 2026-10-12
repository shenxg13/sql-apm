#!/usr/bin/env python3
"""Check installer password round trips against a fresh private PG17 (no real credentials)."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/deployment'))
from credentials import passfile_text, write_private
from create_password import set_password
from install_postgres import render
from verify import instance, Verification


def main():
    pg = Path('/usr/pgsql-17/bin')
    with instance(pg) as (directory, env):
        v = Verification(pg, directory, env)
        v.init()
        v.sql('CREATE ROLE postgres LOGIN SUPERUSER;', admin=True)
        socket = str(directory / 'socket')
        (directory / 'data/pg_hba.conf').write_text(
            'local all apm_test_admin trust\nlocal all postgres trust\nlocal all all scram-sha-256\n'
            'host all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n')
        subprocess.run([pg / 'pg_ctl', '-D', directory / 'data', 'reload'], check=True, capture_output=True)
        passfile = directory / 'passfile'
        checked = []
        for index, password in enumerate(['x', 'a:b', 'a\\b', 'a:\\b', ' space ']):
            for role in ['sql_apm', 'sql_apm_ro']:
                set_password(socket, 55473, role, password)
                write_private(passfile, passfile_text(passfile, [socket], 55473, 'sql_apm', role, password))
                check_env = dict(env, PGPASSFILE=str(passfile))
                result = subprocess.run([pg / 'psql', '-X', '-w', '-At', '-h', socket, '-p', '55473',
                    '-U', role, '-d', 'sql_apm', '-c', 'SELECT current_user;'], env=check_env, capture_output=True, text=True)
                assert result.returncode == 0 and result.stdout.strip() == role, 'credential round trip failed'
                if role == 'sql_apm_ro':
                    denied = subprocess.run([pg / 'psql', '-X', '-w', '-v', 'ON_ERROR_STOP=1', '-h', socket,
                        '-p', '55473', '-U', role, '-d', 'sql_apm', '-c', 'CREATE TABLE sql_apm.forbidden (id int);'],
                        env=check_env, capture_output=True)
                    assert denied.returncode != 0, 'read-only role unexpectedly wrote'
                checked.append(dict(case=index, role=role, connected=True))
        # Load exactly the generated login defaults in clean shells, without
        # touching either real account's HOME or any global service environment.
        (directory / 'postgresql-17.10').symlink_to(pg.parent)
        private = directory / 'private'
        private.mkdir(mode=0o700)
        write_private(private / 'pgpass', passfile.read_text())
        profiles = render(directory, 55473)
        for name, expected in [('sfmon-psql.sh', 'sql_apm'), ('postgres-psql.sh', 'postgres')]:
            profile = directory / name
            profile.write_text(profiles[name])
            command = '. "$1"; psql -X -w -At -c "SELECT current_user;"'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command, 'profile-check', str(profile)],
                                    env=env, capture_output=True, text=True)
            assert result.returncode == 0 and result.stdout.strip() == expected, 'login profile connection failed'
            if expected == 'sql_apm':
                result = subprocess.run(['bash', '--noprofile', '--norc', '-c',
                    '. "$1"; psql -X -w -U sql_apm_ro -At -c "SELECT current_user;"', 'profile-check', str(profile)],
                    env=env, capture_output=True, text=True)
                assert result.returncode == 0 and result.stdout.strip() == 'sql_apm_ro', 'readonly profile override failed'
        wrong = directory / 'wrong-passfile'
        write_private(wrong, passfile_text(wrong, [socket], 55473, 'sql_apm', 'sql_apm', 'wrong-fixture'))
        result = subprocess.run([pg / 'psql', '-X', '-w', '-h', socket, '-p', '55473', '-U', 'sql_apm',
                                 '-d', 'sql_apm', '-c', 'SELECT 1;'], env=dict(env, PGPASSFILE=str(wrong)), capture_output=True)
        assert result.returncode != 0, 'wrong password accepted'
        print(json.dumps(dict(passed=True, round_trips=checked, wrong_password_rejected=True,
                              readonly_writes_rejected=5, login_profiles=3, fixture_only=True)))


if __name__ == '__main__':
    main()
