"""Accepted profile boundary and immutable function-rule content."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from sql_apm.ingestion.mpp.reader import Interpreter
from sql_apm.sql.function_dictionary import validate

ROOT=Path(__file__).resolve().parents[1]


class MppNamingTests(unittest.TestCase):
    def test_dictionary_only_changes_profile_and_rules_version(self):
        before=json.loads((ROOT/'rules/functions/v1.0.1.json').read_text())
        after=json.loads((ROOT/'rules/functions/v1.0.2.json').read_text())
        self.assertEqual(after['profile'],'mpp-sql')
        self.assertEqual(after['rules_version'],'1.0.2')
        before.update(profile='mpp-sql',rules_version='1.0.2')
        self.assertEqual(before,after)
        validate(after)
        for profile in ('hashdata-pg94','other','mpp-csv/1'):
            invalid=copy.deepcopy(after); invalid['profile']=profile
            with self.assertRaisesRegex(ValueError,'unsupported profile'):validate(invalid)

    def test_released_dictionaries_unchanged(self):
        self.assertEqual(hashlib.sha256((ROOT/'rules/functions/v1.json').read_bytes()).hexdigest(),
                         'bd4962567d8f0d0446e246bf2ef4bbc9a9375f77ee82e302b1d5c362f8b2cdf3')
        self.assertEqual(hashlib.sha256((ROOT/'rules/functions/v1.0.1.json').read_bytes()).hexdigest(),
                         '7a75761933c3f48ae59855b16ab3cafe2cd17d5ed21d6fdd2df8ef779aefa822')

    def test_source_profile_boundary(self):
        Interpreter(profile='mpp-csv/1')
        for profile in ('hashdata-csv/1','other','mpp-sql',None):
            with self.assertRaisesRegex(ValueError,'unsupported_profile'):Interpreter(profile=profile)


if __name__=='__main__':unittest.main()
