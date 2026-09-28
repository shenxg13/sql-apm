import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sql_apm.sql.approximate import analyze, MAX_BYTES
from sql_apm.sql.mpp_parser import Unsupported


class ApproximateIntegrationTests(unittest.TestCase):
    def test_explicit_refusal_keeps_original_state(self):
        result = analyze('SELECT * FROM t WHERE id IN (1,')
        self.assertEqual(result['parser_state'], 'unsupported')
        self.assertEqual(result['parser_reason'], 'lexical_unbalanced_bracket')
        self.assertIsNone(result['structure_fingerprint'])
        self.assertEqual(result['approximate']['state'], 'available')
        self.assertEqual(result['approximate']['structural_reason'], result['parser_reason'])

    def test_parse_success_is_not_a_product_fingerprint(self):
        result = analyze('SELECT 1')
        self.assertEqual(result['parser_state'], 'parsed')
        self.assertIsNone(result['approximate'])
        self.assertIsNone(result['structure_fingerprint'])

    def test_unknown_parser_failure_does_not_fallback(self):
        for exc in (RuntimeError('private'), MemoryError('private')):
            with patch('sql_apm.sql.mpp_parser.parse', side_effect=exc):
                result = analyze('SELECT 1')
            self.assertEqual(result['parser_state'], 'failed')
            self.assertEqual(result['parser_reason'], 'parser_exception')
            self.assertIsNone(result['approximate'])
            self.assertNotIn('private', json.dumps(result))

    def test_approximation_failure_not_disguised_as_success(self):
        with patch('sql_apm.sql.approximate.fingerprint', side_effect=RuntimeError('private')):
            result = analyze('SELECT (')
        self.assertEqual(result['parser_state'], 'unsupported')
        self.assertEqual(result['approximate']['state'], 'failed')
        self.assertIsNone(result['approximate']['value'])
        self.assertNotIn('private', json.dumps(result))

    def test_input_limit_precedes_parser(self):
        with patch('sql_apm.sql.mpp_parser.parse') as parse:
            result = analyze(b'x' * (MAX_BYTES + 1))
        parse.assert_not_called()
        self.assertEqual(result['parser_reason'], 'input_size_limit')
        self.assertIsNone(result['approximate'])

    def test_binary_input_and_same_batch(self):
        raw = b'SELECT 1; SELECT * FROM t WHERE id = 2 AND x = \'\xff'
        result = analyze(raw)
        self.assertEqual(result['parser_reason'], 'invalid_encoding')
        self.assertEqual(base64.b64decode(result['approximate']['source']['bytes_base64']), raw)
        self.assertIn(['punctuation', ';'], result['approximate']['normalized']['tokens'])

    def test_cli_exit_codes_and_file_text_equivalence(self):
        command = [sys.executable, '-m', 'sql_apm.diagnostics.approximate_sql']
        for sql, expected in [('SELECT 1', 0), ('SELECT (', 2), (';', 1)]:
            run = subprocess.run(command + ['--sql', sql], capture_output=True, text=True)
            self.assertEqual(run.returncode, expected, run.stderr)
            result = json.loads(run.stdout)
            self.assertIsNone(result['structure_fingerprint'])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.sql'
            path.write_bytes(b'SELECT (')
            file_run = subprocess.run(command + ['--file', str(path)], capture_output=True, text=True)
            text_run = subprocess.run(command + ['--sql', 'SELECT ('], capture_output=True, text=True)
            self.assertEqual(file_run.returncode, 2)
            self.assertEqual(json.loads(file_run.stdout), json.loads(text_run.stdout))
            path.write_bytes(b'SELECT \xff')
            binary = subprocess.run(command + ['--file', str(path)], capture_output=True, text=True)
            self.assertEqual(binary.returncode, 2)
            self.assertEqual(base64.b64decode(json.loads(binary.stdout)['approximate']['source']['bytes_base64']), path.read_bytes())
            path.write_bytes(b'SELECT '+b'x'*MAX_BYTES)
            large = subprocess.run(command + ['--file', str(path)], capture_output=True, text=True)
            self.assertEqual(large.returncode, 1)
            self.assertEqual(json.loads(large.stdout)['parser_reason'], 'input_size_limit')


if __name__ == '__main__':
    unittest.main()
