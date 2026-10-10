"""Daily run rules that need no database: settings, receiving-directory reading, day sorting,
the stop signal and the connection string of the unit files."""
from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from sql_apm.daily import inbox
from sql_apm.daily.config import DailyError, load_config
from sql_apm.ingestion.config import IngestionError
from sql_apm.training.config import TrainingError

RUNTIME_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('psycopg2', 'pglast'))
if RUNTIME_AVAILABLE:
    from sql_apm.daily.interrupt import Interrupter
    from sql_apm.storage.ingestion import connection_check_seconds

ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', str(Path(__file__).resolve().parents[1])))

TODAY = date(2026, 10, 10)
D1, D2, D3 = date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9)


class InboxTests(unittest.TestCase):
    def test_scan_sorts_names_and_follows_no_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, size in [('gpdb-2026-10-08_000000.csv', 3), ('gpdb-2026-10-08_000000.csv.1', 5),
                               ('gpdb-2026-10-09_161546.csv', 1), ('2026-10-08.complete', 0), ('2026-13-01.complete', 0),
                               ('gpdb-2026-02-30_000000.csv', 1), ('gpdb-2026-10-08_000000.csv.gz', 1),
                               ('GPDB-2026-10-08_000000.csv', 1), ('.gpdb-2026-10-08_000000.csv.part', 1)]:
                (root / name).write_bytes(b'x' * size)
            (root / 'folder').mkdir()
            (root / 'folder' / 'gpdb-2026-10-07_000000.csv').write_bytes(b'x')
            (root / 'gpdb-2026-10-07_000000.csv').symlink_to(root / 'folder' / 'gpdb-2026-10-07_000000.csv')
            (root / '2026-10-07.complete').symlink_to(root / '2026-10-08.complete')
            found = inbox.scan(root)
        self.assertEqual(found['days'], {D2: [('gpdb-2026-10-08_000000.csv', 3), ('gpdb-2026-10-08_000000.csv.1', 5)],
                                         D3: [('gpdb-2026-10-09_161546.csv', 1)]})
        self.assertEqual(found['markers'], {D2})
        self.assertEqual(found['other'], ['.gpdb-2026-10-08_000000.csv.part', '2026-10-07.complete', '2026-13-01.complete',
                                          'GPDB-2026-10-08_000000.csv', 'gpdb-2026-02-30_000000.csv',
                                          'gpdb-2026-10-07_000000.csv', 'gpdb-2026-10-08_000000.csv.gz'])

    def test_day_batch_is_stable(self):
        self.assertEqual(inbox.batch_id('S1', D1), 'daily:S1:2026-10-07')
        self.assertEqual(inbox.marker_name(D1), '2026-10-07.complete')

    def classify(self, days, markers, states=None):
        return inbox.classify(dict(days=days, markers=set(markers)), TODAY, states or {})

    def test_only_marked_earlier_days_are_imported_oldest_first(self):
        files = [('a', 1)]
        pending, settled, problems = self.classify({D3: files, D1: files, D2: files, TODAY: files}, [D3, D1, TODAY],
                                                   {D1: 'failed'})
        self.assertEqual(pending, [D1, D3])
        self.assertEqual(settled, [])
        self.assertEqual(problems, [('files_without_marker', D2, None, 1), ('marker_not_before_today', TODAY, None, 1)])

    def test_marker_rules(self):
        later = date(2026, 10, 12)
        pending, settled, problems = self.classify({}, [D1, TODAY, later], {D2: 'complete'})
        self.assertEqual((pending, settled), ([], []))
        self.assertEqual(problems, [('marker_without_files', D1, None, 0), ('marker_not_before_today', TODAY, None, 0),
                                    ('marker_not_before_today', later, None, 0)])
        # An imported day whose files were taken away by hand is not a problem; its marker may stay.
        self.assertEqual(self.classify({}, [D1], {D1: 'complete'}), ([], [D1], []))

    def test_imported_day_is_left_to_the_content_check(self):
        # Names and sizes say nothing about the content: whatever a marked, imported day
        # holds now, it is handed on to be compared with what the import stored.
        state = {D1: 'complete'}
        for files in ([('a', 1), ('b', 2)], [('a', 1)], [('a', 1), ('b', 2), ('c', 0)], []):
            self.assertEqual(self.classify({D1: files} if files else {}, [D1], state), ([], [D1], []))
        # Without a marker the files are left alone even when the day is imported.
        self.assertEqual(self.classify({D1: [('a', 1)]}, [], state), ([], [], [('files_without_marker', D1, None, 1)]))
        self.assertEqual(inbox.log_day('gpdb-2026-10-07_000000.csv.2'), D1)
        self.assertIsNone(inbox.log_day('2026-10-07.complete'))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = dict(build='HashData Warehouse 3.13.13', timezone='UTC+08:00', declaration='synthetic')
        (self.root / 'import.json').write_text(json.dumps(dict(version=1, clusters=['B', 'A', 'C'], batches={},
            sources=dict(S1=dict(source, cluster='A'), S2=dict(source, cluster='B'), S3=dict(source, cluster='A')))))
        (self.root / 'training.json').write_text(json.dumps(dict(version=1, clusters=['A', 'B'], window=dict(cutoff_date='2026-01-01'))))
        for name in ('one', 'two', 'three'):
            (self.root / name).mkdir()
        self.base = dict(version=1, import_config='import.json', training_config='training.json',
                         sources=dict(S1=dict(directory='one'), S2=dict(directory=str(self.root / 'two')), S3=dict(directory='three')))

    def tearDown(self):
        self.temporary.cleanup()

    def load(self, **changes):
        path = self.root / 'daily.json'
        path.write_text(json.dumps(dict(self.base, **changes)))
        return load_config(path)

    def test_defaults_order_and_paths(self):
        config = self.load()
        self.assertEqual(config['clusters'], ['B', 'A'])   # the import configuration's order
        self.assertEqual(config['intervals'], dict(A=1, B=1))
        self.assertEqual((config['cleanup'], config['raw_days'], config['workers'], config['stale_after_hours']), (True, 45, 4, 48))
        self.assertEqual({s: (i['cluster'], i['directory']) for s, i in config['sources'].items()},
                         dict(S1=('A', (self.root / 'one').resolve()), S2=('B', (self.root / 'two').resolve()),
                              S3=('A', (self.root / 'three').resolve())))
        self.assertEqual(config['training_config'], str((self.root / 'training.json').resolve()))

    def test_switches(self):
        config = self.load(build=dict(interval_days=7, clusters=dict(A='off')), raw_files=dict(retention_days='off'),
                           cleanup=dict(enabled=False), workers=8, stale_after_hours=24)
        self.assertEqual(config['intervals'], dict(A=None, B=7))
        self.assertEqual((config['cleanup'], config['raw_days'], config['workers'], config['stale_after_hours']), (False, None, 8, 24))

    def test_rejections(self):
        for changes, error, code in [
                (dict(surprise=1), DailyError, 'unknown_config_key'), (dict(version='1'), DailyError, 'daily_config_version'),
                (dict(workers=9), DailyError, 'invalid_workers'), (dict(build=dict(interval_days=True)), DailyError, 'invalid_build_interval'),
                (dict(build=dict(clusters=dict(A=0))), DailyError, 'invalid_build_interval'),
                (dict(build=dict(clusters=dict(C=1))), DailyError, 'unknown_build_cluster'),
                (dict(build=[]), DailyError, 'invalid_build'), (dict(cleanup=dict(enabled=1)), DailyError, 'invalid_cleanup'),
                (dict(raw_files=dict(retention_days='OFF')), DailyError, 'invalid_raw_retention'),
                (dict(stale_after_hours=8761), DailyError, 'invalid_stale_after_hours'),
                (dict(sources=dict(S1=dict(directory='one'), S9=dict(directory='two'))), DailyError, 'unregistered_source'),
                (dict(sources=dict(S1=dict(directory='absent'))), DailyError, 'receiving_directory_missing'),
                (dict(sources=dict(S1=dict(directory='import.json'))), DailyError, 'receiving_directory_missing'),
                (dict(sources=dict(S1=dict(directory='one'), S3=dict(directory='./one'))), DailyError, 'duplicate_receiving_directory'),
                (dict(sources=dict(S1=dict(directory=''))), DailyError, 'invalid_daily_source'),
                (dict(import_config=''), DailyError, 'invalid_import_config'),
                (dict(import_config='training.json'), IngestionError, 'invalid_config_or_unregistered_source')]:
            with self.assertRaises(error) as raised:
                self.load(**changes)
            self.assertEqual(str(raised.exception), code, changes)
        # A cluster the training configuration does not list cannot be built or cleaned.
        (self.root / 'training.json').write_text(json.dumps(dict(version=1, clusters=['A'], window=dict(cutoff_date='2026-01-01'))))
        with self.assertRaises(TrainingError) as raised:
            self.load()
        self.assertEqual(str(raised.exception), 'unknown_cluster')


