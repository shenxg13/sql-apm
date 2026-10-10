#!/usr/bin/env python3
"""End-to-end check of the run-status dashboard in a headless browser.

Development machine only. It drives an environment made by ``setup_dev.py
synthetic``: real daily runs on synthetic logs of three clusters of its own (K1 to
K3), then what each panel received is compared with values worked out here from
the base tables, without the dashboard's query functions. Every kind of open
problem is produced once. The window is 1920x920.

It adds clusters to the environment, so run the check of the other three
dashboards (verify_e2e.py) first, or use a directory of its own.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts/grafana'), str(ROOT / 'scripts/db')]
from playwright.sync_api import sync_playwright
import verify_e2e as e2e
from verify_daily import Site, many, one
from database.retention import clone_build
from sql_apm.daily import inbox
from sql_apm.daily.config import load_config
from sql_apm.daily.run import DailyRun
from sql_apm.storage.daily import status
from sql_apm.storage.ingestion import connect
from sql_apm.storage.tasks import Task

BOARD, PATH = 'mpp-status', '/d/mpp-status/run-status'
RUN = dict(ok='完成', failed='完成，有失败', aborted='被中止', unfinished='未正常结束', running='运行中')
CLUSTER = dict(pending='没有轮到', running='处理中', ok='完成', failed='完成，有失败', skipped='被跳过：集群正忙', aborted='被中止')
BUILD = dict(not_reached='没有做到这一步', disabled='自动构建已关闭', no_data='还没有导入成功的日期', not_due='未到构建间隔',
             published='已发布', no_samples='没有有效样本，版本未更新', failed='失败')
CLEANUP = dict(not_reached='没有做到这一步', disabled='自动清理已关闭', nothing='没有过期月份', cleaned='已清理',
               pending='有月份等待清理', failed='失败')
RAW = dict(not_reached='没有做到这一步', disabled='自动删除已关闭', nothing='没有到期的文件', deleted='已删除', failed='失败')
PROBLEM = dict(day_failed='导入失败或有冲突的日期', files_without_marker='有文件但没有齐全标记',
               marker_without_files='有齐全标记但没有文件', marker_not_before_today='标记日期不早于今天',
               cleanup_pending='等待清理的月份', cluster_skipped='上次运行跳过了这个集群',
               build_not_succeeded='构建或发布尚未成功', run_not_finished='上次运行没有正常结束',
               no_recent_success='太久没有成功的运行')
REASON = dict(cluster_busy='集群正被其他任务占用', file_unreadable='文件读不了', cleanup_lock_timeout='拿不到锁',
              invalid_thresholds='invalid_thresholds', aborted='被中止', unfinished='未正常结束', raw_delete_failed='删除原始文件失败',
              files_changed_after_import='导入之后文件有增减或内容变化')
# Which step of a run a stored kind of problem belongs to, and how a cluster row says the step was gone through.
STEPS = (({'day_failed', 'files_without_marker', 'marker_without_files', 'marker_not_before_today'}, lambda c: c[22] == 'done'),
         ({'build_not_succeeded'}, lambda c: c[7] != 'not_reached'), ({'cleanup_pending'}, lambda c: c[10] != 'not_reached'))
STARTED = dict(timer='定时', manual='手工')


def stamp(value):
    """Milliseconds of a time as the page shows it: to the second."""
    return None if value is None else int(value.timestamp()) * 1000


def beijing(value):
    return None if value is None else (value.astimezone().strftime('%Y-%m-%d %H:%M:%S'))


def days_text(days):
    days = sorted(days)
    if not days:
        return ''
    if len(days) <= 4:
        return '、'.join(day.strftime('%m-%d') for day in days)
    return '%d 天：%s 至 %s' % (len(days), days[0].strftime('%m-%d'), days[-1].strftime('%m-%d'))


def bytes_text(value):
    for unit, size in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
        if value >= size:
            return '%.1f %s' % (value / size, unit)
    return '%d B' % value


def result_code(state, failed):
    return ('failed' if failed else 'ok') if state in ('finished', 'done') else state


class Expectation:
    """The three lists as the base tables give them. `held` says whether a run is active right now."""
    def __init__(self, db):
        self.db = db
        self.runs = many(db, 'SELECT run_id,started_by,state,failed,reason,started_at,finished_at,stale_after_hours '
                             'FROM mpp_daily_run ORDER BY started_at DESC,run_id DESC')
        # A run left "running" by a dead process is unfinished; nothing runs while this check reads.
        self.runs = [(r[0], r[1], 'unfinished' if r[2] == 'running' else r[2], r[3], 'owner_exited' if r[2] == 'running' else r[4]) + r[5:]
                     for r in self.runs]
        self.clusters = many(db, '''SELECT run_id,scope_id,ordinal,state,failed,reason,newest_imported,build_state,build_reason,
            cutoff_date,cleanup_state,cleanup_reason,months_cleaned,months_pending,released_bytes,raw_state,raw_reason,raw_days,
            raw_files,raw_bytes,started_at,finished_at,import_state FROM mpp_daily_cluster ORDER BY ordinal''')

    def problems(self):
        """Each stored kind from the newest run that went through its step; the rest worked out from the runs themselves."""
        if not self.runs:
            return []
        latest, order = self.runs[0], {run[0]: index for index, run in enumerate(self.runs)}
        started = {run[0]: run[5] for run in self.runs}
        rows = []
        for cluster in [c for c in self.clusters if c[0] == latest[0]]:
            turns = sorted((c for c in self.clusters if c[1] == cluster[1]), key=lambda c: order[c[0]])
            for kinds, gone_through in STEPS:
                examined = [c for c in turns if gone_through(c)]
                if not examined:
                    continue
                run_id = examined[0][0]
                for kind, source, day, month, reason, count in many(self.db, '''SELECT kind,source_id,log_date,result_month,reason,file_count
                        FROM mpp_daily_problem WHERE run_id=%s AND scope_id=%s AND kind=ANY(%s)''', (run_id, cluster[1], sorted(kinds))):
                    detail = REASON.get(reason, reason or '')
                    if count is not None:
                        detail += ('，' if reason else '') + '%d 个文件' % count
                    rows.append((PROBLEM[kind], cluster[1], source or '', day.isoformat() if day else month.strftime('%Y-%m') if month else '',
                                 detail, stamp(started[run_id])))
            reached = [c for c in turns if c[3] != 'pending']
            if reached and reached[0][3] == 'skipped':
                rows.append((PROBLEM['cluster_skipped'], cluster[1], '', '', REASON[reached[0][5]], stamp(started[reached[0][0]])))
        if latest[2] in ('aborted', 'unfinished'):
            rows.append((PROBLEM['run_not_finished'], '全部', '', '', REASON[latest[2]], stamp(latest[5])))
        since = self.last_success() or min(run[5] for run in self.runs)
        if one(self.db, 'SELECT clock_timestamp()-%s>make_interval(hours => %s)', (since, latest[7])):
            rows.append((PROBLEM['no_recent_success'], '全部', '', '', '', stamp(since)))
        return rows

    def last_success(self):
        """End of the newest run that finished without any failure."""
        succeeded = [run[6] for run in self.runs if run[2] == 'finished' and not run[3]]
        return max(succeeded) if succeeded else None

    def cluster_rows(self):
        if not self.runs:
            return []
        latest, open_rows, rows = self.runs[0], self.problems(), []
        for c in [c for c in self.clusters if c[0] == latest[0]]:
            newest = one(self.db, '''SELECT max(d.declared_date) FROM batch_date d JOIN import_batch b USING(batch_id)
                WHERE b.scope_id=%s AND b.state='complete' ''', (c[1],))
            version = many(self.db, '''SELECT s.cutoff_date,v.last_success_at FROM current_version v JOIN build b USING(build_id)
                JOIN config_snapshot s USING(config_id) WHERE v.scope_id=%s''', (c[1],))
            code = latest[2] if c[3] == 'running' else result_code(c[3], c[4])
            rows.append((c[1], newest and newest.isoformat(), version[0][0].isoformat() if version else None,
                         stamp(version[0][1]) if version else None, CLUSTER[code] if code in CLUSTER else RUN[code],
                         sum(row[1] == c[1] for row in open_rows)))
        return rows

    def recent(self, limit=20):
        rows = []
        for run in self.runs[:limit]:
            for c in [c for c in self.clusters if c[0] == run[0]]:
                days = many(self.db, 'SELECT log_date,state,reason FROM mpp_daily_day WHERE run_id=%s AND scope_id=%s ORDER BY log_date,source_id', (run[0], c[1]))
                build = BUILD[c[7]] + ('，截止日 ' + c[9].isoformat() if c[7] in ('published', 'no_samples') else
                                       '：' + REASON.get(c[8], c[8]) if c[7] == 'failed' else '')
                cleanup = CLEANUP[c[10]] + (' %d 个月，释放 %s' % (c[12], bytes_text(c[14])) if c[10] == 'cleaned' else
                                            '：%d 个月' % c[13] if c[10] == 'pending' else '')
                raw = RAW[c[15]] + (' %d 天 %d 个文件，%s' % (c[17], c[18], bytes_text(c[19])) if c[15] == 'deleted' else
                                    '：' + REASON.get(c[16], c[16]) + ('，已删除 %d 个文件，%s' % (c[18], bytes_text(c[19])) if c[18] else '')
                                    if c[15] == 'failed' else '')
                code = run[2] if c[3] == 'running' else result_code(c[3], c[4])
                other = one(self.db, "SELECT coalesce(sum(file_count),0) FROM mpp_daily_problem WHERE run_id=%s AND scope_id=%s AND kind='nonconforming_file'", (run[0], c[1]))
                rows.append((stamp(run[5]), STARTED[run[1]], RUN[result_code(run[2], run[3])], c[1],
                             CLUSTER[code] if code in CLUSTER else RUN[code], days_text([d[0] for d in days if d[1] == 'complete']),
                             '、'.join('%s（%s）' % (d[0].strftime('%m-%d'), REASON.get(d[2], d[2])) for d in days if d[1] != 'complete'),
                             build, cleanup, raw, int(other)))
        return rows


def compare(env, browser, db, label):
    """Open the page and check all seven panels against the base tables. Returns the open problems shown."""
    browser.open(PATH, extra=2500)
    assert not browser.failures, browser.failures[:3]
    expected = Expectation(db)
    latest = expected.runs[0] if expected.runs else None
    tiles = {title: browser.rows(BOARD, title) for title in ('上次运行开始于', '上次运行的结果', '上次成功的运行', '待处理问题数')}
    shown = [tuple(row[:5]) + (row[6],) for row in browser.rows(BOARD, '待处理问题列表')]
    wanted = expected.problems()
    if latest is None:
        assert all(rows == [] for rows in tiles.values()), tiles
    else:
        assert tiles['上次运行开始于'] == [(beijing(latest[5]),)], (tiles, latest)
        assert tiles['上次运行的结果'] == [(RUN[result_code(latest[2], latest[3])] + '（' + STARTED[latest[1]] + '启动）',)], tiles
        assert tiles['上次成功的运行'] == [(beijing(expected.last_success()),)], tiles
        assert tiles['待处理问题数'] == [(len(wanted),)], (tiles, wanted)
    # The read-only command reads the same list.
    told = sorted((PROBLEM[p['kind']], p['scope_id'] or '全部') for p in status(db, 1)['problems'])
    assert told == sorted(row[:2] for row in wanted), (label, told)
    if wanted:
        key = lambda row: tuple('' if value is None else str(value) for value in row)
        assert sorted(shown, key=key) == sorted(wanted, key=key), (label, sorted(shown, key=key), sorted(wanted, key=key))
        hints = {row[0]: row[5] for row in browser.rows(BOARD, '待处理问题列表')}
        assert all(hints.values()), hints
    else:
        assert shown == [('（没有待处理的问题）', '', '', '', '', None)], shown
    clusters = [tuple(row) for row in browser.rows(BOARD, '各集群现状')]
    assert clusters == expected.cluster_rows(), (label, clusters, expected.cluster_rows())
    recent = [(row[0], row[1], row[2], row[3], row[4], row[6], row[7], row[8], row[9], row[10], row[11])
              for row in browser.rows(BOARD, '最近的运行记录')]
    assert recent == expected.recent(), (label, recent[:4], expected.recent()[:4])
    for row, c in zip(browser.rows(BOARD, '最近的运行记录'), [c for r in expected.runs[:20] for c in expected.clusters if c[0] == r[0]]):
        assert (row[5] is None) == (c[21] is None) and (row[5] is None or abs(row[5] - (c[21] - c[20]).total_seconds()) < 0.01), (row[:6], c[20:])
    assert e2e.cut_headers(browser.page) == [], e2e.cut_headers(browser.page)
    return wanted


def layout(browser):
    """Where the panels end in the 1920x920 window."""
    boxes = browser.page.evaluate("""() => {
        const out = {};
        document.querySelectorAll('[data-testid^="data-testid Panel header "]').forEach((panel) => {
            const box = panel.getBoundingClientRect();
            out[panel.getAttribute('data-testid').replace('data-testid Panel header ', '')] =
                [Math.round(box.left), Math.round(box.top), Math.round(box.right), Math.round(box.bottom)];
        });
        return {boxes: out, width: window.innerWidth, height: window.innerHeight,
                sideways: document.documentElement.scrollWidth > window.innerWidth};
    }""")
    return boxes


def clipped_headers(page):
    """Column headers that reach beyond the visible width of their table (the table would scroll sideways)."""
    return page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('[data-testid^="data-testid Panel header "] [role="grid"]').forEach((grid) => {
            const edge = grid.getBoundingClientRect().left + grid.clientWidth;
            grid.querySelectorAll('.rdg-header-row [role="columnheader"]').forEach((cell) => {
                if (cell.textContent.trim() && cell.getBoundingClientRect().right > edge + 1) out.push(cell.textContent.trim());
            });
        });
        return out;
    }""")


