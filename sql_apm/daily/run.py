"""One daily run. For each cluster in turn: import the marked days oldest first,
build once when due, clean expired version results, delete raw files past their time.

Every step calls the function its manual command calls, so the rules stay those
commands' rules. The run adds only the choice of what to do and a record of it.
"""
from datetime import date, timedelta
import hashlib
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
CHANGED = 'files_changed_after_import'


class Busy(Exception):
    """Another task holds the cluster: leave it for the next run."""


def _stamp(seen):
    """What tells one file, in one state, from any other: where it is, how long, when its content
    and when anything about it last changed. Writing to it, or putting another file under its name,
    changes at least one of these."""
    return (seen.st_dev, seen.st_ino, seen.st_size, seen.st_mtime_ns, seen.st_ctime_ns)


def read_proof(path):
    """SHA-256 of a file, and its stamp before and after the reading, all three taken through one
    open descriptor: they describe the very file that was read, whatever its name leads to later."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError('not_a_regular_file')
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest(), _stamp(opened), _stamp(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _blank():
    return dict(state='done', reason=None, failed=False, import_state='not_reached', newest_imported=None,
                build_state='not_reached', build_reason=None, cutoff_date=None, build_id=None, publication_id=None,
                cleanup_state='not_reached', cleanup_reason=None, months_cleaned=0, months_pending=0, released_bytes=0,
                raw_state='not_reached', raw_reason=None, raw_days=0, raw_files=0, raw_bytes=0, stage_seconds={})


class DailyRun:
    def __init__(self, dsn, schema, config, started_by='manual', progress=None, today=None, fault=None, interrupter=None):
        self.dsn, self.schema, self.config, self.started_by = dsn, schema, config, started_by
        self.progress = progress or (lambda **row: None)
        # "Today" is the server's local date; a marker is honoured only for earlier days.
        self.today = today or date.today()
        self.fault = fault or (lambda point, scope=None, name=None: None)
        self.interrupter = interrupter
        self.store = self.run_id = None

    def _stopping(self):
        """A stop signal has arrived: whatever a cancelled statement turned into, the run ends as aborted."""
        if self.interrupter is not None and self.interrupter.stop_requested():
            raise KeyboardInterrupt

    def execute(self):
        """Exit code of the command: 0 nothing failed, 1 a failure or a skipped cluster."""
        db = connect(self.dsn, self.schema)
        if self.interrupter is not None:
            self.interrupter.leave_alone(db)
        try:
            self.store = DailyStore(db)
            if not self.store.acquire():
                self.progress(state='rejected', reason='daily_run_active')
                return 1
            self.run_id = self.store.begin(self.started_by, self.today, self.config['stale_after_hours'],
                                           self.config['clusters'])
            self.progress(phase='run_started', run_id=self.run_id, started_by=self.started_by,
                          local_date=self.today.isoformat(), clusters=self.config['clusters'])
            failed = False
            try:
                self.fault('run_started')
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
        try:
            try:
                self.fault('cluster_started', scope)
                # Taken by someone else: nothing of this cluster is touched, whether or not there is work.
                if self.store.cluster_busy(scope):
                    raise Busy()
                self._import(scope, record, problems)
                self._build(scope, record, problems)
                self._cleanup(scope, record, problems)
                self._raw_files(scope, record, problems)
                self._stopping()
            except Busy:
                record.update(state='skipped', reason=BUSY, failed=True)
            except Exception:
                self._stopping()
                # One cluster's surprise must not cost the others their turn.
                record.update(reason='daily_cluster_failed', failed=True)
        except KeyboardInterrupt:
            record.update(state='aborted', reason='operator_interrupt')
            self.store.cluster_finished(self.run_id, scope, record)
            raise
        self.store.cluster_finished(self.run_id, scope, record)
        self.progress(phase='cluster_finished', run_id=self.run_id, cluster=scope, state=record['state'],
                      reason=record['reason'], failed=record['failed'],
                      newest_imported=record['newest_imported'] and record['newest_imported'].isoformat(),
                      build_state=record['build_state'], build_reason=record['build_reason'],
                      cutoff_date=record['cutoff_date'] and record['cutoff_date'].isoformat(),
                      cleanup_state=record['cleanup_state'], months_cleaned=record['months_cleaned'],
                      months_pending=record['months_pending'], raw_state=record['raw_state'],
                      raw_days=record['raw_days'], raw_files=record['raw_files'], raw_bytes=record['raw_bytes'],
                      open_problems=sum(p['kind'] != 'nonconforming_file' for p in problems))
        return record['failed']

    def _settle(self, scope, problems, source_id, days, found=()):
        """The run has looked at these days and reached a conclusion: that, and the problems found, are
        written down at once and together, not when the cluster is finished with."""
        problems.extend(found)
        self.store.examined(self.run_id, scope, source_id, days, found)

    def _unchanged(self, scope, source_id, item, day, files, prove=False):
        """Is an imported day still what was imported? Returns (verdict, what the batch holds).

        A file whose stamp is the one it had when it was last read and found to be the imported
        content is taken as untouched; anything else is read again and compared with the checksum
        the import stored. With `prove`, every file is read: that is asked before a deletion.
        A file is read through one descriptor, and the stamp kept for it is that of the file
        that was read; the name must still lead to that file when the reading is over.
        Files that a deletion of ours has already taken are not missed.
        """
        known, present = self.store.day_files(source_id, day), dict(files)
        if set(present) - set(known):
            return False, known                          # a file the batch never had
        missing = [name for name, fact in known.items() if name not in present and not fact['removed']]
        if missing and present and not any(fact['removing'] for fact in known.values()):
            return False, known                          # some taken away, some left, and not by us
        for name in sorted(present):
            fact, path = known[name], item['directory'] / name
            try:
                if not prove and not fact['removed'] and fact['stamp'] == _stamp(os.lstat(path)):
                    continue
                sha256, opened, read = read_proof(path)
                if sha256 != fact['sha256'] or opened != read or _stamp(os.lstat(path)) != read:
                    self.store.file_doubted(source_id, day, name)
                    return False, known
            except OSError:
                return False, known
            self.store.file_verified(self.run_id, scope, source_id, day, name, fact['file_id'], read)
            fact.update(stamp=read, removed=False)
            if prove:
                self.fault('file_proven', scope, name)
        return True, known

    def _import(self, scope, record, problems):
        """Every conclusion about a day is written down as it is reached: the problem if there is one,
        and that the day has been looked at. A run that gets no further has still said that much."""
        started, plan = time.monotonic(), []
        record['import_state'] = 'incomplete'
        self.store.cluster_step(self.run_id, scope, record, 'import_state')
        try:
            for order, (source_id, item) in enumerate(self._sources(scope)):
                found = inbox.scan(item['directory'])
                other = [dict(kind='nonconforming_file', source_id=source_id, file_name=name, file_count=1)
                         for name in found['other'][:inbox.OTHER_LIMIT]]
                if len(found['other']) > inbox.OTHER_LIMIT:
                    other.append(dict(kind='nonconforming_file', source_id=source_id,
                                      file_count=len(found['other']) - inbox.OTHER_LIMIT))
                states = self.store.day_states(source_id, sorted(set(found['days']) | found['markers']))
                pending, imported, seen = inbox.classify(found, self.today, states)
                self._settle(scope, problems, source_id, [day for _, day, _, _ in seen], other + [
                    dict(kind=kind, source_id=source_id, log_date=day, reason=reason, file_count=count)
                    for kind, day, reason, count in seen])
                for day in imported:
                    files = found['days'].get(day, [])
                    changed = [] if self._unchanged(scope, source_id, item, day, files)[0] else [
                        dict(kind='day_failed', source_id=source_id, log_date=day, reason=CHANGED, file_count=len(files))]
                    self._settle(scope, problems, source_id, [day], changed)
                    record['failed'] = record['failed'] or bool(changed)
                    self._stopping()
                plan += [(day, order, source_id, item, found['days'][day]) for day in pending]
            for day, _, source_id, item, files in sorted(plan, key=lambda entry: entry[:2]):
                self.fault('before_day', scope, day.isoformat())
                state, reason = self._day(scope, source_id, item, day, files)    # writes its own conclusion
                if state != 'complete':
                    record['failed'] = True
                    problems.append(dict(kind='day_failed', source_id=source_id, log_date=day, reason=reason,
                                         file_count=len(files)))
            record['import_state'] = 'done'
        finally:
            record['newest_imported'] = self.store.newest_imported(scope)
            record['stage_seconds']['import'] = round(time.monotonic() - started, 3)
            self.store.cluster_step(self.run_id, scope, record, 'import_state', 'newest_imported', 'failed')

    def _day(self, scope, source_id, item, day, files):
        entries = [dict(path=str(item['directory'] / name), origin_key=name, closed_and_copied=True)
                   for name, _ in files]
        ingestion = dict(manifest(source_id, item['source'], [day.isoformat()], entries),
                         batch_id=inbox.batch_id(source_id, day))
        started, importer = time.monotonic(), None
        state, reason, records = 'failed', 'ingestion_failed', 0
        try:
            try:
                importer = Importer(self.dsn, self.schema, self.config['workers'], self.progress)
                result = importer.run(ingestion)
                self._stopping()
                records = result['added_records']
                if result['state'] == 'complete':
                    state, reason = 'complete', None
                else:
                    state = 'conflict' if result['state'] == 'conflict' else 'failed'
                    reason = next((f.get('reason') for f in result['files']
                                   if f['state'] not in ('succeeded', 'duplicate_skipped') and f.get('reason')),
                                  'batch_incomplete')
            except IngestionError as error:
                self._stopping()
                if str(error) == BUSY:
                    raise Busy() from None
                reason = str(error)
            except Exception:
                self._stopping()
            finally:
                if importer:
                    importer.close()
            if state == 'complete':
                # Read each file once more against what was stored, and keep what it looks like now:
                # from here on an untouched file is recognised without being read.
                self.fault('day_imported', scope, day.isoformat())
                if not self._unchanged(scope, source_id, item, day, files, prove=True)[0]:
                    state, reason = 'conflict', CHANGED
        except KeyboardInterrupt:
            self.store.day(self.run_id, scope, source_id, day, 'interrupted', 'operator_interrupt', files, 0,
                           round(time.monotonic() - started, 3))
            raise
        self.store.day(self.run_id, scope, source_id, day, state, reason, files, records,
                       round(time.monotonic() - started, 3))
        self.progress(phase='day_finished', run_id=self.run_id, cluster=scope, source=source_id,
                      date=day.isoformat(), state=state, reason=reason, files=len(files), added_records=records)
        return state, reason

    def _build(self, scope, record, problems):
        interval, newest, left = self.config['intervals'][scope], record['newest_imported'], []
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
                self._stopping()
                publication = result['publication']
                record.update(build_id=result['build']['build_id'], publication_id=publication['publication_id'])
                record['stage_seconds']['rebuild_stages'] = result['stage_seconds']
                if result['state'] != 'failed' and publication['result'] in ('published', 'no_samples'):
                    state, reason = publication['result'], None
                else:
                    reason = publication['reason'] or reason
            except IngestionError as error:
                self._stopping()
                if str(error) == BUSY:
                    raise Busy() from None
                reason = str(error)
            except (TrainingError, StatisticsError) as error:
                self._stopping()
                reason = str(error)
            except Exception:
                self._stopping()
            finally:
                record['stage_seconds']['rebuild'] = round(time.monotonic() - started, 3)
            record.update(build_state=state, build_reason=reason)
            if state == 'failed':
                record['failed'] = True
                left = [dict(kind='build_not_succeeded', reason=reason)]
        problems.extend(left)
        self.store.cluster_step(self.run_id, scope, record, 'build_state', 'build_reason', 'cutoff_date', 'build_id',
                                'publication_id', 'failed', problems=left)

    def _cleanup(self, scope, record, problems):
        if not self.config['cleanup']:
            record['cleanup_state'] = 'disabled'
            self.store.cluster_step(self.run_id, scope, record, 'cleanup_state')
            return
        self.fault('before_cleanup', scope)
        started, db, state, reason, pending = time.monotonic(), None, 'nothing', None, []
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
                self._stopping()
                # A lock that could not be had leaves the month for the next run; that is not a failure.
                waiting = [m for m in outcomes if m['state'] == 'lock_timeout'
                           or (m['state'] == 'failed' and m['reason'] == 'cleanup_groups_pending')]
                broken = [m for m in outcomes if m['state'] != 'succeeded' and m not in waiting]
                record.update(months_cleaned=sum(m['state'] == 'succeeded' for m in outcomes),
                              months_pending=len(waiting) + len(broken),
                              released_bytes=sum(m['released_bytes'] for m in outcomes))
                pending = [dict(kind='cleanup_pending', result_month=month['build_month'], reason=month['reason'])
                           for month in waiting + broken]
                state = 'failed' if broken else 'pending' if waiting else 'cleaned'
                reason = broken[0]['reason'] if broken else None
        except IngestionError as error:
            self._stopping()
            if str(error) == BUSY:
                raise Busy() from None
            state, reason = 'failed', str(error)
        except TrainingError as error:
            state, reason = 'failed', str(error)
        except Exception:
            self._stopping()
            state, reason = 'failed', 'cleanup_failed'
        finally:
            if db is not None:
                db.close()
            record['stage_seconds']['cleanup'] = round(time.monotonic() - started, 3)
        problems.extend(pending)
        record.update(cleanup_state=state, cleanup_reason=reason)
        record['failed'] = record['failed'] or state == 'failed'
        self.store.cluster_step(self.run_id, scope, record, 'cleanup_state', 'cleanup_reason', 'months_cleaned',
                                'months_pending', 'released_bytes', 'failed', problems=pending)

    def _raw_files(self, scope, record, problems):
        """Delete the files and marker of days imported whole, proven unchanged, and old enough.

        The cluster is held meanwhile, as by any other step. A day goes in this order, and every
        step of it is written down when it is done: each file is read and found to be the imported
        content; the decision is recorded; each file is deleted only if its name still leads to
        the file that was read, as it was when it was read; the marker is deleted; the day is
        recorded as complete. A deletion cut short anywhere is therefore finished by a later run,
        counted once, and never taken for a change of the input.
        """
        if self.config['raw_days'] is None:
            record['raw_state'] = 'disabled'
            return
        self.fault('before_raw_files', scope)
        started, limit = time.monotonic(), self.today - timedelta(days=self.config['raw_days'])
        try:
            with self.store.cluster_held(scope) as held:
                if not held:
                    raise Busy()
                for source_id, item in self._sources(scope):
                    found = inbox.scan(item['directory'])
                    # Only the last step was lost: marker and files are gone, the day was not yet recorded as complete.
                    for day in self.store.pending_removals(source_id):
                        if day not in found['markers'] and not found['days'].get(day):
                            self._day_gone(scope, source_id, day, record)
                    states = self.store.day_states(source_id, sorted(found['markers']))
                    for day in sorted(day for day in found['markers'] if day < self.today and states.get(day) == 'complete'):
                        files = found['days'].get(day, [])
                        # Begun and not finished: a file still waits, or only the marker is left.
                        begun = any(fact['removing'] for fact in self.store.day_files(source_id, day).values())
                        if not (day < limit or begun):
                            continue
                        self._stopping()
                        if not self._remove_day(scope, source_id, item, day, files, record):
                            if not any(p['kind'] == 'day_failed' and p.get('source_id') == source_id
                                       and p.get('log_date') == day for p in problems):
                                self._settle(scope, problems, source_id, [day], [dict(
                                    kind='day_failed', source_id=source_id, log_date=day, reason=CHANGED, file_count=len(files))])
                            record['failed'] = True
            if record['raw_state'] == 'not_reached':
                record['raw_state'] = 'nothing'
        except OSError:
            record.update(raw_state='failed', raw_reason='raw_delete_failed', failed=True)
        finally:
            record['stage_seconds']['raw_files'] = round(time.monotonic() - started, 3)

    def _remove_day(self, scope, source_id, item, day, files, record):
        """False when a file of the day is not, or is no longer, what the import stored; it and the rest stay."""
        proven, known = self._unchanged(scope, source_id, item, day, files, prove=True)
        present, directory = dict(files), item['directory']
        if not proven:
            return False
        # Reading the files of a day takes time. Before the decision, each must still be the file that was read.
        moved = [name for name in sorted(present) if not self._same(directory / name, known[name]['stamp'])]
        for name in moved:
            self.store.file_doubted(source_id, day, name)
        if moved:
            return False
        self.store.removal_decided(self.run_id, source_id, day)
        for name in sorted(known):
            if known[name]['removed']:
                continue
            if name in present:
                self.fault('before_unlink', scope, name)
                if not self._unlink(directory, name, known[name]['stamp']):
                    self.store.file_doubted(source_id, day, name)
                    return False
                self.fault('after_unlink', scope, name)
            elif not known[name]['removing']:
                continue           # taken away by hand before any deletion of ours
            # Gone now: by this run, or by one that was cut short right after removing it.
            # Counted in the run's record at once, so the records of all runs add up
            # to what was removed even when a run is killed half-way.
            self.store.file_removed(self.run_id, scope, source_id, day, name)
            record['raw_files'] += 1
            record['raw_bytes'] += known[name]['byte_count']
            record['raw_state'] = 'deleted'
        self.fault('before_marker', scope, day.isoformat())
        # The marker says the day is whole. A file of the day that has appeared since the deletion began
        # is not one of the batch: the marker stays with it and the day is reported.
        if inbox.scan(directory)['days'].get(day):
            return False
        self._unlink(directory, inbox.marker_name(day))
        self.fault('after_marker', scope, day.isoformat())
        self._day_gone(scope, source_id, day, record)
        return True

    def _day_gone(self, scope, source_id, day, record):
        if self.store.day_removed(self.run_id, scope, source_id, day):
            record['raw_days'] += 1
            record['raw_state'] = 'deleted'
        self.fault('day_counted', scope, day.isoformat())

    @staticmethod
    def _same(path, stamp):
        try:
            return _stamp(os.lstat(path)) == stamp
        except OSError:
            return False

    @staticmethod
    def _unlink(directory, name, stamp=None):
        """Delete a regular file that really lies in the receiving directory; a link is never followed.

        With `stamp`, only the file that was proven goes: the name must lead to it, unmodified, in the
        look taken immediately before the deletion. False when it does not. The look and the deletion
        are two system calls and nothing can make them one; no other step lies between them.
        """
        path = directory / name
        inside = Path(os.path.realpath(path)).parent == directory
        seen = os.lstat(path)
        if not inside or not stat.S_ISREG(seen.st_mode):
            raise OSError('outside_receiving_directory')
        if stamp is not None and _stamp(seen) != stamp:
            return False
        os.unlink(path)
        return True
