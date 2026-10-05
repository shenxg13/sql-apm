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
from verify import instance, Verification, run
from ingestion.test_reader import row, configuration, write_csv
from sql_apm.ingestion.config import load_config, IngestionError, identity
from sql_apm.ingestion.importer import Importer


def timeout_worker(connection):
    import time
    connection.send('ready')
    connection.recv_bytes()
    time.sleep(30)


def failed_worker(connection):
    import os
    connection.send('ready')
    connection.recv_bytes()
    os._exit(1)


def returned_failure_worker(connection):
    from sql_apm.ingestion.normalizing import failure
    connection.send('ready')
    connection.recv_bytes()
    connection.send(failure('normalization_worker_failed'))


def failed_send(raw):
    raise BrokenPipeError()


def failed_start_worker(connection):
    connection.close()


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
            v.require(v.sql("SELECT count(*) FROM mpp_sql_text WHERE text='SELECT $1;'") == '1', 'exact original reused and native parameter preserved')
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
            v.require(v.sql("SELECT count(*) FROM mpp_sql_text WHERE text='SELECT 8'") == '1', 'SQL metadata survives rollback and is reused exactly')
            v.require(v.sql("SELECT count(DISTINCT s.sql_id)||':'||count(DISTINCT f.value) FROM mpp_sql_text s JOIN mpp_fingerprint f USING(sql_id) WHERE s.text IN ('SELECT 7','SELECT 8')") == '2:1', 'same structure retains distinct original parameter values')
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
                from sql_apm.storage.tasks import Task
                owner.task = Task(owner.db, locked_config['scope_id'], 'import_only').__enter__()
                owner.register(locked_config)
                try:
                    competitor.run(locked_config)
                except IngestionError as error:
                    v.require(str(error) == 'cluster_busy', 'same cluster immediately rejects concurrent owner')
                else:
                    raise AssertionError('concurrent owner admitted')
                v.require(v.sql("SELECT state FROM task WHERE task_id='" + owner.task.task_id + "'") == 'running', 'busy rejection preserves active owner')
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
            partial = root / 'partial.csv'
            write_csv(partial, [row(text='SELECT 1; BOGUS 2;')])
            partial_result = ingest([partial], 'PARTIAL')
            v.require(partial_result['added_occurrences'] == 1 and v.sql("SELECT count(*) FROM mpp_occurrence WHERE request_shape='batch' AND sql_state='uncertain' AND sql_id IS NULL AND outcome='success'") == '1', 'closed partially unsupported batch retains one uncertain whole request')
            # R2-F001: persistent accepted-input failures isolate records; a single
            # failure retries in a fresh process. Infrastructure failures still roll
            # back even after a COPY of 2,000 events (R1-F001 recovery invariant).
            from unittest.mock import patch
            from sql_apm.ingestion.normalizing import ParserWorker, worker as real_worker
            original_start = ParserWorker.start
            scenarios = [(kind, mode) for kind in ('timeout', 'failed', 'returned')
                         for mode in ('transient', 'persistent')]
            scenarios += [('start_failed', 'infrastructure'), ('send_failed', 'infrastructure')]
            for suffix, mode in scenarios:
                batch = 'WORKER_' + suffix + '_' + mode
                candidate = root / (batch + '.csv')
                write_csv(candidate, [row(text="SELECT 'before_" + batch + "'") for _ in range(2000)] +
                          [row(text="SELECT 'after_" + batch + "'")])
                cfg.write_text(json.dumps(configuration(cfg, [candidate], batch)))
                importer = Importer(dsn, workers=1, progress=lambda **kw: None)
                starts = []
                target = dict(timeout=timeout_worker, failed=failed_worker,
                              returned=returned_failure_worker, start_failed=failed_start_worker,
                              send_failed=real_worker)[suffix]
                def controlled_start(child):
                    selected = real_worker if mode == 'transient' and starts else target
                    with patch('sql_apm.ingestion.normalizing.worker', selected):
                        original_start(child)
                    starts.append(child.process.pid)
                    if suffix == 'send_failed':
                        child.connection.send_bytes = failed_send
                replacement = patch.object(ParserWorker, 'start', controlled_start)
                def break_worker(number):
                    if number == 2000:
                        importer.pool.close()
                        importer.pool.timeout = 0.05
                        replacement.start()
                importer.fault = break_worker
                before_events = v.sql('SELECT count(*) FROM mpp_occurrence')
                before_records = v.sql('SELECT count(*) FROM evidence_record')
                try:
                    result = importer.run(load_config(cfg, 'S1', batch))
                    cached = importer.writer.cache.get(("SELECT 'after_" + batch + "'").encode())
                finally:
                    replacement.stop()
                    importer.close()
                reason = ('normalization_timeout' if suffix == 'timeout' else
                          'normalization_worker_start_failed' if suffix == 'start_failed' else
                          'normalization_worker_failed')
                fid = result['files'][0]['file_id']
                if mode == 'infrastructure':
                    v.require(result['state'] == 'failed' and result['files'][0]['reason'] == reason and
                              v.sql("SELECT state FROM import_batch WHERE batch_id='" + batch + "'") == 'failed', batch + ' fails file and batch with fixed reason')
                    v.require(v.sql("SELECT state FROM import_attempt WHERE batch_id='" + batch + "'") == 'failed' and
                              v.sql("SELECT count(*) FROM problem WHERE batch_id='" + batch + "' AND code='" + reason + "' AND effect='fail_file'") == '1', batch + ' persists failed attempt and problem')
                    v.require(v.sql('SELECT count(*) FROM mpp_occurrence') == before_events and
                              v.sql('SELECT count(*) FROM evidence_record') == before_records and
                              v.sql("SELECT count(*) FROM analysis_file WHERE file_id='" + fid + "'") == '0', batch + ' rolls back copied events and evidence')
                    v.require(ingest([candidate], batch)['added_occurrences'] == 2001 and
                              v.sql("SELECT count(*) FROM mpp_occurrence WHERE anchor_ref LIKE '" + fid + ":%' AND sql_state='complete' AND sql_id IS NOT NULL") == '2001', batch + ' retry produces reliable events')
                else:
                    v.require(result['state'] == 'complete' and result['files'][0]['state'] == 'succeeded' and
                              result['added_occurrences'] == 2001 and
                              v.sql("SELECT state FROM import_batch WHERE batch_id='" + batch + "'") == 'complete', batch + ' completes with every event retained')
                    v.require(len(starts) == 2 and len(set(starts)) == 2, batch + ' uses exactly two fresh processes')
                    reliable = '2001' if mode == 'transient' else '2000'
                    v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE anchor_ref LIKE '" + fid + ":%' AND sql_state='complete' AND sql_id IS NOT NULL") == reliable, batch + ' preserves reliable events')
                    if mode == 'persistent':
                        v.require(v.sql("SELECT count(*) FROM problem WHERE batch_id='" + batch + "' AND code='fingerprint_" + reason + "' AND effect='isolate_record'") == '1', batch + ' records isolated fingerprint problem')
                        v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE anchor_ref LIKE '" + fid + ":%' AND sql_state='uncertain' AND sql_id IS NULL AND outcome='success'") == '1', batch + ' preserves actual outcome on isolated record')
                        v.require(cached['fingerprint']['state'] == 'normalization_failed' and
                                  cached['sql_id'] is None and cached['approximate_id'] is None, batch + ' never caches failure as reliable or approximate')
                    else:
                        v.require(v.sql("SELECT count(*) FROM problem WHERE batch_id='" + batch + "' AND code LIKE 'fingerprint_normalization_%'") == '0' and
                                  cached['fingerprint']['state'] == 'reliable', batch + ' recovery has no permanently downgraded record')
                v.require(ingest([candidate], batch)['added_occurrences'] == 0, batch + ' successful file then skips')
            # Real deterministic parser failure below the input-size cap, together
            # with ordinary records, without any worker mock or shortened timeout.
            deterministic = root / 'deterministic.csv'
            expression = 'SELECT ' + '1+' * 100000 + '1'
            write_csv(deterministic, [row(text="SELECT 'ordinary_deterministic'") for _ in range(50)] +
                      [row(text=expression)])
            result = ingest([deterministic], 'DETERMINISTIC')
            fid = result['files'][0]['file_id']
            v.require(len(expression.encode()) == 200008 and result['state'] == 'complete' and
                      result['files'][0]['state'] == 'succeeded' and result['added_occurrences'] == 51 and
                      v.sql("SELECT state FROM import_batch WHERE batch_id='DETERMINISTIC'") == 'complete', 'deterministic input completes file and batch with all 51 events')
            v.require(v.sql("SELECT count(*) FROM mpp_occurrence WHERE anchor_ref LIKE '" + fid + ":%' AND sql_state='complete' AND sql_id IS NOT NULL") == '50', 'deterministic input leaves all 50 ordinary records reliable')
            v.require(v.sql("SELECT count(*) FROM problem WHERE batch_id='DETERMINISTIC' AND code='fingerprint_normalization_worker_failed' AND effect='isolate_record'") == '1' and
                      v.sql("SELECT count(*) FROM mpp_occurrence WHERE anchor_ref LIKE '" + fid + ":%' AND sql_state='uncertain' AND sql_id IS NULL AND outcome='success'") == '1', 'deterministic failure is isolated with fixed reason and actual outcome')
            v.require(v.sql("SELECT octet_length(observed->>'24') FROM evidence_record WHERE file_id='" + fid + "' AND record_no=51") == '200008', 'deterministic failed SQL preserved in full evidence')
            v.require(ingest([deterministic], 'DETERMINISTIC')['added_occurrences'] == 0, 'deterministic completed file skips without normalizing anew')
            # R1-F002: protect succeeded and duplicate_skipped members before writes.
            for preexisting, initially_skipped in [(False, False), (True, False), (False, True)]:
                batch = 'MEMBER_' + str(int(preexisting)) + str(int(initially_skipped))
                candidate = root / (batch + '.csv')
                original_rows = [row(text="SELECT 'old_" + batch + "'")]
                replacement_rows = [row(text="SELECT 'new_" + batch + "'")]
                if initially_skipped:
                    original_elsewhere = root / (batch + '_original.csv')
                    write_csv(original_elsewhere, original_rows)
                    ingest([original_elsewhere], batch + '_original')
                write_csv(candidate, original_rows)
                original_result = ingest([candidate], batch)
                old_fid = original_result['files'][0]['file_id']
                entries_before = v.sql("SELECT file_id||':'||final_attempt_id FROM batch_entry WHERE batch_id='" + batch + "'")
                if preexisting:
                    elsewhere = root / (batch + '_elsewhere.csv')
                    write_csv(elsewhere, replacement_rows)
                    ingest([elsewhere], batch + '_elsewhere')
                before_events = v.sql('SELECT count(*) FROM mpp_occurrence')
                write_csv(candidate, replacement_rows)
                result = ingest([candidate], batch)
                new_fid = result['files'][0]['file_id']
                v.require(result['state'] == 'conflict' and result['files'][0]['reason'] == 'batch_member_changed' and
                          v.sql("SELECT state FROM import_batch WHERE batch_id='" + batch + "'") == 'conflict', batch + ' replacement held, including previously imported content')
                v.require(v.sql("SELECT file_id||':'||final_attempt_id FROM batch_entry WHERE batch_id='" + batch + "'") == entries_before and
                          v.sql('SELECT count(*) FROM mpp_occurrence') == before_events, batch + ' preserves final member and adds no events')
                evidence = json.loads(v.sql("SELECT reason FROM problem WHERE batch_id='" + batch + "' AND code='batch_member_changed' AND effect='block_publication'"))
                v.require(evidence['removed_file_ids'] == [old_fid] and evidence['current_file_ids'] == [new_fid], batch + ' records old and new content identities')
                v.require(ingest([candidate], batch)['state'] == 'conflict', batch + ' repeated conflict cannot become a successful skip')
                write_csv(candidate, original_rows)
                v.require(ingest([candidate], batch)['state'] == 'complete', batch + ' restoring original manifest admits safe repeat')
            # Collapse two successful paths to one content identity: no newly seen ID.
            left, right = root / 'collapse_a.csv', root / 'collapse_b.csv'
            write_csv(left, [row(text='SELECT 901')]); write_csv(right, [row(text='SELECT 902')])
            ingest([left, right], 'COLLAPSE')
            right.write_bytes(left.read_bytes())
            v.require(ingest([left, right], 'COLLAPSE')['state'] == 'conflict' and
                      v.sql("SELECT count(*) FROM batch_entry WHERE batch_id='COLLAPSE'") == '2', 'collapsed aliases cannot remove a successful member')
            # A later path can change after the batch's checksum phase. Its prior
            # successful final attempt must remain protected for the next retry.
            stable_a, stable_b = root / 'stable_a.csv', root / 'stable_b.csv'
            write_csv(stable_a, [row(text='SELECT 903')]); write_csv(stable_b, [row(text='SELECT 904')])
            ingest([stable_a, stable_b], 'STABLE')
            prior_entries = v.sql("SELECT file_id||':'||final_attempt_id FROM batch_entry WHERE batch_id='STABLE' ORDER BY file_id")
            stable_b_id = next(x.split(':AT:')[0] for x in prior_entries.splitlines() if x.split(':AT:')[0] !=
                               ingest([stable_a], 'STABLE_ALIAS')['files'][0]['file_id'])
            stable_b_attempt = v.sql("SELECT final_attempt_id FROM batch_entry WHERE batch_id='STABLE' AND file_id='" + stable_b_id + "'")
            cfg.write_text(json.dumps(configuration(cfg, [stable_a, stable_b], 'STABLE')))
            def change_after_preflight(**progress):
                if progress.get('phase') == 'file_finished' and progress['index'] == 1:
                    write_csv(stable_b, [row(text='SELECT 905')])
            active = Importer(dsn, workers=1, progress=change_after_preflight)
            try:
                changed = active.run(load_config(cfg, 'S1', 'STABLE'))
            finally:
                active.close()
            v.require(changed['state'] == 'failed' and changed['files'][1]['reason'] == 'file_changed_during_read' and
                      v.sql("SELECT final_attempt_id FROM batch_entry WHERE batch_id='STABLE' AND file_id='" + stable_b_id + "'") == stable_b_attempt,
                      'checksum-to-use mutation fails without replacing a successful final attempt')
            v.require(ingest([stable_a, stable_b], 'STABLE')['state'] == 'conflict', 'retry still detects the previously successful changed member')
            # R1-F003: reject every source interpretation version drift, without mutation.
            from sql_apm.ingestion.mpp.reader import PARSER_VERSION
            analysis_id = 'A:' + identity('B1')
            v.require(v.sql("SELECT parser_version FROM analysis WHERE analysis_id='" + analysis_id + "'") == PARSER_VERSION and
                      v.sql("SELECT count(*) FROM mpp_normalization WHERE parser_version='mpp-adapter/9'") == '1', 'source parser and SQL parser have distinct version identities')
            versions = ('mapping_version', 'parser_version', 'association_version')
            for column in versions:
                original = v.sql("SELECT " + column + " FROM analysis WHERE analysis_id='" + analysis_id + "'")
                tasks_before = v.sql('SELECT count(*) FROM task')
                v.sql("UPDATE analysis SET " + column + "='synthetic-drift' WHERE analysis_id='" + analysis_id + "'")
                try:
                    ingest([path], 'B1')
                except IngestionError as error:
                    v.require(str(error) == 'analysis_version_mismatch' and int(v.sql('SELECT count(*) FROM task')) == int(tasks_before)+1 and
                              v.sql("SELECT " + column + " FROM analysis WHERE analysis_id='" + analysis_id + "'") == 'synthetic-drift', column + ' mismatch rejects without rewriting old analysis')
                else:
                    raise AssertionError('analysis version drift admitted')
                finally:
                    v.sql("UPDATE analysis SET " + column + "='" + original + "' WHERE analysis_id='" + analysis_id + "'")
            v.require(ingest([path], 'B1')['added_occurrences'] == 0, 'matching Analysis versions admit duplicate retry')
            # A legacy or upgraded source version cannot absorb a repaired file.
            drift_file = root / 'drift.csv'
            drift_file.write_bytes(b'"unterminated')
            ingest([path, drift_file], 'VERSION_RETRY')
            drift_analysis = 'A:' + identity('VERSION_RETRY')
            write_csv(drift_file, [row(text='SELECT 906')])
            attempts_before = v.sql("SELECT count(*) FROM import_attempt WHERE batch_id='VERSION_RETRY'")
            v.sql("UPDATE analysis SET parser_version='mpp-adapter/9' WHERE analysis_id='" + drift_analysis + "'")
            try:
                ingest([path, drift_file], 'VERSION_RETRY')
            except IngestionError as error:
                v.require(str(error) == 'analysis_version_mismatch' and
                          v.sql("SELECT count(*) FROM import_attempt WHERE batch_id='VERSION_RETRY'") == attempts_before,
                          'legacy Analysis rejects repaired input before new attempts or events')
            else:
                raise AssertionError('legacy analysis absorbed new interpretation')
            v.sql("UPDATE analysis SET parser_version='" + PARSER_VERSION + "' WHERE analysis_id='" + drift_analysis + "'")
            v.require(ingest([path, drift_file], 'VERSION_RETRY')['state'] == 'complete', 'matching version still permits failed-member repair')
            # Exercise the driver after the real two-step legacy migration.
            import hashlib
            names = ('ingest_upgrade',) * 3
            v.init('bootstrap', names=names)
            def upgrade_sql(statement):
                return run([pg_bin / 'psql', '-X', '-w', '-Atq', '-v', 'ON_ERROR_STOP=1',
                            '-h', directory / 'socket', '-p', '55473', '-U', names[2], '-d', names[0]],
                           env, 'SET search_path=ingest_upgrade,pg_catalog;\n' + statement).stdout.strip()
            legacy = (ROOT / 'sql_apm/storage/versions/1.0.0.sql').read_text()
            upgrade_sql('CREATE SCHEMA ingest_upgrade;\n' + legacy +
                        "INSERT INTO schema_version(version,script_sha256) VALUES ('1.0.0','" + hashlib.sha256(legacy.encode()).hexdigest() + "')")
            v.init('upgrade', names=names)
            v.require(upgrade_sql("SELECT count(DISTINCT applied_at) FROM schema_version WHERE version IN ('1.1.0','1.2.0')") == '1', 'real consecutive migrations share receipt timestamp')
            migrated_dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=ingest_upgrade user=ingest_upgrade'
            upgraded = Importer(migrated_dsn, schema='ingest_upgrade', workers=1, progress=lambda **kw: None)
            cfg.write_text(json.dumps(configuration(cfg, [path], 'UPGRADED')))
            try:
                v.require(upgraded.run(load_config(cfg, 'S1', 'UPGRADED'))['state'] == 'complete', 'product import succeeds after real 1.0.0 to 1.7.0 migration')
            finally:
                upgraded.close()
            upgrade_sql("INSERT INTO schema_version(version,script_sha256) VALUES ('9.0.0',repeat('0',64))")
            from sql_apm.storage.ingestion import connect
            try:
                connection = connect(migrated_dsn, 'ingest_upgrade')
            except IngestionError as error:
                v.require(str(error) == 'schema_1_7_0_required', 'unknown future receipt rejected')
            else:
                connection.close()
                raise AssertionError('unknown version admitted')
            print('INGESTION CHECKS:', v.completed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
