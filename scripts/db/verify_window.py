#!/usr/bin/env python3
"""Synthetic Issue #35 file bounds, selection, fallback and output checks."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from verify import instance, Verification
from ingestion.test_reader import row, write_csv, configuration
from sql_apm.ingestion.config import load_config
from sql_apm.ingestion.importer import Importer
from sql_apm.storage.ingestion import connect
from sql_apm.storage.training import TrainingStore
from sql_apm.storage.publication import version_status
from sql_apm.training.config import validate
from sql_apm.baseline.workflow import run


def verify(pg_bin):
    with instance(pg_bin) as (directory, env):
        v = Verification(pg_bin, directory, env); v.init()
        dsn = 'host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        db = connect(dsn, 'sql_apm')
        def make(batch, groups, scope='C1'):
            paths = []
            for i, rows in enumerate(groups):
                path = directory/(batch+'-'+str(i)+'.csv'); write_csv(path, rows); paths.append(path)
            cfg = directory/(batch+'.json'); doc = configuration(cfg, paths, batch)
            if scope != 'C1':
                doc['clusters'] = [scope]; doc['sources'] = {scope:dict(doc['sources']['S1'],cluster=scope)}
                doc['batches'][batch]['source'] = scope
            cfg.write_text(json.dumps(doc))
            return load_config(cfg, 'S1' if scope == 'C1' else scope, batch)
        def event(at, number='1', **fields):
            return row(text='SELECT '+number, message='duration: 0 ms', **dict({'0':at}, **fields))
        def ingest(config, fault=None):
            importer = Importer(dsn, workers=1, progress=lambda **e:None, fault=fault)
            try:return importer.run(config)
            finally:importer.close()
        def bounds(file):
            with db, db.cursor() as cur:
                cur.execute('SELECT first_log_at,last_log_at FROM source_file WHERE file_id=%s',(file,))
                return tuple(t.isoformat() if t else None for t in cur.fetchone())
        def selected(result):
            with db, db.cursor() as cur:
                cur.execute('SELECT batch_id FROM input_batch WHERE input_id=%s ORDER BY batch_id',
                            (result['snapshot']['input_id'],))
                return [r[0] for r in cur]
        def config(scope='C1', cutoff='2026-07-31'):
            return validate(dict(version=1,clusters=[scope],window=dict(days=7,cutoff_date=cutoff)),scope)
        try:
            times = make('TIMES', [[event('2026-07-26 12:00:00 CST'), event('invalid'),
                event('2026-07-25T16:00:00+00:00'), event('2026-07-29 00:00:00 CST',line='1455')]])
            imported = ingest(times); file = imported['files'][0]['file_id']
            expected = ('2026-07-26T00:00:00+08:00','2026-07-29T00:00:00+08:00')
            assert bounds(file) == expected
            assert ingest(times)['files'][0]['state'] == 'duplicate_skipped' and bounds(file) == expected
            unknown = make('UNKNOWN',[[event('bad','2'),event('2026-07-25 12:00:00','2')]])
            assert bounds(ingest(unknown)['files'][0]['file_id']) == (None,None)
            failed = make('FAILED',[[event('2026-07-30 12:00:00 CST','3')]])
            def fault(_): raise RuntimeError('synthetic')
            bad = ingest(failed,fault)
            assert bad['state']=='failed' and bounds(bad['files'][0]['file_id']) == (None,None)
            v.require(True,'all-record min/max, mixed timezone, invalid/naive timestamps, duplicate and atomic rollback')
            old = make('OLD',[[event('2026-07-01 12:00:00 CST','4')]])
            cross = make('CROSS',[[event('2026-07-24 12:00:00 CST','5'),event('2026-07-25 00:00:00 CST','6')]])
            future = make('FUTURE',[[event('2026-08-05 12:00:00 CST','7')]])
            mixed = make('MIXED',[[event('2026-07-01 12:00:00 CST','8')],[event('2026-07-26 12:00:00 CST','9')]])
            for item in (old,cross,future,mixed): assert ingest(item)['state']=='complete'
            progress = []
            full = run(dsn,'sql_apm',config(),times,workers=1,progress=lambda **e:progress.append(e))
            wanted = ['CROSS','FUTURE','MIXED','TIMES','UNKNOWN']
            assert selected(full) == wanted
            with db,db.cursor() as cur:
                cur.execute('SELECT count(*) FROM input_file WHERE input_id=%s',(full['snapshot']['input_id'],))
                assert cur.fetchone()[0] == 6
            again = run(dsn,'sql_apm',config(),progress=lambda **e:progress.append(e))
            assert selected(again) == wanted
            snapshots = [p for p in progress if p['phase']=='snapshot_finished']
            assert len(snapshots)==2
            for p in snapshots:
                assert {k:p[k] for k in ('completed_batches','selected_batches','excluded_batches','window_fallback')} == dict(
                    completed_batches=6,selected_batches=5,excluded_batches=1,window_fallback=False)
            text = json.dumps(progress,default=str)
            assert not any(secret in text for secret in ('SELECT','synthetic_db','synthetic_user'))
            v.require(True,'full/rebuild select inclusive start, future, unknown and whole mixed batches; exclude old/failed; counts private')
            training = TrainingStore(db=db)
            explicit = training.snapshot(config(),['OLD'])
            assert selected(dict(snapshot=explicit)) == ['OLD']
            v.require(True,'explicit training snapshot preserves the supplied old batch')
            shared = make('SHARED',[[event('2026-07-01 12:00:00 CST','4')],
                                   [event('2026-07-26 12:00:00 CST','11')]])
            duplicate = ingest(shared)
            shared_file = duplicate['files'][0]['file_id']
            assert duplicate['files'][0]['state'] == 'duplicate_skipped'
            assert bounds(shared_file) == ('2026-07-01T12:00:00+08:00',)*2
            reused = run(dsn,'sql_apm',config())
            assert 'OLD' not in selected(reused) and 'SHARED' in selected(reused)
            with db,db.cursor() as cur:
                cur.execute('''SELECT d.reason_codes FROM mpp_training_decisions(%s,%s) d
                    JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
                    JOIN evidence_record e ON e.record_id=o.anchor_ref WHERE e.file_id=%s''',
                    (reused['snapshot']['input_id'],reused['snapshot']['config_id'],shared_file))
                assert cur.fetchall() == [(['outside_window'],)]
            v.require(True,'cross-batch duplicate keeps bounds; selected whole batch retains shared old event with outside_window')
            recent = make('FALLBACK',[[event('2026-07-26 12:00:00 CST','10')]],scope='C2')
            current = run(dsn,'sql_apm',config('C2'),recent,workers=1)
            assert current['publication']['result']=='published'
            before = version_status(db,'C2')['current']
            progress = []
            empty = run(dsn,'sql_apm',config('C2','2027-07-31'),progress=lambda **e:progress.append(e))
            assert selected(empty)==['FALLBACK'] and empty['publication']['result']=='no_samples'
            assert version_status(db,'C2')['current']==before
            snapshot = next(p for p in progress if p['phase']=='snapshot_finished')
            assert snapshot['window_fallback'] and snapshot['completed_batches']==snapshot['selected_batches']==1
            assert snapshot['excluded_batches']==0
            v.require(True,'no candidate falls back to complete batches; no_samples retains current with explicit progress flag')
        finally: db.close()
        v.init('check')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
