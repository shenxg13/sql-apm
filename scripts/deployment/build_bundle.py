#!/usr/bin/env python3
"""Assemble checked offline inputs; no production logs or credentials are included."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
BASE = '6451d140d44f4e06cc34862c3e5aff7593d7afeb'
PY_SHA = 'e0fbd5b6e1ee242524430dee3c91baf4cbbaba4a72dd1674b90fda87b713c7ab'
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rpm-collection', type=Path, required=True)
    parser.add_argument('--python-source', type=Path, required=True)
    parser.add_argument('--wheel-dir', type=Path, action='append', required=True)
    parser.add_argument('--postgres-source', type=Path)
    parser.add_argument('--commit', default=BASE)
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        parser.error('fresh output required')
    commit = run('git', 'rev-parse', args.commit + '^{commit}')
    for older, newer in ((BASE, commit), (commit, 'origin/main')):
        subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer], cwd=ROOT, check=True)
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

    add(args.python_source, 'sources/Python-3.9.5.tgz', '3.9.5',
        'https://www.python.org/ftp/python/3.9.5/Python-3.9.5.tgz', PY_SHA)
    pg = args.postgres_source
    if pg is None:
        pg = out / 'postgresql-download.tmp'
        with urllib.request.urlopen(PG_URL, timeout=60) as response, pg.open('wb') as file:
            shutil.copyfileobj(response, file)
    with urllib.request.urlopen(PG_URL + '.sha256', timeout=30) as response:
        publisher_sha = response.read().decode('ascii').split()[0]
    if publisher_sha != PG_SHA:
        raise ValueError('PostgreSQL publisher checksum changed')
    add(pg, 'sources/postgresql-17.10.tar.gz', '17.10', PG_URL, PG_SHA)
    if args.postgres_source is None:
        pg.unlink()
    locked = [line.split() for line in (ROOT / 'requirements.txt').read_text().splitlines()
              if line and not line.startswith('#')]
    for requirement, sha_arg in locked:
        name, version = requirement.split('==')
        sha = sha_arg.split(':')[-1]
        choices = [p for directory in args.wheel_dir for p in directory.glob('*.whl')
                   if p.name.startswith(name.replace('-', '_') + '-' + version + '-')
                   and digest(p) == sha]
        if not choices:
            raise ValueError('missing locked wheel: ' + requirement)
        metadata_url = 'https://pypi.org/pypi/' + name + '/' + version + '/json'
        with urllib.request.urlopen(metadata_url, timeout=30) as response:
            metadata = json.load(response)
        origin = next(item['url'] for item in metadata['urls']
                      if item['filename'] == choices[0].name and item['digests']['sha256'] == sha)
        add(choices[0], 'wheels/' + choices[0].name, version, origin, sha)
    archive = out / 'program.tar.gz'
    subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=app/',
                    '-o', str(archive), commit], cwd=ROOT, check=True)
    entries.append(dict(path=archive.name, version=commit,
                        source='https://github.com/shenxg13/sql-apm/commit/' + commit,
                        sha256=digest(archive), bytes=archive.stat().st_size))
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
    helpers = list((ROOT / 'scripts/deployment').glob('*'))
    helpers += [ROOT / 'scripts/db/verify_publication.py',
                ROOT / 'docs/reports/data/kylin-alma-baseline-2026-10-02.json',
                ROOT / 'docs/reports/kylin-offline-deployment-2026-10-02.md',
                ROOT / 'docs/reports/data/kylin-offline-deployment-2026-10-02.json',
                ROOT / 'docs/runbooks/kylin-offline-deployment.md',
                ROOT / 'docs/runbooks/kylin-validation-record.md']
    for file in sorted(helpers):
        if file.is_file():
            relative = str(file.relative_to(ROOT))
            add(file, 'support/' + relative, 'issue31-support/1',
                'Issue #31 deployment support; separate from main product archive')
    (out / 'manifest.json').write_text(json.dumps(dict(program_commit=commit, files=entries),
                                                  ensure_ascii=False, indent=2) + '\n')
    files = sorted(p for p in out.rglob('*') if p.is_file())
    (out / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + str(p.relative_to(out)) + '\n'
                                          for p in files))
    with tarfile.open(str(out) + '.tar.gz', 'w:gz') as file:
        file.add(out, arcname=out.name)
    bundle = Path(str(out) + '.tar.gz')
    Path(str(bundle) + '.sha256').write_text(digest(bundle) + '  ' + bundle.name + '\n')
    print(json.dumps(dict(bundle=str(bundle), sha256=digest(bundle), files=len(entries))))


if __name__ == '__main__':
    main()