def verify(env, play):
    state = env.state
    dsn = 'host=%s port=%d dbname=%s user=sql_apm' % (state['pg_socket'], state['pg_port'], state['database'])
    db = connect(dsn, state['schema'])
    assert one(db, 'SELECT count(*) FROM mpp_daily_run') == 0, 'this environment already holds daily runs; use a fresh one'
    work = Path(state['directory']) / 'daily'
    if work.exists():      # left by an attempt that stopped before its first run
        shutil.rmtree(work)
    work.mkdir()
    site = Site(work, dsn, dict(KS1='K1', KS2='K2', KS3='K3'))
    today = date.today()
    back = lambda n: today - timedelta(days=n)
    browser = e2e.Browser(play, env, width=1920, height=920)
    run = lambda: site.cli('daily', 'run', '--config', site.path, timeout=600)

    # Installed from files, in the MPP folder, read-only; no run yet.
    _, live = env.api('GET', '/api/dashboards/uid/' + BOARD)
    assert live['meta']['provisioned'] and live['meta']['folderUid'] == 'mpp' and live['dashboard']['title'] == '运行状态'
    assert {item['uid'] for item in env.api('GET', '/api/search?type=dash-db&folderUIDs=mpp')[1]} == {'mpp-search', 'mpp-list', 'mpp-detail', BOARD}
    changed = dict(live['dashboard'], title='运行状态（改）')
    status, body = env.api('POST', '/api/dashboards/db', dict(dashboard=changed, folderUid='mpp', overwrite=True))
    assert status == 400 and 'provisioned' in json.dumps(body).lower(), (status, body)
    assert env.api('POST', '/api/dashboards/db', dict(dashboard=changed, folderUid='mpp', overwrite=True), user='viewer')[0] == 403
    assert env.api('DELETE', '/api/dashboards/uid/' + BOARD)[0] == 400
    compare(env, browser, db, 'empty')
    assert '还没有运行记录' in browser.text() and '（没有待处理的问题）' in browser.text()
    browser.shot('status-empty')
    env.ok('D18: the run-status dashboard is loaded from files into the MPP folder as the fourth packaged dashboard, usable without UI work; '
           'it cannot be saved over or deleted; before any run it shows that there is none')

    # Place markers, run, see the result.
    for source, days in (('KS1', (4, 3)), ('KS2', (4, 3)), ('KS3', (3,))):
        for n in days:
            site.put(source, back(n), base=10 * n)
    done = run()
    assert done.returncode == 0, done.stdout[-1500:]
    assert compare(env, browser, db, 'first run') == []
    rows = browser.rows(BOARD, '最近的运行记录')
    assert [(row[3], row[6], row[8]) for row in rows] == [
        ('K1', days_text([back(4), back(3)]), '已发布，截止日 ' + back(3).isoformat()),
        ('K2', days_text([back(4), back(3)]), '已发布，截止日 ' + back(3).isoformat()),
        ('K3', days_text([back(3)]), '已发布，截止日 ' + back(3).isoformat())], rows
    browser.shot('status-first-run')
    env.ok('D18/D19: markers placed, one run, and the page shows it: the four values of the newest run, one row per cluster and the run record '
           'equal the base tables; no column header is cut')

    # A failure, shown, then recovered from.
    broken = site.put('KS1', back(2), base=30)
    broken.chmod(0)
    done = run()
    assert done.returncode == 1
    shown = compare(env, browser, db, 'failed day')
    assert shown == [(PROBLEM['day_failed'], 'K1', 'KS1', back(2).isoformat(), '文件读不了，1 个文件', shown[0][5])], shown
    assert browser.rows(BOARD, '上次运行的结果') == [('完成，有失败（手工启动）',)]
    # A run that finished with a failure is not a successful one: the third value still names the run before it.
    ends = many(db, 'SELECT finished_at,failed FROM mpp_daily_run ORDER BY started_at')
    assert [failed for _, failed in ends] == [False, True] and browser.rows(BOARD, '上次成功的运行') == [(beijing(ends[0][0]),)]
    assert [row[4] for row in browser.rows(BOARD, '各集群现状')] == ['完成，有失败', '完成', '完成']
    browser.shot('status-failed-day')
    broken.chmod(0o644)
    done = run()
    assert done.returncode == 0
    assert compare(env, browser, db, 'recovered') == []
    assert browser.rows(BOARD, '最近的运行记录')[0][6] == days_text([back(2)])
    env.ok('D19: a day that fails is listed with its reason and the run shows as finished with failures; after the fix the next run imports it '
           'and the list is empty again')

    # Every kind of open problem at once.
    stuck = site.put('KS1', back(1), base=40)
    stuck.chmod(0)
    loose = site.put('KS1', back(6), base=50, marker=False)
    site.mark('KS1', back(7))
    site.mark('KS1', today)
    (site.inbox('KS1') / 'notes.txt').write_text('x')
    site.put('KS2', back(1), base=60)
    site.put('KS3', back(2), base=70)
    old = clone_build(db, one(db, "SELECT build_id FROM current_version WHERE scope_id='K2'"), 'status-old', '2025-01-01', True)
    good = (work / 'training.json').read_text()
    reader = connect(dsn, state['schema'])
    def fault(point, scope=None, name=None):
        if scope == 'K2' and point == 'before_build':
            (work / 'training.json').write_text(json.dumps(dict(site.training, thresholds=dict(nope={}))))
        if scope == 'K2' and point == 'before_cleanup':
            (work / 'training.json').write_text(good)
            with reader.cursor() as cur:
                cur.execute('SELECT 1 FROM mpp_statistic LIMIT 1')
    holder = connect(dsn, state['schema'])
    with Task(holder, 'K3', 'snapshot'):
        code = DailyRun(dsn, state['schema'], load_config(site.path), 'manual', None, None, fault).execute()
    holder.close()
    reader.rollback()
    reader.close()
    assert code == 1
    site.settings['stale_after_hours'] = 1
    site.write()
    def stop(point, scope=None, name=None):
        if point == 'cluster_started':
            raise KeyboardInterrupt
    try:
        DailyRun(dsn, state['schema'], load_config(site.path), 'timer', None, None, stop).execute()
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError('the run was not aborted')
    with db, db.cursor() as cur:
        cur.execute("UPDATE mpp_daily_run SET started_at=started_at-interval '2 hours',finished_at=finished_at-interval '2 hours'")
        cur.execute("UPDATE mpp_daily_cluster SET started_at=started_at-interval '2 hours',finished_at=finished_at-interval '2 hours'")
    shown = compare(env, browser, db, 'every kind')
    kinds = {row[0] for row in shown}
    assert kinds == set(PROBLEM.values()), set(PROBLEM.values()) - kinds
    wanted = {(PROBLEM['day_failed'], 'K1', 'KS1', back(1).isoformat(), '文件读不了，1 个文件'),
              (PROBLEM['files_without_marker'], 'K1', 'KS1', back(6).isoformat(), '1 个文件'),
              (PROBLEM['marker_without_files'], 'K1', 'KS1', back(7).isoformat(), '0 个文件'),
              (PROBLEM['marker_not_before_today'], 'K1', 'KS1', today.isoformat(), '0 个文件'),
              (PROBLEM['build_not_succeeded'], 'K2', '', '', 'invalid_thresholds'),
              (PROBLEM['cleanup_pending'], 'K2', '', '2025-01', '拿不到锁'),
              (PROBLEM['cluster_skipped'], 'K3', '', '', '集群正被其他任务占用'),
              (PROBLEM['run_not_finished'], '全部', '', '', '被中止'),
              (PROBLEM['no_recent_success'], '全部', '', '', '')}
    assert {row[:5] for row in shown} == wanted, {row[:5] for row in shown} ^ wanted
    assert browser.rows(BOARD, '待处理问题数') == [(9,)] and browser.rows(BOARD, '上次运行的结果') == [('被中止（定时启动）',)]
    assert [(row[0], row[4], row[5]) for row in browser.rows(BOARD, '各集群现状')] == [('K1', '被中止', 4), ('K2', '没有轮到', 2), ('K3', '没有轮到', 1)]
    text = browser.text()
    browser.page.locator('[data-testid^="data-testid Panel header 待处理问题列表"] [role="grid"]').evaluate('grid => { grid.scrollTop = grid.scrollHeight; }')
    browser.page.wait_for_timeout(500)
    text += browser.text()
    browser.page.locator('[data-testid^="data-testid Panel header 待处理问题列表"] [role="grid"]').evaluate('grid => { grid.scrollTop = 0; }')
    browser.page.wait_for_timeout(300)
    for label in list(PROBLEM.values()) + ['怎样处理', '被跳过：集群正忙', '有月份等待清理：1 个月', '失败：invalid_thresholds']:
        assert label in text, label
    boxes = layout(browser)
    browser.shot('status-every-problem-1920x920')
    assert (boxes['width'], boxes['height'], boxes['sideways']) == (1920, 920, False), boxes
    assert clipped_headers(browser.page) == [], clipped_headers(browser.page)
    for title, box in boxes['boxes'].items():
        if not title.startswith('最近的运行记录'):
            assert 0 <= box[0] and box[2] <= 1920 and box[3] <= 920, (title, box)
    recent_top = [box[1] for title, box in boxes['boxes'].items() if title.startswith('最近的运行记录')][0]
    assert recent_top < 920 - 150, recent_top
    env.ok('D18: each of the nine kinds of open problem is on the page with its cluster, date or month, reason and what to do; the page equals the base '
           'tables; in a 1920x920 window the four values, the cluster list and the problem list are whole and the run records begin %d px above the '
           'bottom; neither the page nor any table scrolls sideways, no header is cut' % (920 - recent_top))

    # The page offers nothing to act with, to either login.
    assert browser.page.locator('[data-testid^="data-testid Panel header "] a[href]').count() == 0
    assert browser.page.locator('[data-testid^="data-testid Panel header "] button:has-text("提交"), [data-testid="data-testid panel button-submit"]').count() == 0
    viewer = e2e.Browser(play, env, user='viewer', width=1920, height=920)
    viewer.open(PATH, extra=2500)
    assert not viewer.failures and viewer.rows(BOARD, '待处理问题数') == [(9,)]
    assert len(viewer.rows(BOARD, '待处理问题列表')) == 9
    viewer.close()
    env.ok('D18: the page holds no link or control that could start anything; the viewer login sees the same and cannot save it')

    # Everything put right; one run later the page is clean.
    stuck.chmod(0o644)
    loose.unlink()
    (site.inbox('KS1') / inbox.marker_name(back(7))).unlink()
    (site.inbox('KS1') / inbox.marker_name(today)).unlink()
    (site.inbox('KS1') / 'notes.txt').unlink()
    site.settings.pop('stale_after_hours')
    site.write()
    done = run()
    assert done.returncode == 0, done.stdout[-1500:]
    assert compare(env, browser, db, 'all cleared') == []
    newest = browser.rows(BOARD, '最近的运行记录')[:3]
    assert newest[0][6] == days_text([back(1)]) and newest[1][8].startswith('已发布') and newest[1][9].startswith('已清理 1 个月，释放 ') \
        and newest[2][6] == days_text([back(2)]), newest
    assert one(db, 'SELECT cleaned_at IS NOT NULL FROM mpp_result_partition WHERE partition_id=%s', (old,))
    browser.shot('status-cleared')
    env.ok('D19: after the causes are removed one run clears the list: the failed day is imported, the skipped cluster is made up, the build publishes '
           'and the waiting month is cleaned')

    # Reached from the other dashboards by an actual click.
    browser.open('/d/mpp-list/sql-list', extra=2000)
    browser.page.get_by_role('link', name='运行状态').first.click()
    browser.settle(2500)
    assert urllib.parse.urlparse(browser.page.url).path.startswith('/d/' + BOARD + '/'), browser.page.url
    assert browser.rows(BOARD, '各集群现状')
    browser.close()
    db.close()
    env.ok('D18: the link on the other packaged dashboards opens the run-status page')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', type=Path, required=True, help='setup_dev.py synthetic 使用的目录')
    parser.add_argument('--output', type=Path, help='截图和日志的私有目录，默认在 --directory 下')
    args = parser.parse_args()
    output = (args.output or args.directory / 'e2e-status').resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = e2e.Environment(args.directory.resolve(), output)
    assert env.state['data'] == 'synthetic', 'synthetic environments only'
    started = time.time()
    with sync_playwright() as play:
        verify(env, play)
    summary = dict(state='ok', checks=len(env.done), seconds=round(time.time() - started, 1), labels=env.done,
                   grafana=env.api('GET', '/api/health', anonymous=True)[1]['version'])
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n')
    print('RESULT: run-status dashboard checks passed (%d groups, %.0f s)' % (len(env.done), summary['seconds']), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
