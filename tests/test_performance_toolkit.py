"""Independent inspection boundaries, Grafana substitution and disclosure guards."""
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/performance'))
from perfkit.core import Refused,Work,save,Session
from perfkit.dashboards import interpolate,defaults
from perfkit.pack import scan,paths,export_document
from perfkit.plans import redact_plan,Notices
from perfkit.collect import delta
from perfkit.focus import extract
from perfkit.commands import statement_target
from perfkit.metadata import scan_views, declaration
from perfkit.core import encoded


class ToolkitTests(unittest.TestCase):
    def test_public_metadata_is_bound_to_approved_locations(self):
        files={'health.json':encoded({'tables':[{'last_analyze':None}]}),
               'settings.json':encoded({'values':[{'name':'autovacuum_analyze_threshold','setting':'50'}]}),
               'FIELDS.json':encoded({'files':{'health.json':['tables[].last_analyze']}}),
               'SUMMARY.txt':b'health.tables[0].last_analyze = None\n',
               'PUBLIC_METADATA.json':encoded(declaration())}
        scan(scan_views(files),['analyze'])
        for name, value in (
                ('health.json',{'tables':[{'last_analyze':'analyze'}]}),
                ('health.json',{'tables':[{'unverified_analyze':0}]}),
                ('host.json',{'last_analyze':0}),
                ('settings.json',{'values':[{'name':'unverified_analyze','setting':'50'}]}),
                ('settings.json',{'values':[{'name':'autovacuum_analyze_threshold','setting':'analyze'}]})):
            with self.assertRaisesRegex(Refused,'sensitive_value_in:'):
                scan(scan_views({name:encoded(value)}),['analyze'])
        for raw in (b'health.tables[0].last_analyze = analyze\n',b'dynamic analyze\n'):
            with self.assertRaises(Refused):
                scan(scan_views(dict(files,**{'SUMMARY.txt':raw})),['analyze'])
        for value in ({'files':{'health.json':['tables[].unverified_analyze']}},
                      {'files':{},'unexpected':'analyze'}):
            with self.assertRaises(Refused):
                scan(scan_views(dict(files,**{'FIELDS.json':encoded(value)})),['analyze'])
        with self.assertRaisesRegex(Refused,'public_metadata_declaration_changed'):
            scan_views({'PUBLIC_METADATA.json':encoded(dict(declaration(),extra='analyze'))})

    def test_sensitive_values_in_raw_and_escaped_json(self):
        for sentinel in ('SensitiveSecret70', 'quote"line\n中文', '192.0.2.70'):
            with self.assertRaisesRegex(Refused,'sensitive_value_in:sample.json'):
                scan({'sample.json':json.dumps({'value':sentinel}).encode()},[sentinel])
        scan({'sample.json':b'{"count":17}'},['SensitiveSecret70'])

    def test_numeric_cluster_alias_is_not_a_counter_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Work(Path(directory),Path(directory)/'product')
            work.alias('cluster','119')
            self.assertNotIn('119',work.secrets)
            work.secret('119')
            self.assertIn('119',work.secrets)
            work.close()

    def test_generic_fields_and_path_collisions_are_sanitized_before_scan(self):
        data=export_document('samples.json',[{'processes':{'postgres_rss_kib':119120}}],{'postgres'})
        self.assertEqual(data[0]['processes']['database_rss_kib'],119120)
        scan({'samples.json':json.dumps(data).encode()},['postgres'])
        data=export_document('settings.json',{'values':[{'vartype':'string','setting':'/data/postgres/pg.conf','boot_val':None}],
            'build':[{'name':'CONFIGURE','setting':"'--prefix=/data/postgres/runtime' '--with-openssl'"}]},{'postgres'})
        self.assertIn('--with-openssl',data['build'][0]['setting'])
        scan({'settings.json':json.dumps(data).encode()},['postgres'])

    @unittest.skipUnless(importlib.util.find_spec('psycopg2'), 'requires the pinned PostgreSQL driver')
    def test_connection_options_preserve_the_existing_timeout(self):
        from psycopg2.extensions import parse_dsn
        with patch('psycopg2.connect',return_value=Mock()) as connect,patch('psycopg2.extensions.set_wait_callback'):
            with Session("dbname=example options='-c statement_timeout=17'"):
                options=parse_dsn(connect.call_args.args[0])['options']
                self.assertIn('statement_timeout=17',options)
                self.assertIn('default_transaction_read_only=on',options)

    def test_nested_plan_literals_are_never_copied(self):
        raw={'Query Text':'SELECT secret', 'Plan':{'Node Type':'Index Scan','Index Name':'allowed_idx',
             'Filter':"(account = 'secret')",'Output':['secret','secret + 1'],
             'Actual Rows':42,'Actual Loops':3,'Plans':[{'Node Type':'Seq Scan','Relation Name':'secret'}]}}
        clean=redact_plan(raw,{'allowed_idx'})
        self.assertNotIn('secret',json.dumps(clean))
        self.assertEqual(clean['Plan']['Actual Rows'],42)
        self.assertEqual(clean['Plan']['Index Name'],'allowed_idx')
        notice=Notices({'allowed_idx'})
        notice.append('NOTICE: duration: 7.1 ms plan:\n'+json.dumps(raw)+'\nCONTEXT: private secret')
        self.assertEqual(notice.plans,[clean])

    def test_grafana_formats_do_not_cross_escape_boundaries(self):
        at=datetime(2026,7,24,tzinfo=timezone.utc)
        sql=interpolate("SELECT '${q}', '${mode:csv}', '${q:queryparam}', $__timeFrom(), $__interval_ms",
                        {'q':"O'Reilly / 中文",'mode':['request','parse']},at,at,30000)
        self.assertEqual(sql,"SELECT 'O''Reilly / 中文', 'request,parse', 'var-q=O%27Reilly%20%2F%20%E4%B8%AD%E6%96%87', '2026-07-24T00:00:00Z', 30000")
        with self.assertRaises(Refused):
            interpolate('${missing}',{},at,at)

    def test_statement_targets_follow_the_actual_operation(self):
        self.assertEqual(statement_target("SELECT * FROM mpp_view_statistics('x')","case/words/detail"),1000)
        self.assertEqual(statement_target("SELECT mpp_query_history('x')","case/words/history"),5000)
        self.assertEqual(statement_target("SELECT * FROM mpp_view_search('x')","case/exact/detail"),3000)

    def test_all_expands_custom_values(self):
        value=defaults({'templating':{'list':[{'name':'status','current':{'value':['$__all']},
                         'options':[{'value':'$__all'},{'value':'success'},{'value':'failed'}]}]}})
        self.assertEqual(value,{'status':['success','failed']})
        at=datetime(2026,7,24,tzinfo=timezone.utc)
        self.assertEqual(interpolate('${status:queryparam}',value,at,at),'var-status=$__all')

    def test_work_lock_and_atomic_interrupted_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); work=Work(root,root/'product')
            try:
                with self.assertRaises(Refused):
                    Work(root,root/'product')
                with self.assertRaises(KeyboardInterrupt),work.section('timing'):
                    save(work.public/'timing.json',{'completed':1})
                    raise KeyboardInterrupt
                self.assertEqual(json.loads((work.public/'timing-state.json').read_text())['state'],'interrupted')
                self.assertEqual(json.loads((work.public/'timing.json').read_text()),{'completed':1})
                self.assertFalse(list(root.rglob('*.tmp')))
            finally:
                work.close()
            with self.assertRaises(Refused):
                Work(root,root)

    def test_stat_deltas_keep_reset_evidence(self):
        old={'io':[{'backend_type':'client backend','object':'relation','context':'normal','reads':5,'stats_reset':'same'}]}
        new={'io':[{'backend_type':'client backend','object':'relation','context':'normal','reads':12,'stats_reset':'changed'}]}
        item=delta(old,new)['io'][0]
        self.assertEqual(item['values']['reads'],7)
        self.assertFalse(item['stats_reset_unchanged'])

    def test_internal_queries_are_exact_source_slices(self):
        source=(ROOT/'sql_apm/storage/schema.sql').read_text()
        bodies=extract(source)
        self.assertEqual(len(bodies),4)
        for body in bodies.values():
            self.assertIn(body,source)
            self.assertTrue(body.startswith(('WITH','SELECT')))

    def test_field_inventory_uses_paths_not_values(self):
        self.assertEqual(paths({'rows':[{'sensitive':'do not copy'}]}),{'rows','rows[].sensitive'})


if __name__=='__main__':
    unittest.main()
