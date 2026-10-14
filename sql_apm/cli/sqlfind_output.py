"""Plain terminal rendering, without a pager, colors or a line editing library."""
from datetime import datetime, timedelta, timezone
import os
import sys
import unicodedata

BEIJING = timezone(timedelta(hours=8))
TEXT_LIMIT = 4000


def cells(text):
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)


def plain(value):
    """Only ASCII and full-width Chinese characters take part in alignment.

    Other characters are represented by an unambiguous ASCII escape. Original
    SQL is printed separately and never passes through this function.
    """
    value = '未知' if value is None else str(value)
    result = []
    for char in value:
        if ' ' <= char <= '~' or (unicodedata.east_asian_width(char) in ('W', 'F')
                                 and unicodedata.category(char)[0] in 'LP'):
            result.append(char)
        elif char == '\n':
            result.append('\\n')
        elif char == '\r':
            result.append('\\r')
        elif char == '\t':
            result.append('\\t')
        else:
            result.append(ascii(char)[1:-1])
    return ''.join(result)


def pieces(text, width):
    line, size = '', 0
    for char in text:
        used = cells(char)
        if size + used > width and line:
            yield line
            line, size = '', 0
        line += char
        size += used
    if line or not text:
        yield line


def clip(text, width):
    if cells(text) <= width:
        return text
    return next(pieces(text, max(1, width - 3))) + '...'


def stamp(value):
    if value is None:
        return '未知'
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(BEIJING).isoformat(sep=' ', timespec='milliseconds')[:-6]


def duration(value):
    return '未知' if value is None else str(value) + ' ms'


