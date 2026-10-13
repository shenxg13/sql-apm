"""Shared terminal/password-file handling; never include a secret in an error."""
from getpass import getpass
import os
from pathlib import Path
import stat
import tempfile


def validate_password(value):
    if not value or '\n' in value or '\r' in value:
        raise ValueError('密码不能为空或含换行')
    return value


def prompt_password(label):
    first = getpass(label + ': ')
    second = getpass('再次输入: ')
    if first != second:
        raise ValueError('两次输入不一致')
    return validate_password(first)


def read_password(path):
    path = Path(path)
    check_private(path)
    value = path.read_bytes().decode('utf-8')
    return validate_password(value[:-1] if value.endswith('\n') else value)


def check_private(path):
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('password file must be a regular owner-only file')


def private_target(path):
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError('password target must not be a symbolic link')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise ValueError('password directory must have mode 0700')
    if path.exists():
        check_private(path)
    return path


def write_private(path, value):
    path = private_target(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def pgpass_escape(value):
    return str(value).replace('\\', '\\\\').replace(':', '\\:')


def pgpass_fields(line):
    fields, field, escaped = [], '', False
    for char in line:
        if escaped:
            field += char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(field)
            field = ''
        else:
            field += char
    fields.append(field)
    if escaped or len(fields) != 5:
        raise ValueError('invalid existing passfile entry')
    return fields


def passfile_text(path, hosts, port, database, role, password):
    """Replace only this account's exact connection entries, keeping other roles."""
    hosts = list(dict.fromkeys(hosts))
    retained = []
    if path.exists():
        check_private(path)
        for line in path.read_text().splitlines():
            if not line or line.startswith('#'):
                retained.append(line)
                continue
            fields = pgpass_fields(line)
            if fields[:4] not in [[host, str(port), database, role] for host in hosts]:
                retained.append(line)
    for host in hosts:
        retained.append(':'.join(pgpass_escape(v) for v in (host, port, database, role, password)))
    return '\n'.join(retained) + '\n'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='在终端输入两次密码并保存为 0600 文件，不施加长度或字符规则。')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    try:
        path = private_target(args.output)
        if path.exists() and not args.replace:
            raise ValueError('password file exists; use --replace for an intentional change')
        write_private(path, prompt_password(path.name))
        print('OK: password saved; secret not displayed')
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
