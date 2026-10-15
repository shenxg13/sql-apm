#!/usr/bin/env python3
"""sqlfind acceptance on a disposable PG17; synthetic SQL only."""
import argparse
import io
import os
from pathlib import Path
import pty
import shutil
import subprocess
import sys
from datetime import timedelta
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

from verify import instance, Verification
from database import dashboard_data as data
from database.retention import contents
from sql_apm.cli.sqlfind import arguments, search, time_value, InputError
from sql_apm.cli.sqlfind_output import Output, cells
from sql_apm.storage.sqlfind import Finder, rows
from sql_apm.storage.ingestion import connect

DIFFERENCE_VALUES = ('', 'alpha', 'beta', '中文\n值', '\t', ' ', 'a' * 200 + 'Z', 'b' * 200 + 'Y')
DIFFERENCE_SQL = ('/* ' + 'shared-prefix ' * 30 + '*/ SELECT id FROM preview_differences '
                  "WHERE code = 'shared-{value}-tail' /* " + 'shared-suffix ' * 30 + '*/')
CONTROL_SQL = ("SELECT '中文\t原文\r\n尾' FROM control_display /* "
               '\x1b]0;TITLE\x07\x1b[31mRED\x1b[0m\x1b[?1049hALT\x1b[?1049l'
               '\rCR\x7f\x80\x85\x9b\x9f */')
CONTROL_VISIBLE = ("SELECT '中文\t原文\r\n尾' FROM control_display /* "
                   r'\x1b]0;TITLE\x07\x1b[31mRED\x1b[0m\x1b[?1049hALT\x1b[?1049l'
                   r'\rCR\x7f\x80\x85\x9b\x9f */')
ORDINARY_SQL = "SELECT '中文\t原文\r\n尾' FROM ordinary_display;\r\n"


def command(dsn, options, raw=None, entry=None, overrides=None):
    master, slave = pty.openpty()
    try:
        return subprocess.run((entry or [str(ROOT / 'bin/sqlfind')]) + options, input=raw,
            stdin=slave if raw is None else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'SQL_APM_DSN': dsn, 'PYTHONPATH': str(ROOT), **(overrides or {})}, cwd='/tmp', timeout=60)
    finally:
        os.close(master)
        os.close(slave)


def verify_rendered_notes(dsn, finder):
    from sqlfind_terminal import Terminal
    for width in (80, 200):
        for query in ('orders status', 'orders ＝ ￥ ｜', 'orders ' * 21):
            args = arguments([])
            with finder.cursor() as cur:
                notes = rows(cur, 'mpp_view_search_note', [finder.norm, args.mode, query] + [None] * 7)
            refused = next((n['content'] for n in notes if n['item'] == '没有检索'), None)
            expected = refused + '。' if refused else ''.join(n['item'] + ': ' + n['content'] for n in notes)
            result = command(dsn, ['--width', str(width)], query.encode())
            assert result.returncode == (1 if refused else 0) and not result.stderr
            term = Terminal(dsn, ['--mode', 'words'], width)
            try:
                term.until('之后 q 退出。')
                term.send(query + '\n')
                interactive = term.until('之后 q 退出。' if refused else 'Ctrl-C 返回输入。')
                for rendered in (result.stdout.decode(), interactive):
                    if refused:
                        assert expected in rendered.replace('\n', ''), rendered
                    else:
                        note_text = rendered.split('这次检索\n', 1)[1].split('集群: ', 1)[0]
                        assert note_text.replace('\n', '') == expected, note_text
                        assert all(cells(line) <= width for line in note_text.splitlines())
                term.send('\x03' if refused else 'q\n')
                if refused:
                    term.until('q 退出')
                    term.send('q\n')
                term.finish()
            finally:
                term.close()
    print('PASS: rendered search-note fidelity for multiple/full-width/21 terms, CLI and 80/200-column PTY', flush=True)


