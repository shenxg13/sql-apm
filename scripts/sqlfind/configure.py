#!/usr/bin/env python3
"""Install/revert one account's sqlfind login configuration; never touch credentials."""
import argparse
import base64
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
BLOCK = b'\n# sqlfind login begin\n. "$HOME/.sqlfind/login.sh"\n# sqlfind login end\n'


def private_write(path, content):
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(content)


def install(home, root, values):
    home, root = home.resolve(), root.resolve()
    directory = home / '.sqlfind'
    if directory.is_symlink():
        raise ValueError('配置目录不能是符号链接')
    encoded = (json.dumps(values, ensure_ascii=False, indent=2) + '\n').encode()
    if directory.exists():
        expected = directory / 'connection.json'
        if (directory / 'manifest.json').is_file() and expected.read_bytes() == encoded \
                and (directory / 'bin/sqlfind').resolve() == root / 'sqlfind':
            manifest = json.loads((directory / 'manifest.json').read_text())
            if BLOCK not in (home / manifest['profile']).read_bytes():
                raise ValueError('登录配置已变化，请先核对或撤销')
            return []
        raise ValueError('已有不同配置，请先撤销后重新配置')
    if not (root / 'sqlfind').is_file() or not os.access(root / '.venv/bin/python', os.X_OK):
        raise ValueError('程序脚本或 Python 环境不存在')
    passfile = Path(values['passfile'])
    info = passfile.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError('密码文件须由本账号拥有，且仅本账号可读写')
    profile = next((home / p for p in ('.bash_profile', '.bash_login', '.profile')
                    if (home / p).exists() or (home / p).is_symlink()), home / '.bash_profile')
    if profile.is_symlink() or (profile.exists() and not profile.is_file()):
        raise ValueError('登录配置必须是账号主目录中的普通文件')
    original = profile.read_bytes() if profile.exists() else b''
    if b'# sqlfind login begin' in original:
        raise ValueError('登录配置已有 sqlfind 片段，请先核对')
    manifest = dict(profile=profile.name, existed=profile.exists(),
                    original=base64.b64encode(original).decode())
    directory.mkdir(mode=0o700)
    try:
        (directory / 'bin').mkdir(mode=0o700)
        (directory / 'bin/sqlfind').symlink_to(root / 'sqlfind')
        private_write(directory / 'connection.json', encoded)
        login = '# Only the interactive login PATH changes; psql and database environment stay as configured.\n'
        login += 'case ":$PATH:" in *":$HOME/.sqlfind/bin:"*) ;; *) export PATH="$HOME/.sqlfind/bin:$PATH" ;; esac\n'
        private_write(directory / 'login.sh', login.encode())
        private_write(directory / 'manifest.json', (json.dumps(manifest) + '\n').encode())
        # Existing profile bytes and permissions are preserved; only append our block.
        with profile.open('ab') as stream:
            stream.write(BLOCK)
    except BaseException:
        # Leave the manifest for exact, inspectable recovery; never guess at unrelated files.
        raise
    return [profile, directory / 'bin/sqlfind', directory / 'connection.json', directory / 'login.sh', directory / 'manifest.json']


def uninstall(home):
    home = home.resolve()
    directory = home / '.sqlfind'
    if not directory.exists():
        return []
    if directory.is_symlink():
        raise ValueError('配置目录不能是符号链接')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['profile'] not in ('.bash_profile', '.bash_login', '.profile'):
        raise ValueError('撤销记录无效')
    profile = home / manifest['profile']
    if profile.is_symlink():
        raise ValueError('登录配置已变为符号链接，请先核对')
    original = base64.b64decode(manifest['original'], validate=True)
    current = profile.read_bytes() if profile.exists() else b''
    if current == original + BLOCK:
        restored = original
    elif current.count(BLOCK) == 1:
        restored = current.replace(BLOCK, b'', 1)
    elif current == original:
        restored = original  # interrupted install before appending the block
    else:
        raise ValueError('登录片段已被修改，请核对后恢复片段再撤销')
    expected = {'bin', 'connection.json', 'login.sh', 'manifest.json'}
    if set(p.name for p in directory.iterdir()) - expected or set(p.name for p in (directory / 'bin').iterdir()) - {'sqlfind'}:
        raise ValueError('配置目录含其他文件，请先移走这些文件再撤销')
    if not manifest['existed'] and not restored:
        profile.unlink(missing_ok=True)
    else:
        profile.write_bytes(restored)
    changed = [profile]
    for name in ('bin/sqlfind', 'connection.json', 'login.sh', 'manifest.json'):
        path = directory / name
        path.unlink(missing_ok=True)
        changed.append(path)
    (directory / 'bin').rmdir()
    directory.rmdir()
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description='配置或撤销本账号的 sqlfind；密码文件必须已存在，本工具不读取或修改密码。')
    parser.add_argument('action', choices=['install', 'uninstall'])
    parser.add_argument('--host', help='数据库主机或 socket 目录')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--database', default='sql_apm')
    parser.add_argument('--user', default='sql_apm_ro', help='已有只读账号')
    parser.add_argument('--passfile', type=Path, help='已有受保护密码文件的绝对路径')
    parser.add_argument('--schema', default='sql_apm')
    args = parser.parse_args(argv)
    try:
        if args.action == 'install':
            if not args.host or not args.passfile or not args.passfile.is_absolute() or not 1 <= args.port <= 65535:
                raise ValueError('请提供主机、有效端口和密码文件的绝对路径')
            if any(not value or '\n' in value or '\r' in value for value in (args.host, args.database, args.user, args.schema)):
                raise ValueError('连接设置须是非空的单行文本')
            values = dict(host=args.host, port=args.port, dbname=args.database, user=args.user,
                          passfile=str(args.passfile), schema=args.schema)
            changes = install(Path.home(), ROOT, values)
        else:
            changes = uninstall(Path.home())
        for path in changes:
            print('已更新: ' + str(path))
        print('配置完成，新登录后可使用 sqlfind。' if args.action == 'install' else '已撤销 sqlfind 配置，新登录后生效。')
        if not changes:
            print('没有文件需要改动。')
        return 0
    except (OSError, ValueError, KeyError):
        print('配置失败，请核对目录、连接选项、密码文件权限和已有登录片段；未修改数据库或密码。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
