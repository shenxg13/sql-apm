#!/usr/bin/env python3
"""Download the pinned Grafana and plugin archives and verify their digests.

Online step for the development machine. The offline bundle (Issue #52) and
``install.sh`` read the same manifest; ``install.sh`` verifies again and never
downloads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'grafana/components.json'


def components(manifest=MANIFEST):
    document = json.loads(Path(manifest).read_text())
    return [document['grafana']] + document['plugins']


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def verify(directory, manifest=MANIFEST):
    """Return the names whose file is missing or does not match the manifest."""
    return [item['file'] for item in components(manifest)
            if not (directory / item['file']).is_file() or digest(directory / item['file']) != item['sha256']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='下载目录（应在 Git 忽略的位置）')
    parser.add_argument('--verify-only', action='store_true', help='只核对已有文件，不联网')
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for item in components():
        target = args.directory / item['file']
        if target.is_file() and digest(target) == item['sha256']:
            print('OK: ' + item['file'] + ' already present and verified')
            continue
        if args.verify_only:
            continue
        partial = target.with_name(target.name + '.partial')
        print('DOWNLOAD: ' + item['url'], flush=True)
        with urllib.request.urlopen(item['url'], timeout=60) as response, partial.open('wb') as stream:
            for block in iter(lambda: response.read(1 << 20), b''):
                stream.write(block)
        if digest(partial) != item['sha256']:
            # Keep the rejected file out of the place install.sh reads.
            print('ERROR: digest mismatch for ' + item['file'] + '; left as ' + partial.name, file=sys.stderr)
            return 1
        partial.replace(target)
        print('OK: ' + item['file'] + ' downloaded and verified')
    bad = verify(args.directory)
    if bad:
        print('ERROR: missing or mismatching: ' + ', '.join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