class Output:
    def __init__(self, width=None, stream=None):
        self.stream = stream or sys.stdout
        self.requested_width = width
        self.replaced = False

    @property
    def width(self):
        if self.stream.isatty():
            try:
                return max(20, os.get_terminal_size(self.stream.fileno()).columns)
            except OSError:
                pass
        return self.requested_width or 100

    def write(self, text):
        encoding = getattr(self.stream, 'encoding', None) or 'utf-8'
        encoded = text.encode(encoding, 'replace').decode(encoding)
        if encoded != text:
            self.replaced = True
        self.stream.write(encoded)
        self.stream.flush()

    def line(self, text=''):
        for part in pieces(plain(text), self.width):
            self.write(part + '\n')

    def field(self, label, value):
        self.line(label + ': ' + plain(value))

    def table(self, headers, records):
        records = [[plain(value) for value in row] for row in records]
        if not records:
            self.line('无记录')
            return
        widths = [max(cells(plain(head)), *(cells(row[i]) for row in records)) for i, head in enumerate(headers)]
        if sum(widths) + 2 * (len(headers) - 1) <= self.width:
            for row in [[plain(h) for h in headers]] + records:
                self.write('  '.join(v + ' ' * (w - cells(v)) for v, w in zip(row, widths)).rstrip() + '\n')
        else:
            # Label every continuation so long identities and large exact numbers
            # remain complete in an 80-column window.
            for row in records:
                self.line(' | '.join(h + ': ' + value for h, value in zip(headers, row)))

    def footer(self):
        if self.replaced:
            self.write('提示: 当前输出编码无法表示部分字符，已使用替代符号。\n')
            self.replaced = False

    def candidates(self, found, args):
        self.line('时间均为北京时间 (UTC+08:00)')
        self.line('这次检索')
        for note in found['notes']:
            self.field(note['item'], note['content'])
        if args.mode == 'exact':
            self.line('原文比较时 CR LF、单独的 CR、LF 视为相同，其余字符逐一比较。')
            form = found['exact'].get('line_breaks')
            if form:
                self.field('采用库里的换行形式', {'crlf': 'CR LF', 'cr': '单独的 CR', 'lf': 'LF'}[form])
        for label, value in [('集群', args.cluster), ('数据库', args.database), ('执行用户', args.user),
                             ('开始时间', stamp(args.start) if args.start else None),
                             ('结束时间', stamp(args.end) if args.end else None)]:
            self.field(label, value if value is not None else '不限')
        self.field('排序', '记录数多的在前' if args.order == 'count' else '最近执行的在前')
        candidates = found['candidates']
        self.field('符合条件的结构总数', candidates[0]['total_structures'] if candidates else 0)
        self.field('本次显示行数', len(candidates))
        if candidates:
            heads = ['标记', '记录数', '原文数', '身份数', '最近执行时间', '记录最多的身份']
            values = [[str(n), str(r['record_count']), str(r['matched_texts'] if r['matched_texts'] is not None else r['structure_texts']),
                       str(r['identities']), stamp(r['last_at']), plain(r['top_label'])]
                      for n, r in enumerate(candidates, 1)]
            widths = [max(cells(heads[i]), *(cells(row[i]) for row in values)) for i in range(5)]
            remaining = self.width - sum(widths) - 10
            widths.append(max(8, min(44 if self.width >= 160 else 20, remaining)))
            prefix_width = sum(widths) + 10
            inline = self.width - prefix_width - 2 >= 16
            self.line('  '.join(h + ' ' * max(0, w - cells(h)) for h, w in zip(heads, widths))
                      + ('  原文示例' if inline else ''))
            for row, candidate in zip(values, candidates):
                row[-1] = clip(row[-1], widths[-1])
                prefix = '  '.join(v + ' ' * max(0, w - cells(v)) for v, w in zip(row, widths))
                example = plain(' '.join((candidate['example'] or '').split()))
                if inline:
                    self.line(prefix + '  ' + clip(example, self.width - prefix_width - 2))
                else:
                    self.line(prefix.rstrip())
                    self.line('  原文: ' + clip(example, self.width - 8))
        hints = found['hints']
        if hints:
            self.line('逐条提示: 逐条命中不等于整批命中')
            self.field('语句总数', found['exact']['statement_count'])
            if found['exact'].get('hints_truncated'):
                self.line('只显示前 20 条语句')
            self.table(['标记', '状态', '记录数', '结构指纹'],
                       [('s' + str(h['statement']), h['state_label'], h['record_count'], h['fingerprint']) for h in hints])
        self.footer()

    def detail(self, detail, full_text=False):
        self.line('时间均为北京时间 (UTC+08:00)')
        if 'hint' in detail:
            h = detail['hint']
            self.field('结构指纹', h['fingerprint'])
            self.field('状态', h['state_label'])
            self.line('没有可查看的执行记录和身份。')
            return
        candidate = detail['candidate']
        self.field('结构指纹', candidate['fingerprint'])
        self.field('该结构原文份数', candidate['structure_texts'])
        if candidate['matched_texts'] is not None:
            self.field('本次命中原文份数', candidate['matched_texts'])
        text = detail['text']
        if text:
            number = next(r['number'] for r in detail['originals'] if r['sql_id'] == text['sql_id'])
            note = f'编号 {number}，' + ('当前选中的原文' if text['selected'] else '当前原文范围的一份示例')
            self.field('原文', note)
            raw = text['sql_text']
            self.field('原文总字符数', len(raw))
            self.line('原文开始')
            self.write(raw if full_text else raw[:TEXT_LIMIT])
            self.write('\n')
            self.line('原文结束')
            if not full_text and len(raw) > TEXT_LIMIT:
                self.line('原文已截断，使用 --show 标记 --full-text 或交互命令 f 查看全文。')
        self.line('身份 (* 表示当前所选)')
        self.table(['序号', '集群', '数据库', '执行用户', '记录数', '首次执行', '最近执行', '当前基线'],
            [(str(n) + ('*' if n == detail['identity'] else ''), r['scope_id'], r['database'], r['execution_user'],
              r['record_count'], stamp(r['first_at']), stamp(r['last_at']), '有' if r['has_baseline'] else '无')
             for n, r in enumerate(detail['identities'], 1)])
        self.line('当前基线')
        self.line('基线口径：当前身份整个 SQL 结构的整体基线，不随原文和历史时间筛选改变。')
        version = detail['version']
        if version:
            self.field('版本标识', version['build_id'])
            self.field('发布时间', stamp(version['published_at']))
            self.field('训练窗口', stamp(version['window_start']) + ' 至 ' + stamp(version['window_end']) + ' (右端不含)')
        statistics = [s for s in detail['statistics'] if s['sample_state'] == 'available']
        if not statistics:
            self.line('该身份没有当前基线。')
        labels = detail['labels']
        records = []
        for s in statistics:
            conditions = s['sufficiency']
            def metric(key, gate):
                value = duration(s[key])
                return value if conditions[gate]['met'] else value + ' (样本不足，仅供参考)'
            records.append([labels['timing', s['timing_type']], s['included_count'], s['active_days'],
                metric('mean_ms', 'basic'), metric('p50_ms', 'basic'), metric('p95_ms', 'p95'),
                metric('p99_ms', 'p99'), metric('max_ms', 'basic')] +
                ['满足' if conditions[k]['met'] else '不满足' for k in ('basic', 'p95', 'p99')])
        if records:
            self.table(['计时类别', '有效样本', '活跃天数', '平均', 'P50', 'P95', 'P99', '最大', '基础条件', 'P95条件', 'P99条件'], records)
        self.history(detail)

    def history(self, detail):
        labels = detail['labels']
        self.line('执行历史')
        selected = detail['identities'][detail['identity'] - 1]
        self.field('身份', ' / '.join('未知' if selected[k] is None else str(selected[k])
                                   for k in ('scope_id', 'database', 'execution_user')))
        self.field('计时类别', labels['timing', detail['timing']])
        self.original_scope(detail)
        self.field('时间设置', '沿用切换原文前的范围' if detail.get('window_kept') else
                   '自定义' if detail['custom_window'] else '最近一次执行往前 7 天')
        self.field('范围', stamp(detail['start']) + ' 至 ' + stamp(detail['end']) + ' (右端不含)')
        self.field('范围内记录数', sum(c['records'] for c in detail['counts']))
        for count in detail['counts']:
            self.field(count['label'], count['records'])
        order = '最新在前' if detail['order'] == 'latest' else '最慢在前'
        self.field('本次显示', f"{len(detail['executions'])} 条，上限 {detail['limit']} 条，{order}")
        self.table(['原文编号', '结束时间', '耗时', '状态', '与当前基线比较', '单条或整批'],
            [(r['original_number'], stamp(r['end_at']), duration(r['duration_ms']), labels['outcome', r['outcome']],
              r['comparison'], labels['shape', r['request_shape']]) for r in detail['executions']])
        self.footer()

    def original_scope(self, detail):
        selected = [r for r in detail['originals'] if r['in_scope']]
        name = {'matched': '本次检索命中的原文', 'all': '该结构的全部原文'}.get(detail['original'])
        self.field('原文范围', (name + f'（共 {len(selected)} 份）') if name else
                   f"指定原文（编号 {selected[0]['number']}）")
        self.field('其中当前身份、计时和时间内出现的原文数', sum(r['records'] > 0 for r in selected))

    def originals(self, detail):
        self.line('原文列表：编号在本次检索的同一结构内保持不变。')
        self.original_scope(detail)
        self.line('记录数和最近执行时间按当前身份、计时及历史时间范围统计；0 表示此范围内没有记录。')
        self.table(['原文编号', '本次命中', '记录数', '最近执行时间', '原文摘要'],
            [(r['number'], '是' if r['matched'] else '否', r['records'], stamp(r['last_at']),
              clip(' '.join(r['example'].split()) + ('...' if r['chars'] > len(r['example']) else ''), 80))
             for r in detail['originals'] if r['in_scope']])
        self.line('v 编号 选择原文并看历史；f 查看所选原文全文；v matched 本次命中；v all 全部原文。')
        self.footer()
