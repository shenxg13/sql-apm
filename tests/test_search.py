"""Runtime search checks; the host-Python dictionary CI has no product wheels."""
import contextlib
import importlib.util
import io
import json
import unittest
import unittest.mock

RUNTIME_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('psycopg2', 'pglast'))
if RUNTIME_AVAILABLE:
    from sql_apm.cli.search import TEXT_MAX_BYTES, exact, fingerprint_input, main, statement_inputs
    from sql_apm.sql.normalization import MAX_BYTES, Normalizer


@unittest.skipUnless(RUNTIME_AVAILABLE, 'requires the pinned PostgreSQL/parser product runtime')
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

    def test_a_pasted_fingerprint_is_recognised_as_the_text_search_recognises_it(self):
        value = 'struct:sql-normalization/5:' + 'a' * 64
        for given in (value, ' \t' + value + '\r\n', '\f\v' + value, value.encode()):
            self.assertEqual(fingerprint_input(given), value)
        for given in (value[:-1], value + 'b', 'x ' + value, value + ' ' + value, value.upper(), '\u00a0' + value,
                      'SELECT 1', '', b'\xff' + value.encode()):
            self.assertIsNone(fingerprint_input(given), given)

    def test_the_complete_sql_limit_comes_before_the_fingerprint_lookup(self):
        class Asked:
            """Stands for the exact-search function: records what it is asked and answers like it."""
            def __init__(self):
                self.values = None

            def execute(self, statement, values):
                self.values = list(values)

            def fetchone(self):
                fingerprint, reason = self.values[1], self.values[-1]
                return (dict(state='has_baseline' if fingerprint else 'unreliable_fingerprint', fingerprint=fingerprint, reason=reason),)

        normalizer, value = Normalizer(), 'struct:sql-normalization/5:' + 'a' * 64
        filters = ['C2', None, 'someone']
        for size, direct in ((len(value), True), (MAX_BYTES - 1, True), (MAX_BYTES, True), (MAX_BYTES + 1, False), (MAX_BYTES + 10, False)):
            for padded in (value + ' ' * (size - len(value)), ' \t\n' + value + '\n' * (size - len(value) - 3)):
                if len(padded) < len(value) + 3 and padded != value:
                    continue  # the bare value has no room for blanks in front
                for given in (padded, padded.encode()):
                    asked = Asked()
                    answer = exact(asked, normalizer, given, filters)
                    self.assertEqual(asked.values[3:6], filters)
                    if direct:
                        self.assertEqual((asked.values[1], asked.values[-1], answer['state']), (value, None, 'has_baseline'), size)
                    else:
                        self.assertEqual((asked.values[1], asked.values[-1], answer['state']),
                                         (None, 'input_size_limit', 'unreliable_fingerprint'), size)
                        self.assertNotIn('statement_hints', answer)
        # what an entry keeps of a longer input (its first MAX_BYTES + 1 bytes) is refused as well
        longer = (value + ' ' * (MAX_BYTES + 1 - len(value)) + ' SELECT 2').encode()
        asked = Asked()
        self.assertEqual(exact(asked, normalizer, longer[:MAX_BYTES + 1], filters)['reason'], 'input_size_limit')
        self.assertIsNone(asked.values[1])

    def test_words_and_passages_over_256_kb_are_refused_before_the_database(self):
        self.assertEqual(TEXT_MAX_BYTES, 256 * 1024)
        for words in (['find', 'x' * (TEXT_MAX_BYTES + 1)], ['find', 'x' * (TEXT_MAX_BYTES + 1), '--mode', 'passage'],
                      ['find', '中' * (TEXT_MAX_BYTES // 3 + 1)]):  # the limit counts UTF-8 bytes
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(words), 1)
            self.assertEqual(json.loads(out.getvalue()), {'state': 'failed', 'reason': 'search_input_too_large'})
        # At the limit the size is not the reason: without a database the search fails later, for another reason.
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.dict('os.environ', {'SQL_APM_DSN': 'host=/nonexistent port=1 dbname=x connect_timeout=1'}):
            self.assertEqual(main(['find', 'x' * TEXT_MAX_BYTES]), 1)
        self.assertNotEqual(json.loads(out.getvalue())['reason'], 'search_input_too_large')

    def test_text_search_has_two_modes_and_complete_sql_is_not_one_of_them(self):
        for words in (['find', 'x', '--mode', 'exact'], ['find', 'x', '--mode'], ['find', '--mode', 'passage']):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(words), 1)
            self.assertEqual(json.loads(out.getvalue()), {'state': 'failed', 'reason': 'invalid_arguments'})
