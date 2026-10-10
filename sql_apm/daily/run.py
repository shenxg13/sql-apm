"""One daily run. For each cluster in turn: import the marked days oldest first,
build once when due, clean expired version results, delete raw files past their time.

Every step calls the function its manual command calls, so the rules stay those
commands' rules. The run adds only the choice of what to do and a record of it.
"""
from datetime import date, timedelta
import os
from pathlib import Path
import stat
import time

from sql_apm.baseline import workflow
from sql_apm.daily import inbox
from sql_apm.ingestion.config import IngestionError, manifest
from sql_apm.ingestion.importer import Importer
from sql_apm.storage.cleanup import CleanupStore, preview
from sql_apm.storage.daily import DailyStore
from sql_apm.storage.ingestion import connect
from sql_apm.storage.statistics import StatisticsError
from sql_apm.training.config import TrainingError, load_config as load_training, load_retention

BUSY = 'cluster_busy'


class Busy(Exception):
    """Another task holds the cluster: leave it for the next run."""


def _blank():
    return dict(state='done', reason=None, failed=False, newest_imported=None,
                build_state='not_reached', build_reason=None, cutoff_date=None, build_id=None, publication_id=None,
                cleanup_state='not_reached', cleanup_reason=None, months_cleaned=0, months_pending=0, released_bytes=0,
                raw_state='not_reached', raw_reason=None, raw_days=0, raw_files=0, raw_bytes=0, stage_seconds={})


