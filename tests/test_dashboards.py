"""Static checks of the delivered Grafana files; no Grafana, database or network."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import unittest

ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', str(Path(__file__).resolve().parents[1]))) / 'grafana'
RUNTIME_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('psycopg2', 'pglast'))
PLUGINS = {'grafana-postgresql-datasource', 'volkovlabs-form-panel', 'yesoreyeram-infinity-datasource'}
PANELS = {'table', 'stat', 'text', 'timeseries', 'barchart', 'bargauge', 'row', 'volkovlabs-form-panel'}
UIDS = {'mpp-search.json': ('mpp-search', 'SQL 检索'), 'mpp-list.json': ('mpp-list', 'SQL 列表'),
        'mpp-detail.json': ('mpp-detail', 'SQL 详情'), 'mpp-status.json': ('mpp-status', '运行状态')}


def panels(board):
    for outer in board['panels']:
        yield outer
        yield from outer.get('panels', [])


class DeliveredGrafanaFiles(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'components.json').read_text())
        self.boards = {path.name: json.loads(path.read_text()) for path in sorted((ROOT / 'dashboards').glob('*.json'))}

    def test_manifest_pins_grafana_13_and_exactly_three_plugins(self):
        grafana = self.manifest['grafana']
        self.assertTrue(grafana['version'].startswith('13.'))
        self.assertEqual({item['id'] for item in self.manifest['plugins']}, PLUGINS)
        for item in [grafana] + self.manifest['plugins']:
            self.assertRegex(item['sha256'], '^[0-9a-f]{64}$')
            self.assertRegex(item['url'], r'^https://(dl\.grafana\.com|grafana\.com)/')
            self.assertIn(item['version'], item['file'])

    def test_four_packaged_dashboards_with_stable_identifiers(self):
        self.assertEqual({name: (board['uid'], board['title']) for name, board in self.boards.items()}, UIDS)
        for name, board in self.boards.items():
            self.assertEqual(board['timezone'], 'Asia/Shanghai')
            self.assertLessEqual({panel['type'] for panel in panels(board)}, PANELS)
            sources = set(re.findall(r'"uid": "(sql-apm-[a-z]+)"', json.dumps(board)))
            self.assertLessEqual(sources, {'sql-apm-pg', 'sql-apm-fingerprint'})
            for panel in panels(board):
                for target in panel.get('targets', []):
                    if 'panelId' in target:
                        continue  # a total taken from another panel's result, checked below
                    self.assertTrue(target['rawSql'].startswith('/* ' + board['uid'] + ' panel ' + str(panel['id']) + ' '))
            for variable in board['templating']['list']:
                if variable['type'] == 'query':
                    self.assertTrue(variable['query'].startswith('/* ' + board['uid'] + ' variable ' + variable['name'] + ' */'))
        detail = {item['name'] for item in self.boards['mpp-detail.json']['templating']['list']}
        self.assertLessEqual({'fp', 'identity', 'timing', 'version', 'sqlid', 'mode', 'q', 'hit'}, detail)

    def test_status_dashboard_only_displays_and_shows_run_values_once(self):
        board = self.boards['mpp-status.json']
        listed = list(panels(board))
        self.assertEqual([(panel['type'], panel['title'].split('（')[0]) for panel in listed],
                         [('stat', '上次运行开始于'), ('stat', '上次运行的结果'), ('stat', '上次正常结束的运行'), ('stat', '待处理问题数'),
                          ('table', '各集群现状'), ('table', '待处理问题列表'), ('table', '最近的运行记录')])
        text = json.dumps(board, ensure_ascii=False)
        # Read through the read-only database source only; nothing on the page can act.
        self.assertEqual(set(re.findall(r'"uid": "(sql-apm-[a-z]+)"', text)), {'sql-apm-pg'})
        for panel in listed:
            sql = panel['targets'][0]['rawSql']
            self.assertRegex(sql, r'^/\* mpp-status panel \d+ A \*/ SELECT ')
            self.assertIsNone(re.search(r'\b(INSERT|UPDATE|DELETE|CALL|DO|pg_advisory)\b', sql), sql)
            self.assertNotIn('links', panel['fieldConfig']['defaults'])
            self.assertTrue(panel['description'])
        self.assertEqual({link['title'] for link in board['links']}, {'SQL 检索', 'SQL 列表'})
        self.assertEqual((board['timepicker'], board['refresh']), ({'hidden': True}, '1m'))
        # The time and result of the newest run are the same for every cluster: tiles above, not columns.
        clusters = [item['matcher']['options'] for item in listed[4]['fieldConfig']['overrides']]
        self.assertEqual(clusters, ['cluster', 'newest_imported', 'version_cutoff', 'version_published_at', 'last_result', 'open_problems'])
        self.assertEqual([panel['gridPos'] for panel in listed[:4]], [dict(x=x, y=0, w=6, h=3) for x in (0, 6, 12, 18)])
        self.assertEqual([(panel['gridPos']['y'], panel['gridPos']['h'], panel['gridPos']['w']) for panel in listed[4:]],
                         [(3, 5, 24), (8, 5, 24), (13, 8, 24)])
        for other in ('mpp-search.json', 'mpp-list.json', 'mpp-detail.json'):
            self.assertIn('/d/mpp-status/run-status', [link['url'] for link in self.boards[other]['links']])

    def test_a_total_of_the_whole_list_is_shown_once_and_not_in_every_row(self):
        found = {}
        for name, board in self.boards.items():
            numbered = {panel['id']: panel for panel in panels(board)}
            self.assertEqual(len(numbered), len(list(panels(board))))
            for panel in panels(board):
                for target in panel.get('targets', []):
                    if 'panelId' not in target or panel['type'] == 'barchart':  # the chart of the execution list has its own test
                        continue
                    self.assertEqual((panel['type'], target['datasource']['uid']), ('stat', '-- Dashboard --'))
                    source = numbered[target['panelId']]
                    field = panel['options']['reduceOptions']['fields'].strip('/^$')
                    self.assertEqual(source['type'], 'table')
                    self.assertRegex(source['targets'][0]['rawSql'], r'\b[a-z]\.' + field + r'\b')
                    hidden = [item for item in source['fieldConfig']['overrides'] if item['matcher']['options'] == field
                              and any(entry['id'] == 'custom.hideFrom.viz' and entry['value'] for entry in item['properties'])]
                    self.assertEqual(len(hidden), 1, field)
                    found.setdefault(name, []).append(field)
        self.assertEqual(found, {'mpp-search.json': ['total_structures'], 'mpp-list.json': ['ranked_rows', 'ranked_rows'],
                                 'mpp-detail.json': ['range_texts', 'matching']})

    def test_search_result_example_shows_more_of_the_text_on_its_mark(self):
        panel = [item for item in panels(self.boards['mpp-search.json']) if item.get('title', '').startswith('结果')][0]
        fields = {item['matcher']['options']: {entry['id']: entry['value'] for entry in item['properties']}
                  for item in panel['fieldConfig']['overrides'] if item['matcher']['id'] == 'byName'}
        self.assertEqual((fields['example']['custom.tooltip.field'], fields['example']['custom.tooltip.placement']), ('example_more', 'left'))
        self.assertTrue(fields['example_more']['custom.hideFrom.viz'])
        self.assertRegex(panel['targets'][0]['rawSql'], r'END example_more\n')
        # The row link goes to every field except that text: Grafana adds links given for one
        # field to the links of all fields, so an empty list for it would not remove the link.
        self.assertNotIn('links', panel['fieldConfig']['defaults'])
        linked = [item for item in panel['fieldConfig']['overrides'] if item['matcher']['id'] == 'byRegexp']
        self.assertEqual([[entry['id'] for entry in item['properties']] for item in linked], [['links']])
        pattern = re.compile(linked[0]['matcher']['options'].strip('/'))
        self.assertIsNone(pattern.match('example_more'))
        for name in ('example', 'fingerprint', 'record_count', 'url', 'example_more_2'):
            self.assertIsNotNone(pattern.match(name), name)

    def test_form_code_survives_variable_substitution_and_keeps_text_unescaped(self):
        form = [panel for panel in panels(self.boards['mpp-search.json']) if panel['type'] == 'volkovlabs-form-panel']
        self.assertEqual(len(form), 1)
        options = form[0]['options']
        version = [item['version'] for item in self.manifest['plugins'] if item['id'] == 'volkovlabs-form-panel'][0]
        self.assertEqual(form[0]['pluginVersion'], version)
        for code in (options['initial']['code'], options['update']['code'], options['resetAction']['code']):
            self.assertNotIn('$', code)
            self.assertNotIn('[[', code)
            self.assertNotIn('patchFormValue(', code)
        editor = [element for element in options['elements'] if element['id'] == 'sql'][0]
        self.assertEqual((editor['type'], editor['isEscaping']), ('code', False))
        modes = [element for element in options['elements'] if element['id'] == 'mode'][0]
        self.assertEqual(([option['value'] for option in modes['options']], modes['value']), (['words', 'passage', 'exact'], 'words'))

    def test_coloured_chart_draws_the_same_points_per_text_as_the_main_chart(self):
        charts = {panel['title'].split('：')[0].split('，')[0]: panel for panel in panels(self.boards['mpp-detail.json']) if panel['type'] == 'timeseries'}
        main, coloured = charts['每次执行的耗时'], charts['每格最慢和最快各一次']
        self.assertIn('按${text_order}取前 10 份原文', coloured['title'])
        self.assertIn("WHEN 'slowest'", main['targets'][0]['rawSql'])
        self.assertIn("WHEN 'fastest'", main['targets'][0]['rawSql'])
        # no filter on the kind of point: both the slowest and the fastest of a slot are drawn for each text
        self.assertIn('mpp_view_points(', coloured['targets'][0]['rawSql'])
        self.assertNotIn('p.kind', coloured['targets'][0]['rawSql'])
        for panel in (main, coloured):
            self.assertIn('每格 $__interval', panel['title'])
            self.assertIn('$__interval_ms', panel['targets'][0]['rawSql'])

    def test_tables_keep_headers_whole_and_the_differing_part_carries_the_text(self):
        tables = [panel for board in self.boards.values() for panel in panels(board) if panel['type'] == 'table']
        self.assertGreater(len(tables), 15)
        # A header stays on one line and is never cut: every shown column is at least as wide as its header
        # (14 px for an ideograph or full-width mark, at least 4 px for anything else, 12 px of padding and
        # 18 px for the arrow shown while the table is sorted by it), and a table too wide for the window scrolls sideways.
        for panel in tables:
            self.assertNotIn('wrapHeaderText', panel['fieldConfig']['defaults']['custom'], panel['title'])
            for item in panel['fieldConfig']['overrides']:
                config = {entry['id']: entry['value'] for entry in item['properties']}
                if item['matcher']['id'] != 'byName' or config.get('custom.hideFrom.viz'):
                    continue
                least = sum(14 if ord(char) > 0x2E7F else 4 for char in config['displayName']) + 30
                self.assertGreaterEqual(config.get('custom.width', config.get('custom.minWidth', 0)), least, (panel['title'], config['displayName']))
        # a duration is right-aligned and carries its unit: every duration column is wide enough for "23.9 hours"
        for panel in tables:
            for item in panel['fieldConfig']['overrides']:
                config = {entry['id']: entry['value'] for entry in item['properties']}
                if config.get('unit') == 'ms':
                    self.assertGreaterEqual(config.get('custom.width', config.get('custom.minWidth', 0)), 88, (panel['title'], item['matcher']['options']))
        layers = [panel for panel in panels(self.boards['mpp-detail.json']) if panel['title'].startswith('分层明细')][0]
        self.assertEqual(layers['options']['frozenColumns'], {'left': 1})  # 24 columns: the table scrolls sideways, the bucket stays
        self.assertEqual(len(layers['fieldConfig']['overrides']), 24)
        texts = [panel for panel in panels(self.boards['mpp-detail.json']) if panel['title'].startswith('范围内出现过的原文')][0]
        fields = {item['matcher']['options']: {entry['id']: entry['value'] for entry in item['properties']}
                  for item in texts['fieldConfig']['overrides'] if item['matcher']['id'] == 'byName'}
        self.assertEqual((fields['differing']['custom.tooltip.field'], fields['differing']['custom.tooltip.placement']), ('differing_more', 'right'))
        self.assertTrue(fields['differing_more']['custom.hideFrom.viz'])
        self.assertIn('取前 10 份', texts['title'])
        sql = texts['targets'][0]['rawSql']
        for part in ('mpp_view_common_prefix(', 'differing_more', '各份原文都相同，从略', '这里只是其中一段'):
            self.assertIn(part, sql)
        linked = [item for item in texts['fieldConfig']['overrides'] if item['matcher']['id'] == 'byRegexp']
        self.assertEqual(len(linked), 1)
        self.assertIsNone(re.compile(linked[0]['matcher']['options'].strip('/')).match('differing_more'))

    def test_chart_of_the_execution_list_reuses_the_list(self):
        flat = list(panels(self.boards['mpp-detail.json']))
        listed = [panel for panel in flat if panel['title'].startswith('明细：')][0]
        chart = [panel for panel in flat if panel['title'].startswith('明细图')][0]
        self.assertEqual((chart['type'], chart['datasource']['uid'], chart['targets'][0]['panelId']), ('barchart', '-- Dashboard --', listed['id']))
        self.assertLess(chart['gridPos']['y'], listed['gridPos']['y'])
        drawn = chart['transformations'][0]['options']['include']['names']
        self.assertEqual(drawn, ['bar', 'bar_p50', 'bar_over50', 'bar_over95', 'bar_over99', 'bar_plain', 'bar_none'])
        self.assertEqual((chart['options']['xField'], chart['options']['stacking']), ('bar', 'normal'))
        hidden = {item['matcher']['options'] for item in listed['fieldConfig']['overrides']
                  if any(entry['id'] == 'custom.hideFrom.viz' and entry['value'] for entry in item['properties'])}
        self.assertLessEqual(set(drawn), hidden)  # the list itself looks as before
        colours = {item['matcher']['options']: {entry['id']: entry['value'] for entry in item['properties']} for item in chart['fieldConfig']['overrides']}
        self.assertEqual([colours[name]['color']['fixedColor'] for name in drawn[1:5]], ['green', 'yellow', 'orange', 'red'])
        self.assertEqual((colours['bar_none']['custom.axisPlacement'], colours['bar_none']['max']), ('hidden', 1))
        sql = listed['targets'][0]['rawSql']
        for label in ('不高于 P50', '高于 P50', '高于 P95', '高于 P99', '没有基线', '基线样本不足，不作参照'):
            self.assertIn("'" + label + "'", sql)
        self.assertRegex(sql, r'bar_none,e\.matching\n')  # the total stays the last column

    def test_pages_follow_the_chosen_filters_and_the_chosen_text(self):
        search, listing, detail = (self.boards[name] for name in ('mpp-search.json', 'mpp-list.json', 'mpp-detail.json'))
        # databases and users come from the execution records, so identities without a baseline can be chosen
        for board in (search, listing):
            options = {item['name']: item['query'] for item in board['templating']['list'] if item['name'] in ('database', 'user')}
            self.assertEqual(set(options), {'database', 'user'})
            for query in options.values():
                self.assertIn('FROM mpp_occurrence', query)
                self.assertNotIn('mpp_baseline_group', query)
        # complete SQL: the filters go to the service, and hints are shown only for the filters they were computed for
        form = [panel for panel in panels(search) if panel['type'] == 'volkovlabs-form-panel'][0]
        code = form['options']['update']['code']
        for part in ("['cluster', 'database', 'user'].forEach", 'request[name] = fromToken(variables[name])', 'state.xfor =',
                     "state.xreason = 'empty_input'", "state.xreason = 'input_too_large'", 'bytes.length > 262144', ".replace(/\\r\\n?/g, '\\n')",
                     "state.xbreak = result.line_breaks || ''"):
            self.assertIn(part, code)
        hidden = {item['name'] for item in search['templating']['list'] if item.get('hide') == 2}
        self.assertLessEqual({'xfor', 'sent', 'xhints', 'xstate', 'xreason', 'xsql', 'xbreak', 'q', 'qd', 'fp'}, hidden)
        note = [panel for panel in panels(search) if panel['title'] == '这次检索'][0]['targets'][0]['rawSql']
        for part in ("'${q}${fp}${xstate}${sent}'<>''", '输入为空', '256 KB', "'${xfor}'<>'${cluster}|${database}|${user}'", '三种换行',
                     "'${xsql}'<>'' AND '${xbreak}' IN ('crlf','cr','lf')", '结果按库里的形式给出'):
            self.assertIn(part, note)
        hints = [panel for panel in panels(search) if panel['title'].startswith('整批没有命中')][0]['targets'][0]['rawSql']
        self.assertIn("WHERE '${xfor}'='${cluster}|${database}|${user}'", hints)
        self.assertIn('&var-cluster=${cluster}&var-database=${database}&var-user=${user}', hints)
        # detail: only a text of the structure in view is applied; the raw choice appears in one place
        names = {item['name']: item for item in detail['templating']['list']}
        self.assertIn("mpp_view_sql_text('${norm}','${fp}','${sqlid}') t WHERE t.selected", names['sid']['query'])
        queries = [target['rawSql'] for panel in panels(detail) for target in panel.get('targets', []) if 'rawSql' in target]
        queries += [item['target']['rawSql'] for item in detail['annotations']['list'] if 'target' in item]
        queries += [names[name]['query'] for name in ('sql_text', 'sql_note')]
        self.assertGreater(len([query for query in queries if "'${sid}'" in query]), 6)
        self.assertEqual([query for query in queries if '${sqlid' in query], [])
        # the text panel shows the whole text a segment at a time, and nothing but characters of the text
        self.assertIn('substr(x.sql_text,(x.part-1)*200000+1,200000)', names['sql_text']['query'])
        self.assertNotIn('只显示前', names['sql_text']['query'])
        self.assertEqual((names['part']['type'], names['part']['hide']), ('textbox', 2))
        # the duration chart keeps its plot without any duration: two rows with no value, hidden from legend and tooltip
        chart = [panel for panel in panels(detail) if panel['title'].startswith('每次执行的耗时')][0]
        self.assertEqual([target['refId'] for target in chart['targets']], ['A', 'B', 'C'])
        self.assertIn('NULL::double precision AS "时间范围"', chart['targets'][2]['rawSql'])
        kept = [item for item in chart['fieldConfig']['overrides'] if item['matcher'].get('options') == '时间范围']
        self.assertEqual(kept[0]['properties'][0]['value'], {'legend': True, 'tooltip': True, 'viz': False})

    def test_search_page_is_laid_out_for_a_1080p_screen(self):
        board = self.boards['mpp-search.json']
        placed = {panel['title'].split('：')[0].split('（')[0]: tuple(panel['gridPos'][key] for key in 'xywh') for panel in panels(board)}
        # 21 grid lines (38 px each) fit under the bars of a 1920x1080 browser window: input, note, total and results
        self.assertEqual({name: placed[name] for name in ('输入', '命中的 SQL 结构总数', '这次检索', '结果')},
                         {'输入': (0, 0, 12, 8), '命中的 SQL 结构总数': (12, 0, 12, 3), '这次检索': (12, 3, 12, 5), '结果': (0, 8, 24, 13)})
        self.assertNotIn('三种方式', placed)  # its content moved onto the switch and the panel description
        form = [panel for panel in panels(board) if panel['type'] == 'volkovlabs-form-panel'][0]
        modes = [element for element in form['options']['elements'] if element['id'] == 'mode'][0]
        for text in (form['description'], modes['tooltip']):
            for rule in ('只按空白', '最多 20 个词', '不受 20 个词的限制', '连续出现', '结构指纹相同', '不区分英文字母大小写', '粘贴一个结构指纹值'):
                self.assertIn(rule, text)
        self.assertEqual([option['label'] for option in modes['options']], ['按词：每个词都要出现', '整段：整个输入连续出现', '完整 SQL：结构相同即命中'])
        # the result list does not scroll sideways at that width: fixed widths leave the example at least its minimum
        results = [panel for panel in panels(board) if panel['title'].startswith('结果')][0]
        fields = {item['matcher']['options']: {entry['id']: entry['value'] for entry in item['properties']}
                  for item in results['fieldConfig']['overrides'] if item['matcher']['id'] == 'byName'}
        shown = {name: config for name, config in fields.items() if not config.get('custom.hideFrom.viz')}
        fixed = sum(config.get('custom.width', 0) for config in shown.values())
        self.assertEqual([name for name, config in shown.items() if 'custom.width' not in config], ['example'])
        self.assertLessEqual(fixed + shown['example']['custom.minWidth'], 1780)

    def test_configuration_templates_keep_the_agreed_protections(self):
        ini = (ROOT / 'grafana.ini.template').read_text()
        for line in ('[auth.anonymous]\nenabled = false', 'allow_loading_unsigned_plugins =\n', 'preinstall_disabled = true',
                     'admin_password = $__file{@ADMIN_PASSWORD_FILE@}', 'type = sqlite3', 'default_language = zh-Hans',
                     'default_timezone = Asia/Shanghai', 'allow_sign_up = false'):
            self.assertIn(line, ini)
        sources = (ROOT / 'provisioning/datasources.yaml.template').read_text()
        self.assertIn('url: 127.0.0.1:@PG_PORT@', sources)
        self.assertIn('user: @READONLY_ROLE@', sources)
        self.assertIn('password: $__file{@DB_PASSWORD_FILE@}', sources)
        self.assertIn('url: http://127.0.0.1:@SERVICE_PORT@', sources)
        provider = (ROOT / 'provisioning/dashboards.yaml.template').read_text()
        self.assertIn('allowUiUpdates: false', provider)
        self.assertIn('folderUid: mpp', provider)
        for name in ('sql-apm-grafana.service.template', 'sql-apm-fingerprint.service.template'):
            unit = (ROOT / 'systemd' / name).read_text()
            self.assertIn('Restart=on-failure', unit)
            self.assertIn('WantedBy=multi-user.target', unit)
        self.assertIn('fingerprint-service --port @SERVICE_PORT@', (ROOT / 'systemd/sql-apm-fingerprint.service.template').read_text())

    @unittest.skipUnless(RUNTIME_AVAILABLE, 'requires the pinned PostgreSQL/parser product runtime')
    def test_fingerprint_service_accepts_only_a_loopback_address(self):
        from sql_apm.service.fingerprint import main
        for host in ('0.0.0.0', '192.168.0.10', 'example.org', '::'):
            with contextlib.redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit) as stopped:
                main(['--host', host, '--port', '0'])
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn('loopback', output.getvalue())

    @unittest.skipUnless(RUNTIME_AVAILABLE, 'requires the pinned PostgreSQL/parser product runtime')
    def test_another_line_break_form_is_taken_only_with_a_stored_text_as_proof(self):
        from sql_apm.service.fingerprint import other_form
        from sql_apm.sql.normalization import Normalizer

        class Stored:
            """Stands for the lookup of a stored text by structure and by content with its line breaks as LF."""
            def __init__(self, texts):
                self.texts, self.asked, self.row = texts, [], None

            def execute(self, statement, values):
                rules, fingerprint, low, high, plain = values
                self.asked.append(fingerprint)
                self.row = ('stored',) if self.texts.get(fingerprint) == plain and low <= high else None

            def fetchone(self):
                return self.row

        normalizer = Normalizer()
        value = lambda text: normalizer.normalize(text)['fingerprint']['value']
        # a constant that stays in the structure: the statement is another structure in another line-break form
        kept = "SELECT replace(note, 'kept{}constant', '') AS cleaned{}FROM kept_breaks"
        sent, crlf, cr = (kept.format(mark, mark) for mark in ('\n', '\r\n', '\r'))
        self.assertEqual(len({value(sent), value(crlf), value(cr)}), 3)
        for stored_as, form, other in ((kept.format('\r\n', '\n'), 'crlf', crlf), (kept.format('\r', '\n'), 'cr', cr)):
            cursor = Stored({value(stored_as): sent})
            self.assertEqual(other_form(cursor, normalizer, 'N', value(sent), sent), (form, other.encode(), 'stored'))
            # the structure fits but another character differs: nothing is taken
            self.assertIsNone(other_form(Stored({value(stored_as): sent + ' '}), normalizer, 'N', value(sent), sent))
        # the input's own form is not tried again; a sender of CR LF finds a text stored with LF
        cursor = Stored({value(sent): sent})
        self.assertEqual(other_form(cursor, normalizer, 'N', value(crlf), crlf), ('lf', sent.encode(), 'stored'))
        self.assertEqual(cursor.asked, [value(sent)])
        # where the forms are one structure, and without any line break, nothing is asked
        for text in ("SELECT a\nFROM t WHERE b = 'x\ny'", 'SELECT a FROM t'):
            cursor = Stored({})
            self.assertIsNone(other_form(cursor, normalizer, 'N', value(text), text))
            self.assertEqual(cursor.asked, [])
