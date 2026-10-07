"""SQL boundary preservation and public JSON error handling."""
import contextlib
import io
import json
import unittest

from sql_apm.cli.search import main, statement_inputs


class SearchTests(unittest.TestCase):
    def test_statement_hints_keep_quoted_semicolons_and_comments(self):
        source = "/*+hint*/ SELECT ';'; -- tail\nSELECT $$a;b$$; /* tail */"
        pieces = statement_inputs(source)
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[0], "/*+hint*/ SELECT ';';")
        self.assertIn('-- tail', pieces[1])
        self.assertEqual(statement_inputs('; -- only\n;'), [])
        self.assertEqual(len(statement_inputs('SELECT 中文; SELECT 2')), 2)

    def test_argument_errors_are_json_without_input(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['exact', '--secret-sql', 'PRIVATE_TEXT']), 1)
        self.assertEqual(json.loads(out.getvalue()), {'state': 'failed', 'reason': 'invalid_arguments'})
