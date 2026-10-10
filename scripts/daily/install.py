#!/usr/bin/env python3
"""Render the systemd timer and one-shot service of the daily run into a directory.

Nothing is installed: copy the two files to /etc/systemd/system as root (or, for a
unit of the current user, to ~/.config/systemd/user) and enable the timer. Running
this again with other values and copying again changes the time or the limits.
The connection string must not contain a password; give a PGPASSFILE instead.
"""
import argparse
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'daily/systemd'


def fail(message):
    print('ERROR: ' + message, file=sys.stderr)
    raise SystemExit(1)


def render(template, values):
    text = template.read_text()
    for key, value in values.items():
        text = text.replace('@' + key + '@', str(value))
    left = sorted(set(re.findall(r'@[A-Z_]+@', text)))
    if left:
        fail('unresolved placeholders in ' + template.name + ': ' + ', '.join(left))
    # An option that is not used leaves an empty line behind.
    return re.sub(r'\n{2,}(?=[A-Z])', '\n', text)


def plain(value, label):
    """A value written into a unit file: one line, no quoting or specifier surprises."""
    value = str(value)
    if not value or re.search(r'["\\\n\r]', value) or value != value.strip():
        fail(label + ' must be one line without quotes or backslashes')
    return value.replace('%', '%%')


def word(value, label):
    value = plain(value, label)
    if re.search(r'\s', value):
        fail(label + ' must not contain blanks')
    return value


def refuse_password(dsn):
    """Read the connection string the way libpq does, in either of its forms, and refuse any password in it.

    Nothing of the string is repeated in the message: it may be the secret itself.
    """
    try:
        from psycopg2.extensions import parse_dsn
    except ImportError:
        fail('run this with the project interpreter: the connection string is checked with the database driver')
    try:
        fields = parse_dsn(dsn)
    except Exception:
        fail('--dsn is not a valid connection string')
    if not fields:
        fail('--dsn is empty')
    if any('password' in name.lower() for name in fields):
        fail('--dsn must not contain a password; use --passfile')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True, help='生成的两个单元文件放到这个目录')
    parser.add_argument('--config', type=Path, required=True, help='每日运行的配置文件')
    parser.add_argument('--dsn', required=True, help='数据库连接串，不含密码，例如 "host=/path/to/socket port=5432 dbname=sql_apm user=sql_apm"')
    parser.add_argument('--passfile', type=Path, help='PGPASSFILE（写入单元文件）')
    parser.add_argument('--at', action='append', metavar='HH:MM', help='每天启动的时刻，服务器本地时间；可重复给出，默认 17:00')
    parser.add_argument('--max-hours', type=float, default=12, help='定时启动的运行最长多少小时，默认 12')
    parser.add_argument('--check-seconds', type=int, default=10, help='连接存活检查间隔（秒），默认 10')
    parser.add_argument('--fetch-config', type=Path, help='传输脚本的配置文件；给出后先拉取再运行')
    parser.add_argument('--schema', default='sql_apm')
    parser.add_argument('--app-root', type=Path, default=ROOT, help='程序目录')
    parser.add_argument('--python', type=Path, default=Path(sys.executable), help='项目解释器')
    parser.add_argument('--user', default=os.environ.get('USER', ''), help='运行每日运行的系统用户')
    parser.add_argument('--user-unit', action='store_true', help='生成当前用户的单元（systemctl --user），不写 User=')
    parser.add_argument('--name', default='sql-apm-daily', help='单元名，默认 sql-apm-daily')
    args = parser.parse_args()
    times = args.at or ['17:00']
    for value in times:
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value):
            fail('--at must be HH:MM in 24-hour form')
    if len(set(times)) != len(times):
        fail('--at lists the same time twice')
    if not 0 < args.max_hours <= 168:
        fail('--max-hours must be above 0 and at most 168')
    if not 0 <= args.check_seconds <= 3600:
        fail('--check-seconds must be between 0 and 3600')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', args.name):
        fail('--name must be lower-case letters, digits and hyphens')
    refuse_password(args.dsn)
    if not args.user_unit and not args.user:
        fail('--user is required for a system unit')
    for path, label in [(args.config, '--config'), (args.passfile, '--passfile'), (args.fetch_config, '--fetch-config')]:
        if path is not None and not path.is_file():
            fail(label + ' is not a file')
    app = args.app_root.resolve()
    if not (app / 'sql_apm/__main__.py').is_file():
        fail('--app-root does not hold the program')
    fetch = ''
    if args.fetch_config:
        # The leading "-": a failed pull does not keep the run from handling days already marked.
        fetch = 'ExecStartPre=-' + word(app / 'daily/fetch-logs.sh', 'the program directory') + ' --config ' + \
            word(args.fetch_config.resolve(), '--fetch-config')
    values = dict(
        NAME=args.name, APP_ROOT=word(app, '--app-root'), PYTHON=word(args.python.absolute(), '--python'),
        CONFIG=word(args.config.resolve(), '--config'), SCHEMA=word(args.schema, '--schema'), DSN=plain(args.dsn, '--dsn'),
        USER_LINE='' if args.user_unit else 'User=' + word(args.user, '--user'),
        PASSFILE_LINE='Environment="PGPASSFILE=' + plain(args.passfile.resolve(), '--passfile') + '"' if args.passfile else '',
        CHECK_SECONDS=args.check_seconds, FETCH_LINE=fetch, MAX_SECONDS=str(round(args.max_hours * 3600)) + 's',
        CALENDAR_LINES='\n'.join('OnCalendar=*-*-* ' + value + ':00' for value in times))
    args.output.mkdir(parents=True, exist_ok=True)
    for suffix in ('service', 'timer'):
        target = args.output / (args.name + '.' + suffix)
        target.write_text(render(SOURCE / ('sql-apm-daily.' + suffix + '.template'), values))
        print('OK: ' + str(target))
    print('NEXT: copy both files to the systemd unit directory, reload systemd and enable ' + args.name + '.timer')


if __name__ == '__main__':
    raise SystemExit(main())
