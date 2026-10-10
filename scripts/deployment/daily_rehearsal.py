#!/usr/bin/env python3
"""Prepare copied real inputs and compare final manual/daily statistics, outside the app."""
import argparse
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil

from acceptance import save, verify_baseline
from statistic_comparison import LIMIT, export_statistics, compare_statistics
from verify_package import digest, verify


def prepare(config, output):
    """Use the nine-task manifests, preserving their source identity and training rules."""
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    sources, batches, clusters = {}, {}, []
    training = None
    copied = []
    for cluster in ('119', '120'):
        source = json.loads((config / ('import-' + cluster + '.json')).read_text())
        rule = json.loads((config / ('training-' + cluster + '.json')).read_text())
        rule.pop('clusters')
        if training is not None and rule != training:
            raise ValueError('training configurations differ')
        training = rule
        clusters.append(cluster)
        sources.update(source['sources'])
        directory = output / 'inbox' / cluster
        directory.mkdir(parents=True)
        for batch in source['batches'].values():
            for member in batch['files']:
                original = Path(member['path'])
                target = directory / original.name
                if target.exists():
                    raise ValueError('duplicate log filename')
                shutil.copy2(original, target)  # Never hard-link the preserved evidence.
                checksum = digest(original)
                if digest(target) != checksum:
                    raise ValueError('copied log differs')
                copied.append(dict(file=cluster + '/' + original.name, sha256=checksum))
    save(output / 'import.json', dict(version=1, clusters=clusters, sources=sources, batches=batches))
    save(output / 'training.json', dict(training, clusters=clusters))
    save(output / 'daily.json', dict(version=1, import_config='import.json', training_config='training.json',
        sources={name: dict(directory='inbox/' + source['cluster']) for name, source in sources.items()},
        workers=4, cleanup=dict(enabled=True), raw_files=dict(retention_days='off')))
    save(output / 'copied-files.json', copied)
    print(json.dumps(dict(copied_files=len(copied), markers_created=False)))


def snapshot(app, schema, output):
    import psycopg2
    from psycopg2 import sql
    verify(app, installed=True)
    metadata = json.loads((app / 'RELEASE.json').read_text())
    from acceptance import product_files
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(program_commit=metadata['commit'], product_sha256=product_files(metadata), clusters={})
    with closing(psycopg2.connect(os.environ['SQL_APM_DSN'])) as db:
        with db, db.cursor() as cur:
            cur.execute(sql.SQL('SET search_path={},pg_catalog').format(sql.Identifier(schema)))
            cur.execute("SET TIME ZONE 'Asia/Shanghai'; SET work_mem='16MB'")
        for cluster in ('119', '120'):
            with db, db.cursor() as cur:
                cur.execute('''SELECT v.build_id,c.cutoff_date,c.window_start,c.window_end,
                    to_jsonb(c)-'config_id' FROM current_version v JOIN build b USING(build_id)
                    JOIN config_snapshot c ON c.config_id=b.config_id WHERE v.scope_id=%s''', (cluster,))
                row = cur.fetchone()
                if row is None:
                    raise ValueError('missing current version: ' + cluster)
            build, cutoff, start, end, configuration = row
            report['clusters'][cluster] = dict(build_id=build, cutoff_date=str(cutoff),
                window_start=str(start), window_end=str(end), configuration=configuration,
                statistics=export_statistics(db, build, output, cluster))
    save(output / 'results.json', report)
    print(json.dumps(dict(exported=True, clusters=2)))


def compare(manual, daily, output, cross_machine=False):
    left, right = [json.loads((path / 'results.json').read_text()) for path in (manual, daily)]
    verify_baseline(dict(commit=right['program_commit'], files=right['product_sha256']), left)
    report = dict(passed=True, cross_machine=cross_machine, clusters={})
    for cluster in ('119', '120'):
        a, b = left['clusters'][cluster], right['clusters'][cluster]
        same = all(a[key] == b[key] for key in ('cutoff_date', 'window_start', 'window_end', 'configuration'))
        stats = compare_statistics(b['statistics'], daily, a['statistics'], manual,
                                   absolute_limit=LIMIT if cross_machine else Decimal(0))
        report['clusters'][cluster] = dict(same_configuration_and_window=same, statistics=stats)
        report['passed'] &= same and stats['passed']
    save(output, report)
    print(json.dumps(report))
    if not report['passed']:
        raise ValueError('manual/daily final results differ; evidence preserved')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    p = actions.add_parser('prepare')
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = actions.add_parser('snapshot')
    p.add_argument('--app-root', type=Path, required=True)
    p.add_argument('--schema', default='sql_apm')
    p.add_argument('--output', type=Path, required=True)
    p = actions.add_parser('compare')
    p.add_argument('--manual', type=Path, required=True)
    p.add_argument('--daily', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cross-machine', action='store_true',
                   help='allow only the confirmed 1e-12 logarithmic rounding difference between machines')
    args = vars(parser.parse_args())
    action = args.pop('action')
    if action == 'snapshot':
        args['app'] = args.pop('app_root')
    globals()[action](**args)


if __name__ == '__main__':
    main()
