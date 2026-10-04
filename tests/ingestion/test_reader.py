import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from sql_apm.ingestion.config import IngestionError, load_config
from sql_apm.ingestion.mpp.reader import Interpreter, Records


def row(line='1946', message='duration: 1200000.001 ms', text='SELECT $1;', **fields):
    values = [''] * 30
    defaults = {0: '2026-07-23 00:10:00.000001 CST', 1: 'synthetic_user', 2: 'synthetic_db',
                3: 'p12', 7: '2026-07-22 20:00:00 CST', 9: 'con17', 10: 'cmd1',
                11: 'seg-1', 16: 'LOG', 17: '00000', 18: message, 24: text, 27: 'postgres.c', 28: line}
    defaults.update({int(k): v for k, v in fields.items()})
    for key, value in defaults.items():
        values[key] = value
    return values


def configuration(path, files, batch='B1'):
    return {'version': 1, 'clusters': ['C1'], 'sources': {'S1': {'cluster': 'C1',
            'build': 'HashData Warehouse 3.13.13', 'timezone': 'UTC+08:00', 'declaration': 'synthetic'}},
            'batches': {batch: {'source': 'S1', 'files_confirmed_complete': True, 'dates': ['2026-07-23'],
                'files': [{'path': str(f), 'closed_and_copied': True} for f in files]}}}


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        csv.writer(stream).writerows(rows)


class ReaderTests(unittest.TestCase):
    def test_five_timings_and_dates(self):
        for line, kind in [('1946', 'request'), ('2219', 'parse'), ('2224', 'parse'), ('2603', 'bind'), ('2608', 'bind')]:
            event = Interpreter().interpret(row(line), 'R1')
            self.assertEqual(kind, event['timing'])
            self.assertEqual(Decimal('1200000.001'), event['duration'])
            self.assertEqual('2026-07-22T23:50:00+08:00', event['start'].isoformat())
            self.assertEqual('success', event['outcome'])
        self.assertIsNone(Interpreter().interpret(row('1455'), 'R1'))
        self.assertIsNone(Interpreter().interpret(row('334', **{'27': 'autostats.c'}), 'R1'))

    def test_zero_malformed_duration_and_unknown_timestamp(self):
        zero = Interpreter().interpret(row(message='duration: 0 ms'), 'Z')
        self.assertEqual(zero['end'], zero['start'])
        for message in ('duration: -1 ms', 'duration: NaN ms', 'duration: invalid ms'):
            event = Interpreter().interpret(row(message=message), 'I')
            self.assertIsNone(event['duration'])
            self.assertEqual('unknown', event['outcome'])
        self.assertIsNone(Interpreter().interpret(row(**{'0': 'invalid time'}), 'T')['end'])

    def test_execute_first_fetch_single_consumption_and_command_change(self):
        for prefix, kind in [('execute', 'execute_first'), ('execute fetch from', 'execute_fetch')]:
            parser = Interpreter()
            parser.interpret(row('2764', prefix + ' stmt/portal: SELECT $1;'), 'R1')
            result = parser.interpret(row('2843', **{'10': 'cmd2'}), 'R2')
            self.assertEqual(kind, result['timing'])
            self.assertEqual('R1', result['support'])
            self.assertIsNone(parser.interpret(row('2843'), 'R3')['timing'])

    def test_inline_envelope_whitespace_and_internal_logs(self):
        parser = Interpreter()
        text = ' \nSELECT $1;'
        parser.interpret(row('2764', 'execute p: ' + text, text=text), 'S')
        parser.interpret(row('299', 'optimizer notice', text='', **{'27': 'COptTasks.cpp'}), 'I')
        parser.interpret(row('334', **{'27': 'autostats.c'}), 'A')
        self.assertEqual('execute_first', parser.interpret(row('2843', text=text), 'E')['timing'])

    def test_execute_ambiguity_mismatch_missing_identity_error_and_order(self):
        cases = [([row('2764', 'execute p: SELECT $1;')] * 2, row('2843'), 'execute_start_ambiguous'),
                 ([row('2764', 'execute p: SELECT $1;')], row('2843', text='SELECT 2'), 'execute_context_mismatch'),
                 ([], row('2843', **{'7': ''}), 'session_identity_missing'),
                 ([row('2764', 'execute p: SELECT $1;'), row('0', 'failure', **{'16': 'ERROR'})], row('2843'), 'execute_start_missing'),
                 ([row('2764', 'execute p: SELECT $1;', **{'0': '2026-07-24 00:00:00 CST'})], row('2843'), 'execute_order_invalid')]
        for starts, finish, reason in cases:
            parser = Interpreter()
            for number, start in enumerate(starts):
                parser.interpret(start, str(number))
            result = parser.interpret(finish, 'last')
            self.assertEqual(reason, result['timing_reason'])
            self.assertIsNone(result['timing'])

    def test_failures_and_stage_independence(self):
        parser = Interpreter()
        bind = parser.interpret(row('2603'), 'B')
        for message, state in [('canceling statement due to user request', 'cancelled'),
                               ('canceling statement due to statement timeout', 'timed_out'), ('other error', 'failed')]:
            result = parser.interpret(row('1', message, **{'16': 'ERROR', '17': '57014'}), 'E')
            self.assertEqual(state, result['outcome'])
            self.assertIsNone(result['duration'])
            self.assertIsNone(result['start'])
            self.assertIsNotNone(result['end'])
        self.assertEqual('success', bind['outcome'])
        self.assertIsNone(parser.interpret(row('1', 'authentication failed', text='', **{'16': 'FATAL'}), 'F'))
        self.assertIsNone(parser.interpret(row('1', 'error', **{'16': 'ERROR', '11': 'seg0'}), 'F'))

    def test_csv_multiline_invalid_bytes_and_boundary_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.csv'
            write_csv(path, [row(text='SELECT\n1'), row()])
            records = Records(path)
            observed = list(records)
            self.assertEqual((1, 1, 2), observed[0][:3])
            self.assertEqual((2, 3, 3), observed[1][:3])
            path.write_bytes(path.read_bytes().replace(b'SELECT\n1', b'SELECT\xff\x00'))
            self.assertEqual(b'SELECT\xff\x00', list(Records(path))[0][3][24].encode('utf8', 'surrogateescape'))
            path.write_bytes(b'"unfinished\n')
            with self.assertRaisesRegex(IngestionError, 'csv_boundary'):
                list(Records(path))

    def test_config_refuses_ambiguous_and_unconfirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            doc = configuration(path, ['a.csv'])
            path.write_text(json.dumps(doc))
            self.assertEqual('C1', load_config(path, 'S1', 'B1')['scope_id'])
            with self.assertRaises(IngestionError):
                load_config(path, 'missing', 'B1')
            doc['sources']['S1']['cluster'] = ['C1', 'C2']
            path.write_text(json.dumps(doc))
            with self.assertRaises(IngestionError):
                load_config(path, 'S1', 'B1')
            path.write_text('{"version":1,"version":1}')
            with self.assertRaisesRegex(IngestionError, 'duplicate_config_key'):
                load_config(path, 'S1', 'B1')