class DailyRun:
    def __init__(self, dsn, schema, config, started_by='manual', progress=None, today=None, fault=None):
        self.dsn, self.schema, self.config, self.started_by = dsn, schema, config, started_by
        self.progress = progress or (lambda **row: None)
        # "Today" is the server's local date; a marker is honoured only for earlier days.
        self.today = today or date.today()
        self.fault = fault or (lambda point, scope=None: None)
        self.store = self.run_id = None

    def execute(self):
        """Exit code of the command: 0 nothing failed, 1 a failure or a skipped cluster."""
        db = connect(self.dsn, self.schema)
        try:
            self.store = DailyStore(db)
            if not self.store.acquire():
                self.progress(state='rejected', reason='daily_run_active')
                return 1
            self.run_id = self.store.begin(self.started_by, self.today, self.config['stale_after_hours'],
                                           self.config['clusters'])
            self.progress(phase='run_started', run_id=self.run_id, started_by=self.started_by,
                          local_date=self.today.isoformat(), clusters=self.config['clusters'])
            self.fault('run_started')
            failed = False
            try:
                for scope in self.config['clusters']:
                    failed = self._cluster(scope) or failed
            except KeyboardInterrupt:
                self.store.finish(self.run_id, 'aborted', None, 'operator_interrupt')
                raise
            self.store.finish(self.run_id, 'finished', failed, None)
            self.progress(phase='run_finished', run_id=self.run_id, state='failed' if failed else 'succeeded')
            return 1 if failed else 0
        finally:
            db.close()

    def _sources(self, scope):
        return [(source_id, item) for source_id, item in self.config['sources'].items() if item['cluster'] == scope]

    def _cluster(self, scope):
        record, problems = _blank(), []
        self.store.cluster_started(self.run_id, scope)
        self.progress(phase='cluster_started', run_id=self.run_id, cluster=scope)
        self.fault('cluster_started', scope)
        try:
            try:
                self._import(scope, record, problems)
                self._build(scope, record, problems)
                self._cleanup(scope, record, problems)
                self._raw_files(scope, record)
            except Busy:
                record.update(state='skipped', reason=BUSY, failed=True)
                problems.append(dict(kind='cluster_skipped', reason=BUSY))
            except Exception:
                # One cluster's surprise must not cost the others their turn.
                record.update(reason='daily_cluster_failed', failed=True)
        except KeyboardInterrupt:
            record.update(state='aborted', reason='operator_interrupt')
            self.store.cluster_finished(self.run_id, scope, record, [])
            raise
        self.store.cluster_finished(self.run_id, scope, record, problems)
        self.progress(phase='cluster_finished', run_id=self.run_id, cluster=scope, state=record['state'],
                      reason=record['reason'], failed=record['failed'],
                      newest_imported=record['newest_imported'] and record['newest_imported'].isoformat(),
                      build_state=record['build_state'], build_reason=record['build_reason'],
                      cutoff_date=record['cutoff_date'] and record['cutoff_date'].isoformat(),
                      cleanup_state=record['cleanup_state'], months_cleaned=record['months_cleaned'],
                      months_pending=record['months_pending'], raw_state=record['raw_state'],
                      raw_files=record['raw_files'], raw_bytes=record['raw_bytes'],
                      open_problems=sum(p['kind'] != 'nonconforming_file' for p in problems))
        return record['failed']

    def _import(self, scope, record, problems):
        started, plan = time.monotonic(), []
        try:
            for order, (source_id, item) in enumerate(self._sources(scope)):
                found = inbox.scan(item['directory'])
                for name in found['other'][:inbox.OTHER_LIMIT]:
                    problems.append(dict(kind='nonconforming_file', source_id=source_id, file_name=name, file_count=1))
                if len(found['other']) > inbox.OTHER_LIMIT:
                    problems.append(dict(kind='nonconforming_file', source_id=source_id,
                                         file_count=len(found['other']) - inbox.OTHER_LIMIT))
                states, recorded = self.store.day_states(source_id, sorted(set(found['days']) | found['markers']))
                pending, _, seen = inbox.classify(found, self.today, states, recorded)
                for kind, day, reason, count in seen:
                    problems.append(dict(kind=kind, source_id=source_id, log_date=day, reason=reason, file_count=count))
                    record['failed'] = record['failed'] or kind == 'day_failed'
                plan += [(day, order, source_id, item, found['days'][day]) for day in pending]
            for day, _, source_id, item, files in sorted(plan, key=lambda entry: entry[:2]):
                state, reason = self._day(scope, source_id, item, day, files)
                if state != 'complete':
                    record['failed'] = True
                    problems.append(dict(kind='day_failed', source_id=source_id, log_date=day, reason=reason,
                                         file_count=len(files)))
        finally:
            record['newest_imported'] = self.store.newest_imported(scope)
            record['stage_seconds']['import'] = round(time.monotonic() - started, 3)

    def _day(self, scope, source_id, item, day, files):
        entries = [dict(path=str(item['directory'] / name), origin_key=name, closed_and_copied=True)
                   for name, _ in files]
        ingestion = dict(manifest(source_id, item['source'], [day.isoformat()], entries),
                         batch_id=inbox.batch_id(source_id, day))
        started, importer = time.monotonic(), None
        state, reason, records = 'failed', 'ingestion_failed', 0
        try:
            importer = Importer(self.dsn, self.schema, self.config['workers'], self.progress)
            result = importer.run(ingestion)
            records = result['added_records']
            if result['state'] == 'complete':
                state, reason = 'complete', None
            else:
                state = 'conflict' if result['state'] == 'conflict' else 'failed'
                reason = next((f.get('reason') for f in result['files']
                               if f['state'] not in ('succeeded', 'duplicate_skipped') and f.get('reason')),
                              'batch_incomplete')
        except IngestionError as error:
            if str(error) == BUSY:
                raise Busy() from None
            reason = str(error)
        except KeyboardInterrupt:
            self.store.day(self.run_id, scope, source_id, day, 'interrupted', 'operator_interrupt', files, 0,
                           round(time.monotonic() - started, 3))
            raise
        except Exception:
            pass
        finally:
            if importer:
                importer.close()
        if state == 'complete':
            # The sizes that were read, for noticing a later change without reading the files again.
            files = [(name, os.stat(item['directory'] / name).st_size) for name, _ in files]
        self.store.day(self.run_id, scope, source_id, day, state, reason, files, records,
                       round(time.monotonic() - started, 3))
        self.progress(phase='day_finished', run_id=self.run_id, cluster=scope, source=source_id,
                      date=day.isoformat(), state=state, reason=reason, files=len(files), added_records=records)
        return state, reason

    def _build(self, scope, record, problems):
        interval, newest = self.config['intervals'][scope], record['newest_imported']
        current = self.store.current_cutoff(scope)
        if interval is None:
            record['build_state'] = 'disabled'
        elif newest is None:
            record['build_state'] = 'no_data'
        elif current is not None and (newest - current).days < interval:
            record['build_state'] = 'not_due'
        else:
            self.fault('before_build', scope)
            started, state, reason = time.monotonic(), 'failed', 'workflow_failed'
            record['cutoff_date'] = newest
            try:
                training, months = load_training(self.config['training_config'], scope,
                                                 cutoff_date=newest.isoformat(), with_retention=True)
                result = workflow.run(self.dsn, self.schema, training, None, None, self.config['workers'],
                                      self.progress, months)
                publication = result['publication']
                record.update(build_id=result['build']['build_id'], publication_id=publication['publication_id'])
                record['stage_seconds']['rebuild_stages'] = result['stage_seconds']
                if result['state'] != 'failed' and publication['result'] in ('published', 'no_samples'):
                    state, reason = publication['result'], None
                else:
                    reason = publication['reason'] or reason
            except IngestionError as error:
                if str(error) == BUSY:
                    raise Busy() from None
                reason = str(error)
            except (TrainingError, StatisticsError) as error:
                reason = str(error)
            except Exception:
                pass
            finally:
                record['stage_seconds']['rebuild'] = round(time.monotonic() - started, 3)
            record.update(build_state=state, build_reason=reason)
            if state == 'failed':
                record['failed'] = True
                problems.append(dict(kind='build_not_succeeded', reason=reason))

    def _cleanup(self, scope, record, problems):
        if not self.config['cleanup']:
            record['cleanup_state'] = 'disabled'
            return
        self.fault('before_cleanup', scope)
        started, db, state, reason = time.monotonic(), None, 'nothing', None
        try:
            months = load_retention(self.config['training_config'], scope)
            db = connect(self.dsn, self.schema)
            try:
                due = [m for m in preview(db, scope, months)['months'] if m['state'] == 'expired' or m['groups_pending']]
            except IngestionError as error:
                if str(error) != 'unknown_cluster':
                    raise
                due = []
            if due:
                outcomes = [m for m in CleanupStore(db).execute(scope, months)['months']
                            if m['state'] not in ('retained', 'protected', 'already_cleaned')]
                # A lock that could not be had leaves the month for the next run; that is not a failure.
                waiting = [m for m in outcomes if m['state'] == 'lock_timeout'
                           or (m['state'] == 'failed' and m['reason'] == 'cleanup_groups_pending')]
                broken = [m for m in outcomes if m['state'] != 'succeeded' and m not in waiting]
                record.update(months_cleaned=sum(m['state'] == 'succeeded' for m in outcomes),
                              months_pending=len(waiting) + len(broken),
                              released_bytes=sum(m['released_bytes'] for m in outcomes))
                for month in waiting + broken:
                    problems.append(dict(kind='cleanup_pending', result_month=month['build_month'],
                                         reason=month['reason']))
                state = 'failed' if broken else 'pending' if waiting else 'cleaned'
                reason = broken[0]['reason'] if broken else None
        except IngestionError as error:
            if str(error) == BUSY:
                raise Busy() from None
            state, reason = 'failed', str(error)
        except TrainingError as error:
            state, reason = 'failed', str(error)
        except Exception:
            state, reason = 'failed', 'cleanup_failed'
        finally:
            if db is not None:
                db.close()
            record['stage_seconds']['cleanup'] = round(time.monotonic() - started, 3)
        record.update(cleanup_state=state, cleanup_reason=reason)
        record['failed'] = record['failed'] or state == 'failed'

    def _raw_files(self, scope, record):
        """Delete the files and marker of days imported whole, unchanged since, and old enough."""
        if self.config['raw_days'] is None:
            record['raw_state'] = 'disabled'
            return
        self.fault('before_raw_files', scope)
        started, limit = time.monotonic(), self.today - timedelta(days=self.config['raw_days'])
        state, reason = 'nothing', None
        try:
            for source_id, item in self._sources(scope):
                found = inbox.scan(item['directory'])
                due = sorted(day for day in found['markers'] if day < limit)
                states, recorded = self.store.day_states(source_id, due)
                narrowed = dict(days={day: found['days'].get(day, []) for day in due}, markers=set(due))
                for day in inbox.classify(narrowed, self.today, states, recorded)[1]:
                    for name, size in narrowed['days'][day]:
                        self._unlink(item['directory'], name)
                        record['raw_files'] += 1
                        record['raw_bytes'] += size
                    self._unlink(item['directory'], inbox.marker_name(day))
                    record['raw_days'] += 1
                    state = 'deleted'
        except OSError:
            state, reason = 'failed', 'raw_delete_failed'
            record['failed'] = True
        record.update(raw_state=state, raw_reason=reason)
        record['stage_seconds']['raw_files'] = round(time.monotonic() - started, 3)

    @staticmethod
    def _unlink(directory, name):
        # Resolve first: only a regular file that really lies in the receiving directory goes.
        path = directory / name
        if Path(os.path.realpath(path)).parent != directory or not stat.S_ISREG(os.lstat(path).st_mode):
            raise OSError('outside_receiving_directory')
        os.unlink(path)
