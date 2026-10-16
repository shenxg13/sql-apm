#!/usr/bin/env python3
"""Export, inspect and install the single Kylin runtime artifact (bootstrap: Python >= 3.7).

Only the three explicit runtime trees are included, never pgdata, logs, credentials
or application sources. Installation does not install RPMs or run a compiler.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile

PREFIX = '/data/sql-apm'
TREES = ('python-3.13.16', 'postgresql-17.10', 'app/.venv')
FORMAT = 'sql-apm-kylin-runtime/1'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            result.update(block)
    return result.hexdigest()


def inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def payload_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('invalid runtime path')
    if not any(name == tree or name.startswith(tree + '/') for tree in TREES):
        raise ValueError('unexpected runtime tree: ' + name)
    return path


def check_link(name, target):
    path = Path(os.path.normpath(target if target.startswith('/') else PREFIX + '/' + str(PurePosixPath(name).parent / target)))
    if not any(inside(path, Path(PREFIX) / tree) for tree in TREES):
        raise ValueError('runtime link leaves runtime trees: ' + name)


def inventory(root):
    rows = {}
    for tree in TREES:
        start = root / tree
        if not start.is_dir() or start.is_symlink():
            raise ValueError('missing real runtime directory: ' + tree)
        paths = [start]
        for directory, names, files in os.walk(start, followlinks=False):
            names[:] = sorted(name for name in names if name != '__pycache__')
            paths.extend(Path(directory) / name for name in names + sorted(files)
                         if not name.endswith(('.pyc', '.pyo')))
        for path in paths:
            name = path.relative_to(root).as_posix()
            payload_path(name)
            mode = stat.S_IMODE(path.lstat().st_mode)
            if mode & 0o7000:
                raise ValueError('unexpected special mode: ' + name)
            if path.is_symlink():
                target = os.readlink(path)
                check_link(name, target)
                rows[name] = dict(type='symlink', target=target)
            elif path.is_dir():
                rows[name] = dict(type='directory', mode=mode)
            elif path.is_file():
                rows[name] = dict(type='file', mode=mode, bytes=path.stat().st_size, sha256=digest(path))
            else:
                raise ValueError('unexpected runtime file type: ' + name)
    return dict(sorted(rows.items()))


def save(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def export_runtime(root, bundle, evidence, output):
    if str(root) != PREFIX or str(root.resolve()) != PREFIX:
        raise ValueError('runtime must be built at /data/sql-apm')
    if output.exists():
        raise ValueError('fresh artifact directory required; do not recompile the accepted artifact')
    app = json.loads((root / 'app/RELEASE.json').read_text())
    from verify_package import verify
    verify(root / 'app', installed=True)
    source = json.loads((bundle / 'manifest.json').read_text())
    if source['program_commit'] != app['commit']:
        raise ValueError('build bundle and installed program differ')
    inputs = {}
    for entry in source['files']:
        if entry['path'].startswith(('sources/', 'wheels/', 'rpms/', 'rpm-evidence/')):
            if digest(bundle / entry['path']) != entry['sha256']:
                raise ValueError('build input checksum mismatch: ' + entry['path'])
            inputs[entry['path']] = entry
    required = ('system-release.txt', 'architecture.txt', 'packages-before.tsv', 'packages-after.tsv',
                'compile-runtime.sh', 'build.log', 'selfcheck.json', 'prefix-details.txt')
    for name in required:
        if not (evidence / name).is_file() or not (evidence / name).stat().st_size:
            raise ValueError('missing compilation evidence: ' + name)
    if 'Kylin' not in (evidence / 'system-release.txt').read_text() or (evidence / 'architecture.txt').read_text().strip() != 'x86_64':
        raise ValueError('artifact must come from the Kylin x86_64 build host')
    checks = json.loads((evidence / 'selfcheck.json').read_text())
    if not checks['passed'] or checks['program_commit'] != app['commit'] or checks['checks'] != CHECKS:
        raise ValueError('all compilation-path acceptance entries must pass before export')
    rows = inventory(root)
    # Full binary/text scan: records every file that embeds the fixed prefix.
    references = []
    for name, entry in rows.items():
        if entry['type'] == 'symlink':
            if PREFIX in entry['target']:
                references.append(name)
        elif entry['type'] == 'file':
            with (root / name).open('rb') as stream:
                tail = b''
                for block in iter(lambda: stream.read(1 << 20), b''):
                    data = tail + block
                    if PREFIX.encode() in data:
                        references.append(name)
                        break
                    tail = data[-len(PREFIX):]
    output.mkdir(parents=True)
    (output / 'build-evidence').mkdir()
    for name in required:
        shutil.copyfile(evidence / name, output / 'build-evidence' / name)
    shutil.copytree(evidence / 'checks', output / 'build-evidence/checks')
    document = dict(format=FORMAT, prefix=PREFIX, python='3.13.16', postgresql='17.10',
                    program_commit=app['commit'], requirements_sha256=digest(root / 'app/requirements.txt'),
                    build_inputs=inputs, files=rows, embedded_prefix_paths=references,
                    evidence={name: digest(evidence / name) for name in required})
    save(output / 'runtime-manifest.json', document)
    archive = output / 'runtime.tar.gz'
    with archive.open('xb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as tar:
            for name, entry in rows.items():
                tar.inodes.clear()  # Store hardlinked files independently, never emit hardlink members.
                info = tar.gettarinfo(str(root / name), arcname=name)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                if entry['type'] == 'file':
                    with (root / name).open('rb') as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)
    (output / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.relative_to(output).as_posix() + '\n'
        for p in sorted(output.rglob('*')) if p.is_file()))
    inspect_artifact(output)
    print('OK: single runtime artifact exported; retain this directory and its checksum')


def inspect_artifact(directory):
    if any(path.is_symlink() for path in directory.rglob('*')):
        raise ValueError('artifact container must not contain symbolic links')
    document = json.loads((directory / 'runtime-manifest.json').read_text())
    if document['format'] != FORMAT or document['prefix'] != PREFIX or document['python'] != '3.13.16' or document['postgresql'] != '17.10':
        raise ValueError('unsupported runtime identity')
    checksums = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split('  ', 1)
        path = directory / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or digest(path) != checksum:
            raise ValueError('runtime artifact checksum mismatch: ' + name)
        checksums[name] = checksum
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual != set(checksums) | {'SHA256SUMS'} or not {'runtime.tar.gz', 'runtime-manifest.json'} <= set(checksums):
        raise ValueError('runtime artifact file set mismatch')
    rows = document['files']
    with tarfile.open(directory / 'runtime.tar.gz') as tar:
        seen = set()
        for member in tar:
            name = member.name
            payload_path(name)
            if name in seen or name not in rows:
                raise ValueError('duplicate or unexpected runtime member: ' + name)
            seen.add(name)
            entry = rows[name]
            if member.issym() and entry['type'] == 'symlink':
                check_link(name, member.linkname)
                if member.linkname != entry['target']:
                    raise ValueError('runtime link mismatch: ' + name)
            elif member.isfile() and entry['type'] == 'file':
                value = hashlib.sha256()
                with tar.extractfile(member) as stream:
                    for block in iter(lambda: stream.read(1 << 20), b''):
                        value.update(block)
                if value.hexdigest() != entry['sha256'] or member.size != entry['bytes'] or member.mode != entry['mode']:
                    raise ValueError('runtime file mismatch: ' + name)
            elif not (member.isdir() and entry['type'] == 'directory' and member.mode == entry['mode']):
                raise ValueError('runtime member type/mode mismatch: ' + name)
            # No payload member can be below a link or file, irrespective of archive order.
            for parent in PurePosixPath(name).parents:
                if str(parent) in rows and rows[str(parent)]['type'] != 'directory':
                    raise ValueError('runtime member below non-directory: ' + name)
        if seen != set(rows) or not set(TREES) <= seen:
            raise ValueError('runtime archive file set mismatch')
    for name, checksum in document['evidence'].items():
        if checksums.get('build-evidence/' + name) != checksum:
            raise ValueError('build evidence mismatch')
    return document


def install_runtime(root, artifact):
    if str(root) != PREFIX or str(root.resolve()) != PREFIX:
        raise ValueError('binary installation only supports /data/sql-apm; use the compilation appendix')
    for tree in TREES:
        target = root / tree
        if target.exists() or target.is_symlink():
            raise ValueError('runtime target exists; inspect it before installing: ' + tree)
    if (root / 'app').is_symlink() or not (root / 'app').is_dir():
        raise ValueError('unpack the program into a real app directory first')
    document = inspect_artifact(artifact)
    from verify_package import verify
    verify(root / 'app', installed=True)  # The bootstrap interpreter may already have written __pycache__.
    if digest(root / 'app/requirements.txt') != document['requirements_sha256']:
        raise ValueError('runtime dependencies differ from program requirements')
    # Candidate documentation/metadata may change; the exact locked wheels must not.
    with tempfile.TemporaryDirectory(prefix='.runtime-install-', dir=str(root)) as folder:
        stage = Path(folder)
        with tarfile.open(artifact / 'runtime.tar.gz') as tar:
            for member in tar:
                path = stage / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    path.mkdir(exist_ok=True)
                    path.chmod(member.mode)
                elif member.issym():
                    path.symlink_to(member.linkname)
                else:
                    with tar.extractfile(member) as source, path.open('xb') as target:
                        shutil.copyfileobj(source, target)
                    path.chmod(member.mode)
        for tree in TREES:
            (stage / tree).rename(root / tree)
    shutil.copyfile(artifact / 'runtime-manifest.json', root / 'runtime-manifest.json')
    verify_installed(root)
    print('OK: runtime installed; run runtime_check.py before continuing installation')


def verify_installed(root):
    document = json.loads((root / 'runtime-manifest.json').read_text())
    if inventory(root) != document['files']:
        raise ValueError('installed runtime differs from the single exported artifact')
    return document


CHECKS = ['python', 'postgres', 'initdb', 'psql', 'temporary-database', 'package',
          'unit', 'database', 'publication', 'smoke', 'cleanup', 'search', 'views', 'grafana', 'daily', 'daily-recovery', 'sqlfind']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'inspect', 'install', 'verify-installed'])
    parser.add_argument('--root', type=Path, default=Path(PREFIX))
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    if args.action == 'export':
        if not all((args.artifact, args.bundle, args.evidence)):
            parser.error('export requires --artifact, --bundle and --evidence')
        export_runtime(args.root, args.bundle, args.evidence, args.artifact)
    elif args.action == 'verify-installed':
        verify_installed(args.root)
        print('OK: installed runtime matches artifact')
    else:
        if not args.artifact:
            parser.error('--artifact is required')
        if args.action == 'install':
            install_runtime(args.root, args.artifact)
        else:
            inspect_artifact(args.artifact)
            print('OK: runtime artifact verified')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, tarfile.TarError) as error:
        raise SystemExit('STOP: ' + str(error) + '; see INSTALL.html compilation appendix; no RPM or compiler was run')
