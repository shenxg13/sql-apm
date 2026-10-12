#!/usr/bin/env python3
"""Prompt twice, set a database SCRAM password via local peer, and write passfiles."""
import argparse
from pathlib import Path
import subprocess
import sys

from credentials import (passfile_text, private_target, prompt_password,
                         validate_password, write_private)


def set_password(socket, port, role, password):
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import encrypt_password
    db = psycopg2.connect(host=socket, port=port, dbname='postgres', user='postgres', connect_timeout=10)
    try:
        verifier = encrypt_password(validate_password(password), role, db, algorithm='scram-sha-256')
        with db, db.cursor() as cur:
            cur.execute(sql.SQL('ALTER ROLE {} PASSWORD %s').format(sql.Identifier(role)), (verifier,))
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--tcp-host')
    parser.add_argument('--role', choices=['sql_apm', 'sql_apm_ro'], default='sql_apm')
    parser.add_argument('--passfile', type=Path)
    parser.add_argument('--service-passfile', type=Path, help='只读账号的独立指纹服务连接文件')
    parser.add_argument('--password-file', type=Path, help='保存供 Grafana 使用的只读账号原始密码')
    parser.add_argument('--apply', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.apply:
        try:
            set_password(args.socket, args.port, args.role, sys.stdin.read())
        except Exception:
            parser.exit(1, 'password_setup_failed; check local peer access\n')
        return
    if not args.passfile or not args.tcp_host:
        parser.error('--passfile and --tcp-host are required')
    if args.role != 'sql_apm_ro' and (args.service_passfile or args.password_file):
        parser.error('service/password files are only for sql_apm_ro')
    paths = [p for p in (args.passfile, args.service_passfile, args.password_file) if p]
    root = Path(__file__).resolve().parents[2]
    if len({p.resolve() for p in paths}) != len(paths) or any(root == p.resolve() or root in p.resolve().parents for p in paths):
        parser.error('credential files must be distinct and outside the program directory')
    for path in paths:
        private_target(path)
    password = prompt_password(args.role)
    hosts = [args.socket, '127.0.0.1', args.tcp_host]
    outputs = [(args.passfile, passfile_text(args.passfile, hosts, args.port, 'sql_apm', args.role, password))]
    if args.service_passfile:
        outputs.append((args.service_passfile, passfile_text(args.service_passfile, ['127.0.0.1'],
                        args.port, 'sql_apm', args.role, password)))
    if args.password_file:
        outputs.append((args.password_file, password))
    result = subprocess.run(['sudo', '-n', '-u', 'postgres', sys.executable, str(Path(__file__).resolve()),
                             '--apply', '--socket', args.socket, '--port', str(args.port), '--role', args.role],
                            input=password, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('password_setup_failed; existing credential files unchanged; inspect peer access')
    try:
        for path, value in outputs:
            write_private(path, value)
    except OSError:
        raise RuntimeError('database password changed but file update failed; repair permissions and rerun with the same password') from None
    print('OK: SCRAM password and protected files configured; secret not displayed')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        sys.exit(str(error))
