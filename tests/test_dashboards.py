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
                    self.assertTrue(target['rawSql'].startswith('/* ' + board['uid'] + ' panel ' + str(panel['id']) + ' '))
            for variable in board['templating']['list']:
                if variable['type'] == 'query':
                    self.assertTrue(variable['query'].startswith('/* ' + board['uid'] + ' variable ' + variable['name'] + ' */'))
        detail = {item['name'] for item in self.boards['mpp-detail.json']['templating']['list']}
        self.assertLessEqual({'fp', 'identity', 'timing', 'version', 'sqlid', 'mode', 'q', 'hit'}, detail)

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
