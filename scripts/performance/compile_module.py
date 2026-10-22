#!/usr/bin/env python3
"""Build only auto_explain with the installed Kylin runtime's PGXS; never install it."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tarfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/data/sql-apm'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, out = args.root.resolve(), args.output.resolve()
    if out == root or out.is_relative_to(root) or out.exists():
        parser.error('use a new output directory outside the product root')
    manifest = root/'offline-bundle/runtime/runtime-manifest.json'
    source = root/'offline-bundle/sources/postgresql-17.10.tar.gz'
    runtime = json.loads(manifest.read_text())
    expected = runtime['build_inputs']['sources/postgresql-17.10.tar.gz']['sha256']
    if sha(source) != expected or runtime['postgresql'] != '17.10':
        raise SystemExit('source_or_runtime_mismatch')
    pg_config = root/'postgresql-17.10/bin/pg_config'
    def config(option):
        return subprocess.check_output([pg_config, option], text=True).strip()
    if config('--version') != 'PostgreSQL 17.10':
        raise SystemExit('postgresql_17_10_required')
    out.mkdir(parents=True, mode=0o700)
    with tarfile.open(source) as archive:
        archive.extractall(out/'source', filter='data')
    module = out/'source/postgresql-17.10/contrib/auto_explain'
    command = ['make', 'USE_PGXS=1', 'PG_CONFIG='+str(pg_config)]
    with (out/'build.log').open('xb') as log:
        subprocess.run(command, cwd=module, stdout=log, stderr=subprocess.STDOUT, check=True)
    artifact = out/'auto_explain.so'
    artifact.write_bytes((module/'auto_explain.so').read_bytes())
    (out/'COPYRIGHT').write_bytes((out/'source/postgresql-17.10/COPYRIGHT').read_bytes())
    record = dict(format='sql-apm-auto-explain/1', version=config('--version'),
        configure=config('--configure'), cflags=config('--cflags'), cc=config('--cc'),
        pgxs=config('--pgxs'), command=command, method='installed_runtime_PGXS',
        source_sha256=expected, runtime_manifest_sha256=sha(manifest),
        runtime_pg_config_sha256=sha(pg_config), module_sha256=sha(artifact),
        architecture=platform.machine(), kernel=platform.release(),
        copyright_sha256=sha(out/'COPYRIGHT'),
        system_release=Path('/etc/os-release').read_text(), build_log_sha256=sha(out/'build.log'))
    (out/'module.json').write_text(json.dumps(record, sort_keys=True, indent=2)+'\n')
    print('auto_explain built; not installed; sha256='+record['module_sha256'])


if __name__ == '__main__':
    main()
