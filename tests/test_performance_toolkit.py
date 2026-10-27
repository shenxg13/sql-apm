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
from perfkit.core import Refused,Work,save,Session,check_versions
from perfkit.dashboards import interpolate,defaults
from perfkit.pack import scan,paths,export_document
from perfkit.plans import redact_plan,Notices
from perfkit.collect import delta
from perfkit.focus import extract,extract_settings
from perfkit.commands import statement_target
from perfkit.metadata import scan_views, declaration
from perfkit.core import encoded
from perfkit import cases, overview, pack
from perfkit.vocabulary import fixed, sql_fields, Vocabulary


class ToolkitTests(unittest.TestCase):
    def test_toolkit_110_accepts_only_the_new_product_schema_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)
            for product,schema,accepted in [('v1.2.0','1.13.0',True),('v1.1.0','1.12.0',False),
                                             ('v1.2.0','1.12.0',False),('v1.2.0','1.14.0',False)]:
                with self.subTest(product=product,schema=schema):
                    (app/'RELEASE.json').write_text(json.dumps(dict(version=product)))
                    session=Mock(query=Mock(return_value=[dict(version=schema)]))
                    if accepted:
                        self.assertEqual(check_versions(app,session)['schema'],'1.13.0')
                    else:
                        with self.assertRaises(Refused):check_versions(app,session)

    @unittest.skipUnless(importlib.util.find_spec('pglast'), 'requires the pinned PostgreSQL parser')
    def test_build_and_kernel_field_names_are_checked_before_timing(self):
        with patch('perfkit.collect.read',return_value='SyntheticHostMetric70: 1\n'):
            vocabulary=fixed()
        for value in ('CC','CFLAGS','SyntheticHostMetric70','"SyntheticHostMetric70"',
                      'heap_bytes','table_bytes','index_bytes','"heap_bytes"'):
            self.assertIsNotNone(vocabulary.collision(value))
        self.assertIsNone(vocabulary.collision('SELECT'))
        self.assertEqual(sql_fields(['SELECT %s AS explicit_label, t.natural_label, count(*) FROM t',
                                     'SELECT name FROM (']),
                         {'explicit_label','natural_label','count'})

    @unittest.skipUnless(importlib.util.find_spec('pglast'), 'requires the pinned PostgreSQL parser')
    def test_automatic_inputs_skip_colliding_names_without_changing_scan(self):
        vocabulary = fixed(Mock(query=Mock(side_effect=[
            [{'name':'mpp_drop_result_month'},{'name':'xact_commit'}], [], []])))
        for source in ('drop table BusinessSentinel70', 'set BusinessSentinel70 = 7',
                       'vacuum BusinessSentinel70', 'select "state" from BusinessSentinel70'):
            skipped = []
            values = cases.inputs(source,vocabulary,skipped)
            self.assertEqual(len(values),5)
            self.assertTrue(skipped)
            self.assertEqual(values,cases.inputs(source,vocabulary))
            self.assertEqual(values[-1]['text'],source)
            self.assertTrue(all(vocabulary.collision(v['text']) is None for v in values))
            self.assertNotIn('BusinessSentinel70',json.dumps(skipped))
            files={'cases.json':encoded(skipped),'health.json':encoded({'tables':[{'state':0,'vacuum_count':1}]})}
            scan(scan_views(files),[v['text'] for v in values])
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                work=Work(root,root/'product')
                try:
                    save(root/'MANIFEST.json',{'version':'1'})
                    for name,raw in files.items():
                        (work.public/name).write_bytes(raw)
                    for value in values:
                        work.secret(value['text'])
                    self.assertTrue(pack.package(work,root).is_file())
                finally:
                    work.close()
        with self.assertRaises(cases.NoSafeInput):
            cases.inputs('state',Vocabulary({'fixed':['state']}))
        with self.assertRaisesRegex(Refused,'sensitive_value_in:deltas.json'):
            scan({'deltas.json':encoded({'xact_commit':1})},['commit'])

    @unittest.skipUnless(importlib.util.find_spec('pglast'), 'requires the pinned PostgreSQL parser')
    def test_predictable_public_values_are_skipped_before_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);app=root/'product';source=app/'sql_apm'
            source.mkdir(parents=True)
            (source/'ingestion.py').write_text('def public_callable():\n    pass\n')
            dashboards=root/'boards';dashboards.mkdir()
            for uid in ('mpp-search','mpp-detail','mpp-list','mpp-status'):
                save(dashboards/(uid+'.json'),dict(uid=uid,templating=dict(list=[dict(name='public_variable')]),
                    panels=[dict(targets=[dict(refId='Anno')])]))
            host={'/proc/cpuinfo':'model name : Intel FixtureProcessor\n',
                  '/proc/self/mountinfo':'1 0 1:1 / / rw - xfs root rw\n',
                  '/sys/kernel/mm/transparent_hugepage/enabled':'always [madvise] never\n'}
            io=dict(backend_type='autovacuum launcher',object='relation',context='bulkread')
            with patch('perfkit.collect.read',side_effect=lambda path:host.get(str(path))), \
                    patch('perfkit.vocabulary.platform.release',return_value='FixtureKernel'), \
                    patch('perfkit.vocabulary.platform.machine',return_value='x86_64'):
                vocabulary=fixed(Mock(query=Mock(side_effect=[[],[],[io]])),app,dashboards)
            for value in ('T0','T1','T2','T20','Intel','never','x86_64','xfs','FixtureKernel',
                          'launcher','bulkread','ingestion','public_callable','Anno','public_variable'):
                self.assertIsNotNone(vocabulary.collision(value),value)
            fixtures=[('T2','host-state.json',dict(part='host',state='complete',started_at='2026-10-23T20:15:00+08:00')),
                      ('Intel','host.json',dict(cpu_model='Intel FixtureProcessor')),
                      ('bulkread','health.json',dict(io=[io])),
                      ('ingestion','timing.json',dict(pages=[],http=[],commands=[dict(item='case-0001/test',ms=1,
                           state='ok',exit_code=0,output_bytes=0,statements=[dict(item='case-0001/sql',ms=1,
                           state='ok',sites=[dict(file='sql_apm/ingestion.py',function='public_callable',line=1)])])])),
                      ('Anno','timing.json',dict(pages=[],http=[],commands=[],refs=['Anno','public_variable']))]
            save(root/'MANIFEST.json',{'version':'fixture'})
            for ordinal,(word,name,document) in enumerate(fixtures):
                with self.subTest(word=word):
                    raw=encoded(document)
                    with self.assertRaises(Refused):
                        scan(scan_views({name:raw}),[word])
                    skipped=[];values=cases.inputs('select '+word+' UniqueBusinessSentinel70',vocabulary,skipped)
                    self.assertNotIn(word,[v['text'] for v in values])
                    self.assertTrue(any(s['location'].startswith('runtime:') for s in skipped))
                    target=root/str(ordinal);target.mkdir();work=Work(target,app)
                    try:
                        (work.public/name).write_bytes(raw)
                        save(work.public/'cases.json',skipped)
                        for value in values:work.secret(value['text'])
                        self.assertTrue(pack.package(work,root).is_file())
                    finally:work.close()

    @unittest.skipUnless(importlib.util.find_spec('psycopg2'), 'requires the pinned PostgreSQL driver')
    def test_identity_collision_refuses_before_selecting_or_reusing_stale_cases(self):
        def query(sql,params=None):
            if sql == 'SHOW statement_timeout':
                return [{'statement_timeout':'2min'}]
            if 'set_config' in sql:
                return []
            if 'mpp_view_rules' in sql:
                return [{'value':'rules'}]
            if 'FROM scope' in sql:
                return [{'scope_id':'119'}]
            if 'SELECT DISTINCT database,execution_user' in sql:
                return [{'database':'temp','execution_user':'BusinessSentinel70'}]
            self.fail('selection must not start')
        with tempfile.TemporaryDirectory() as directory:
            work=Work(Path(directory),Path(directory)/'product')
            save(work.private/'cases.json',[{'old':'case'}])
            try:
                with patch('perfkit.cases.fixed',return_value=Vocabulary({'postgres:catalog':['temp_files']})):
                    with self.assertRaisesRegex(Refused,'fixed_name_collision:database:cluster-0001/identity-1'):
                        cases.choose(Mock(query=Mock(side_effect=query)),work)
                self.assertFalse((work.private/'cases.json').exists())
                self.assertIn('temp',work.secrets)
                self.assertIn('BusinessSentinel70',work.secrets)
                public=(work.public/'cases.json').read_text()
                self.assertNotIn('temp',public)
                self.assertNotIn('BusinessSentinel70',public)
                self.assertIn('fixed_name_collision',public)
            finally:
                work.close()

    @unittest.skipUnless(importlib.util.find_spec('psycopg2'), 'requires the pinned PostgreSQL driver')
    def test_refused_operator_input_is_still_scanned_when_packing_partial_results(self):
        def query(sql,params=None):
            if sql == 'SHOW statement_timeout':
                return [{'statement_timeout':'2min'}]
            if 'set_config' in sql:
                return []
            self.fail('operator refusal must precede data selection')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);work=Work(root,root/'product')
            try:
                save(root/'extra.json',[{'cluster':'119','input':'commit'}])
                with patch('perfkit.cases.fixed',return_value=Vocabulary({'fixed':['xact_commit','119']})):
                    with self.assertRaisesRegex(Refused,'fixed_name_collision:operator_input:extra-1'):
                        cases.choose(Mock(query=Mock(side_effect=query)),work,root/'extra.json')
                self.assertIn('commit',work.secrets)
                self.assertNotIn('119',work.secrets)
                save(root/'MANIFEST.json',{'commit':'fixture'})
                with self.assertRaisesRegex(Refused,'sensitive_value_in:TOOLKIT.json'):
                    pack.package(work,root)
            finally:
                work.close()

    @unittest.skipUnless(importlib.util.find_spec('psycopg2'), 'requires the pinned PostgreSQL driver')
    def test_selection_falls_back_through_originals_and_ranked_identities(self):
        for next_identity in (False,True):
            def query(sql,params=None):
                if sql == 'SHOW statement_timeout':
                    return [{'statement_timeout':'2min'}]
                if 'set_config' in sql or 'SELECT DISTINCT database,execution_user' in sql:
                    return []
                if 'mpp_view_rules' in sql:
                    return [{'value':'rules'}]
                if 'FROM scope' in sql:
                    return [{'scope_id':'119'}]
                if 'WITH identities' in sql:
                    return [dict(kind=kind,scope_id='119',fingerprint='fp'+str(rank),database=None,
                        execution_user=None,candidate_rank=rank,events=1,texts=1,structure_texts=1,chars=20)
                        for kind in range(6) for rank in (1,2)]
                if 'SELECT t.sql_id,t.text' in sql:
                    bad=dict(sql_id='original1',text='state')
                    good=dict(sql_id='original2',text='SELECT BusinessSentinel70')
                    return [bad] if next_identity and params[1]=='fp1' else [bad,good]
                if 'mpp_view_identity_token' in sql:
                    return [{'token':'identity'}]
                self.fail('unexpected selection query')
            with self.subTest(next_identity=next_identity), tempfile.TemporaryDirectory() as directory:
                work=Work(Path(directory),Path(directory)/'product')
                try:
                    with patch('perfkit.cases.fixed',return_value=Vocabulary({'fixed':['state']})):
                        selected=cases.choose(Mock(query=Mock(side_effect=query)),work)
                    self.assertEqual(len(selected),1)
                    self.assertEqual(selected[0]['kinds'],list(cases.KINDS))
                    self.assertEqual(selected[0]['fingerprint'],'fp2' if next_identity else 'fp1')
                    self.assertEqual(selected[0]['sql_id'],'original2')
                    self.assertNotIn('BusinessSentinel70',(work.public/'cases.json').read_text())
                finally:
                    work.close()

    @unittest.skipUnless(importlib.util.find_spec('psycopg2'), 'requires the pinned PostgreSQL driver')
    def test_selection_timeout_skips_cluster_restores_limit_and_preserves_cancel(self):
        from psycopg2.errors import QueryCanceled
        visited=[]
        def query(sql,params=None):
            if sql == 'SHOW statement_timeout':
                return [{'statement_timeout':'17ms'}]
            if 'set_config' in sql:
                return []
            if 'mpp_view_rules' in sql:
                return [{'value':'rules'}]
            if 'FROM scope' in sql:
                return [{'scope_id':'119'},{'scope_id':'120'}]
            if 'SELECT DISTINCT database,execution_user' in sql:
                return []
            if 'WITH identities' in sql:
                visited.append(params[0])
                if params[0]=='119':
                    raise QueryCanceled
                return []
            self.fail('unexpected selection query')
        with tempfile.TemporaryDirectory() as directory:
            work=Work(Path(directory),Path(directory)/'product')
            session=Mock(query=Mock(side_effect=query))
            try:
                with patch('perfkit.cases.fixed',return_value=Vocabulary({})):
                    self.assertEqual(cases.choose(session,work),[])
                self.assertEqual(visited,['119','120'])
                rows=json.loads((work.public/'cases.json').read_text())
                self.assertEqual(sum(r.get('reason')=='selection_timeout' for r in rows),6)
                self.assertEqual(session.query.call_args.args[1],('17ms',))
                with patch('perfkit.cases.select_cases',side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        cases.choose(session,work)
                self.assertEqual(session.query.call_args.args[1],('17ms',))
                self.assertFalse((work.private/'cases.json').exists())
            finally:
                work.close()

    def test_overviews_share_counts_bound_width_and_are_scanned(self):
        good=dict(item='case-0001/run-1/http/exact',state='has_baseline',ms=3,attention=False)
        statement=dict(item='case-0001/run-1/panel/1',state='timeout',ms=17,attention=True)
        page=dict(item='case-0001/run-1/mpp-search/common',page_estimate_ms=17,
                  variables_ms=1,panels_ms=16,slowest_panel_ms=16,
                  statements=[statement,statement],incomplete=True)
        timing=dict(pages=[page],commands=[],http=[good])
        documents={'timing.json':timing,'timing-state.json':dict(state='complete',seconds=3)}
        self.assertEqual(overview.counts([good])['failed'],0)
        self.assertEqual(overview.counts([page])['timeout'],1)
        self.assertEqual(overview.counts([page])['failed'],0)
        self.assertEqual(overview.counts([page])['over_reference'],0)
        slow=dict(statement,state='ok',ms=1001,target_ms=1000)
        self.assertEqual(overview.counts([dict(page,statements=[slow,slow])])['over_reference'],1)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,value in documents.items():
                save(root/name,value)
            terminal=overview.terminal(root,'timing')
        self.assertLessEqual(len(terminal.splitlines()),10)
        self.assertTrue(all(overview.width(line)<=80 for line in terminal.splitlines()))
        bundle=overview.bundle(documents)
        self.assertLessEqual(len(bundle)+2,40)
        self.assertIn(overview.count_line('pages',[page]),terminal)
        self.assertIn(overview.count_line('pages',[page]),bundle)
        summary=pack.summary({name:encoded(value) for name,value in documents.items()})
        self.assertIn('查询计时（毫秒）',summary)
        with self.assertRaises(Refused):
            scan({'SUMMARY.txt':summary.encode()},['case-0001'])

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
        self.assertEqual(extract_settings(source)['mpp_view_baseline_ranking'],
                         dict(plan_cache_mode='force_custom_plan',parallel_setup_cost='0',parallel_tuple_cost='0'))
        self.assertEqual(len(bodies),4)
        for body in bodies.values():
            self.assertIn(body,source)
            self.assertTrue(body.startswith(('WITH','SELECT')))

    def test_field_inventory_uses_paths_not_values(self):
        self.assertEqual(paths({'rows':[{'sensitive':'do not copy'}]}),{'rows','rows[].sensitive'})


if __name__=='__main__':
    unittest.main()
