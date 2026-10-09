#!/usr/bin/env python3
"""End-to-end check of the three packaged dashboards in a headless browser.

Development machine only. It drives the environment made by
``setup_dev.py synthetic`` and compares what each panel received with values
computed straight from the base tables. The browser comes from
``prepare_browser.sh``; it is a verification tool and enters no package.

Screenshots and the detailed log stay in --output (a private directory); the
summary holds only check names, counts and digests.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts/grafana')]
from playwright.sync_api import sync_playwright
import build_dashboards as boards
from database import dashboard_data as data

PG = dict(uid='sql-apm-pg')
SUBMIT, RESET = 'data-testid panel button-submit', 'data-testid panel button-reset'
MARK = re.compile(r'^/\* (\S+) (panel \d+|variable \w+|annotation)(?: (\w+))? \*/')


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')


def ms(value):
    """Epoch milliseconds from a time parameter, which Grafana rewrites to ISO form."""
    if value.isdigit():
        return int(value)
    from datetime import datetime
    return round(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def literal(text):
    """A SQL expression for arbitrary text that needs no quoting."""
    return "convert_from(decode('" + base64.b64encode(text.encode()).decode() + "','base64'),'UTF8')"


class Environment:
    def __init__(self, directory, output):
        self.state = json.loads((directory / 'state.json').read_text())
        self.base = 'http://127.0.0.1:%d' % self.state['grafana_port']
        self.passwords = {user: Path(self.state[user + '_password_file']).read_text().strip() for user in ('admin', 'viewer')}
        self.output = output
        self.dashboards = boards.build()
        self.done = []

    def ok(self, label):
        self.done.append(label)
        print('PASS: ' + label, flush=True)

    def api(self, method, path, body=None, user='admin', password=None, anonymous=False):
        request = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        if not anonymous:
            secret = password if password is not None else self.passwords[user]
            request.add_header('Authorization', 'Basic ' + base64.b64encode((user + ':' + secret).encode()).decode())
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read() or b'null')
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except ValueError:
                return error.code, None

    def sql(self, statement, user='admin'):
        """Independent expectation: plain SQL through the read-only data source."""
        status, body = self.api('POST', '/api/ds/query', dict(queries=[dict(refId='A', datasource=PG, rawSql=statement, format='table')],
                                                               **{'from': 'now-1h', 'to': 'now'}), user=user)
        assert status == 200, (status, body, statement[:200])
        values = body['results']['A']['frames'][0]['data']['values']
        return [tuple(column[index] for column in values) for index in range(len(values[0]))] if values else []

    def one(self, statement):
        return self.sql(statement)[0][0]

    def panel(self, board, title):
        found = []
        for panel in self.dashboards[board]['panels']:
            for candidate in [panel] + panel.get('panels', []):
                if candidate.get('title', '').startswith(title):
                    found.append(candidate)
        assert len(found) == 1, (board, title, len(found))
        return found[0]


class Browser:
    """One logged-in page that records every dashboard query result by its tag."""
    def __init__(self, play, env, user='admin', width=1600, height=1300, agent=None):
        self.env = env
        self.browser = play.chromium.launch()
        options = dict(viewport=dict(width=width, height=height), locale='zh-CN', timezone_id='Asia/Shanghai')
        if agent:
            options['user_agent'] = agent
        self.context = self.browser.new_context(**options)
        self.context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=env.base)
        self.page = self.context.new_page()
        self.results, self.services, self.failures, self.requests, self.timings = {}, [], [], [], []
        self.page.on('response', self.record)
        self.page.goto(env.base + '/login')
        self.page.fill('input[name=user]', user)
        self.page.fill('input[name=password]', env.passwords[user])
        self.page.click('button[type=submit]')
        self.page.wait_for_url(lambda url: '/login' not in url, timeout=30000)
        self.page.evaluate("window.localStorage.setItem('grafana.navigation.docked','false')")

    def record(self, response):
        if '/api/ds/query' not in response.url:
            return
        try:
            sent = response.request.post_data_json
            body = response.json()
        except Exception:
            return
        for query in sent.get('queries', []):
            ref, result = query.get('refId'), (body.get('results') or {}).get(query.get('refId')) or {}
            if query.get('datasource', {}).get('type', '').startswith('yesoreyeram'):
                self.services.append((response.status, result))
                continue
            match = MARK.match(query.get('rawSql') or '')
            if not match:
                continue
            key = (match.group(1), match.group(2), match.group(3) or '')
            self.requests.append(key)
            self.timings.append((key, response.request.timing.get('responseEnd', -1)))
            if response.status != 200 or result.get('error'):
                self.failures.append((key, result.get('error') or response.status))
                continue
            frames = result.get('frames') or []
            self.results[key] = [dict(names=[field['name'] for field in frame['schema']['fields']],
                                      rows=list(zip(*frame['data']['values'])) if frame['data']['values'] else [])
                                 for frame in frames]

    def settle(self, extra=1200):
        try:
            self.page.wait_for_load_state('networkidle', timeout=90000)
        except Exception:
            pass
        self.page.wait_for_timeout(extra)

    def open(self, path, extra=1500):
        self.results.clear()
        self.failures.clear()
        del self.requests[:]
        del self.timings[:]
        started = time.monotonic()
        self.page.goto(self.env.base + path)
        self.settle(extra)
        self.loaded_seconds = time.monotonic() - started - extra / 1000
        return self

    def cost(self):
        """Queries of the last page load: how many, their summed and slowest time, and the wall time."""
        known = [value for _, value in self.timings if value >= 0]
        return dict(queries=len(self.timings), summed_seconds=round(sum(known) / 1000, 2),
                    slowest_seconds=round(max(known) / 1000, 2) if known else 0, wall_seconds=round(self.loaded_seconds, 2))

    def rows(self, board, title, ref='A', frame=0):
        key = (board, 'panel ' + str(self.env.panel(board + '.json', title)['id']), ref)
        assert key in self.results, ('no result for panel', title, sorted(self.results)[:40], self.failures[:3])
        frames = self.results[key]
        return frames[frame]['rows'] if frames else []

    def variable(self, board, name):
        frames = self.results.get((board, 'variable ' + name, ''))
        assert frames is not None, ('no result for variable', name)
        return frames[0]['rows'] if frames else []

    def variables(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.page.url).query, keep_blank_values=True)
        return {key[4:] if key.startswith('var-') else key: value[0] for key, value in query.items()}

    def text(self):
        # The code editor renders spaces as non-breaking spaces.
        return self.page.inner_text('body').replace('\u00a0', ' ')

    def shot(self, name):
        self.page.screenshot(path=str(self.env.output / (name + '.png')))

    def total(self, title):
        """The number a total panel displays on the page (it takes it from a table's result, not from a query of its own)."""
        box = self.page.locator('[data-testid^="data-testid Panel header %s"]' % title).first
        box.scroll_into_view_if_needed()
        for _ in range(100):
            lines = [line.strip() for line in box.inner_text().replace('\u00a0', ' ').split('\n') if line.strip()]
            if len(lines) > 1 and lines[-1].replace(',', '').isdigit():
                return int(lines[-1].replace(',', ''))
            self.page.wait_for_timeout(200)
        raise AssertionError(('no number displayed', title, lines))

    # ---- search form
    def type_sql(self, text, then=None):
        editor = self.page.locator('.monaco-editor').first
        editor.click()
        self.page.keyboard.press('Control+A')
        self.page.keyboard.press('Delete')
        # A real paste from the clipboard, as a user does it; synthetic typing would
        # trigger the editor's auto-indent and is not what is being checked.
        self.page.evaluate('(value) => navigator.clipboard.writeText(value)', text)
        self.page.keyboard.press('Control+V')
        self.page.wait_for_timeout(150 if len(text) < 5000 else 800)
        if then:
            self.page.keyboard.type(then)

    def editor_value(self):
        # Only the search form's editor; other pages leave read-only editors behind.
        return self.page.evaluate('''() => {
            const root = document.querySelector('[data-testid^="data-testid Panel header 输入"]');
            return window.monaco.editor.getEditors().filter((item) => root && root.contains(item.getDomNode())).map((item) => item.getValue());
        }''')

    def mode(self):
        return self.page.evaluate("() => (document.querySelector('input[data-testid^=\"data-testid radio-button-option\"]:checked') || {}).getAttribute('data-testid')").rsplit(' ', 1)[1]

    def search(self, text, mode, then=None):
        self.type_sql(text, then)
        self.page.get_by_test_id('data-testid radio-button-option ' + mode).click(force=True)
        self.results.clear()
        del self.services[:]
        before = self.page.url
        self.page.get_by_test_id(SUBMIT).click()
        self.settle(1500)
        # A slow search is still running after the page looked idle: wait for both panels.
        if self.page.url != before:
            wanted = [('mpp-search', 'panel ' + str(self.env.panel('mpp-search.json', title)['id']), 'A') for title in ('这次检索', '结果')]
            for _ in range(600):
                if all(key in self.results or key in [failed for failed, _ in self.failures] for key in wanted):
                    break
                self.page.wait_for_timeout(200)

    def close(self):
        self.browser.close()


def hover_text(text):
    """What the mark on an example cell shows for this original text, computed apart from the dashboard query."""
    kept, used, dropped = [], 0, False
    for number, line in enumerate(re.split('\r?\n', text[:boards.HOVER_CHARS]), 1):
        used += 1 + (len(line) + (len(line.encode()) - len(line)) // 2) // boards.HOVER_WRAP
        if used <= boards.HOVER_LINES or number == 1:
            kept.append(line)
        else:
            dropped = True
    note = '\n……（这份原文共 %d 个字符，这里只是开头；点这一行进入详情看全文）' % len(text)
    return '\n'.join(kept) + (note if dropped or len(text) > boards.HOVER_CHARS else '')


def fixed_inputs():
    """G10: characters that a careless transport changes without any error."""
    return [('line breaks, tabs and double quotes', 'select a,\n\t"b"  from "t" -- note\nwhere x = \'1\'\n'),
            ('backslashes and a literal \\n', "SELECT E'a\\nb', 'c:\\\\dir\\n', '\\\\\\\\' FROM t"),
            ('dollar names and template marks', 'SELECT $name, ${var}, $__timeFrom(), [[x]], {{y}}, %s, $1 FROM t WHERE a = $2'),
            ('Chinese and other non-ASCII', "SELECT '中文，全角；引号“”' AS 名称, 'é', '😀' FROM 表"),
            ('a statement of more than 60 KB', data.LONG)]


def arrival(env, play):
    """G10: what reaches the database and the service is byte-identical to the input."""
    browser = Browser(play, env)
    browser.open('/d/mpp-search/sql-search')
    cases = fixed_inputs()
    assert len(cases[-1][1].encode()) >= 60 * 1024
    digests = []
    for label, text in cases + [('typing after a paste', 'SELECT 1,\n\'a\\nb\'')]:
        then = ' x' if label == 'typing after a paste' else None
        final = text + (then or '')
        expected = hashlib.sha256(final.encode()).hexdigest()
        for mode in ('words', 'passage'):
            browser.search(text, mode, then)
            note = dict(browser.rows('mpp-search', '这次检索'))
            assert note['到达数据库的输入'] == '%d 个字符，SHA-256 %s' % (len(final), expected), (label, mode, note['到达数据库的输入'][:60])
            assert browser.variables()['q'] == b64(final) and browser.variables()['mode'] == mode
        browser.search(text, 'exact', then)
        status, answer = browser.services[-1]
        received = answer['frames'][0]['schema']['meta']['custom']['data']['input']
        assert status == 200 and received == dict(bytes=len(final.encode()), sha256=expected), (label, received)
        assert browser.variables()['q'] == '' and browser.editor_value() == [final]
        digests.append((label, len(final.encode()), expected[:12]))
    # The editor keeps one line-break form: a pasted CR LF arrives as LF, nothing else changes.
    browser.search('SELECT 1,\r\n2\r\nFROM t', 'exact')
    received = browser.services[-1][1]['frames'][0]['schema']['meta']['custom']['data']['input']
    assert received['sha256'] == hashlib.sha256(b'SELECT 1,\n2\nFROM t').hexdigest()
    log = Path(env.state['service_log']).read_text()
    assert '中文' not in log and 'SELECT' not in log.upper().replace('"RESULT"', '') and str(len(data.LONG.encode())) in log
    browser.close()
    # The same editor under a Windows browser identity: line breaks must not change either.
    windows = Browser(play, env, agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')
    windows.open('/d/mpp-search/sql-search')
    multi = 'SELECT a\nFROM t\nWHERE b = 1'
    windows.search(multi, 'exact')
    received = windows.services[-1][1]['frames'][0]['schema']['meta']['custom']['data']['input']
    assert received['sha256'] == hashlib.sha256(multi.encode()).hexdigest(), 'line breaks differ under a Windows browser identity'
    windows.search(multi, 'passage')
    assert dict(windows.rows('mpp-search', '这次检索'))['到达数据库的输入'].endswith(hashlib.sha256(multi.encode()).hexdigest())
    windows.close()
    env.ok('G10: %d fixed inputs in three modes (and one under a Windows browser identity) reach the database and the service with the same digest: %s'
           % (len(digests), '; '.join('%s %d bytes %s' % item for item in digests)))


def configuration(env):
    """G6/G7: versions, storage, provisioning, plugins."""
    manifest = json.loads((ROOT / 'grafana/components.json').read_text())
    status, health = env.api('GET', '/api/health', anonymous=True)
    assert status == 200 and health['version'] == manifest['grafana']['version'] and health['version'].startswith('13.')
    home = Path(env.state['home'])
    ini = (home / 'conf/grafana.ini').read_text()
    assert 'type = sqlite3' in ini and (home / 'data/grafana.db').is_file()
    assert 'allow_loading_unsigned_plugins =\n' in ini and 'preinstall_disabled = true' in ini
    assert env.passwords['admin'] not in ini and 'default_language = zh-Hans' in ini
    _, sources = env.api('GET', '/api/datasources')
    listed = {item['uid']: item for item in sources}
    assert set(listed) == {'sql-apm-pg', 'sql-apm-fingerprint'} and all(item['readOnly'] for item in sources)
    assert listed['sql-apm-pg']['url'] == '127.0.0.1:%d' % env.state['pg_port'] and listed['sql-apm-pg']['user'] == env.state['readonly_role']
    assert listed['sql-apm-fingerprint']['url'] == 'http://127.0.0.1:%d' % env.state['service_port']
    assert env.sql("SELECT current_user, host(inet_server_addr())") == [(env.state['readonly_role'], '127.0.0.1')]
    psql = [str(Path(env.state['pg_bin']) / 'psql'), '-X', '-w', '-Atc', 'SELECT current_user', '-h', '127.0.0.1', '-p', str(env.state['pg_port']),
            '-U', env.state['readonly_role'], '-d', env.state['database']]
    clean = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    wrong = subprocess.run(psql, env=dict(clean, PGPASSWORD='not-the-password'), capture_output=True, text=True)
    assert wrong.returncode and 'password authentication failed' in wrong.stderr
    assert subprocess.run(psql, env=clean, capture_output=True, text=True).returncode
    right = subprocess.run(psql, env=dict(clean, PGPASSFILE=str(Path(env.state['directory']) / 'private/service-pgpass')), capture_output=True, text=True)
    assert right.stdout.strip() == env.state['readonly_role']
    hba = (Path(env.state['directory']) / 'pgdata/pg_hba.conf').read_text()
    assert 'host %s %s 127.0.0.1/32 scram-sha-256' % (env.state['database'], env.state['readonly_role']) in hba
    _, found = env.api('GET', '/api/search?type=dash-db')
    assert {(item['uid'], item['title'], item.get('folderTitle')) for item in found} == \
        {('mpp-search', 'SQL 检索', 'MPP'), ('mpp-list', 'SQL 列表', 'MPP'), ('mpp-detail', 'SQL 详情', 'MPP')}
    for name, board in env.dashboards.items():
        assert board['timezone'] == 'Asia/Shanghai'
        _, live = env.api('GET', '/api/dashboards/uid/' + board['uid'])
        assert live['meta']['provisioned'] and live['dashboard']['description'] == board['description']
    _, preferences = env.api('GET', '/api/org/preferences')
    assert preferences['timezone'] == 'Asia/Shanghai' and preferences['language'] == 'zh-Hans'
    env.ok('G4/G6: Grafana %s from the pinned manifest; own data in its file database; data sources and three dashboards usable without UI work; '
           'both data sources on 127.0.0.1, the database one as the read-only account with a password; Chinese and Beijing time' % health['version'])
    _, plugins = env.api('GET', '/api/plugins')
    external = {item['id']: item for item in plugins if item.get('signatureType') in ('community', 'commercial') or item['id'] in {p['id'] for p in manifest['plugins']}}
    assert set(external) == {plugin['id'] for plugin in manifest['plugins']} == {'grafana-postgresql-datasource', 'volkovlabs-form-panel', 'yesoreyeram-infinity-datasource'}
    for plugin in manifest['plugins']:
        assert external[plugin['id']]['info']['version'] == plugin['version'] and external[plugin['id']]['signature'] == 'valid'
    assert sorted(item.name for item in (home / 'plugins').iterdir()) == sorted(external)
    log = (home / 'logs/grafana.log').read_text()
    assert 'Installing plugin' not in log and 'msg="Plugin successfully installed"' not in log
    allowed = {'table', 'stat', 'text', 'timeseries', 'barchart', 'bargauge', 'row', 'volkovlabs-form-panel'}
    sources = {'grafana-postgresql-datasource', 'yesoreyeram-infinity-datasource', 'grafana'}
    for board in env.dashboards.values():
        text = json.dumps(board)
        types = {panel['type'] for outer in board['panels'] for panel in [outer] + outer.get('panels', [])}
        assert types <= allowed, types - allowed
        assert set(re.findall(r'"type": "([a-z-]+-datasource|grafana)"', text)) <= sources
    env.ok('G7: exactly the three pinned plugins with valid signatures; unsigned plugins not allowed; nothing downloaded at start-up; '
           'packaged dashboards use only them and built-in panels')


def login(env, play):
    """G8: no access without a login; the viewer can use everything and save nothing."""
    for path in ('/api/search', '/api/dashboards/uid/mpp-search', '/api/datasources'):
        assert env.api('GET', path, anonymous=True)[0] == 401
    assert env.api('POST', '/api/ds/query', dict(queries=[]), anonymous=True)[0] == 401
    assert env.api('GET', '/api/org', password='admin')[0] == 401 and env.api('GET', '/api/org', user='viewer', password='viewer')[0] == 401
    browser = play.chromium.launch()
    page = browser.new_page()
    page.goto(env.base + '/d/mpp-search/sql-search')
    page.wait_for_url(lambda url: '/login' in url, timeout=30000)
    browser.close()
    assert env.api('GET', '/api/user', user='viewer')[1]['login'] == 'viewer'
    assert [org['role'] for org in env.api('GET', '/api/user/orgs', user='viewer')[1]] == ['Viewer']
    viewer = Browser(play, env, user='viewer')
    viewer.open('/d/mpp-search/sql-search')
    viewer.search('orders status', 'words')
    assert len(viewer.rows('mpp-search', '结果')) == 1 and not viewer.failures
    viewer.search(data.SPARSE, 'exact')
    assert viewer.variables()['xstate'] == 'has_baseline' and len(viewer.rows('mpp-search', '结果')) == 1
    viewer.open('/d/mpp-list/sql-list?from=%d&to=%d' % env.last_day)
    assert viewer.rows('mpp-list', 'SQL 身份排行：所选时间范围内') and viewer.rows('mpp-list', 'SQL 身份排行：当前基线版本') and not viewer.failures
    viewer.open(env.detail)
    assert viewer.rows('mpp-detail', '关键数值') and viewer.rows('mpp-detail', '三项样本条件') and not viewer.failures
    viewer.close()
    _, live = env.api('GET', '/api/dashboards/uid/mpp-list', user='viewer')
    copy = dict(live['dashboard'], uid='viewer-copy', id=None, title='viewer copy')
    assert env.api('POST', '/api/dashboards/db', dict(dashboard=copy, folderUid='mpp-custom', overwrite=False), user='viewer')[0] == 403
    assert env.api('POST', '/api/folders', dict(uid='viewer-folder', title='x'), user='viewer')[0] == 403
    assert env.sql('SELECT 1', user='viewer') == [(1,)]
    status, body = env.api('POST', '/api/ds/query', dict(queries=[dict(refId='A', datasource=PG, rawSql='DELETE FROM mpp_occurrence', format='table')],
                                                       **{'from': 'now-1h', 'to': 'now'}), user='viewer')
    assert status >= 400 and 'read-only transaction' in json.dumps(body)
    env.ok('G8: anonymous requests and default passwords refused; the viewer uses search and all three dashboards, cannot save a dashboard or a folder, '
           'and its statements run as the read-only account')


def folders(env):
    """G9: packaged dashboards are read-only; copies and everything else survive a new packaged version."""
    assert env.api('GET', '/api/folders/mpp')[1]['title'] == 'MPP'
    custom = env.api('GET', '/api/folders/mpp-custom')[1]
    assert custom['title'] == '用户自定义' and custom['parentUid'] == 'mpp'
    _, live = env.api('GET', '/api/dashboards/uid/mpp-list')
    changed = dict(live['dashboard'], title='SQL 列表（改）')
    status, body = env.api('POST', '/api/dashboards/db', dict(dashboard=changed, folderUid='mpp', overwrite=True))
    assert status == 400 and 'provisioned' in json.dumps(body).lower(), (status, body)
    assert env.api('DELETE', '/api/dashboards/uid/mpp-list')[0] == 400
    env.api('DELETE', '/api/dashboards/uid/mpp-e2e-copy')
    copy = dict(live['dashboard'], uid='mpp-e2e-copy', id=None, title='SQL 列表（我的副本）')
    status, saved = env.api('POST', '/api/dashboards/db', dict(dashboard=copy, folderUid='mpp-custom', overwrite=False))
    assert status == 200 and env.api('GET', '/api/dashboards/uid/mpp-e2e-copy')[1]['meta']['folderUid'] == 'mpp-custom'
    users = env.api('GET', '/api/org/users')[1]
    preferences = env.api('GET', '/api/org/preferences')[1]
    home = Path(env.state['home'])
    target = home / 'dashboards/mpp/mpp-list.json'
    original = target.read_text()
    try:
        document = json.loads(original)
        document['description'] = 'packaged revision for the coexistence check'
        target.write_text(json.dumps(document, ensure_ascii=False))
        assert env.api('POST', '/api/admin/provisioning/dashboards/reload')[0] == 200
        for _ in range(40):
            if env.api('GET', '/api/dashboards/uid/mpp-list')[1]['dashboard']['description'] == document['description']:
                break
            time.sleep(0.5)
        else:
            raise AssertionError('new packaged version was not loaded')
        assert env.api('GET', '/api/dashboards/uid/mpp-e2e-copy')[1]['dashboard']['title'] == 'SQL 列表（我的副本）'
        assert env.api('GET', '/api/org/users')[1] == users and env.api('GET', '/api/org/preferences')[1] == preferences
        assert env.api('GET', '/api/folders/mpp-custom')[1]['parentUid'] == 'mpp'
        assert {item['uid'] for item in env.api('GET', '/api/search?type=dash-db&folderUIDs=mpp')[1]} == {'mpp-search', 'mpp-list', 'mpp-detail'}
    finally:
        target.write_text(original)
        env.api('POST', '/api/admin/provisioning/dashboards/reload')
    for _ in range(40):
        if env.api('GET', '/api/dashboards/uid/mpp-list')[1]['dashboard']['description'] == json.loads(original)['description']:
            break
        time.sleep(0.5)
    assert env.api('DELETE', '/api/dashboards/uid/mpp-e2e-copy')[0] == 200
    env.ok('G9: MPP holds the three packaged dashboards and 用户自定义; saving over or deleting a packaged dashboard is refused; '
           'a copy saved into 用户自定义, the accounts and the settings are unchanged after a new packaged version is loaded')


def search(env, play):
    """G11/G12: the search page, entering the detail page, and coming back."""
    browser = Browser(play, env, height=5200)  # tall enough for every panel to load
    browser.open('/d/mpp-search/sql-search')
    assert browser.mode() == 'words'
    body = browser.text()
    for label in ('集群', '数据库', '执行用户', '时间', '按词：每个词都要出现', '整段：整个输入连续出现', '完整 SQL：结构相同即命中', '检索'):
        assert label in body, label
    assert browser.variable('mpp-search', 'cluster') == [('全部', '*', 0), ('C1', b64('C1'), 1), ('C2', b64('C2'), 1)]
    # words: explanation, total and cap
    browser.search('wide_ SELECT', 'words')
    rows = browser.rows('mpp-search', '结果')
    names = [field for field in browser.results[('mpp-search', 'panel %d' % env.panel('mpp-search.json', '结果')['id'], 'A')][0]['names']]
    assert len(rows) == 50 and {row[names.index('total_structures')] for row in rows} == {60}
    # the total is shown once beside the explanation, not as a column repeated in every row
    assert browser.total('命中的 SQL 结构总数') == 60
    assert '结构总数' not in browser.page.locator('[data-testid^="data-testid Panel header 结果"]').first.inner_text()
    note = dict(browser.rows('mpp-search', '这次检索'))
    assert note['检索方式'].startswith('按词') and note['切出的词'] == '共 2 个：wide_  ｜  select'
    assert urllib.parse.urlparse(browser.page.url).path.startswith('/d/mpp-search')
    # the mark on an example cell shows more of that original text: unchanged when short, its beginning when long
    more = names.index('example_more')
    for words, text in (('special_chars', data.SPECIAL), ('big_list', data.LONG)):
        browser.open('/d/mpp-search/sql-search?var-mode=words&var-q=%s' % b64(words), extra=2500)
        found = browser.rows('mpp-search', '结果')
        assert len(found) == 1 and found[0][more] == hover_text(text), words
    assert found[0][more].startswith(data.LONG[:boards.HOVER_CHARS] + '\n……（这份原文共 %d 个字符' % len(data.LONG))
    panel = browser.page.locator('[data-testid^="data-testid Panel header 结果"]').first
    mark, box = panel.get_by_test_id('data-testid tableng tooltip caret'), browser.page.get_by_test_id('data-testid tableng tooltip wrapper')
    assert mark.count() == 1 and box.count() == 0
    mark.hover()
    browser.page.wait_for_timeout(800)
    shown = box.first.inner_text().replace('\u00a0', ' ')
    assert ' '.join(shown.split()) == ' '.join(found[0][more].split()) and box.first.locator('a').count() == 0
    assert box.first.bounding_box()['height'] < 620, box.first.bounding_box()
    mark.click()  # a click keeps it while the pointer is elsewhere; a click elsewhere closes it; neither leaves the page
    browser.page.mouse.move(300, 300)
    browser.page.wait_for_timeout(500)
    assert box.first.is_visible() and urllib.parse.urlparse(browser.page.url).path.startswith('/d/mpp-search')
    browser.page.mouse.click(300, 300)
    browser.page.wait_for_timeout(500)
    assert box.count() == 0 or not box.first.is_visible()
    assert panel.locator('a').count() > 0  # the cells of the row still lead to the detail page
    browser.search(' '.join('w%d' % n for n in range(21)), 'words')
    note = dict(browser.rows('mpp-search', '这次检索'))
    assert '超过 20 个；请改用“整段”方式' in note['没有检索'] and browser.rows('mpp-search', '结果') == []
    assert '请改用“整段”方式' in browser.text()
    # cluster filter and time filter
    browser.search('orders status', 'words')
    whole = browser.rows('mpp-search', '结果')[0]
    busy = whole[names.index('fingerprint')]
    assert whole[names.index('record_count')] == env.one("SELECT count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.value='%s'" % busy)
    assert (whole[names.index('matched_texts')], whole[names.index('structure_texts')], whole[names.index('identities')]) == (80, 80, 3)
    browser.open('/d/mpp-search/sql-search?var-mode=words&var-q=%s&var-cluster=%s' % (b64('orders status'), b64('C2')))
    assert browser.rows('mpp-search', '结果')[0][names.index('record_count')] == 300
    assert browser.editor_value() == ['orders status'] and browser.mode() == 'words'
    browser.open('/d/mpp-search/sql-search?var-mode=words&var-q=%s&var-timefilter=on&from=%d&to=%d' % ((b64('orders status'),) + env.last_day))
    assert browser.rows('mpp-search', '结果')[0][names.index('record_count')] == env.one(
        "SELECT count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.value='%s' AND o.end_at>=to_timestamp(%d/1000.0) AND o.end_at<to_timestamp(%d/1000.0)"
        % ((busy,) + env.last_day))
    env.ok('G11: mode switch with the meanings on it, default by words; filters; this-search note with the words or the refusal; one list for every mode, 50 of 60 with the total; the mark on an example cell shows more of that text; stays on the list')
    # entering the detail page from a words search
    browser.open('/d/mpp-search/sql-search')
    browser.search("status '2026-06-28'", 'words')
    row = browser.rows('mpp-search', '结果')[0]
    # Identity and range come from the records of the texts that were hit, so the hits are inside the range.
    top = env.sql("SELECT o.scope_id,o.database,o.execution_user,count(*),(extract(epoch FROM max(o.end_at))*1000)::bigint FROM mpp_occurrence o "
                  "JOIN mpp_fingerprint f USING(sql_id) JOIN mpp_sql_text t USING(sql_id) WHERE f.value='%s' AND strpos(t.text,%s)>0 "
                  "GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 1" % (busy, literal("'2026-06-28'")))[0]
    assert row[names.index('matched_texts')] == 5
    browser.page.locator('[data-testid^="data-testid Panel header 结果"] a').first.click()
    browser.settle(2500)
    opened = browser.variables()
    assert urllib.parse.urlparse(browser.page.url).path.startswith('/d/mpp-detail')
    assert opened['fp'] == busy and json.loads(base64.urlsafe_b64decode(opened['identity'] + '==')) == list(top[:3])
    assert ms(opened['to']) - ms(opened['from']) == 7 * 86400 * 1000 + 1 and abs(ms(opened['to']) - 1 - top[4]) <= 1
    assert opened['mode'] == 'words' and opened['q'] == b64("status '2026-06-28'") and opened['sqlid'] == ''
    texts = browser.rows('mpp-detail', '范围内出现过的原文')
    hit = [row[1] for row in texts]
    assert hit[:5] == ['命中'] * 5 and set(hit[5:]) == {''} and len(texts) == 10
    # back to the search page: the box and the mode are filled in again
    browser.page.get_by_text('回到 SQL 检索').click()
    browser.settle(2500)
    assert urllib.parse.urlparse(browser.page.url).path.startswith('/d/mpp-search')
    assert browser.editor_value() == ["status '2026-06-28'"] and browser.mode() == 'words'
    assert browser.variables()['q'] == b64("status '2026-06-28'") and len(browser.rows('mpp-search', '结果')) == 1
    # a passage that hits one text opens on that text
    browser.search('"a"."b" FROM\n   "order ITEMS"', 'passage')
    row = browser.rows('mpp-search', '结果')[0]
    assert row[names.index('matched_texts')] == 1
    quoted = env.one('SELECT sql_id FROM mpp_sql_text WHERE text=' + literal(data.QUOTED))
    browser.page.locator('[data-testid^="data-testid Panel header 结果"] a').first.click()
    browser.settle(2500)
    assert browser.variables()['sqlid'] == quoted and browser.variables()['mode'] == 'passage'
    assert 'Order Items' in browser.text() and '所选的一份原文' in browser.text()
    browser.page.go_back()
    browser.settle(2500)
    assert browser.editor_value() == ['"a"."b" FROM\n   "order ITEMS"'] and browser.mode() == 'passage'
    # searching again from the refilled box sends exactly the same text
    browser.page.get_by_test_id(SUBMIT).click()
    browser.settle(1500)
    assert browser.variables()['q'] == b64('"a"."b" FROM\n   "order ITEMS"') and browser.variables()['mode'] == 'passage'
    # complete SQL: identical stored text, then a different layout of the same structure
    chosen_text = data.BUSY.format(status=3, day='2026-06-28')
    chosen = env.one('SELECT sql_id FROM mpp_sql_text WHERE text=' + literal(chosen_text))
    browser.search(chosen_text, 'exact')
    state = browser.variables()
    assert (state['mode'], state['fp'], state['xstate'], state['xsql'], state['q']) == ('exact', busy, 'has_baseline', chosen, '')
    note = dict(browser.rows('mpp-search', '这次检索'))
    assert note['结果'] == '库里有这个结构，并且有基线' and chosen in note['一字不差的原文'] and note['结构指纹'] == busy
    row = browser.rows('mpp-search', '结果')
    assert len(row) == 1 and row[0][names.index('fingerprint')] == busy and row[0][names.index('record_count')] == whole[names.index('record_count')]
    browser.page.locator('[data-testid^="data-testid Panel header 结果"] a').first.click()
    browser.settle(2500)
    opened = browser.variables()
    assert opened['sqlid'] == chosen and opened['hit'] == chosen and opened['mode'] == 'exact'
    assert {row[5] for row in browser.rows('mpp-detail', '明细：')} == {chosen}
    browser.page.get_by_text('回到 SQL 检索').click()
    browser.settle(2500)
    assert browser.editor_value() == [chosen_text] and browser.mode() == 'exact' and browser.variables()['fp'] == busy
    browser.search('select  O.ID, o.amount\nfrom orders o where o.status = 99 and o.created_at >= \'2000-01-01\'', 'exact')
    assert browser.variables()['xsql'] == '' and browser.variables()['fp'] == busy
    assert dict(browser.rows('mpp-search', '这次检索'))['一字不差的原文'] == '库里没有与输入一字不差的原文'
    # the other outcomes
    for text, state, phrase in ((data.FAILING, 'records_without_baseline', '当前版本没有它的基线'), ('SELECT never FROM seen_e2e', 'not_seen', '库里没有这个结构'),
                                ('SELECT ?', 'unreliable_fingerprint', '无法生成可靠指纹')):
        browser.search(text, 'exact')
        assert browser.variables()['xstate'] == state and phrase in dict(browser.rows('mpp-search', '这次检索'))['结果'], state
        assert len(browser.rows('mpp-search', '结果')) == (1 if state == 'records_without_baseline' else 0)
    # a batch that did not match as a whole: per-statement hints, each selectable
    browser.search('SELECT x FROM nowhere_e2e; ' + data.SPARSE.replace('north', 'east'), 'exact')
    hints = browser.rows('mpp-search', '整批没有命中时的逐条提示')
    sparse = env.one("SELECT f.value FROM mpp_fingerprint f JOIN mpp_sql_text t USING(sql_id) WHERE t.text=" + literal(data.SPARSE))
    assert [(h[0], h[1], h[3]) for h in hints] == [(1, '库里没有这个结构', hints[0][3]), (2, '库里有这个结构，并且有基线', sparse)]
    assert browser.variables()['xstate'] == 'not_seen' and browser.rows('mpp-search', '结果') == []
    browser.page.locator('[data-testid^="data-testid Panel header 整批没有命中"] a').nth(len(hints[0]) - 1).click()
    browser.settle(2500)
    assert browser.variables()['fp'] == sparse and len(browser.rows('mpp-search', '结果')) == 1
    assert len(browser.rows('mpp-search', '整批没有命中时的逐条提示')) == 2
    # the service is not needed for words and passage; without it, complete SQL says so
    browser.shot('search')
    browser.close()
    env.ok('G12: a row opens the detail page on the identity with the most records and its last seven days; texts hit by the search come first and are marked; '
           'a single hit or an identical stored text opens on that text; batch hints can be selected one by one; coming back refills the input and the mode')


def service_down(env, play):
    """G5: with the service stopped only complete-SQL search is affected, and says so."""
    pid_file = Path(env.state['directory']) / 'run/fingerprint.pid'
    pid = int(pid_file.read_text())
    listening = subprocess.run(['ss', '-ltnH'], capture_output=True, text=True).stdout
    assert ('127.0.0.1:%d ' % env.state['service_port']) in listening and ('0.0.0.0:%d ' % env.state['service_port']) not in listening
    os.killpg(pid, signal.SIGTERM)
    time.sleep(1.5)
    browser = Browser(play, env)
    try:
        browser.open('/d/mpp-search/sql-search')
        browser.search(data.SPARSE, 'exact')
        assert browser.variables()['xstate'] == 'service_unavailable'
        assert dict(browser.rows('mpp-search', '这次检索'))['结果'].startswith('指纹服务不可用')
        assert '指纹服务不可用' in browser.text()
        browser.search('orders status', 'words')
        assert len(browser.rows('mpp-search', '结果')) == 1
        browser.open(env.detail)
        assert browser.rows('mpp-detail', '关键数值') and browser.rows('mpp-detail', '三项样本条件') and not browser.failures
    finally:
        browser.close()
        environment = dict(os.environ, PGPASSFILE=str(Path(env.state['directory']) / 'private/service-pgpass'),
                           SQL_APM_DSN='host=127.0.0.1 port=%d dbname=%s user=%s' % (env.state['pg_port'], env.state['database'], env.state['readonly_role']))
        with open(env.state['service_log'], 'a') as stream:
            process = subprocess.Popen([env.state['python'], '-m', 'sql_apm', 'fingerprint-service',
                                        '--port', str(env.state['service_port']), '--schema', env.state['schema']], cwd=ROOT, env=environment,
                                       stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        pid_file.write_text(str(process.pid))
        for _ in range(60):
            try:
                urllib.request.urlopen('http://127.0.0.1:%d/v1/health' % env.state['service_port'], timeout=2)
                break
            except OSError:
                time.sleep(0.5)
    env.ok('G5: the service listens on the loopback address only; while it is stopped complete-SQL search says so and words, passages and the other pages work')


def boards_label(outcome):
    return dict(success='成功', failed='失败', cancelled='取消', timed_out='超时', unknown='结果未知')[outcome]


def expectations(env, fingerprint, where, timing="o.timing_type='request'"):
    """Records of one identity and timing category in the detail range, straight from the base tables."""
    return ("FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.value='%s' AND %s AND (%s OR (o.timing_type IS NULL AND o.outcome<>'success')) "
            "AND o.end_at>=to_timestamp(%d/1000.0) AND o.end_at<to_timestamp(%d/1000.0)" % ((fingerprint, where, timing) + env.range))


def detail(env, play):
    """G13–G19 on the busiest synthetic identity, the extended-protocol SQL and the small ones."""
    browser = Browser(play, env, height=5200)
    busy, where = env.busy, "o.scope_id='C1' AND o.database='shop' AND o.execution_user='app_user'"
    facts = expectations(env, busy, where)
    browser.open(env.detail, extra=3000)
    assert not browser.failures, browser.failures[:3]
    # ---- G13: top bar and SQL text
    identities = browser.variable('mpp-detail', 'identity')
    assert [row[0] for row in identities] == [row[0] for row in env.sql(
        "SELECT o.scope_id||' / '||o.database||' / '||o.execution_user||'（'||count(*)||' 条记录）',count(*) FROM mpp_occurrence o "
        "JOIN mpp_fingerprint f USING(sql_id) WHERE f.value='%s' GROUP BY o.scope_id,o.database,o.execution_user ORDER BY 2 DESC" % busy)]
    assert [row[1] for row in browser.variable('mpp-detail', 'timing')] == ['request']
    versions = browser.variable('mpp-detail', 'version')
    published = env.sql("SELECT b.build_id FROM build b JOIN publication p USING(build_id) LEFT JOIN current_version v USING(build_id) "
                        "WHERE b.scope_id='C1' AND p.result='published' ORDER BY (v.build_id IS NOT NULL) DESC,p.at DESC")
    assert [row[1] for row in versions] == [row[0] for row in published] and '当前生效' in versions[0][0]
    current = versions[0][1]
    body = browser.text()
    for label in ('指纹', '身份', '计时类别', '基线版本', '状态', '耗时不低于', '耗时不高于', '明细排序', '原文排序', '分层明细', 'FROM orders o', '同一结构的一份示例（共 80 份原文）'):
        assert label in body, label
    assert '异常' not in body
    listed = browser.rows('mpp-detail', '这个集群已发布的版本')
    assert [row[-1] for row in listed] == [row[0] for row in published] and listed[0][0] == '● 正在查看' and listed[1][-2] == '可选作参照'
    env.ok('G13: the identity list holds exactly the combinations that occurred, the timing category defaults correctly, only usable versions can be chosen; '
           'the SQL text panel shows an example of the structure; the page states no anomaly verdict')
    # ---- G14: baseline area equals the stored statistics
    stored = env.sql("SELECT s.included_count,cardinality(s.active_dates),s.p50_ms,s.p95_ms,s.p99_ms,s.max_ms,s.min_ms,s.p25_ms,s.p75_ms,s.p90_ms "
                     "FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id) WHERE s.build_id='%s' AND s.layer='overall' AND g.fingerprint_value='%s' "
                     "AND g.scope_id='C1' AND g.database='shop' AND g.execution_user='app_user' AND g.timing_type='request'" % (current, busy))[0]
    key = browser.rows('mpp-detail', '关键数值')
    assert [(row[0], float(row[1])) for row in key] == list(zip(('样本数', '活跃天数', 'P50', 'P95', 'P99', '最大'), map(float, stored[:6])))
    assert {row[3] for row in key} == {'text'}
    bars = browser.rows('mpp-detail', '耗时分位')[0]
    assert [float(x) for x in bars] == [float(stored[i]) for i in (6, 7, 2, 8, 9, 3, 4, 5)]
    overview = browser.rows('mpp-detail', '五类计时一览')
    assert overview[0][0] == '请求整体' and overview[0][1] == stored[0] and overview[-1][0] == '没有样本的类别：Execute 首次、Execute 续取、Parse、Bind'
    assert len(overview) == 2 and '没有样本的类别：Execute 首次、Execute 续取、Parse、Bind' in body
    conditions = browser.rows('mpp-detail', '三项样本条件')
    assert [(row[0], row[1]) for row in conditions] == [('基础', '满足'), ('P95', '满足'), ('P99', '满足')]
    hour = browser.rows('mpp-detail', '一天内各小时的规律')
    day = browser.rows('mpp-detail', '训练窗口内每一天')
    direct_hour = env.sql("SELECT lpad(s.bucket_number::text,2,'0')||' 时',s.p50_ms,s.p95_ms FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id) "
                          "WHERE s.build_id='%s' AND s.layer='hour' AND s.included_count>0 AND g.fingerprint_value='%s' AND g.scope_id='C1' AND g.database='shop' "
                          "AND g.execution_user='app_user' AND g.timing_type='request' ORDER BY s.bucket_number" % (current, busy))
    assert [(r[0], float(r[1]), float(r[2])) for r in hour] == [(r[0], float(r[1]), float(r[2])) for r in direct_hour] and len(hour) == 12 and len(day) == 28
    # ---- G16: comparison
    known = env.one("SELECT count(o.duration_ms) " + facts)
    compare = browser.rows('mpp-detail', '超过基线的执行')
    for row, index in zip(compare, (2, 3, 4)):
        above = env.one("SELECT count(*) " + facts + " AND o.duration_ms>%s" % stored[index])
        assert (float(row[1]), row[2], row[3], row[6]) == (float(stored[index]), known, above, '') and abs(float(row[4]) - above / known) < 0.00006
    assert [float(row[5]) for row in compare] == [0.5, 0.05, 0.01]
    # ---- G17: history
    tiles = browser.rows('mpp-detail', '范围内的记录')[0]
    by_outcome = dict(env.sql("SELECT o.outcome,count(*) " + facts + " GROUP BY 1"))
    assert list(tiles) == [sum(by_outcome.values())] + [by_outcome.get(name, 0) for name in ('success', 'failed', 'cancelled', 'timed_out', 'unknown')]
    main = env.panel('mpp-detail.json', '每次执行的耗时')
    assert main['fieldConfig']['defaults']['custom']['scaleDistribution'] == dict(type='linear') and main['fieldConfig']['defaults']['custom']['drawStyle'] == 'points'
    points = browser.rows('mpp-detail', '每次执行的耗时')
    slowest = [row for row in points if row[1] is not None]
    fastest = [row for row in points if row[2] is not None]
    assert 0 < len(fastest) <= len(slowest) < known and all(float(row[1] or row[2]) > 0 for row in points)
    title = browser.page.locator('[data-testid^="data-testid Panel header 每次执行的耗时"]').first.get_attribute('data-testid')
    interval = re.search(r'每格 (\d+)(ms|s|m|h)', title)
    assert interval, title
    seconds = int(interval.group(1)) * dict(ms=0.001, s=1, m=60, h=3600)[interval.group(2)]
    slots = env.sql("SELECT count(*),max(o.duration_ms),min(o.duration_ms) " + facts + " AND o.duration_ms IS NOT NULL GROUP BY floor(extract(epoch FROM o.end_at)/%s)" % seconds)
    assert len(slowest) == len(slots) and len(fastest) == sum(1 for slot in slots if slot[0] > 1)
    assert sorted(float(row[1]) for row in slowest) == sorted(float(slot[1]) for slot in slots)
    reference = browser.results[('mpp-detail', 'panel %d' % main['id'], 'B')][0]
    assert reference['names'][1:] == ['P50 基线', 'P95 基线', 'P99 基线'] and len(reference['rows']) == 2
    assert [float(x) for x in reference['rows'][0][1:]] == [float(stored[i]) for i in (2, 3, 4)]
    marks = browser.results[('mpp-detail', 'annotation', '')][0]['rows']
    bad = sum(by_outcome.get(name, 0) for name in ('failed', 'cancelled', 'timed_out'))
    assert bad > 0 and sum(int(re.search(r'(\d+) 次', row[1]).group(1)) for row in marks) == bad and {row[2] for row in marks} <= {'失败', '取消', '超时'}
    counts = browser.results[('mpp-detail', 'panel %d' % env.panel('mpp-detail.json', '记录数')['id'], 'A')][0]
    assert set(counts['names'][1:]) == {boards_label(name) for name in by_outcome}
    assert sum(int(value) for row in counts['rows'] for value in row[1:] if value is not None) == sum(by_outcome.values())
    # zoomed in far enough, every execution is its own point
    narrow = env.sql("SELECT (extract(epoch FROM date_trunc('minute',max(o.end_at))-interval '20 minutes')*1000)::bigint,(extract(epoch FROM date_trunc('minute',max(o.end_at)))*1000)::bigint " + facts)[0]
    zoom = Browser(play, env, height=5200)
    zoom.open(env.detail.split('&from=')[0] + '&from=%d&to=%d' % narrow, extra=2500)
    inside = env.one("SELECT count(o.duration_ms) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.value='%s' AND %s AND o.timing_type='request' "
                     "AND o.end_at>=to_timestamp(%d/1000.0) AND o.end_at<to_timestamp(%d/1000.0)" % ((busy, where) + narrow))
    close = zoom.rows('mpp-detail', '每次执行的耗时')
    assert inside > 5 and len([r for r in close if r[1] is not None]) == inside and not [r for r in close if r[2] is not None]
    # dragging across the chart zooms the time range
    canvas = zoom.page.locator('[data-testid^="data-testid Panel header 每次执行的耗时"] .u-over').first
    box = canvas.bounding_box()
    zoom.page.mouse.move(box['x'] + box['width'] * 0.30, box['y'] + box['height'] / 2)
    zoom.page.mouse.down()
    zoom.page.mouse.move(box['x'] + box['width'] * 0.60, box['y'] + box['height'] / 2, steps=8)
    zoom.page.mouse.up()
    zoom.settle(2000)
    dragged = zoom.variables()
    assert ms(dragged['from']) > narrow[0] and ms(dragged['to']) < narrow[1] and 0.2 < (ms(dragged['to']) - ms(dragged['from'])) / (narrow[1] - narrow[0]) < 0.4
    # the two pattern charts do not follow the time range
    assert zoom.rows('mpp-detail', '一天内各小时的规律') == hour and zoom.rows('mpp-detail', '训练窗口内每一天') == day
    assert [float(r[1]) for r in zoom.rows('mpp-detail', '关键数值')] == [float(r[1]) for r in key]
    zoom.close()
    env.ok('G14: key numbers, percentile bars, the five-category overview with the no-sample line, sample conditions and both pattern charts equal the stored statistics '
           'and do not follow the time range')
    # duration filters act on the chart and the list
    limited = Browser(play, env, height=5200)
    limited.open(env.detail + '&var-dmin=300&var-dmax=600', extra=2500)
    between = env.one("SELECT count(*) " + facts + " AND o.duration_ms BETWEEN 300 AND 600")
    shown = [float(r[1] if r[1] is not None else r[2]) for r in limited.rows('mpp-detail', '每次执行的耗时')]
    assert shown and all(300 <= value <= 600 for value in shown)
    table = limited.rows('mpp-detail', '明细：')
    assert table[0][-1] == between and all(300 <= float(r[1]) <= 600 for r in table) and len(table) == min(between, 200)
    assert limited.total('所选时间范围内，符合状态和耗时筛选的一共有多少条') == between
    assert list(limited.rows('mpp-detail', '范围内的记录')[0]) == list(tiles)
    limited.close()
    env.ok('G17: a linear axis; when dense, each slot shows its slowest and fastest execution and the title states the step; zoomed in, every execution is one point; '
           'dragging zooms; failures, cancellations and timeouts are marks, never zero; the duration filters act on the chart and the list; counts equal an independent count')
    # ---- G18: per-text table and selecting one text
    texts = browser.rows('mpp-detail', '范围内出现过的原文')
    direct = env.sql("SELECT o.sql_id,count(*),max(o.duration_ms),count(*) FILTER (WHERE o.duration_ms>%s),count(o.duration_ms) " % stored[3] + facts + " GROUP BY 1 ORDER BY 2 DESC,1 LIMIT 10")
    assert [(r[8], r[3], float(r[5])) for r in texts] == [(r[0], r[1], float(r[2])) for r in direct]
    assert all(abs(float(t[6]) - d[3] / d[4]) < 0.00006 for t, d in zip(texts, direct)) and {t[7] for t in texts} == {env.one("SELECT count(DISTINCT o.sql_id) " + facts)}
    assert browser.total('所选时间范围内一共出现过多少份不同的原文') == texts[0][7] and '范围内原文数' not in browser.text()
    colored = env.panel('mpp-detail.json', '每格最慢的一次，按原文着色')
    extra = [env.panel('mpp-detail.json', name)['id'] for name in ('星期几的规律', '每周', '被排除的样本及原因', '五类计时 × 全部指标', '分层明细')]
    assert not [key for key in browser.requests if key[1] in {'panel %d' % colored['id']} | {'panel %d' % n for n in extra}]
    browser.page.get_by_text('按原文着色的点图（展开后查询）').click()
    browser.settle(2500)
    frames = browser.results[('mpp-detail', 'panel %d' % colored['id'], 'A')]
    assert len(frames[0]['names']) == 11 and all(name.startswith('#') for name in frames[0]['names'][1:])
    for order, column in (('median', 4), ('slowest', 5)):
        ordered = Browser(play, env, height=5200)
        ordered.open(env.detail + '&var-text_order=' + order, extra=2000)
        values = [float(r[column]) for r in ordered.rows('mpp-detail', '范围内出现过的原文')]
        assert values == sorted(values, reverse=True) and len(values) == 10
        ordered.close()
    first = texts[0][8]
    browser.page.locator('[data-testid^="data-testid Panel header 范围内出现过的原文"] a').first.click()
    browser.settle(3000)
    assert browser.variables()['sqlid'] == first
    one = "AND o.sql_id='%s'" % first
    by_outcome_one = dict(env.sql("SELECT o.outcome,count(*) " + facts + one + " GROUP BY 1"))
    assert browser.rows('mpp-detail', '范围内的记录')[0][0] == sum(by_outcome_one.values())
    assert browser.rows('mpp-detail', '超过基线的执行')[0][2] == env.one("SELECT count(o.duration_ms) " + facts + one)
    assert float(browser.rows('mpp-detail', '超过基线的执行')[0][1]) == float(stored[2])
    assert {r[5] for r in browser.rows('mpp-detail', '明细：')} == {first}
    assert [float(r[1]) for r in browser.rows('mpp-detail', '关键数值')] == [float(r[1]) for r in key]
    assert len(browser.rows('mpp-detail', '范围内出现过的原文')) == 10
    assert '只看一份原文' in browser.text() and '所选的一份原文' in browser.text()
    browser.page.get_by_text('点这里回到全部原文').click()
    browser.settle(3000)
    assert browser.variables()['sqlid'] == '' and browser.rows('mpp-detail', '范围内的记录')[0][0] == sum(by_outcome.values())
    env.ok('G18: the per-text table equals independent statistics in three orders, ten texts at most; selecting a text limits the tiles, comparison, charts and list to it '
           'while the baseline stays; the coloured chart is collapsed and queries only when opened')
    # ---- G15: baseline details (collapsed until opened)
    browser.page.get_by_text('基线明细（展开后查询').click()
    browser.settle(3000)
    matrix = browser.rows('mpp-detail', '五类计时 × 全部指标')
    assert [row[0] for row in matrix] == [label for _, label in boards.METRICS] and len(matrix) == 20
    full = env.sql("SELECT " + ",".join('s.' + column for column, _ in boards.METRICS if column != 'active_days') + ",cardinality(s.active_dates) "
                   "FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id) WHERE s.build_id='%s' AND s.layer='overall' AND g.fingerprint_value='%s' "
                   "AND g.scope_id='C1' AND g.database='shop' AND g.execution_user='app_user' AND g.timing_type='request'" % (current, busy))[0]
    order = [column for column, _ in boards.METRICS if column != 'active_days'] + ['active_days']
    for row, (column, _) in zip(matrix, boards.METRICS):
        empty = 0 if column in ('included_count', 'active_days', 'excluded_count') else None
        assert abs(float(row[1]) - float(full[order.index(column)])) < 1e-9 and row[2:] == (empty,) * 4, column
    excluded = browser.rows('mpp-detail', '被排除的样本及原因')
    assert excluded == [('落在排除时段', 40)]
    assert len(browser.rows('mpp-detail', '星期几的规律')) == 7 and len(browser.rows('mpp-detail', '每周')) == 5
    for layer, buckets in (('overall', 1), ('day', 28), ('week', 5), ('weekday', 7), ('hour', 13)):  # 12 working hours and the excluded hour
        layered = Browser(play, env, height=5200)
        layered.open(env.detail + '&var-layer=' + layer, extra=1500)
        layered.page.get_by_text('基线明细（展开后查询').click()
        layered.settle(2500)
        table = layered.rows('mpp-detail', '分层明细')
        direct = env.sql("SELECT s.included_count,s.p50_ms,s.p99_ms,s.log_mad FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id) WHERE s.build_id='%s' AND s.layer='%s' "
                         "AND g.fingerprint_value='%s' AND g.scope_id='C1' AND g.database='shop' AND g.execution_user='app_user' AND g.timing_type='request' "
                         "AND s.included_count+s.excluded_count>0 ORDER BY s.bucket_date,s.bucket_number" % (current, layer, busy))
        names = layered.results[('mpp-detail', 'panel %d' % env.panel('mpp-detail.json', '分层明细')['id'], 'A')][0]['names']
        assert len(table) == len(direct) == buckets and len(names) == 4 + 20, (layer, len(table))
        for row, expected in zip(table, direct):
            number = lambda value: None if value is None else float(value)
            assert (row[names.index('included_count')], number(row[names.index('p50_ms')]), number(row[names.index('p99_ms')])) == (expected[0], number(expected[1]), number(expected[2]))
            assert row[1].startswith(('满足', '不满足：')) and row[3].startswith(('满足', '不满足：'))
        layered.close()
    env.ok('G15: with the details opened, five timing categories by twenty stored values, the five layers bucket by bucket with all 17 metrics and their own conditions, '
           'and the exclusion reasons equal the stored statistics')
    # ---- G19: execution list
    table = browser.rows('mpp-detail', '明细：')
    latest = env.sql("SELECT o.analysis_id||o.occurrence_id,o.duration_ms,o.outcome,o.sql_id " + facts + " ORDER BY o.end_at DESC,o.analysis_id DESC,o.occurrence_id DESC LIMIT 200")
    assert len(table) == 200 and [r[5] for r in table] == [r[3] for r in latest] and table[0][-1] == sum(by_outcome.values())
    assert browser.total('所选时间范围内，符合状态和耗时筛选的一共有多少条') == sum(by_outcome.values())
    assert '符合筛选的条数' not in browser.text()
    assert [None if r[1] is None else float(r[1]) for r in table] == [None if r[1] is None else float(r[1]) for r in latest]
    assert {r[4] for r in table} == {'单条'} and {r[6] for r in table} == {'c1.csv'} and all(r[7].isdigit() for r in table)
    assert {r[8] for r in table} >= {'参与训练'} and {r[3] for r in table} <= {'耗时未知', '高于 P99', '高于 P95', '高于 P50', '不高于 P50'}
    slow = Browser(play, env, height=5200)
    slow.open(env.detail + '&var-list_order=slowest', extra=2500)
    slowest_rows = slow.rows('mpp-detail', '明细：')
    expected = env.sql("SELECT o.duration_ms " + facts + " AND o.duration_ms IS NOT NULL ORDER BY o.duration_ms DESC LIMIT 200")
    assert [float(r[1]) for r in slowest_rows] == [float(r[0]) for r in expected]
    assert '最多 200 条' in slow.text() and '最慢的在前' in slow.text()
    slow.open(env.detail + '&var-status=failed&var-status=timed_out', extra=2500)
    filtered = slow.rows('mpp-detail', '明细：')
    assert len(filtered) == by_outcome.get('failed', 0) + by_outcome.get('timed_out', 0) and {r[2] for r in filtered} <= {'失败', '超时'}
    assert all(r[1] is None and r[3] == '耗时未知' and r[8].startswith('被排除：') for r in filtered)
    slow.shot('detail-status')
    assert '未知' in slow.text()
    slow.close()
    excluded = Browser(play, env, height=5200)
    excluded.open(env.detail.split('&from=')[0] + '&from=%d&to=%d' % (1783620000000, 1783623600000), extra=2500)
    assert {r[8] for r in excluded.rows('mpp-detail', '明细：')} == {'被排除：落在排除时段'}
    older = [row[1] for row in versions][1]
    excluded.open(env.detail + '&var-version=' + urllib.parse.quote(older), extra=2500)
    assert {r[8] for r in excluded.rows('mpp-detail', '明细：') if r[2] == '成功'} == {'在训练窗口之外'}
    assert float(excluded.rows('mpp-detail', '关键数值')[0][1]) != float(key[0][1])
    excluded.close()
    browser.shot('detail')
    browser.close()
    env.ok('G19: the list has every agreed field, newest first by default and slowest first on request, both over the whole range, 200 rows at most as its title says; '
           'unknown durations stay unknown; training decisions under the chosen version are shown as they are')
    # ---- other shapes of SQL
    other = Browser(play, env, height=5200)
    other.open(env.detail_of('products category_id'), extra=3000)
    assert [row[1] for row in other.variable('mpp-detail', 'timing')] == ['execute_first', 'execute_fetch', 'parse', 'bind', 'unknown']
    assert other.variables().get('timing', 'execute_first') == 'execute_first' and 'Execute 首次（阶段或调用' in other.text() and '不加总成执行次数' in other.text()
    overview = other.rows('mpp-detail', '五类计时一览')
    assert [row[0] for row in overview] == ['Execute 首次（阶段或调用）', 'Execute 续取（阶段或调用）', 'Parse（阶段或调用）', 'Bind（阶段或调用）', '没有样本的类别：请求整体']
    other.open(env.detail_of('audit_log'), extra=3000)
    key = other.rows('mpp-detail', '关键数值')
    assert [(row[0], row[3]) for row in key][4] == ('P99（样本不足，仅供参考）', '#8e8e8e') and {row[3] for row in key[:4]} == {'text'}
    assert other.rows('mpp-detail', '超过基线的执行')[2][3:7] == (None, None, 0.01, '基线样本不足，不作参照')
    assert [(r[0], r[1], r[4]) for r in other.rows('mpp-detail', '三项样本条件')][2] == ('P99', '不满足', '样本数不足')
    reference = other.results[('mpp-detail', 'panel %d' % main['id'], 'B')][0]
    assert reference['names'][1:] == ['P50 基线', 'P95 基线'] and '样本不足，仅供参考' in other.text() and '基线样本不足，不作参照' in other.text() and '异常' not in other.text()
    other.shot('detail-insufficient')
    other.open(env.detail_of('customers north'), extra=3000)
    assert {row[3] for row in other.rows('mpp-detail', '关键数值')[2:]} == {'#8e8e8e'} and {row[1] for row in other.rows('mpp-detail', '三项样本条件')} == {'不满足'}
    assert not [frame for frame in other.results[('mpp-detail', 'panel %d' % main['id'], 'B')] if frame['rows']] and {r[6] for r in other.rows('mpp-detail', '超过基线的执行')} == {'基线样本不足，不作参照'}
    other.open(env.detail_of('failure_only'), extra=3000)
    assert [row[1] for row in other.variable('mpp-detail', 'timing')] == ['unknown'] and other.rows('mpp-detail', '关键数值') == []
    tiles = other.rows('mpp-detail', '范围内的记录')[0]
    assert tiles[0] == tiles[2] > 0 and tiles[1] == 0 and {r[1] for r in other.rows('mpp-detail', '明细：')} == {None}
    # links inside the page: the time-range hint, a row of the five-category overview, the original-text cell
    extended_url = env.detail_of('products category_id')
    last_ms = int(extended_url.rsplit('&to=', 1)[1]) - 1
    other.open(extended_url.split('&from=')[0] + '&from=%d&to=%d' % (last_ms - 40 * 86400000, last_ms - 39 * 86400000), extra=2500)
    assert other.rows('mpp-detail', '范围内的记录')[0][0] == 0
    other.page.get_by_text('点这里设为这条 SQL 最近一次执行往前 7 天').click()
    other.settle(2500)
    moved = other.variables()
    assert (ms(moved['from']), ms(moved['to'])) == (last_ms - 7 * 86400000, last_ms + 1) and other.rows('mpp-detail', '范围内的记录')[0][0] > 0
    other.page.locator('[data-testid^="data-testid Panel header 五类计时一览"] a', has_text='Parse').first.click()
    other.settle(2500)
    assert other.variables()['timing'] == 'parse' and {row[0] for row in other.rows('mpp-detail', '关键数值')} >= {'样本数', 'P50'}
    cell = other.page.locator('[data-testid^="data-testid Panel header 明细："] a').first
    wanted = cell.inner_text()
    cell.click(position=dict(x=20, y=10))
    other.settle(2500)
    assert wanted.startswith('S:') and other.variables()['sqlid'] == wanted and {row[5] for row in other.rows('mpp-detail', '明细：')} == {wanted}
    # a fingerprint pasted into the top box is enough
    other.open('/d/mpp-detail/sql-detail?var-fp=' + busy, extra=3000)
    assert other.rows('mpp-detail', '关键数值') and 'FROM orders o' in other.text()
    other.open('/d/mpp-detail/sql-detail?var-fp=struct:x:' + '0' * 64, extra=2000)
    assert '没有找到这个指纹' in other.text() and not other.failures
    other.close()
    env.ok('G13/G16: extended-protocol SQL defaults to Execute first and names stages as stages; unmet sample conditions grey the value and say so, draw no reference line '
           'and are not compared; a failure-only SQL is shown under the untimed records; the links inside the page work; a pasted fingerprint opens the page')


def listing(env, play):
    """G20: both rankings and the version list."""
    browser = Browser(play, env, height=2600)
    browser.open('/d/mpp-list/sql-list?from=%d&to=%d' % env.last_day, extra=2500)
    assert not browser.failures, browser.failures[:3]
    ranked = browser.rows('mpp-list', 'SQL 身份排行：所选时间范围内')
    direct = env.sql("SELECT o.scope_id,o.database,o.execution_user,o.timing_type,count(*),sum(o.duration_ms),max(o.duration_ms),f.value FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) "
                     "WHERE o.end_at>=to_timestamp(%d/1000.0) AND o.end_at<to_timestamp(%d/1000.0) AND o.timing_type IN ('request','execute_first') GROUP BY 1,2,3,4,8 ORDER BY 5 DESC" % env.last_day)
    timed = [row for row in ranked if row[3] != '无计时类别']
    assert [row[4] for row in timed] == [row[4] for row in direct] and len(ranked) == ranked[0][11]
    # each ranking's total is shown once above its table, not as a column repeated in every row
    assert browser.total('所选时间范围内，符合筛选的一共有多少行') == len(ranked) and '符合条件的行数' not in browser.text()
    assert (timed[0][0], timed[0][1], timed[0][2], timed[0][3], float(timed[0][6]), float(timed[0][8]), timed[0][10]) == \
        (direct[0][0], direct[0][1], direct[0][2], '请求整体', float(direct[0][5]), float(direct[0][6]), direct[0][7])
    errors = env.one("SELECT count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE o.end_at>=to_timestamp(%d/1000.0) AND o.end_at<to_timestamp(%d/1000.0) "
                     "AND o.timing_type IS NULL AND o.outcome<>'success' AND f.value='%s' AND o.scope_id='C1' AND o.execution_user='app_user'" % (env.last_day + (env.busy,)))
    assert timed[0][5] == errors and [row for row in ranked if row[3] == '无计时类别']
    baseline = browser.rows('mpp-list', 'SQL 身份排行：当前基线版本')
    stored = env.sql("SELECT g.scope_id,g.database,g.execution_user,s.included_count,s.p95_ms,g.fingerprint_value FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id) "
                     "JOIN current_version v USING(build_id) WHERE s.layer='overall' AND s.included_count>0 AND g.timing_type IN ('request','execute_first') ORDER BY s.p95_ms DESC,s.group_id")
    assert [float(row[7]) for row in baseline] == [float(row[4]) for row in stored] and baseline[0][13] == len(stored)
    assert browser.total('当前基线版本里，符合筛选的一共有多少行') == len(stored)
    assert (baseline[0][0], baseline[0][1], baseline[0][2], baseline[0][4], baseline[0][12]) == (stored[0][0], stored[0][1], stored[0][2], stored[0][3], stored[0][5])
    versions = browser.rows('mpp-list', '已发布的基线版本')
    published = env.sql("SELECT b.scope_id,b.build_id,c.window_days,(v.build_id IS NOT NULL) FROM build b JOIN publication p USING(build_id) JOIN config_snapshot c USING(config_id) "
                        "LEFT JOIN current_version v USING(build_id) WHERE p.result='published' ORDER BY b.scope_id,p.at DESC")
    assert [(row[0], row[9], row[5], row[6]) for row in versions] == [(row[0], row[1], row[2], '当前生效' if row[3] else '历史版本') for row in published]
    assert {row[7] for row in versions} == {'保留'} and {row[8] for row in versions} == {'与当前规则相同'}
    spans = browser.rows('mpp-list', '数据的时间范围')
    assert [row[0] for row in spans] == ['C1', 'C2'] and '设为时间范围' in browser.text()
    # "set as time range" moves to the last 24 hours of that cluster's logs
    expected = env.sql("SELECT (extract(epoch FROM max(f.last_log_at)-interval '24 hours')*1000)::bigint,"
                       "(extract(epoch FROM max(f.last_log_at)+interval '1 second')*1000)::bigint FROM source_file f WHERE f.scope_id='C1'")[0]
    browser.open('/d/mpp-list/sql-list', extra=2000)
    assert browser.rows('mpp-list', 'SQL 身份排行：所选时间范围内') == []
    assert browser.total('所选时间范围内，符合筛选的一共有多少行') == 0
    browser.page.locator('[data-testid^="data-testid Panel header 数据的时间范围"] a').first.click()
    browser.settle(2500)
    moved = browser.variables()
    assert urllib.parse.urlparse(browser.page.url).path.startswith('/d/mpp-list') and (ms(moved['from']), ms(moved['to'])) == expected
    assert browser.rows('mpp-list', 'SQL 身份排行：所选时间范围内')
    # sort orders and filters
    for order, column in (('total', 6), ('slowest', 8), ('not_success', 5)):
        browser.open('/d/mpp-list/sql-list?from=%d&to=%d&var-rank_order=%s' % (env.last_day + (order,)), extra=2000)
        values = [float(row[column]) for row in browser.rows('mpp-list', 'SQL 身份排行：所选时间范围内') if row[column] is not None]
        assert values == sorted(values, reverse=True) and values
    browser.open('/d/mpp-list/sql-list?from=%d&to=%d&var-cluster=%s&var-base_order=samples&var-min_samples=300&var-timings=request' % (env.last_day + (b64('C1'),)), extra=2000)
    filtered = browser.rows('mpp-list', 'SQL 身份排行：当前基线版本')
    assert filtered and {row[0] for row in filtered} == {'C1'} and all(row[4] >= 300 for row in filtered) and {row[3] for row in filtered} == {'请求整体'}
    assert {row[0] for row in browser.rows('mpp-list', '已发布的基线版本')} == {'C1'}
    # a row opens the detail page
    browser.open('/d/mpp-list/sql-list?from=%d&to=%d' % env.last_day, extra=2000)
    browser.page.locator('[data-testid^="data-testid Panel header SQL 身份排行：所选时间范围内"] a').first.click()
    browser.settle(3000)
    opened = browser.variables()
    assert urllib.parse.urlparse(browser.page.url).path.startswith('/d/mpp-detail') and opened['fp'] == direct[0][7] and opened['timing'] == 'request'
    assert (ms(opened['from']), ms(opened['to'])) == env.last_day and browser.rows('mpp-detail', '关键数值')
    browser.shot('list-to-detail')
    browser.close()
    env.ok('G20: the time-range ranking and the current-baseline ranking equal independent statistics, with orders and filters; the version list is complete; '
           '"set as time range" moves to the last day of data; a row opens the detail page')


def real(env, play):
    """G21/G26 on the full real data: page numbers of sampled identities against the base tables, and what each page costs."""
    report = dict(samples=[], pages=[])
    picks = env.sql("""WITH identities AS MATERIALIZED (
            SELECT o.scope_id,o.database,o.execution_user,f.value,count(*) records,count(DISTINCT o.sql_id) texts,
                count(*) FILTER (WHERE o.timing_type='request') requests,count(*) FILTER (WHERE o.timing_type='execute_first') executes,
                count(*) FILTER (WHERE o.timing_type IS NOT NULL) timed,count(*) FILTER (WHERE o.outcome<>'success') bad,
                (extract(epoch FROM max(o.end_at))*1000)::bigint last_ms
            FROM mpp_occurrence o JOIN mpp_fingerprint f ON f.sql_id=o.sql_id AND f.state='reliable'
            WHERE o.database IS NOT NULL AND o.execution_user IS NOT NULL AND o.end_at IS NOT NULL GROUP BY 1,2,3,4)
        (SELECT 'most records',* FROM identities ORDER BY records DESC LIMIT 1) UNION ALL
        (SELECT 'most original texts',* FROM identities ORDER BY texts DESC,records DESC LIMIT 1) UNION ALL
        (SELECT 'failed records only',* FROM identities WHERE bad=records ORDER BY records DESC LIMIT 1) UNION ALL
        (SELECT 'extended protocol',* FROM identities WHERE executes>50 AND requests=0 ORDER BY md5(value) LIMIT 1) UNION ALL
        (SELECT 'insufficient samples',* FROM identities WHERE requests BETWEEN 5 AND 20 AND timed=requests ORDER BY md5(value) LIMIT 1) UNION ALL
        (SELECT 'random with requests',* FROM identities WHERE requests>50 ORDER BY md5(value||'x') LIMIT 2)""")
    assert {row[0] for row in picks} >= {'most records', 'most original texts', 'failed records only', 'extended protocol', 'insufficient samples'}
    browser = Browser(play, env, height=5200)
    for kind, scope, database, user, fingerprint, records, texts, requests, executes, timed, bad, last_ms in picks:
        identity = base64.urlsafe_b64encode(json.dumps([scope, database, user], ensure_ascii=False).encode()).decode().rstrip('=')
        env.range = (last_ms - 7 * 86400000, last_ms + 1)
        browser.open('/d/mpp-detail/sql-detail?var-fp=%s&var-identity=%s&from=%d&to=%d' % ((fingerprint, identity) + env.range), extra=4000)
        assert not browser.failures, (kind, browser.failures[:2])
        cost = browser.cost()
        where = "o.scope_id=%s AND o.database=%s AND o.execution_user=%s" % (literal(scope), literal(database), literal(user))
        timings = [row[1] for row in browser.variable('mpp-detail', 'timing')]
        order = ['request', 'execute_first', 'execute_fetch', 'parse', 'bind']
        have = dict(env.sql("SELECT coalesce(o.timing_type,'unknown'),count(*) FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) "
                            "WHERE f.value='%s' AND %s GROUP BY 1" % (fingerprint, where)))
        assert [name for name in timings if name != 'unknown'] == [name for name in order if name in have], kind
        timing = timings[0]
        facts = expectations(env, fingerprint, where, "o.timing_type='%s'" % timing if timing != 'unknown' else 'false') if timing != 'unknown' else \
            ("FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.value='%s' AND %s AND o.timing_type IS NULL "
             "AND o.end_at>=to_timestamp(%d/1000.0) AND o.end_at<to_timestamp(%d/1000.0)" % ((fingerprint, where) + env.range))
        by_outcome = dict(env.sql("SELECT o.outcome,count(*) " + facts + " GROUP BY 1"))
        tiles = browser.rows('mpp-detail', '范围内的记录')[0]
        assert list(tiles) == [sum(by_outcome.values())] + [by_outcome.get(name, 0) for name in ('success', 'failed', 'cancelled', 'timed_out', 'unknown')], kind
        stored = env.sql("SELECT s.included_count,cardinality(s.active_dates),s.p50_ms,s.p95_ms,s.p99_ms,s.max_ms FROM mpp_statistic s "
                         "JOIN mpp_baseline_group g USING(group_id) JOIN current_version v ON v.build_id=s.build_id AND v.scope_id=g.scope_id "
                         "WHERE s.layer='overall' AND g.fingerprint_value='%s' AND g.scope_id=%s AND g.database=%s AND g.execution_user=%s AND g.timing_type='%s'"
                         % (fingerprint, literal(scope), literal(database), literal(user), timing))
        key = browser.rows('mpp-detail', '关键数值')
        if stored and stored[0][0] > 0:
            assert [float(row[1]) for row in key] == [float(value) for value in stored[0]], kind
            known = env.one("SELECT count(o.duration_ms) " + facts)
            for row, value in zip(browser.rows('mpp-detail', '超过基线的执行'), stored[0][2:5]):
                if row[2] is not None and row[3] is not None:
                    assert (row[2], row[3]) == (known, env.one("SELECT count(*) " + facts + " AND o.duration_ms>%s" % value)), kind
                else:
                    assert row[6] == '基线样本不足，不作参照', kind
        else:
            assert key == [], kind
        direct = env.sql("SELECT o.sql_id,count(*),max(o.duration_ms) " + facts + " GROUP BY 1 ORDER BY 2 DESC,1 LIMIT 10")
        shown = browser.rows('mpp-detail', '范围内出现过的原文')
        assert [(row[8], row[3]) for row in shown] == [(row[0], row[1]) for row in direct], kind
        assert [None if row[5] is None else float(row[5]) for row in shown] == [None if row[2] is None else float(row[2]) for row in direct], kind
        latest = env.sql("SELECT o.sql_id,o.duration_ms,o.outcome " + facts + " ORDER BY o.end_at DESC,o.analysis_id DESC,o.occurrence_id DESC LIMIT 200")
        table = browser.rows('mpp-detail', '明细：')
        assert [(row[5], None if row[1] is None else float(row[1]), row[2]) for row in table] == \
            [(row[0], None if row[1] is None else float(row[1]), boards_label(row[2])) for row in latest], kind
        assert not table or table[0][-1] == sum(by_outcome.values())
        points = browser.rows('mpp-detail', '每次执行的耗时')
        title = browser.page.locator('[data-testid^="data-testid Panel header 每次执行的耗时"]').first.get_attribute('data-testid')
        interval = re.search(r'每格 (\d+)(ms|s|m|h)', title)
        seconds = int(interval.group(1)) * dict(ms=0.001, s=1, m=60, h=3600)[interval.group(2)]
        slots = env.sql("SELECT count(*) " + facts + " AND o.duration_ms IS NOT NULL GROUP BY floor(extract(epoch FROM o.end_at)/%s)" % seconds)
        assert len([row for row in points if row[1] is not None]) == len(slots) and len([row for row in points if row[2] is not None]) == sum(1 for slot in slots if slot[0] > 1), kind
        report['samples'].append(dict(kind=kind, identity_records=records, identity_texts=texts, timing=timing, range_records=sum(by_outcome.values()),
                                      range_texts=shown[0][7] if shown else 0, equal=True, **cost))
        report['pages'].append(dict(page='detail, 7 days, ' + kind, **cost))
        print('real', kind, cost, flush=True)
    # list page: the last 24 hours of data of each cluster
    for scope, last_ms in env.sql("SELECT scope_id,(extract(epoch FROM max(end_at))*1000)::bigint FROM mpp_occurrence GROUP BY 1 ORDER BY 1"):
        window = (last_ms - 86400000, last_ms + 1)
        browser.open('/d/mpp-list/sql-list?from=%d&to=%d&var-cluster=%s' % (window + (b64(scope),)), extra=4000)
        assert not browser.failures, browser.failures[:2]
        ranked = browser.rows('mpp-list', 'SQL 身份排行：所选时间范围内')
        direct = env.sql("SELECT count(*),f.value FROM mpp_occurrence o JOIN mpp_fingerprint f ON f.sql_id=o.sql_id AND f.state='reliable' WHERE o.scope_id=%s "
                         "AND o.end_at>=to_timestamp(%d/1000.0) AND o.end_at<to_timestamp(%d/1000.0) AND o.timing_type IN ('request','execute_first') "
                         "GROUP BY o.database,o.execution_user,f.value,o.timing_type ORDER BY 1 DESC LIMIT 100" % ((literal(scope),) + window))
        assert [row[4] for row in ranked if row[3] != '无计时类别'][:len(direct)] == [row[0] for row in direct][:len([r for r in ranked if r[3] != '无计时类别'])]
        baseline = browser.rows('mpp-list', 'SQL 身份排行：当前基线版本')
        stored = env.sql("SELECT s.p95_ms FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id) JOIN current_version v ON v.build_id=s.build_id "
                         "WHERE v.scope_id=%s AND s.layer='overall' AND s.included_count>0 AND g.timing_type IN ('request','execute_first') ORDER BY s.p95_ms DESC LIMIT 100" % literal(scope))
        assert [float(row[7]) for row in baseline] == [float(row[0]) for row in stored]
        report['pages'].append(dict(page='list, 24 hours, one cluster', range_rows=ranked[0][11] if ranked else 0, equal=True, **browser.cost()))
        print('real list', browser.cost(), flush=True)
    # search page: the three modes
    text = env.one("SELECT t.text FROM mpp_sql_text t JOIN mpp_fingerprint f USING(sql_id) WHERE f.state='reliable' AND length(t.text) BETWEEN 200 AND 4000 "
                   "AND EXISTS (SELECT FROM mpp_occurrence o WHERE o.sql_id=t.sql_id) ORDER BY md5(t.sql_id) LIMIT 1")
    browser.open('/d/mpp-search/sql-search', extra=2000)
    for mode, value in (('words', 'select'), ('words', 'select from where'), ('passage', 'select *'), ('words', 'zz_no_search_match_51'), ('exact', text)):
        started = time.monotonic()
        del browser.timings[:]
        browser.search(value, mode)
        elapsed = time.monotonic() - started - 1.5
        rows = browser.rows('mpp-search', '结果')
        known = [item for _, item in browser.timings if item >= 0]
        entry = dict(page='search, ' + mode + (' (stored statement)' if mode == 'exact' else ': ' + value), rows=len(rows), queries=len(browser.timings),
                     summed_seconds=round(sum(known) / 1000, 2), slowest_seconds=round(max(known) / 1000, 2) if known else 0, wall_seconds=round(elapsed, 2))
        if mode == 'exact':
            assert len(rows) == 1 and browser.variables()['xsql'] != '' and browser.variables()['xstate'] in ('has_baseline', 'records_without_baseline')
        report['pages'].append(entry)
        print('real search', entry, flush=True)
    browser.close()
    (env.output / 'real-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n')
    env.ok('G21/G26: on the full real data, %d sampled identities (most records, most texts, failed only, extended protocol, insufficient samples, random) show the '
           'same numbers as direct statistics of the base tables; queries and time per page recorded' % len(report['samples']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='setup_dev.py synthetic 使用的目录')
    parser.add_argument('--output', type=Path, help='截图和日志的私有目录，默认在 --directory 下')
    parser.add_argument('--only', help='只运行名称含此文字的检查（调试用）')
    args = parser.parse_args()
    output = (args.output or args.directory / 'e2e').resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = Environment(args.directory.resolve(), output)
    if env.state['data'] != 'synthetic':
        # Real data: page numbers of sampled identities and the cost of each page; nothing identifying is printed.
        with sync_playwright() as play:
            real(env, play)
        print('RESULT: real-data page checks passed', flush=True)
        return 0
    last = env.sql("SELECT (extract(epoch FROM date_trunc('day',max(end_at) AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')*1000)::bigint FROM mpp_occurrence")[0][0]
    env.last_day = (last, last + 86400000)
    env.busy = env.one("SELECT f.value FROM mpp_fingerprint f JOIN mpp_sql_text t USING(sql_id) WHERE t.text=" + literal(data.BUSY.format(status=1, day='2026-06-26')))

    def detail_of(words):
        row = env.sql("SELECT f.value,o.scope_id,o.database,o.execution_user,(extract(epoch FROM max(o.end_at))*1000)::bigint,count(*) FROM mpp_occurrence o "
                      "JOIN mpp_fingerprint f USING(sql_id) JOIN mpp_sql_text t USING(sql_id) WHERE " +
                      " AND ".join("strpos(t.text,'%s')>0" % word for word in words.split()) + " GROUP BY 1,2,3,4 ORDER BY 6 DESC LIMIT 1")[0]
        identity = base64.urlsafe_b64encode(json.dumps(list(row[1:4]), ensure_ascii=False).encode()).decode().rstrip('=')
        return '/d/mpp-detail/sql-detail?var-fp=%s&var-identity=%s&from=%d&to=%d' % (row[0], identity, row[4] - 7 * 86400000, row[4] + 1)
    env.detail_of = detail_of
    env.range = (last - 2 * 86400000, last + 86400000)
    identity = base64.urlsafe_b64encode(json.dumps(['C1', 'shop', 'app_user']).encode()).decode().rstrip('=')
    env.detail = '/d/mpp-detail/sql-detail?var-fp=%s&var-identity=%s&from=%d&to=%d' % ((env.busy, identity) + env.range)
    started = time.time()
    with sync_playwright() as play:
        steps = [('configuration', lambda: configuration(env)), ('login', lambda: login(env, play)), ('folders', lambda: folders(env)),
                 ('arrival', lambda: arrival(env, play)), ('search', lambda: search(env, play)), ('service', lambda: service_down(env, play)),
                 ('detail', lambda: detail(env, play)), ('listing', lambda: listing(env, play))]
        for name, step in steps:
            if not args.only or args.only in name:
                step()
    summary = dict(state='ok', checks=len(env.done), seconds=round(time.time() - started, 1), labels=env.done,
                   grafana=env.api('GET', '/api/health', anonymous=True)[1]['version'])
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n')
    print('RESULT: end-to-end dashboard checks passed (%d groups, %.0f s)' % (len(env.done), summary['seconds']), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