@unittest.skipUnless(RUNTIME_AVAILABLE, 'requires the pinned PostgreSQL/parser product runtime')
class ConnectionCheckTests(unittest.TestCase):
    def test_interval_setting(self):
        name = 'SQL_APM_CONNECTION_CHECK_SECONDS'
        with mock.patch.dict(os.environ):
            os.environ.pop(name, None)
            self.assertEqual(connection_check_seconds(), 10)
            for value, seconds in [('0', 0), ('2', 2), ('3600', 3600)]:
                os.environ[name] = value
                self.assertEqual(connection_check_seconds(), seconds)
            for value in ('', '-1', '1.5', '10s', '3601', '١٠'):
                os.environ[name] = value
                with self.assertRaises(IngestionError) as raised:
                    connection_check_seconds()
                self.assertEqual(str(raised.exception), 'invalid_connection_check_seconds')


@unittest.skipUnless(RUNTIME_AVAILABLE, 'requires the pinned PostgreSQL/parser product runtime')
class InterrupterTests(unittest.TestCase):
    def test_signal_cancels_waiting_statements_until_the_run_has_left(self):
        class Connection:
            cancelled = 0

            def cancel(self):
                self.cancelled += 1
        before = signal.getsignal(signal.SIGUSR1)
        with Interrupter(signals=(signal.SIGUSR1,), pause=0.01) as stop:
            waiting, recording = Connection(), Connection()
            stop.connections.update((waiting, recording))
            stop.leave_alone(recording)
            self.assertFalse(stop.stop_requested())
            with self.assertRaises(KeyboardInterrupt):
                os.kill(os.getpid(), signal.SIGUSR1)
                time.sleep(5)
            deadline = time.monotonic() + 5
            while waiting.cancelled < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            os.kill(os.getpid(), signal.SIGUSR1)       # a second signal does not cut the recording of the abort short
            time.sleep(0.05)
            self.assertTrue(stop.stop_requested())
            self.assertGreaterEqual(waiting.cancelled, 3)
            self.assertEqual(recording.cancelled, 0)
        self.assertEqual(signal.getsignal(signal.SIGUSR1), before)


