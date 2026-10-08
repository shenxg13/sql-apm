#!/usr/bin/env python3
"""Regression checks for Issue #49's confirmed stop conditions; no database run."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/deployment'))
from verify_python_runtime import compare_runs


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.old, self.new = self.root/'old', self.root/'new'
        self.output = self.root/'comparison.json'
        for directory in (self.old, self.new):
            directory.mkdir()
        tasks = {}
        for cluster, total in (('119', 5), ('120', 4)):
            for step in range(total):
                tasks[cluster+'-'+str(step)] = dict(
                    resources=dict(seconds=100, process_peak_rss_bytes=1000),
                    stages=dict(import_seconds=50), normalization_timeouts=0)
        self.report = dict(complete=True, tasks=tasks, postgres_configuration='same',
                           manifest_sha256='same', verification_sha256={'measurement': 'same'},
                           contents=dict(omitted={}, mapped_identifiers={}, tables={
                               'table_'+str(i): dict(rows=1, sha256='same') for i in range(57)}))
        self.write(self.old, self.report)

    def write(self, directory, report):
        (directory/'report.json').write_text(json.dumps(report))

    def compare(self):
        self.write(self.new, self.report)
        with redirect_stdout(io.StringIO()):
            compare_runs(self.old, self.new, self.output)

    def test_memory_is_recorded_without_stopping(self):
        self.report['tasks']['120-0']['resources']['process_peak_rss_bytes'] = 1500
        self.compare()
        result = json.loads(self.output.read_text())
        self.assertTrue(result['passed'])
        self.assertEqual(result['tasks']['120-0']['memory_ratio'], 1.5)
        self.assertEqual(result['memory_policy'], 'record_only')
        self.assertEqual(result['memory_over_legacy_threshold'], ['120-0'])

    def test_total_elapsed_still_stops(self):
        self.report['tasks']['120-3']['resources']['seconds'] = 191
        with self.assertRaises(SystemExit):
            self.compare()
        self.assertFalse(json.loads(self.output.read_text())['elapsed_gate'])

    def test_nonzero_timeout_still_stops(self):
        self.report['tasks']['120-1']['normalization_timeouts'] = 1
        with self.assertRaises(SystemExit):
            self.compare()
        self.assertFalse(json.loads(self.output.read_text())['timeout_gate'])

    def test_any_table_difference_still_stops(self):
        self.report['contents']['tables']['table_3']['sha256'] = 'different'
        with self.assertRaises(SystemExit):
            self.compare()
        self.assertEqual(json.loads(self.output.read_text())['different_tables'], ['table_3'])

    def test_missing_task_refused_before_comparison(self):
        del self.report['tasks']['120-3']
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.compare()
        self.assertFalse(self.output.exists())

    def test_changed_measurement_refused_before_comparison(self):
        self.report['verification_sha256']['measurement'] = 'different'
        with self.assertRaisesRegex(ValueError, 'measurement input differs'):
            self.compare()
        self.assertFalse(self.output.exists())

    def test_reexport_provenance_retained(self):
        self.report['reexport'] = dict(comparison_sha256='exporter', driver_sha256='driver', decision='confirmed',
                                      original_report_sha256='old-report')
        self.write(self.old, self.report)
        self.report['reexport']['original_report_sha256'] = 'new-report'
        self.compare()
        result = json.loads(self.output.read_text())
        self.assertTrue(result['passed'])
        self.assertEqual(result['reexport']['old']['original_report_sha256'], 'old-report')
        self.assertEqual(result['reexport']['new']['original_report_sha256'], 'new-report')

    def test_mixed_reexport_code_refused(self):
        self.report['reexport'] = dict(comparison_sha256='exporter', driver_sha256='driver', decision='confirmed')
        self.write(self.old, self.report)
        self.report['reexport']['comparison_sha256'] = 'different-exporter'
        with self.assertRaisesRegex(ValueError, 're-export input differs'):
            self.compare()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