def verify_control_display(dsn, finder):
    from sqlfind_terminal import Terminal
    for query, raw, expected, count in (('control_display', CONTROL_SQL, CONTROL_VISIBLE, 12),
                                         ('ordinary_display', ORDINARY_SQL, ORDINARY_SQL, 0)):
        detail = finder.detail(search(finder, query.encode(), arguments([])), '1', arguments([]))
        number = next(r['number'] for r in detail['originals'] if r['sql_id'] == detail['text']['sql_id'])
        assert detail['text']['sql_text'] == raw
        for width in (80, 200):
            results = []
            for options in ([], ['--full-text'], ['--view', 'text']):
                result = command(dsn, ['--show', '1', '--width', str(width), *options], query.encode())
                assert result.returncode == 0 and not result.stderr
                results.append(result.stdout.decode())
            term = Terminal(dsn, ['--mode', 'words'], width)
            try:
                term.until('之后 q 退出。')
                term.send(query + '\n')
                term.until('Ctrl-C 返回输入。')
                term.send('1\n')
                results.append(term.until('Ctrl-C 返回列表。'))
                term.send('f\n')
                results.append(term.until('Ctrl-C 返回列表。'))
                term.send('q\n')
                term.finish()
            finally:
                term.close()
            for rendered in results:
                displayed = rendered.split('原文开始\n', 1)[1].split('\n原文结束', 1)[0]
                assert displayed == expected, repr(displayed)
                assert f'原文总字符数: {len(raw)}\n' in rendered and f'编号 {number}' in rendered
                assert ('控制字符' in rendered) == bool(count)
                if count:
                    assert f'{count} 个控制字符' in rendered
                remaining = rendered.replace('\r\n', '').replace('\n', '').replace('\t', '')
                assert all(ord(c) >= 32 and not 0x7f <= ord(c) <= 0x9f for c in remaining)
        assert finder.detail(search(finder, query.encode(), arguments([])), '1', arguments([]))['text'] == detail['text']
    print('PASS: original control escapes and counts in detail/f/text/full-text; ordinary CRLF/tabs and text identity preserved', flush=True)


