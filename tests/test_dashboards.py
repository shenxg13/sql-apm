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
        'mpp-detail.json': ('mpp-detail', 'SQL 详情')}


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

    def test_three_packaged_dashboards_with_stable_identifiers(self):
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

    def test_a_total_of_the_whole_list_is_shown_once_and_not_in_every_row(self):
        found = {}
        for name, board in self.boards.items():
            numbered = {panel['id']: panel for panel in panels(board)}
            self.assertEqual(len(numbered), len(list(panels(board))))
            for panel in panels(board):
                for target in panel.get('targets', []):
                    if 'panelId' not in target:
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
