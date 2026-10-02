#!/usr/bin/env python3
"""Run the original checks outside app, against the explicitly selected program."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

RESOURCES = Path(__file__).resolve().parents[2]


def setup(app):
    app = app.resolve()
    for name in ('sql_apm/__init__.py', 'scripts/db/initialize.sh', 'rules/functions/v1.0.1.json'):
        if not (app / name).is_file():
            raise ValueError('missing selected application resource: ' + name)
    paths = [str(app), str(RESOURCES), str(RESOURCES / 'tests'), str(RESOURCES / 'scripts/db')]
    # Children run from app or the verification directory, never the development checkout.
    os.environ['SQL_APM_APP_ROOT'] = str(app)
    os.environ['PYTHONPATH'] = os.pathsep.join(paths)
    sys.path[:0] = paths
    import sql_apm
    if Path(sql_apm.__file__).resolve() != app / 'sql_apm/__init__.py':
        raise ValueError('verification imported a different application')
    if (app / 'RELEASE.json').exists():
        spec = importlib.util.spec_from_file_location('delivered_package', app / 'scripts/deployment/verify_package.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(json.dumps(module.verify(app, installed=True)), flush=True)
    print('APPLICATION: ' + str(app), flush=True)
    return app


def unit():
    # These three historical probes are test subjects, not application runtime dependencies.
    # Only their explicit names are loaded; core sql_apm modules always come from app.
    for name in ('function_probe', 'statement_census', 'log_supplement'):
        path = RESOURCES / 'probes' / (name + '.py')
        if path.exists():
            qualified = 'sql_apm.diagnostics.' + name
            spec = importlib.util.spec_from_file_location(qualified, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified] = module
            spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.discover(str(RESOURCES / 'tests'))
    if suite.countTestCases() != 66:
        raise ValueError('v0.1.0 verification kit must retain all 66 ordinary tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def smoke(app, pg_bin):
    from verify import instance, Verification
    from ingestion.test_reader import row, write_csv, configuration
    with instance(pg_bin) as (directory, env):
        v = Verification(pg_bin, directory, env)
        v.init()
        v.init('check')
        csv, config, training = directory / 'sample.csv', directory / 'import.json', directory / 'training.json'
        write_csv(csv, [row(text='SELECT 1', message='duration: 2 ms'),
                        row(text='SELECT 1', message='duration: 3 ms')])
        config.write_text(json.dumps(configuration(config, [csv])))
        training.write_text(json.dumps(dict(version=1, clusters=['C1'], window=dict(cutoff_date='2026-07-31'))))
        env['SQL_APM_DSN'] = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        calls = [
            ['full', '--config', str(config), '--source', 'S1', '--batch', 'B1',
             '--training-config', str(training), '--workers', '1'],
            ['rebuild', '--cluster', 'C1', '--training-config', str(training), '--cutoff-date', '2026-07-31'],
            ['status', '--cluster', 'C1'], ['history', '--cluster', 'C1'],
        ]
        results = []
        attempts = None
        for command in calls:
            result = subprocess.run([sys.executable, '-m', 'sql_apm', *command], cwd=app, env=env,
                                    text=True, capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError('smoke CLI failed: ' + command[0] + '\n' + result.stdout + result.stderr)
            payload = json.loads(result.stdout.splitlines()[-1])
            results.append(payload)
            if command[0] in ('full', 'rebuild'):
                assert payload['state'] == 'succeeded' and payload['publication']['result'] == 'published'
                count = v.sql('SELECT count(*) FROM import_attempt')
                if attempts is not None:
                    assert count == attempts, 'rebuild imported again'
                attempts = count
        assert results[2]['current']['build_id'] == results[1]['build']['build_id']
        assert len(results[3]['versions']) == 2
        print(json.dumps(dict(passed=True, commands=['bootstrap', 'schema', 'check', 'full', 'rebuild', 'status', 'history'],
                              versions=2, import_attempts=int(attempts))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    parser.add_argument('check', choices=['unit', 'database', 'publication', 'smoke'])
    args = parser.parse_args()
    app = setup(args.app_root)
    if args.check == 'unit':
        return unit()
    if args.check == 'smoke':
        smoke(app, args.pg_bin)
    else:
        name = 'verify.py' if args.check == 'database' else 'verify_publication.py'
        path = RESOURCES / 'scripts/db' / name
        sys.argv = [str(path), '--pg-bin', str(args.pg_bin)]
        runpy.run_path(str(path), run_name='__main__')
    return 0


if __name__ == '__main__':
    sys.exit(main())
