#!/usr/bin/env python3
"""Verify the complete delivered program tree; no database or network access."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify(root, installed=False):
    root = root.resolve()
    document = json.loads((root / 'RELEASE.json').read_text())
    expected = document['files']
    actual = set()
    for directory, names, files in os.walk(root):
        parent = Path(directory)
        for name in names[:]:
            path = parent / name
            if installed and (path == root / '.venv' or name == '__pycache__'):
                names.remove(name)
                continue
            if path.is_symlink():
                raise ValueError('unexpected symbolic link: ' + str(path.relative_to(root)))
        for name in files:
            path = parent / name
            if path.is_symlink():
                raise ValueError('unexpected symbolic link: ' + str(path.relative_to(root)))
            actual.add(str(path.relative_to(root)))
    if actual != set(expected) | {'RELEASE.json', 'SHA256SUMS'}:
        raise ValueError('package file set mismatch: missing=' + str(sorted(set(expected) - actual)) +
                         '; extra=' + str(sorted(actual - set(expected) - {'RELEASE.json', 'SHA256SUMS'})))
    for name, checksum in expected.items():
        if Path(name).is_absolute() or '..' in Path(name).parts or digest(root / name) != checksum:
            raise ValueError('package checksum mismatch: ' + name)
    checksums = ''.join(digest(root / name) + '  ' + name + '\n'
                        for name in sorted(set(expected) | {'RELEASE.json'}))
    if (root / 'SHA256SUMS').read_text() != checksums:
        raise ValueError('SHA256SUMS differs from package files')
    if (root / 'VERSION').read_text().strip() != document['version']:
        raise ValueError('package version mismatch')
    if (root / 'PROGRAM_COMMIT').read_text().strip() != document['commit']:
        raise ValueError('package commit mismatch')
    return dict(passed=True, version=document['version'], kind=document['kind'],
                commit=document['commit'], files=len(actual))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--installed', action='store_true', help='Allow .venv and Python bytecode caches')
    args = parser.parse_args()
    print(json.dumps(verify(args.app_root, args.installed)))
