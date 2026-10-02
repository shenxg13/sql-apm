#!/usr/bin/env python3
"""Build a versioned program and separate verification kit from a fixed commit."""
import argparse
import fnmatch
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from render_manual import PAGES, render
from verify_package import digest, verify

ROOT = Path(__file__).resolve().parents[2]
BASE = '6451d140d44f4e06cc34862c3e5aff7593d7afeb'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def archive(directory, output):
    """Stable metadata and ordering; identical committed inputs give identical bytes."""
    with output.open('xb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            for path in sorted(directory.rglob('*')):
                if not path.is_file():
                    continue
                info = tar.gettarinfo(str(path), arcname=directory.name + '/' + str(path.relative_to(directory)))
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if path.suffix == '.sh' else 0o644
                with path.open('rb') as file:
                    tar.addfile(info, file)
    Path(str(output) + '.sha256').write_text(digest(output) + '  ' + output.name + '\n')


def select(source, destination, patterns):
    files = [str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()]
    selected = set()
    for pattern in patterns:
        matches = {name for name in files if fnmatch.fnmatchcase(name, pattern)}
        if not matches:
            raise ValueError('package rule matched no files: ' + pattern)
        selected.update(matches)
    for name in sorted(selected):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    return selected


def compare_product(app, previous):
    paths = sorted(str(p.relative_to(app)) for p in app.rglob('*') if p.is_file() and
                   (p.relative_to(app).parts[0] in ('sql_apm', 'rules') or
                    str(p.relative_to(app)) in ('requirements.txt', 'scripts/db/initialize.sh')))
    rows = []
    with tarfile.open(previous, 'r:gz') as tar:
        for name in paths:
            try:
                original = tar.extractfile('app/' + name).read()
            except KeyError:
                raise ValueError('previous program lacks product file: ' + name) from None
            before = hashlib.sha256(original).hexdigest()
            after = digest(app / name)
            rows.append(dict(path=name, previous_sha256=before, candidate_sha256=after, equal=before == after))
    return dict(previous_program_sha256=digest(previous), files=rows,
                all_equal=all(row['equal'] for row in rows),
                note='Only identical product files support inheritance of prior nine-task evidence; '
                     'candidate deployment and four-worker verification remain separate.')


def build(args):
    commit = git('rev-parse', args.commit + '^{commit}')
    subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, commit], cwd=ROOT, check=True)
    if args.kind == 'release':
        subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'origin/main'], cwd=ROOT, check=True)
    output = args.output.resolve()
    if output.exists():
        raise ValueError('fresh output directory required')
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='sql-apm-release-') as directory:
        source = Path(directory) / 'source'
        source.mkdir()
        raw = Path(directory) / 'source.tar'
        subprocess.run(['git', 'archive', '--format=tar', '-o', str(raw), commit], cwd=ROOT, check=True)
        with tarfile.open(raw) as tar:
            for member in tar.getmembers():
                if member.name.startswith('/') or '..' in Path(member.name).parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsupported repository archive member')
            tar.extractall(source)
        # Never report a builder revision different from the code executing here.
        for name in ('build_release.py', 'render_manual.py', 'verify_package.py', 'package-files.json', 'build-requirements.txt'):
            relative = 'scripts/deployment/' + name
            if (source / relative).read_bytes() != (ROOT / relative).read_bytes():
                raise ValueError('commit the build tools before building: ' + relative)
        rules = json.loads((source / 'scripts/deployment/package-files.json').read_text())
        app, validation = output / 'app', output / 'verification'
        select(source, app, rules['program'])
        select(source, validation, rules['verification'])
        for name in rules['verification_probes']:
            target = validation / 'probes' / (name + '.py')
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source / 'sql_apm/diagnostics' / (name + '.py'), target)
        html, html_checks = render(source, commit, args.version)
        (app / 'INSTALL.html').write_text(html, encoding='utf-8')
        (app / 'VERSION').write_text(args.version + '\n')
        (app / 'PROGRAM_COMMIT').write_text(commit + '\n')
        metadata = dict(format='sql-apm-release/1', version=args.version, kind=args.kind,
                        prerelease=True, production_use=False, commit=commit, product_base=BASE,
                        build_requirements_sha256=digest(source / 'scripts/deployment/build-requirements.txt'),
                        manual_sources={name: digest(source / name) for name in PAGES}, html_checks=html_checks,
                        files={str(p.relative_to(app)): digest(p) for p in sorted(app.rglob('*')) if p.is_file()})
        save(app / 'RELEASE.json', metadata)
        (app / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + str(p.relative_to(app)) + '\n'
                                               for p in sorted(app.rglob('*')) if p.is_file()))
        verify(app)
        (validation / 'PROGRAM_COMMIT').write_text(commit + '\n')
        (validation / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + str(p.relative_to(validation)) + '\n'
                                                       for p in sorted(validation.rglob('*')) if p.is_file()))
        if args.previous_program:
            comparison = compare_product(app, args.previous_program)
            save(output / 'product-files-comparison.json', comparison)
            if not comparison['all_equal']:
                raise ValueError('product changed; review comparison and affected acceptance before proceeding')
        program = output / ('sql-apm-' + args.version + '.tar.gz')
        archive(app, program)
        archive(validation, output / ('sql-apm-verification-' + args.version + '.tar.gz'))
        save(output / 'build-result.json', dict(program=str(program), sha256=digest(program),
                                              bytes=program.stat().st_size, commit=commit, html_checks=html_checks))
        print((output / 'build-result.json').read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--version', choices=['v0.1.0'], default='v0.1.0')
    parser.add_argument('--kind', choices=['candidate', 'release'], default='candidate')
    parser.add_argument('--previous-program', type=Path)
    build(parser.parse_args())
