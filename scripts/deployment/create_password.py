#!/usr/bin/env python3
"""Create a protected passfile and set a SCRAM verifier over local peer admin."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--tcp-host')
    parser.add_argument('--passfile', type=Path)
    parser.add_argument('--apply', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.apply:
        import psycopg2
        from psycopg2.extensions import encrypt_password
        password = sys.stdin.read()
        if len(password) < 32:
            raise ValueError('invalid generated credential')
        db = psycopg2.connect(host=args.socket, port=args.port, dbname='postgres', user='postgres')
        try:
            verifier = encrypt_password(password, 'sql_apm', db, algorithm='scram-sha-256')
            with db, db.cursor() as cur:
                cur.execute('ALTER ROLE sql_apm PASSWORD %s', (verifier,))
        finally:
            db.close()
        return
    if not args.passfile or not args.tcp_host:
        parser.error('--passfile and --tcp-host are required')
    path = args.passfile.resolve()
    if Path(__file__).resolve().parents[2] in path.parents:
        parser.error('passfile must be outside program directory')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        parser.error('passfile parent must have mode 0700')
    password = secrets.token_urlsafe(36)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        for host in (args.socket, args.tcp_host):
            stream.write(f'{host}:{args.port}:sql_apm:sql_apm:{password}\n')
    # Only the anonymous stdin pipe carries plaintext; subprocess args and
    # output contain none. The administrator sends only a SCRAM verifier to PG.
    result = subprocess.run(['sudo', '-n', '-u', 'postgres', sys.executable, str(Path(__file__).resolve()),
                             '--apply', '--socket', args.socket, '--port', str(args.port)],
                            input=password, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('password_setup_failed; protected passfile retained; inspect peer access')
    print('SCRAM password configured; protected passfile created; secret not displayed')


if __name__ == '__main__':
    main()
