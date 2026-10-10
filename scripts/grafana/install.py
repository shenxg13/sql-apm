#!/usr/bin/env python3
"""Install the pinned Grafana, its three plugins and the SQL APM configuration.

``files`` works offline: it verifies the archives against grafana/components.json,
unpacks them and renders the configuration, data sources, packaged dashboards and
systemd unit files below --home. ``accounts`` talks to the running Grafana on the
loopback address: it creates the viewer login and the "用户自定义" folder once and
sets the organisation defaults. Both steps can be repeated.

Passwords are read from files the operator owns (mode 0600); they never appear in
arguments, rendered files or output.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'grafana'
FOLDER_UID, CUSTOM_UID, HOME_UID = 'mpp', 'mpp-custom', 'mpp-search'


def fail(message):
    print('ERROR: ' + message, file=sys.stderr)
    raise SystemExit(1)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def secret(path, label):
    """A password file must be private to its owner and hold one non-empty line."""
    path = Path(path).resolve()
    if not path.is_file():
        fail(label + ' password file is missing')
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        fail(label + ' password file must not be readable by group or others')
    value = path.read_text().rstrip('\n')
    if not value or '\n' in value:
        fail(label + ' password file must hold exactly one non-empty line')
    return path, value


def render(template, values):
    text = template.read_text()
    for key, value in values.items():
        text = text.replace('@' + key + '@', str(value))
    left = sorted(set(re.findall(r'@[A-Z_]+@', text)))
    if left:
        fail('unresolved placeholders in ' + template.name + ': ' + ', '.join(left))
    return text


def safe_members(names, target):
    """Reject archive entries that would leave the target directory."""
    root = target.resolve()
    for name in names:
        if not (root / name).resolve().is_relative_to(root):
            fail('archive entry leaves the target directory')


def files(args):
    manifest = json.loads((SOURCE / 'components.json').read_text())
    home = args.home.resolve()
    if ROOT in home.parents and 'var' not in home.relative_to(ROOT).parts[:1]:
        fail('--home must be outside the program directory')
    items = [manifest['grafana']] + manifest['plugins']
    for item in items:
        path = args.files / item['file']
        if not path.is_file():
            fail('missing archive: ' + item['file'])
        if digest(path) != item['sha256']:
            fail('digest mismatch, refusing to install: ' + item['file'])
    print('OK: ' + str(len(items)) + ' archives match grafana/components.json')
    _, _ = secret(args.admin_password_file, 'Grafana admin')
    _, _ = secret(args.db_password_file, 'read-only database')
    for name in ('conf/provisioning/datasources', 'conf/provisioning/dashboards', 'conf/provisioning/plugins',
                 'conf/provisioning/alerting', 'dashboards/mpp', 'plugins', 'data', 'logs', 'systemd'):
        (home / name).mkdir(parents=True, exist_ok=True)
    os.chmod(home / 'data', 0o700)
    grafana = home / manifest['grafana']['directory']
    if not (grafana / 'bin/grafana').is_file():
        staging = home / (manifest['grafana']['directory'] + '.unpacking')
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        with tarfile.open(args.files / manifest['grafana']['file']) as archive:
            safe_members(archive.getnames(), staging)
            archive.extractall(staging, filter='tar')
        inner = [entry for entry in staging.iterdir() if (entry / 'bin/grafana').is_file()]
        if len(inner) != 1:
            fail('unexpected Grafana archive layout')
        inner[0].rename(grafana)
        shutil.rmtree(staging)
    for plugin in manifest['plugins']:
        target = home / 'plugins' / plugin['id']
        # Nothing may be added inside a plugin directory: Grafana verifies the
        # signed file list. The plugin's own plugin.json tells its version.
        try:
            if json.loads((target / 'plugin.json').read_text())['info']['version'] == plugin['version']:
                continue
        except (OSError, ValueError, KeyError):
            pass
        shutil.rmtree(target, ignore_errors=True)
        with zipfile.ZipFile(args.files / plugin['file']) as archive:
            safe_members(archive.namelist(), home / 'plugins')
            if not all(name.startswith(plugin['id'] + '/') for name in archive.namelist()):
                fail('unexpected plugin archive layout: ' + plugin['id'])
            for member in archive.infolist():
                extracted = archive.extract(member, home / 'plugins')
                mode = member.external_attr >> 16
                if mode:
                    os.chmod(extracted, stat.S_IMODE(mode))
    values = dict(HOME=home, GRAFANA=grafana, LISTEN=args.listen, PORT=args.port,
                  ADMIN_PASSWORD_FILE=Path(args.admin_password_file).resolve(),
                  DB_PASSWORD_FILE=Path(args.db_password_file).resolve(),
                  PG_PORT=args.pg_port, DATABASE=args.database, SCHEMA=args.schema, READONLY_ROLE=args.readonly_role,
                  SERVICE_PORT=args.service_port, APP_ROOT=args.app_root.resolve(), PYTHON=args.python.absolute(),
                  PGPASSFILE=Path(args.service_passfile).resolve() if args.service_passfile else '', USER=args.user)
    rendered = {'conf/grafana.ini': 'grafana.ini.template',
                'conf/provisioning/datasources/sql-apm.yaml': 'provisioning/datasources.yaml.template',
                'conf/provisioning/dashboards/sql-apm.yaml': 'provisioning/dashboards.yaml.template',
                'systemd/sql-apm-grafana.service': 'systemd/sql-apm-grafana.service.template',
                'systemd/sql-apm-fingerprint.service': 'systemd/sql-apm-fingerprint.service.template'}
    for target, template in rendered.items():
        (home / target).write_text(render(SOURCE / template, values))
    # Only the packaged dashboards are replaced; nothing else below --home is removed.
    packaged = sorted((SOURCE / 'dashboards').glob('mpp-*.json'))
    if len(packaged) != 4:
        fail('expected exactly four packaged dashboards')
    for stale in (home / 'dashboards/mpp').glob('*.json'):
        stale.unlink()
    for dashboard in packaged:
        shutil.copyfile(dashboard, home / 'dashboards/mpp' / dashboard.name)
    print('OK: Grafana ' + manifest['grafana']['version'] + ', plugins, configuration and four packaged dashboards in ' + str(home))
    print('START: ' + str(grafana / 'bin/grafana') + ' server --homepath ' + str(grafana) + ' --config ' + str(home / 'conf/grafana.ini'))


class Api:
    def __init__(self, port, user, password):
        self.base = 'http://127.0.0.1:' + str(port)
        self.authorization = 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()

    def call(self, method, path, body=None, expected=(200,)):
        request = urllib.request.Request(self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': self.authorization, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read() or b'null')
        except urllib.error.HTTPError as error:
            if error.code in expected:
                return error.code, None
            fail('Grafana API ' + method + ' ' + path + ' returned ' + str(error.code))
        except OSError:
            fail('Grafana is not reachable on 127.0.0.1:' + self.base.rsplit(':', 1)[1])


def accounts(args):
    _, admin = secret(args.admin_password_file, 'Grafana admin')
    _, viewer = secret(args.viewer_password_file, 'Grafana viewer')
    if admin == 'admin':
        fail('the default admin password must not be kept')
    api = Api(args.port, 'admin', admin)
    status, _ = api.call('GET', '/api/users/lookup?loginOrEmail=' + args.viewer_login, expected=(200, 404))
    if status == 404:
        api.call('POST', '/api/admin/users', dict(name='查看账号', login=args.viewer_login, password=viewer))
        print('OK: viewer login created: ' + args.viewer_login)
    else:
        print('OK: viewer login already exists: ' + args.viewer_login)
    _, user = api.call('GET', '/api/users/lookup?loginOrEmail=' + args.viewer_login)
    _, memberships = api.call('GET', '/api/users/' + str(user['id']) + '/orgs')
    if [m['role'] for m in memberships if m['orgId'] == 1] != ['Viewer']:
        api.call('PATCH', '/api/orgs/1/users/' + str(user['id']), dict(role='Viewer'))
    status, _ = api.call('GET', '/api/folders/' + FOLDER_UID, expected=(200, 404))
    if status == 404:
        fail('the packaged folder is not provisioned yet; start Grafana with the rendered configuration first')
    status, _ = api.call('GET', '/api/folders/' + CUSTOM_UID, expected=(200, 404))
    if status == 404:
        api.call('POST', '/api/folders', dict(uid=CUSTOM_UID, title='用户自定义', parentUid=FOLDER_UID))
        print('OK: folder MPP/用户自定义 created')
    else:
        print('OK: folder MPP/用户自定义 already exists')
    api.call('PATCH', '/api/org/preferences', dict(homeDashboardUID=HOME_UID, timezone='Asia/Shanghai', language='zh-Hans'))
    print('OK: organisation defaults set (home dashboard, Beijing time, Chinese)')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest='action', required=True)
    first = actions.add_parser('files', help='核对摘要、解压并生成配置（不联网）')
    first.add_argument('--home', type=Path, required=True, help='Grafana 的安装和数据目录，须在程序目录之外')
    first.add_argument('--files', type=Path, required=True, help='放有清单所列安装文件的目录')
    first.add_argument('--db-password-file', required=True, help='只读数据库账号的密码文件')
    first.add_argument('--pg-port', type=int, required=True)
    first.add_argument('--database', default='sql_apm')
    first.add_argument('--schema', default='sql_apm')
    first.add_argument('--readonly-role', default='sql_apm_ro')
    first.add_argument('--listen', default='0.0.0.0', help='Grafana 监听地址')
    first.add_argument('--service-port', type=int, default=3001, help='指纹服务端口（只监听 127.0.0.1）')
    first.add_argument('--service-passfile', help='指纹服务使用的 PGPASSFILE（写入单元文件）')
    first.add_argument('--app-root', type=Path, default=ROOT, help='程序目录（写入单元文件）')
    first.add_argument('--python', type=Path, default=Path(sys.executable), help='项目解释器（写入单元文件）')
    first.add_argument('--user', default=os.environ.get('USER', ''), help='运行两个进程的系统用户（写入单元文件）')
    second = actions.add_parser('accounts', help='创建查看账号和“用户自定义”文件夹（Grafana 须已启动）')
    second.add_argument('--viewer-password-file', required=True)
    second.add_argument('--viewer-login', default='viewer')
    for action in (first, second):
        action.add_argument('--admin-password-file', required=True, help='Grafana admin 的密码文件')
        action.add_argument('--port', type=int, default=3000, help='Grafana 端口')
    args = parser.parse_args()
    return (files if args.action == 'files' else accounts)(args)


if __name__ == '__main__':
    raise SystemExit(main())
