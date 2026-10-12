#!/usr/bin/env python3
"""Set the read-only database account's password from a file the operator owns.

Only a SCRAM verifier is sent to PostgreSQL. The same password is written to a
libpq passfile for the fingerprint service; Grafana reads the operator's file.
"""
import argparse
import os
from pathlib import Path
import sys

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import encrypt_password

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deployment"))
from credentials import read_password, private_target, passfile_text, write_private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-dsn', required=True, help='管理员连接（本机 socket），不含密码')
    parser.add_argument('--role', default='sql_apm_ro')
    parser.add_argument('--database', default='sql_apm')
    parser.add_argument('--port', type=int, required=True, help='只读账号经 127.0.0.1 连接的端口')
    parser.add_argument('--password-file', type=Path, required=True, help='操作者设定的密码，单独一行，权限 0600')
    parser.add_argument('--passfile', type=Path, required=True, help='为指纹服务生成的 PGPASSFILE')
    args = parser.parse_args()
    password = read_password(args.password_file)
    target = private_target(args.passfile)
    content = passfile_text(target, ['127.0.0.1'], args.port, args.database, args.role, password)
    db = psycopg2.connect(args.admin_dsn, connect_timeout=10)
    try:
        verifier = encrypt_password(password, args.role, db, algorithm='scram-sha-256')
        with db, db.cursor() as cur:
            cur.execute(sql.SQL('ALTER ROLE {} PASSWORD %s').format(sql.Identifier(args.role)), (verifier,))
    finally:
        db.close()
    write_private(target, content)
    print('OK: SCRAM password set for ' + args.role + '; passfile written; secret not displayed')


if __name__ == '__main__':
    sys.exit(main())