@unittest.skipUnless(RUNTIME_AVAILABLE, 'requires the pinned PostgreSQL/parser product runtime')
class UnitFileTests(unittest.TestCase):
    """The unit files are readable by every local user: no form of the connection string may carry a password."""
    def generate(self, dsn, passfile=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'daily.json').write_text('{}')
            (root / 'pgpass').write_text('')
            words = [sys.executable, str(ROOT / 'scripts/daily/install.py'), '--user-unit', '--output', str(root / 'units'),
                     '--config', str(root / 'daily.json'), '--dsn', dsn]
            done = subprocess.run(words + (['--passfile', str(root / 'pgpass')] if passfile else []),
                                  capture_output=True, text=True)
            written = sorted(path.name for path in (root / 'units').glob('*'))
            service = (root / 'units' / 'sql-apm-daily.service').read_text() if written else ''
        return done, written, service

    def test_password_is_refused_in_every_form_of_the_connection_string(self):
        secret = 'Zx9q7LmW'
        encoded = ''.join('%%%02X' % ord(letter) for letter in secret)
        for dsn in ('host=/run/pg dbname=sql_apm user=sql_apm password=' + secret,
                    "host=/run/pg dbname=sql_apm user=sql_apm password = '" + secret + "'",
                    'postgresql://sql_apm:' + secret + '@db.example/sql_apm',
                    'postgres://sql_apm:' + encoded + '@db.example:5432/sql_apm',
                    'postgresql://sql_apm@db.example/sql_apm?password=' + secret,
                    'postgresql://sql_apm@db.example/sql_apm?sslmode=require&password=' + encoded,
                    'postgresql:///sql_apm?host=/run/pg&sslpassword=' + secret):
            done, written, _ = self.generate(dsn, passfile=True)
            self.assertEqual((done.returncode, written), (1, []), dsn)
            self.assertIn('--dsn must not contain a password', done.stderr)
            for text in (secret, encoded):
                self.assertNotIn(text, done.stdout + done.stderr)
        for dsn, message in (('host=/run/pg dbname', 'not a valid connection string'), ('   ', '--dsn is empty'),
                             ('postgresql://sql_apm@db.example/sql_apm?secret=' + secret, 'not a valid connection string')):
            done, written, _ = self.generate(dsn)
            self.assertEqual((done.returncode, written), (1, []), dsn)
            self.assertIn(message, done.stderr)
            self.assertNotIn(secret, done.stdout + done.stderr)

    def test_connection_string_without_a_password_is_written_as_given(self):
        for dsn in ('host=/run/pg port=5432 dbname=sql_apm user=sql_apm', 'postgresql://sql_apm@db.example:5432/sql_apm',
                    'postgresql:///sql_apm?host=/run/pg&user=sql_apm'):
            done, written, service = self.generate(dsn, passfile=True)
            self.assertEqual((done.returncode, written), (0, ['sql-apm-daily.service', 'sql-apm-daily.timer']), done.stderr)
            self.assertIn('SQL_APM_DSN=' + dsn + '"', service)
            self.assertIn('PGPASSFILE=', service)
            self.assertNotIn('password', service.lower().replace('pgpassfile', ''))


if __name__ == '__main__':
    unittest.main()
