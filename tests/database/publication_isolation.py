"""Current-version parser isolation counts, using real synthetic imports."""
import json
import os
import subprocess
import sys
from unittest.mock import patch

from ingestion.test_reader import configuration, row, write_csv
from sql_apm.baseline.workflow import run
from sql_apm.ingestion.config import load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.ingestion.normalizing import NormalizingPool, failure
from sql_apm.storage.ingestion import connect
from sql_apm.storage.publication import version_status
from sql_apm.training.config import validate


def verify_isolation(v, dsn, root, app_root):
    keys = ('fingerprint_normalization_timeout', 'fingerprint_normalization_worker_failed')
    db = connect(dsn, 'sql_apm')
    try:
        assert all(version_status(db, 'C1')['current'][key] == 0 for key in keys)
        assert version_status(db, 'absent')['current'] is None
        path = root / 'isolated.csv'
        write_csv(path, [row(text='SELECT 123', message='duration: 2 ms'),
                         row(text="SELECT 'timeout_fixture'", message='duration: 2 ms'),
                         row(text="SELECT 'timeout_fixture'", message='duration: 3 ms'),
                         row(text="SELECT 'worker_fixture'", message='duration: 4 ms')])
        cfg = root / 'isolated.json'
        doc = configuration(cfg, [path], 'ISOLATED')
        doc['clusters'] = ['ISOLATION']
        doc['sources']['ISOLATION'] = doc['sources'].pop('S1')
        doc['sources']['ISOLATION']['cluster'] = 'ISOLATION'
        doc['batches']['ISOLATED']['source'] = 'ISOLATION'
        cfg.write_text(json.dumps(doc))
        original = NormalizingPool.map

        def simulate(pool, inputs):
            results = original(pool, inputs)
            for index, raw in enumerate(inputs):
                if raw == b"SELECT 'timeout_fixture'":
                    results[index] = failure('normalization_timeout')
                elif raw == b"SELECT 'worker_fixture'":
                    results[index] = failure('normalization_worker_failed')
            return results

        config = validate(dict(version=1, clusters=['ISOLATION'], window=dict(cutoff_date='2026-07-31')), 'ISOLATION')
        with patch.object(NormalizingPool, 'map', simulate):
            value = run(dsn, 'sql_apm', config, load_config(cfg, 'ISOLATION', 'ISOLATED'), workers=1)
        assert value['publication']['result'] == 'published'
        current = version_status(db, 'ISOLATION')['current']
        assert [current[key] for key in keys] == [2, 1]
        assert all(type(current[key]) is int for key in keys)
        v.require(True, 'isolated records publish; status counts repeated timeout SQL twice and worker failure once')

        # A later import is deliberately outside the current frozen manifest.
        extra = root / 'later.csv'
        write_csv(extra, [row(text="SELECT 'timeout_fixture'", message='duration: 5 ms',
                             **{'0': '2026-07-26 10:00:00 CST'})])
        doc['batches']['LATER'] = dict(doc['batches']['ISOLATED'], files=[dict(
            path=str(extra), origin_key='later.csv', closed_and_copied=True)])
        cfg.write_text(json.dumps(doc))
        importer = Importer(dsn, workers=1)
        try:
            with patch.object(NormalizingPool, 'map', simulate):
                importer.run(load_config(cfg, 'ISOLATION', 'LATER'))
        finally:
            importer.close()
        assert version_status(db, 'ISOLATION')['current'] == current
        assert all(version_status(db, 'C1')['current'][key] == 0 for key in keys)
        result = subprocess.run([sys.executable, '-m', 'sql_apm', 'status', '--cluster', 'ISOLATION'],
                                cwd=app_root, env=dict(os.environ, SQL_APM_DSN=dsn),
                                capture_output=True, text=True, check=True, timeout=20)
        payload = json.loads(result.stdout)['current']
        assert [payload[key] for key in keys] == [2, 1]
        assert not any(secret in result.stdout for secret in (
            'synthetic_db', 'synthetic_user', 'SELECT', 'timeout_fixture', 'worker_fixture'))
        next_version = run(dsn, 'sql_apm', config)
        assert next_version['publication']['result'] == 'published'
        assert [version_status(db, 'ISOLATION')['current'][key] for key in keys] == [3, 1]
        v.require(True, 'status excludes later imports and other clusters, emits integer zeros/counts without source identities')
    finally:
        db.close()
