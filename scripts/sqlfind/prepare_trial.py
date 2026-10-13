#!/usr/bin/env python3
"""Prepare/start/stop a private development trial from a few complete sample files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def run(argv):
    done = subprocess.run([str(x) for x in argv], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError('trial preparation command failed: ' + str(argv[0]))


def prepare(directory, pg_bin, sources):
    if directory.exists():
        raise ValueError('trial directory must be new')
    if not sources or len(sources) > 4:
        raise ValueError('choose one to four complete log files')
    if not directory.is_relative_to(ROOT / 'var'):
        raise ValueError('trial data must stay under the ignored var directory')
    for source in sources:
        if not source.is_file() or not re.fullmatch(r'gpdb-\d{4}-\d{2}-\d{2}_\d{6}\.csv(?:\.\d+)?', source.name):
            raise ValueError('expected a complete local MPP sample log')
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)
    sock, data = directory / 'socket', directory / 'pgdata'
    sock.mkdir(mode=0o700)
    run([pg_bin / 'initdb', '-D', data, '-U', 'apm_trial_admin', '--auth-local=trust',
         '--auth-host=reject', '--encoding=UTF8', '--locale=C'])
    with (data / 'postgresql.conf').open('a') as stream:
        stream.write("\nlisten_addresses=''\nport=55462\nunix_socket_directories='" + str(sock) + "'\n"
                     "log_statement='none'\nlog_min_error_statement='panic'\n")
    run([pg_bin / 'pg_ctl', '-D', data, '-l', directory / 'postgres.log', '-w', 'start'])
    try:
        run([ROOT / 'scripts/db/initialize.sh', 'all', '--host', sock, '--port', '55462', '--pg-bin', pg_bin,
             '--admin-user', 'apm_trial_admin', '--admin-database', 'postgres'])
        from sql_apm.baseline.workflow import run as build
        from sql_apm.ingestion.config import load_config
        from sql_apm.training.config import validate
        from sql_apm.storage.ingestion import connect
        import psycopg2
        from psycopg2.extensions import encrypt_password
        dsn = 'host=' + str(sock) + ' port=55462 dbname=sql_apm user=sql_apm'
        document = dict(version=1, clusters=[], sources={}, batches={})
        provenance, builds = [], []
        for number, source in enumerate(sources, 1):
            scope, source_id, batch = 'C' + str(number), 'S' + str(number), 'B' + str(number)
            folder = directory / 'samples' / scope
            folder.mkdir(parents=True)
            target = folder / source.name
            shutil.copyfile(source, target)
            checksum = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            original, copied = checksum(source), checksum(target)
            if original != copied:
                raise ValueError('sample copy changed')
            day = source.name[5:15]
            provenance.append(dict(source=str(source), copy=str(target), sha256=original, bytes=target.stat().st_size))
            document['clusters'].append(scope)
            document['sources'][source_id] = dict(cluster=scope, build='HashData Warehouse 3.13.13', timezone='UTC+08:00',
                declaration='development trial: complete copied file from the existing local MPP samples; not a full day or production baseline')
            document['batches'][batch] = dict(source=source_id, files_confirmed_complete=True, dates=[day],
                files=[dict(path=str(target), closed_and_copied=True)])
        config = directory / 'import.json'
        config.write_text(json.dumps(document))
        for number, source in enumerate(sources, 1):
            scope, source_id, batch = 'C' + str(number), 'S' + str(number), 'B' + str(number)
            training = dict(version=1, clusters=document['clusters'], window=dict(cutoff_date=source.name[5:15], days=7))
            result = build(dsn, 'sql_apm', validate(training, scope), load_config(config, source_id, batch), workers=1)
            if result['state'] != 'succeeded':
                raise ValueError('trial build did not succeed')
            builds.append(dict(cluster=scope, state=result['state'], build_id=result['build']['build_id']))
        password = secrets.token_urlsafe(24)
        admin = psycopg2.connect('host=' + str(sock) + ' port=55462 dbname=sql_apm user=apm_trial_admin')
        try:
            verifier = encrypt_password(password, 'sql_apm_ro', admin, algorithm='scram-sha-256')
            with admin, admin.cursor() as cur:
                cur.execute('ALTER ROLE sql_apm_ro PASSWORD %s', (verifier,))
        finally:
            admin.close()
        passfile = directory / 'pgpass'
        descriptor = os.open(passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(str(sock).replace('\\', '\\\\').replace(':', '\\:') + ':55462:sql_apm:sql_apm_ro:' + password + '\n')
        (data / 'pg_hba.conf').write_text('local all sql_apm_ro scram-sha-256\nlocal all all trust\nhost all all 0.0.0.0/0 reject\n')
        run([pg_bin / 'pg_ctl', '-D', data, '-w', 'reload'])
        reader = connect('host=' + str(sock) + ' port=55462 dbname=sql_apm user=sql_apm_ro passfile=' + str(passfile), 'sql_apm')
        try:
            with reader, reader.cursor() as cur:
                cur.execute('SELECT current_user,(SELECT count(*) FROM sql_apm.mpp_occurrence),'
                            '(SELECT count(*) FROM sql_apm.mpp_sql_text)')
                role, occurrences, texts = cur.fetchone()
                assert role == 'sql_apm_ro'
        finally:
            reader.close()
        (directory / 'provenance.json').write_text(json.dumps(dict(files=provenance, builds=builds,
            occurrences=occurrences, texts=texts), indent=2) + '\n')
        print('Trial ready: %d complete sample files; %d records; %d original texts.' % (len(sources), occurrences, texts))
    except BaseException:
        run([pg_bin / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'start', 'stop', 'status'])
    parser.add_argument('--directory', type=Path, default=ROOT / 'var/issue62/trial')
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('--sample', type=Path, action='append', default=[])
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(ROOT / 'var'):
        parser.error('directory must be under the ignored var directory')
    if args.action == 'prepare':
        prepare(directory, args.pg_bin, [p.resolve() for p in args.sample])
    else:
        command = [args.pg_bin / 'pg_ctl', '-D', directory / 'pgdata', args.action]
        if args.action == 'start':
            command += ['-l', directory / 'postgres.log', '-w']
        elif args.action == 'stop':
            command += ['-m', 'fast', '-w']
        run(command)
        print('Trial ' + args.action + ': OK')
