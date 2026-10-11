#!/usr/bin/env python3
"""Assemble checked offline inputs; no production logs or credentials are included."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
from urllib.parse import unquote

from verify_package import verify

ROOT = Path(__file__).resolve().parents[2]
BASE = '6451d140d44f4e06cc34862c3e5aff7593d7afeb'
PY_SHA = 'cfac63bddf956deafb1172ca131ae5dcaafd6f95056086e233fca205593ed427'
PG_SHA = 'e4b43025f32ea3d271be64365d284c8462cffd41d80db0c3df6fc62417a2d9dc'
PG_URL = 'https://ftp.postgresql.org/pub/source/v17.10/postgresql-17.10.tar.gz'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def grafana_inputs(app, cache, offline=False):
    """Use the candidate's pins for both cached inputs and official downloads."""
    manifest = json.loads((app / 'grafana/components.json').read_text())
    cache.mkdir(parents=True, exist_ok=True)
    result = []
    for item in [manifest['grafana']] + manifest['plugins']:
        target = cache / item['file']
        if not target.is_file():
            if offline:
                raise ValueError('missing offline Grafana input: ' + item['file'])
            partial = target.with_name(target.name + '.partial')
            with urllib.request.urlopen(item['url'], timeout=60) as response, partial.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            if digest(partial) != item['sha256']:
                raise ValueError('Grafana download checksum mismatch: ' + item['file'])
            partial.replace(target)
        if digest(target) != item['sha256']:
            raise ValueError('Grafana input checksum mismatch: ' + item['file'])
        result.append((target, item))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rpm-collection', type=Path, required=True)
    parser.add_argument('--python-source', type=Path, required=True)
    parser.add_argument('--wheel-dir', type=Path, action='append', required=True)
    parser.add_argument('--postgres-source', type=Path)
    parser.add_argument('--grafana-files', type=Path, required=True,
                        help='Cache for official pinned Grafana/plugin downloads; verified before inclusion')
    parser.add_argument('--release-dir', type=Path, required=True,
                        help='Output of build_release.py; includes the separate verification kit')
    parser.add_argument('--source-manifest', type=Path,
                        help='Reuse verified source URLs from a prior bundle without network access')
    args = parser.parse_args()
    if args.source_manifest and args.postgres_source is None:
        parser.error('--source-manifest requires --postgres-source for offline input reuse')
    out = args.output.resolve()
    if out.exists():
        parser.error('fresh output required')
    release = json.loads((args.release_dir / 'app/RELEASE.json').read_text())
    verify(args.release_dir / 'app')
    commit = release['commit']
    sources = ({entry['path']: entry for entry in json.loads(args.source_manifest.read_text())['files']}
               if args.source_manifest else {})
    out.mkdir(parents=True)
    entries = []

    def add(source, relative, version, origin, expected=None):
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        checksum = digest(destination)
        if expected and checksum != expected:
            raise ValueError('checksum mismatch: ' + relative)
        entries.append(dict(path=relative, version=version, source=origin, sha256=checksum,
                            bytes=destination.stat().st_size))

    add(args.python_source, 'sources/Python-3.13.16.tgz', '3.13.16',
        'https://www.python.org/ftp/python/3.13.16/Python-3.13.16.tgz', PY_SHA)
    for path, item in grafana_inputs(args.release_dir / 'app', args.grafana_files,
                                    offline=bool(args.source_manifest)):
        add(path, 'grafana/' + item['file'], item['version'], item['url'], item['sha256'])
    pg = args.postgres_source
    if pg is None:
        pg = out / 'postgresql-download.tmp'
        with urllib.request.urlopen(PG_URL, timeout=60) as response, pg.open('wb') as file:
            shutil.copyfileobj(response, file)
    if args.source_manifest:
        pg_entry = sources['sources/postgresql-17.10.tar.gz']
        if pg_entry['source'] != PG_URL:
            raise ValueError('unexpected prior PostgreSQL source')
        publisher_sha = pg_entry['sha256']
    else:
        with urllib.request.urlopen(PG_URL + '.sha256', timeout=30) as response:
            publisher_sha = response.read().decode('ascii').split()[0]
    if publisher_sha != PG_SHA:
        raise ValueError('PostgreSQL publisher checksum changed')
    add(pg, 'sources/postgresql-17.10.tar.gz', '17.10', PG_URL, PG_SHA)
    if args.postgres_source is None:
        pg.unlink()
    locked = [line.split() for line in (args.release_dir / 'app/requirements.txt').read_text().splitlines()
              if line and not line.startswith('#')]
    for requirement, sha_arg in locked:
        name, version = requirement.split('==')
        sha = sha_arg.split(':')[-1]
        choices = [p for directory in args.wheel_dir for p in directory.glob('*.whl')
                   if p.name.startswith(name.replace('-', '_') + '-' + version + '-')
                   and digest(p) == sha]
        if not choices:
            raise ValueError('missing locked wheel: ' + requirement)
        if args.source_manifest:
            entry = sources['wheels/' + choices[0].name]
            if entry['sha256'] != sha or not entry['source'].startswith('https://files.pythonhosted.org/'):
                raise ValueError('prior wheel metadata differs from lock')
            origin = entry['source']
        else:
            metadata_url = 'https://pypi.org/pypi/' + name + '/' + version + '/json'
            with urllib.request.urlopen(metadata_url, timeout=30) as response:
                metadata = json.load(response)
            origin = next(item['url'] for item in metadata['urls']
                          if item['filename'] == choices[0].name and item['digests']['sha256'] == sha)
        add(choices[0], 'wheels/' + choices[0].name, version, origin, sha)
    build = json.loads((args.release_dir / 'build-result.json').read_text())
    if build['commit'] != commit:
        raise ValueError('program build commit mismatch')
    add(args.release_dir / ('sql-apm-' + release['version'] + '.tar.gz'), 'program.tar.gz',
        release['version'], 'https://github.com/shenxg13/sql-apm/commit/' + commit, build['sha256'])
    verification = args.release_dir / ('sql-apm-verification-' + release['version'] + '.tar.gz')
    add(verification, 'verification.tar.gz', release['version'], 'verification from commit ' + commit,
        Path(str(verification) + '.sha256').read_text().split()[0])
    (out / 'PROGRAM_COMMIT').write_text(commit + '\n')
    urls = (args.rpm_collection / 'download-urls.txt').read_text().splitlines()
    rpm_rows = [line.split('\t') for line in (args.rpm_collection / 'packages.tsv').read_text().splitlines()]
    signatures = (args.rpm_collection / 'signatures.txt').read_text()
    expected_rpms = dict((line.split()[1].removeprefix('rpms/'), line.split()[0])
                         for line in (args.rpm_collection / 'SHA256SUMS').read_text().splitlines())
    for filename, name, version in rpm_rows:
        if filename + ': digests signatures OK' not in signatures:
            raise ValueError('missing signature evidence: ' + filename)
        origins = [url for url in urls if url.startswith(('http://', 'https://'))
                   and unquote(url.rsplit('/', 1)[-1]) == filename]
        if not origins:
            raise ValueError('missing RPM origin: ' + filename)
        add(args.rpm_collection / 'rpms' / filename, 'rpms/' + filename,
            name + '-' + version, origins[0], expected_rpms[filename])
    # A local repository lets yum solve only the requested roots, instead of
    # installing/upgrading every RPM in the complete dependency collection.
    for file in sorted((args.rpm_collection / 'rpms' / 'repodata').glob('*')):
        add(file, 'rpms/repodata/' + file.name, 'rpm-repository-metadata', 'createrepo_c')
    if not (out / 'rpms/repodata/repomd.xml').is_file():
        raise ValueError('RPM collection needs createrepo_c metadata')
    for file in sorted(args.rpm_collection.glob('*')):
        if file.is_file():
            add(file, 'rpm-evidence/' + file.name, 'collection/1', 'snapshot-initial Kylin host')
    for filename in release['documents']:
        add(args.release_dir / 'app' / filename, filename, release['version'],
            'Markdown document generated from commit ' + commit, release['files'][filename])
    add(args.rpm_collection / 'compile-packages.txt', 'support/compile-packages.txt',
        'compile-package-roots/1', 'original RPM collection')
    (out / 'manifest.json').write_text(json.dumps(dict(program_commit=commit, version=release['version'],
        prerelease=True, kind=release['kind'], files=entries),
                                                  ensure_ascii=False, indent=2) + '\n')
    files = sorted(p for p in out.rglob('*') if p.is_file())
    (out / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + str(p.relative_to(out)) + '\n'
                                          for p in files))
    with Path(str(out) + '.tar.gz').open('xb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode='w') as tar:
                for path in sorted(p for p in out.rglob('*') if p.is_file()):
                    info = tar.gettarinfo(str(path), arcname='offline-bundle/' + str(path.relative_to(out)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    info.mode = 0o644
                    with path.open('rb') as source:
                        tar.addfile(info, source)
    bundle = Path(str(out) + '.tar.gz')
    Path(str(bundle) + '.sha256').write_text(digest(bundle) + '  ' + bundle.name + '\n')
    print(json.dumps(dict(bundle=str(bundle), sha256=digest(bundle), files=len(entries))))


if __name__ == '__main__':
    main()