def verify_broken_pipe(dsn):
    # A large full text prevents the producer finishing before head closes.
    producer = subprocess.Popen([str(ROOT / 'bin/sqlfind'), '--show', '1', '--view', 'text'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'SQL_APM_DSN': dsn}, cwd='/tmp')
    consumer = subprocess.Popen(['head', '-1'], stdin=producer.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    producer.stdout.close()
    producer.stdout = None
    try:
        producer.stdin.write(b'long_text')
        producer.stdin.close()
        producer.stdin = None
        first_line, consumer_errors = consumer.communicate(timeout=60)
        _, producer_errors = producer.communicate(timeout=60)
        assert first_line and consumer.returncode == 0 and not consumer_errors
        assert producer.returncode == 1 and not producer_errors, (producer.returncode, producer_errors)
    finally:
        for process in (producer, consumer):
            if process.poll() is None:
                process.kill()
            process.wait()
    print('PASS: real sqlfind | head -1 exits 1 with empty stderr', flush=True)


def verify_originals(finder):
    args = arguments([])
    found = search(finder, b'orders 2026-06-26', args)
    default = finder.detail(found, '1', args)
    assert default['original'] == 'matched'
    catalog = default['originals']
    selected = [r for r in catalog if r['in_scope']]
    assert len(selected) == found['candidates'][0]['matched_texts'] == 5
    assert len(catalog) == 80
    assert all('2026-06-26' in r['example'] for r in selected)
    numbers = {r['sql_id']: r['number'] for r in catalog}
    common = [finder.norm, default['candidate']['fingerprint'],
              default['identities'][default['identity'] - 1]['identity'], default['timing']]
    build = default['version']['build_id']
    for order in ('latest', 'slowest'):
        actual = finder.detail(found, '1', args, order=order, limit=1000)
        with finder.cursor() as cur:
            cur.execute('''SELECT r.* FROM mpp_view_records(%s,%s,%s,%s,%s,%s) r
                WHERE sql_id=ANY(%s) ORDER BY
                CASE WHEN %s='slowest' THEN duration_ms END DESC NULLS LAST,
                end_at DESC,analysis_id DESC,occurrence_id DESC''',
                common + [actual['start'], actual['end'], [r['sql_id'] for r in selected], order])
            expected = [dict(r) for r in cur.fetchall()]
            assert 20 < len(expected) < 1000 and any(r['duration_ms'] is None for r in expected)
            assert sum(c['records'] for c in actual['counts']) == len(expected)
            assert len(actual['executions']) == len(expected)
            for got, reference in zip(actual['executions'], expected):
                assert all(got[k] == reference[k] for k in reference if k != 'anchor_ref')
                assert got['original_number'] == numbers[got['sql_id']] and got['matching'] == len(expected)
            # Every enriched row, including unknown durations and baseline comparison,
            # agrees with the existing per-text execution function.
            canonical = {}
            for text in selected:
                records = rows(cur, 'mpp_view_executions', common + [build, actual['start'], actual['end'],
                    text['sql_id'], None, None, None, order, 1000])
                canonical.update({(r['analysis_id'], r['occurrence_id']): r for r in records})
            for got in actual['executions']:
                reference = canonical[got['analysis_id'], got['occurrence_id']]
                assert all(got[k] == v for k, v in reference.items() if k != 'matching')
        first = finder.detail(found, '1', args, order=order, limit=1)
        assert first['executions'] == actual['executions'][:1]
        if order == 'slowest':
            assert first['executions'][0]['occurrence_id'] not in {r['occurrence_id'] for r in default['executions']}
            assert actual['executions'][-1]['duration_ms'] is None
            tied = actual['executions'][:2]
            assert tied[0]['duration_ms'] == tied[1]['duration_ms'] == 99999
            assert tied[0]['end_at'] > tied[1]['end_at']
        assert actual['statistics'] == default['statistics']
    for original in ('all', selected[0]['sql_id'], 'matched'):
        changed = finder.detail(found, '1', args, original=original)
        assert {r['sql_id']: r['number'] for r in changed['originals']} == numbers
        assert changed['statistics'] == default['statistics']
        if original == 'all':
            assert sum(c['records'] for c in changed['counts']) > sum(c['records'] for c in default['counts'])
        elif original != 'matched':
            assert all(r['sql_id'] == original for r in changed['executions'])
            assert changed['text']['selected'] and changed['text']['sql_id'] == original
        for width in (80, 200):
            stream = io.StringIO()
            Output(width, stream).originals(finder.originals(changed))
            assert all(cells(line) <= width for line in stream.getvalue().splitlines())
    second = next(n for n, r in enumerate(default['identities'], 1) if r['scope_id'] == 'C2')
    alternate = finder.detail(found, '1', args, identity=second)
    assert len([r for r in alternate['originals'] if r['in_scope']]) == 5
    assert sum(r['in_scope'] and r['records'] > 0 for r in alternate['originals']) == 1
    absent = next(r for r in alternate['originals'] if r['in_scope'] and r['records'] == 0)
    empty = finder.detail(found, '1', args, identity=second, original=absent['sql_id'])
    assert empty['executions'] == empty['counts'] == [] and empty['text']['sql_id'] == absent['sql_id']
    assert empty['statistics'] == alternate['statistics']
    # Candidate filters determine membership even when history uses another window.
    scoped_args = arguments(['--cluster', 'C2', '--start', '2026-07-23', '--end', '2026-07-24'])
    scoped = search(finder, b'orders', scoped_args)
    scoped_detail = finder.detail(scoped, '1', scoped_args)
    assert sum(r['matched'] for r in scoped_detail['originals']) == scoped['candidates'][0]['matched_texts'] == 1
    fingerprint = default['candidate']['fingerprint']
    direct = search(finder, fingerprint.encode(), args)
    assert finder.detail(direct, '1', args)['original'] == 'all'
    print('PASS: original subsets/all/single, full-range slowest, stable trace IDs, counts, zero records and baseline parity', flush=True)


def verify_differences(finder):
    args = arguments([])
    found = search(finder, b'preview_differences', args)
    detail = finder.detail(found, '1', args)
    before = [dict(r) for r in detail['originals']]
    listed = finder.originals(detail)
    assert detail['originals'] == before
    assert {r['example'] for r in listed['originals']} == {DIFFERENCE_SQL[:180]}
    assert sum(r['records'] == 0 for r in listed['originals']) == 1
    expected = {v if v and len(v) <= 160 else '（此处无字符）' if not v else
                v[:100] + ' ... ' + v[-50:] for v in DIFFERENCE_VALUES}
    assert {r['differing'] for r in listed['originals']} == expected
    assert listed['statistics'] == detail['statistics'] and listed['executions'] == detail['executions']
    for width in (80, 200):
        stream = io.StringIO()
        Output(width, stream).originals(listed)
        rendered = stream.getvalue()
        assert all(cells(line) <= width for line in rendered.splitlines())
        assert 'alpha' in rendered and 'beta' in rendered and '\\t' in rendered and '\\x20' in rendered
        assert '中文\\n值' in rendered and '原文不同部分' in rendered
        assert 'shared-prefix' not in rendered and 'shared-suffix' not in rendered
    selected = listed['originals'][0]
    single = finder.detail(found, '1', args, original=selected['sql_id'])
    single_list = finder.originals(single)
    assert [r['differing'] for r in single_list['originals'] if r['in_scope']] == ['（只有这一份原文）']
    # The comparison follows the displayed set, rather than the whole structure.
    subset = search(finder, b'preview_differences shared-a', args)
    subset_detail = finder.originals(finder.detail(subset, '1', args))
    assert sorted(r['differing'] for r in subset_detail['originals'] if r['in_scope']) == [
        'a' * 100 + ' ... ' + 'a' * 49 + 'Z', 'lpha']
    print('PASS: full-text differences beyond 180 chars, common suffix, zero-count texts, whitespace, long/empty/single and scoped lists', flush=True)


def verify_noninteractive(dsn, finder):
    args = arguments([])
    raw = b'orders 2026-06-26'
    found = search(finder, raw, args)
    default = finder.detail(found, '1', args)
    single = next(r for r in default['originals'] if r['in_scope'])
    alternate = next(n for n, r in enumerate(default['identities'], 1) if r['scope_id'] == 'C2')
    other = finder.detail(found, '1', args, identity=alternate)
    zero = next(r for r in other['originals'] if r['in_scope'] and not r['records'])
    old = (time_value('2026-06-26'), time_value('2026-06-27'))
    empty = (time_value('2030-01-01'), time_value('2030-01-02'))
    # CLI parameters are compared with the same selections used by the existing
    # interactive/function checks, including a different candidate time filter.
    cases = [([], {}),
             (['--original', 'matched', '--history-order', 'slowest', '--limit', '1'],
              dict(original='matched', order='slowest', limit=1)),
             (['--original', 'all', '--history-order', 'slowest', '--limit', '1000'],
              dict(original='all', order='slowest', limit=1000)),
             (['--original', str(single['number']), '--limit', '1000',
               '--history-start', old[0].isoformat(), '--history-end', old[1].isoformat()],
              dict(original=single['sql_id'], limit=1000, window=old)),
             (['--original', str(zero['number']), '--identity', str(alternate), '--timing', 'request'],
              dict(original=zero['sql_id'], identity=alternate, timing='request')),
             (['--original', 'all', '--history-start', '2030-01-01', '--history-end', '2030-01-02'],
              dict(original='all', window=empty))]
    for width in (80, 200):
        for options, settings in cases:
            expected = finder.detail(found, '1', args, **settings)
            assert expected['statistics'] == (other if settings.get('identity') == alternate else default)['statistics']
            for view in ('detail', 'originals', 'history', 'text'):
                done = command(dsn, ['--show', '1', '--width', str(width), '--view', view, *options], raw)
                assert done.returncode == 0 and not done.stderr, (options, view, done.stdout[-500:])
                actual = done.stdout.decode()
                assert '========== 操作' not in actual and 'SQL 详情操作：' not in actual
                stream = io.StringIO()
                output = Output(width, stream)
                if view == 'originals':
                    output.originals(finder.originals(expected), interactive=False)
                    assert '结构指纹:' in actual and '身份:' in actual and '范围:' in actual
                    assert '\nv 编号 ' not in actual
                elif view == 'history':
                    output.history(expected)
                    assert '结构指纹:' in actual
                elif view == 'text':
                    output.original_text(expected)
                else:
                    output.detail(expected)
                assert stream.getvalue().strip() in actual, (options, view)
                if view != 'detail':
                    assert '\n当前基线\n' not in actual and '身份 (* 表示当前所选)' not in actual
                if view in ('originals', 'history'):
                    assert '原文开始\n' not in actual
                    assert all(cells(line) <= width for line in actual.splitlines())
                if view == 'text':
                    assert actual.split('原文开始\n', 1)[1].split('\n原文结束', 1)[0] == expected['text']['sql_text']
                    assert '\n执行历史\n' not in actual
        # Candidate filtering and historical time filtering must stay independent.
        filters = ['--cluster', 'C1', '--start', '2026-07-23', '--end', '2026-07-24']
        scoped_args = arguments(filters)
        scoped = search(finder, raw, scoped_args)
        historic = finder.detail(scoped, '1', scoped_args, window=old, order='slowest', limit=1000)
        assert historic['executions'] and historic['end'] < scoped_args.start
        result = command(dsn, [*filters, '--show', '1', '--view', 'history', '--width', str(width),
            '--history-start', '2026-06-26', '--history-end', '2026-06-26T16:00:00Z',
            '--history-order', 'slowest', '--limit', '1000'], raw)
        stream = io.StringIO()
        Output(width, stream).history(historic)
        assert result.returncode == 0 and stream.getvalue().strip() in result.stdout.decode()
        # Long full text and the old --full-text detail behavior remain distinct.
        text = command(dsn, ['--show', '1', '--view', 'text', '--width', str(width)], b'big_list')
        assert text.returncode == 0 and data.LONG in text.stdout.decode()
        full = command(dsn, ['--show', '1', '--full-text', '--width', str(width)], b'big_list')
        assert full.returncode == 0 and data.LONG in full.stdout.decode() and '当前基线' in full.stdout.decode()
        # The real difference list must include values beyond the shared prefix.
        differences = command(dsn, ['--show', '1', '--view', 'originals', '--width', str(width)], b'preview_differences')
        assert differences.returncode == 0
        displayed = differences.stdout.decode()
        assert 'alpha' in displayed and 'beta' in displayed and 'shared-prefix' not in displayed
        for view in ('originals', 'history', 'text'):
            missing = command(dsn, ['--mode', 'exact', '--show', 's1', '--view', view],
                              b'SELECT absent_column_a; SELECT absent_column_b;')
            assert missing.returncode == 0 and not missing.stderr
            assert ('没有可查看的原文' if view == 'text' else '没有可查看的执行记录') in missing.stdout.decode()
    print('PASS: noninteractive history/original/view options, shared results, independent windows, long text, differences, zero records and empty hints', flush=True)


def verify(pg_bin):
    assert not (ROOT / 'sqlfind').exists() and not (ROOT / 'sqlfind').is_symlink()
    with instance(pg_bin) as (directory, env):
        v = Verification(pg_bin, directory, env)
        v.init()
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        # Two old but very slow executions, with equal durations, exercise global
        # ordering outside the latest page and the time tie-breaker.
        synthetic = data.events()
        session = data.Session(99, 'app_user', 'shop')
        for n in (1, 2):
            event = session.request(time_value('2026-07-17 12:00:00') + timedelta(seconds=n),
                data.BUSY.format(status=n, day='2026-06-26'), 99999)
            synthetic.extend(values for _, values in event)
        for n, value in enumerate(DIFFERENCE_VALUES):
            moment = time_value('2026-07-01' if n == 0 else '2026-07-21') + timedelta(seconds=n)
            synthetic.extend(values for _, values in session.request(moment, DIFFERENCE_SQL.format(value=value), 25))
        for raw in (CONTROL_SQL, ORDINARY_SQL):
            synthetic.extend(values for _, values in session.request(time_value('2026-07-21'), raw, 25))
        with patch.object(data, 'events', return_value=sorted(synthetic, key=lambda r: r[0])):
            data.load(dsn, directory)
        owner = connect(dsn, 'sql_apm')
        before = contents(owner)
        finder = Finder(dsn + '_ro', 'sql_apm')
        sys.path.insert(0, str(ROOT / 'scripts/tests'))
        verify_rendered_notes(dsn + '_ro', finder)
        verify_control_display(dsn + '_ro', finder)
        verify_broken_pipe(dsn + '_ro')
        verify_originals(finder)
        verify_differences(finder)
        verify_noninteractive(dsn + '_ro', finder)
        for mode, text in [('words', 'orders status'), ('passage', 'from orders'), ('exact', data.BUSY.format(status=1, day='2026-06-26')),
                           ('exact', data.LATER), ('exact', data.EXTENDED), ('exact', data.UNBASED_MIXED),
                           ('exact', 'SELECT v FROM absent_here'), ('exact', 'not a sql'),
                           ('exact', data.KEPT_CRLF.replace('\r\n', '\n')), ('exact', data.LONGEST),
                           ('words', 'wide_'), ('exact', 'SELECT v FROM wide_00; SELECT v FROM wide_01;')]:
            args = arguments(['--mode', mode])
            raw = text.encode()
            found = search(finder, raw, args)
            with finder.cursor() as cur:
                answer = found['exact']
                note = rows(cur, 'mpp_view_search_note', [finder.norm, mode, text, answer.get('fingerprint'),
                    answer.get('state'), answer.get('reason'), answer.get('exact_sql_id'), None, None, None])
                assert found['notes'] == note
                candidates = rows(cur, 'mpp_view_search', [finder.norm, mode, answer.get('fingerprint') if mode == 'exact' else text,
                    None, None, None, None, None, 'count', answer.get('exact_sql_id')])
                assert [{k: r[k] for k in candidates[0]} for r in found['candidates']] == candidates if candidates else not found['candidates']
            if text == 'wide_':
                assert len(found['candidates']) == 50 and found['candidates'][0]['total_structures'] == 60
            if text == 'not a sql':
                assert found['exact']['state'] == 'unreliable_fingerprint'
            if text == data.KEPT_CRLF.replace('\r\n', '\n'):
                assert found['exact']['line_breaks'] == 'crlf'
            if mode == 'exact':
                assert finder.service.exact(raw, [None] * 3) == found['exact']
            for mark in [str(i) for i in range(1, min(2, len(found['candidates'])) + 1)] + ['s' + str(h['statement']) for h in found['hints']]:
                detail = finder.detail(found, mark, args)
                if 'hint' in detail:
                    continue
                candidate = detail['candidate']
                with finder.cursor() as cur:
                    expected_text = rows(cur, 'mpp_view_sql_text', [finder.norm, candidate['fingerprint'], detail['text']['sql_id']])[0]
                    expected_text['selected'] = detail['original'] not in ('all', 'matched')
                    assert detail['text'] == expected_text
                    token = detail['identities'][detail['identity'] - 1]['identity']
                    version = detail['version']
                    build = version['build_id'] if version and version['selectable'] else None
                    assert detail['statistics'] == rows(cur, 'mpp_view_statistics', [finder.norm, candidate['fingerprint'], token, build, 'overall'])
                    assert [{k: v for k, v in r.items() if k != 'original_number'} for r in detail['executions']] == rows(cur, 'mpp_view_executions', [finder.norm, candidate['fingerprint'], token, detail['timing'],
                        build, detail['start'], detail['end'], detail['text']['sql_id'] if detail['text']['selected'] else None,
                        None, None, None, 'latest', 20])
                for width in (80, 200):
                    stream = io.StringIO()
                    output = Output(width, stream)
                    output.candidates(found, args)
                    assert all(cells(line) <= width for line in stream.getvalue().splitlines())
                    stream.seek(0)
                    stream.truncate()
                    output.detail(detail, True)
                    rendered = stream.getvalue()
                    raw_text = rendered.split('原文开始\n', 1)[1].split('\n原文结束', 1)[0]
                    assert raw_text == detail['text']['sql_text']
                    tables = rendered.split('\n原文结束', 1)[1]
                    assert all(cells(line) <= width for line in tables.splitlines())
                for number in range(1, len(detail['identities']) + 1):
                    changed = finder.detail(found, mark, args, number, limit=1)
                    for timing in changed['timings']:
                        chosen = finder.detail(found, mark, args, number, timing['timing'], limit=1)
                        assert chosen['identity'] == number and chosen['timing'] == timing['timing']
                        assert len(chosen['executions']) <= 1
            print('PASS: ' + mode + ' synthetic case', flush=True)
        # History limits and explicit boundaries reuse the same stored baseline.
        history_args = arguments(['--cluster', 'C1', '--database', 'shop', '--user', 'app_user'])
        history_found = search(finder, b'orders', history_args)
        default = finder.detail(history_found, '1', history_args)
        expanded = finder.detail(history_found, '1', history_args, limit=1000)
        assert len(default['executions']) == 20 and len(expanded['executions']) == 1000
        assert expanded['executions'][:20] == default['executions']
        begin, finish = expanded['executions'][10]['end_at'], expanded['executions'][3]['end_at']
        expected = [r for r in expanded['executions'] if begin <= r['end_at'] < finish]
        expected = [dict(r, matching=len(expected)) for r in expected]
        for size in (1, 1000):
            bounded = finder.detail(history_found, '1', history_args, limit=size, window=(begin, finish))
            assert bounded['executions'] == expected[:size]
            assert sum(c['records'] for c in bounded['counts']) == len(expected)
            assert bounded['version'] == default['version'] and bounded['statistics'] == default['statistics']
            assert any(r['end_at'] == begin for r in expected) and all(r['end_at'] != finish for r in expected)
            for width in (80, 200):
                stream = io.StringIO()
                Output(width, stream).history(bounded)
                assert f'本次显示: {len(expected[:size])} 条，上限 {size} 条，最新在前' in stream.getvalue()
                assert all(cells(line) <= width for line in stream.getvalue().splitlines())
        old_window = (time_value('2026-06-26'), time_value('2026-06-27'))
        older = finder.detail(history_found, '1', history_args, limit=100, window=old_window)
        assert older['executions'] and all(old_window[0] <= r['end_at'] < old_window[1] < default['start'] for r in older['executions'])
        assert older['statistics'] == default['statistics'] and older['version'] == default['version']
        empty = finder.detail(history_found, '1', history_args, window=(time_value('2030-01-01'), time_value('2030-01-02')))
        assert not empty['executions'] and not empty['counts'] and empty['statistics'] == default['statistics']
        print('PASS: history limits, inclusive/exclusive bounds, old/empty windows and unchanged baseline', flush=True)
        for n in (20, 21):
            try:
                search(finder, ('orders ' * n).encode(), arguments([]))
                assert n == 20
            except InputError:
                assert n == 21
        batch = ';'.join('SELECT v FROM wide_%02d' % n for n in range(25))
        hints = search(finder, batch.encode(), arguments(['--mode', 'exact']))
        assert hints['exact']['statement_count'] == 25 and hints['exact']['hints_truncated']
        assert len(hints['hints']) == 20
        fingerprint = finder.service.normalizer.normalize(data.EXTENDED)['fingerprint']['value']
        for mode, size in [('words', 256 * 1024), ('passage', 256 * 1024), ('exact', 512 * 1024)]:
            args = arguments(['--mode', mode])
            found = search(finder, fingerprint.encode() + b' ' * (size - len(fingerprint)), args)
            assert found['candidates'][0]['fingerprint'] == fingerprint
            try:
                search(finder, fingerprint.encode() + b' ' * (size + 1 - len(fingerprint)), args)
            except InputError:
                pass
            else:
                raise AssertionError('oversized fingerprint accepted')
        for order in ('count', 'recent'):
            args = arguments(['--cluster', 'C1', '--database', 'shop', '--user', 'app_user',
                              '--start', '2026-07-23', '--end', '2026-07-24', '--order', order])
            found = search(finder, b'orders', args)
            with finder.cursor() as cur:
                expected = rows(cur, 'mpp_view_search', [finder.norm, 'words', 'orders', 'C1', 'shop', 'app_user', args.start, args.end, order])
                assert [{k: row[k] for k in expected[0]} for row in found['candidates']] == expected
        for options, raw in [(['--show', '999'], b'orders'), (['--full-text'], b'orders'),
                             (['--show', '1', '--original', '999999'], b'orders'),
                             (['--show', '1', '--view', 'history', '--history-start', '2026-07-01'], b'orders'),
                             (['--mode', 'exact', '--show', 's1', '--original', '1'],
                              b'SELECT absent_column_a; SELECT absent_column_b;'),
                             (['--show', '1', '--identity', '999'], b'orders'),
                             (['--show', '1', '--timing', 'parse'], b'orders'), ([], b''), (['--limit', '0'], b'orders')]:
            done = command(dsn + '_ro', options, raw)
            assert done.returncode == 1 and not done.stderr and b'Traceback' not in done.stdout
        # A real read-only role timeout, while the test owner holds a table lock.
        import psycopg2
        admin = psycopg2.connect('host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=apm_test_admin')
        try:
            with admin, admin.cursor() as cur:
                cur.execute("ALTER ROLE sql_apm_ro SET statement_timeout='150ms'")
            with owner.cursor() as cur:
                cur.execute('LOCK mpp_sql_text IN ACCESS EXCLUSIVE MODE')
            timed = command(dsn + '_ro', [], b'orders')
            assert timed.returncode == 1 and '超时' in timed.stdout.decode() and not timed.stderr
            owner.rollback()
            with admin, admin.cursor() as cur:
                cur.execute("ALTER ROLE sql_apm_ro SET statement_timeout='120s'")
        finally:
            owner.rollback()
            admin.close()
        encoded = command(dsn + '_ro', ['--mode', 'exact', '--show', '1'], data.SPECIAL.encode(),
                          overrides={'PYTHONIOENCODING': 'ascii'})
        assert encoded.returncode == 0 and not encoded.stderr and b'?' in encoded.stdout
        sample = b" \r\nSELECT 'a\\b' AS name\rFROM orders "
        sample_file = directory / 'input.sql'
        sample_file.write_bytes(sample)
        responses = [command(dsn + '_ro', ['--mode', 'passage', sample.decode()]),
                     command(dsn + '_ro', ['--mode', 'passage', '--file', str(sample_file)]),
                     command(dsn + '_ro', ['--mode', 'passage'], sample)]
        assert all(r.returncode == 0 and not r.stderr for r in responses), [(r.returncode, r.stderr) for r in responses]
        assert len({r.stdout for r in responses}) == 1
        alias = directory / 'sqlfind-link'
        alias.symlink_to(ROOT / 'bin/sqlfind')
        linked = command(dsn + '_ro', ['--mode', 'passage'], sample, [str(alias)])
        module = command(dsn + '_ro', ['--mode', 'passage'], sample,
                         [str(ROOT / '.venv/bin/python'), '-B', '-m', 'sql_apm', 'sqlfind'])
        assert linked.stdout == responses[0].stdout
        assert module.returncode == 0 and module.stdout == responses[0].stdout
        aliases = directory / 'links with spaces'
        aliases.mkdir()
        relative = aliases / 'sqlfind'
        relative.symlink_to('../sqlfind-link')
        chained = command(dsn + '_ro', ['--mode', 'passage'], sample, [str(relative)])
        assert chained.returncode == 0 and chained.stdout == responses[0].stdout
        # A fresh source tree catches first-import bytecode writes, not just warmed caches.
        fresh = directory / 'fresh app'
        shutil.copytree(ROOT / 'sql_apm', fresh / 'sql_apm', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / 'rules', fresh / 'rules')
        (fresh / 'bin').mkdir()
        shutil.copy2(ROOT / 'bin/sqlfind', fresh / 'bin/sqlfind')
        (fresh / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
        empty_home = directory / 'empty-home'
        empty_home.mkdir()
        snapshot = lambda base: {str(p.relative_to(base)): (p.stat().st_size, p.stat().st_mtime_ns)
                                 for p in base.rglob('*') if p.is_file()}
        files_before = snapshot(fresh), snapshot(empty_home)
        no_cache = command(dsn + '_ro', [], b'orders', [str(fresh / 'bin/sqlfind')],
                           overrides={'PYTHONPATH': str(fresh), 'HOME': str(empty_home)})
        assert no_cache.returncode == 0, no_cache.stderr
        assert (snapshot(fresh), snapshot(empty_home)) == files_before
        sys.path.insert(0, str(ROOT / 'scripts/tests'))
        from sqlfind_terminal import verify as verify_terminal
        verify_terminal(dsn + '_ro', directory, owner)
        assert contents(owner) == before
        owner.close()
        v.init('check')
        print('RESULT: sqlfind database and entry acceptance passed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
