#!/usr/bin/env python3
"""Product importer acceptance on a private, disposable PostgreSQL 17 instance."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from verify import instance, Verification
from ingestion.test_reader import row, configuration, write_csv
from sql_apm.ingestion.config import load_config, IngestionError
from sql_apm.ingestion.importer import Importer


def verify(pg_bin):
    with instance(pg_bin) as (directory, env):
        v = Verification(pg_bin, directory, env)
        v.init()
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, cfg = root / 'a.csv', root / 'config.json'
            rows = [row(), row(), row('2219', text=''), row('2224'), row('2603'), row('2608'),
                    row('2764', 'execute p: SELECT $1;'), row('2843'),
                    row('2764', 'execute fetch from p: SELECT $1;'), row('2843'), row('2843'),
                    row('1455'), row('334', **{'27': 'autostats.c'}),
                    row('1', 'ordinary error', **{'16': 'ERROR', '17': '42P01'}),
                    row('1', 'canceling statement due to user request', **{'16': 'ERROR', '17': '57014'}),
                    row('1', 'canceling statement due to statement timeout', **{'16': 'ERROR', '17': '57014'}),
                    row('1', 'authentication failed', text='', **{'16': 'FATAL'}),
                    row(text='SELECT 1; SELECT * FROM t WHERE id IN (1,'),
                    row(text='/*'), row(text='SELECT 1; SELECT 2;')]
            write_csv(path, rows)
            def ingest(files, batch, fault=None, mutate=None):
                document = configuration(cfg, files, batch)
                if mutate:
                    mutate(document)
                cfg.write_text(json.dumps(document))
                importer = Importer(dsn, workers=2, progress=lambda **kw: None, fault=fault)
                try:
                    return importer.run(load_config(cfg, 'S1', batch))
                finally:
                    importer.close()
            result = ingest([path], 'B1')
            v.require(result['state'] == 'complete', 'synthetic complete batch: ' + json.dumps(result))
            v.require(v.sql('SELECT count(*) FROM evidence_record') == '20', 'all CSV logical records retained')
            v.require(v.sql('SELECT count(*) FROM mpp_occurrence') == '15', 'actual events retained, outside timing excluded')
            v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE outcome IN ('failed','cancelled','timed_out') AND duration_ms IS NULL AND estimated_start_at IS NULL") == '3', 'failure/cancel/timeout preserve NULL times')
            v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE timing_type IN ('execute_first','execute_fetch')") == '2', 'first and fetch each consume one anchor')
            v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE timing_type IS NULL AND association_state='unpaired'") == '1', 'unpaired remains outside five timings')
            v.require(v.sql('SELECT count(DISTINCT state) FROM mpp_approximate_result') == '2', 'approximate available and unavailable stored')
            v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE sql_state='incomplete' AND sql_id IS NULL AND outcome='success'") == '2', 'incomplete SQL preserves successful event but no complete SQL')
            v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE request_shape='batch'") == '1', 'whole multi-statement request')
            v.require(v.sql('SELECT count(*) FROM mpp_decision') == '0', 'no training decision constructed')
            v.require(ingest([path], 'B1')['added_records'] == 0, 'same batch repeat adds zero')
            renamed = root / 'renamed.csv'
            renamed.write_bytes(path.read_bytes())
            v.require(ingest([renamed], 'RENAMED')['added_records'] == 0, 'renamed exact file skipped')
            bad = root / 'broken.csv'
            bad.write_bytes(b'"unterminated')
            v.require(ingest([path, bad], 'BAD')['state'] == 'failed', 'CSV boundary failure blocks batch')
            missing = root / 'absent.csv'
            v.require(ingest([missing], 'MISSING')['state'] == 'failed', 'missing manifest member blocks batch')
            write_csv(missing, [row(text='SELECT 7')])
            v.require(ingest([missing], 'MISSING')['state'] == 'complete', 'previously unreadable member can be retried')
            retry = root / 'retry.csv'
            write_csv(retry, [row(text='SELECT 8') for _ in range(2001)])
            before = v.sql('SELECT count(*) FROM mpp_occurrence')
            def fault(number):
                raise IngestionError('synthetic_interruption')
            v.require(ingest([retry], 'RETRY', fault)['state'] == 'failed', 'failure after COPY recorded')
            v.require(v.sql('SELECT count(*) FROM mpp_occurrence') == before, 'partial file data rolled back')
            v.require(ingest([retry], 'RETRY')['added_occurrences'] == 2001, 'unfinished file replayed exactly once')
            v.require(ingest([retry], 'RETRY')['added_occurrences'] == 0, 'successful retry then skipped')
            original = root / 'original.csv'
            write_csv(original, [row(text='SELECT 11', **{'0': '2026-07-23 01:00:00 CST'}),
                                 row(text='SELECT 12', **{'0': '2026-07-23 01:00:01 CST'})])
            v.require(ingest([original], 'ORIGINAL')['state'] == 'complete', 'edge baseline file')
            overlap = root / 'overlap.csv'
            overlap.write_bytes(original.read_bytes())
            with overlap.open('a') as file:
                import csv
                csv.writer(file).writerow(row(text='SELECT 13'))
            v.require(ingest([overlap], 'OVERLAP')['state'] == 'conflict', 'appended renamed file held on exact record edges')
            fixed = root / 'fixed.csv'
            write_csv(fixed, [row(text='SELECT 50')])
            def origin(doc):
                next(iter(doc['batches'].values()))['files'][0]['origin_key'] = 'declared-rotation-id'
            v.require(ingest([fixed], 'FIXED', mutate=origin)['state'] == 'complete', 'manual original identity')
            write_csv(fixed, [row(text='SELECT 51')])
            v.require(ingest([fixed], 'CHANGED', mutate=origin)['state'] == 'conflict', 'declared original identity changed')
            v.require(v.sql("SELECT count(*) FROM import_batch WHERE state='conflict'") == '2', 'conflicts persist as non-publishable batches')
            # A repaired CSV has a different content identity; stale failed entries
            # must not coexist with a claimed complete manifest.
            write_csv(bad, [row(text='SELECT 71')])
            v.require(ingest([path, bad], 'BAD')['state'] == 'complete', 'corrected CSV replaces failed manifest projection')
            v.require(v.sql("SELECT count(*) FROM batch_entry e JOIN import_attempt a ON a.attempt_id=e.final_attempt_id WHERE e.batch_id='BAD' AND a.state NOT IN ('succeeded','duplicate_skipped')") == '0', 'complete batch has no failed final entries')
            # Same cluster is rejected without changing the owner task or batch.
            cfg.write_text(json.dumps(configuration(cfg, [path], 'LOCK')))
            locked_config = load_config(cfg, 'S1', 'LOCK')
            owner = Importer(dsn, workers=1, progress=lambda **kw: None)
            competitor = Importer(dsn, workers=1, progress=lambda **kw: None)
            try:
                owner.register(locked_config)
                try:
                    competitor.run(locked_config)
                except IngestionError as error:
                    v.require(str(error) == 'cluster_busy', 'same cluster immediately rejects concurrent owner')
                else:
                    raise AssertionError('concurrent owner admitted')
                v.require(v.sql("SELECT state FROM task WHERE task_id='" + owner.task_id + "'") == 'running', 'busy rejection preserves active owner')
            finally:
                competitor.close()
                owner.close()
            v.require(ingest([path], 'LOCK')['state'] == 'complete', 'released cluster lock admits retry')
            # Real process death, not merely an exception in a transaction.
            import subprocess
            crash = root / 'crash.csv'
            write_csv(crash, [row(text='SELECT 81') for _ in range(2001)])
            cfg.write_text(json.dumps(configuration(cfg, [crash], 'CRASH')))
            helper = root / 'crash.py'
            helper.write_text("import os,signal,sys\n" +
                "sys.path.insert(0," + repr(str(ROOT)) + ")\n" +
                "from sql_apm.ingestion.importer import Importer\n" +
                "from sql_apm.ingestion.config import load_config\n" +
                "def die(number): os.kill(os.getpid(),signal.SIGKILL)\n" +
                "if __name__ == '__main__':\n" +
                " i=Importer(" + repr(dsn) + ",workers=1,progress=lambda **kw:None,fault=die)\n" +
                " i.run(load_config(" + repr(str(cfg)) + ",'S1','CRASH'))\n")
            before = v.sql('SELECT count(*) FROM mpp_occurrence')
            killed = subprocess.run([sys.executable, str(helper)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            v.require(killed.returncode == -9, 'SIGKILL terminates actual importer after COPY')
            v.require(v.sql('SELECT count(*) FROM mpp_occurrence') == before, 'process death rolls back uncommitted events')
            v.require(ingest([crash], 'CRASH')['added_occurrences'] == 2001, 'killed import safely retries without duplicates')
            v.require(v.sql("SELECT count(*) FROM import_attempt WHERE batch_id='CRASH' AND state='interrupted'") == '1', 'orphaned running attempt retained as interrupted')
            # Invalid bytes remain round-trippable approximate evidence.
            invalid = root / 'invalid.csv'
            write_csv(invalid, [row(text='SELECT INVALID_BYTE')])
            invalid.write_bytes(invalid.read_bytes().replace(b'INVALID_BYTE', b'\xff\x00'))
            v.require(ingest([invalid], 'INVALID')['state'] == 'complete', 'invalid SQL bytes isolate record without losing CSV boundaries')
            v.require(v.sql("SELECT count(*) FROM mpp_approximate_input WHERE raw_bytes=decode('53454c45435420ff00','hex')") == '1', 'invalid SQL bytes preserved exactly')
            v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE sql_state='invalid_encoding' AND outcome='success'") == '1', 'parser rejection never changes outcome to execution failure')
            # Reusing a batch cannot silently shorten a previously accepted manifest.
            try:
                ingest([path], 'BAD')
            except IngestionError as error:
                v.require(str(error) == 'batch_manifest_changed', 'frozen manifest cannot be shortened')
            else:
                raise AssertionError('changed manifest accepted')
            alias_a, alias_b = root / 'alias_a.csv', root / 'alias_b.csv'
            write_csv(alias_a, [row(text='SELECT 100')])
            alias_b.write_bytes(alias_a.read_bytes())
            v.require(ingest([alias_a, alias_b], 'ALIASES')['state'] == 'complete', 'same manifest aliases share content')
            write_csv(alias_b, [row(text='SELECT 101')])
            v.require(ingest([alias_a, alias_b], 'ALIASES')['state'] == 'complete', 'path reconciliation preserves other alias')
            v.require(v.sql("SELECT count(*) FROM batch_entry WHERE batch_id='ALIASES'") == '2', 'manifest final identities match both paths')
            origin_alias = root / 'origin_alias.csv'
            write_csv(origin_alias, [row(text='SELECT 50')])
            def alternate_origin(doc):
                next(iter(doc['batches'].values()))['files'][0]['origin_key'] = 'alternate-declared-origin'
            v.require(ingest([origin_alias], 'ORIGIN_ALIAS', mutate=alternate_origin)['added_records'] == 0, 'duplicate content retains an additional manual origin')
            origin_alias.write_bytes(alias_a.read_bytes())
            v.require(ingest([origin_alias], 'ORIGIN_ALIAS_CHANGE', mutate=alternate_origin)['state'] == 'conflict', 'origin change is held even when new content already exists elsewhere')
            print('INGESTION CHECKS:', v.completed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
