#!/usr/bin/env python3
"""Generate the three packaged dashboards into grafana/dashboards/.

The JSON files are committed; this program is their source. Identifiers that
other dashboards may link to are stable: the dashboard uids (mpp-search,
mpp-list, mpp-detail) and the variable names passed between them.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'grafana/dashboards'
PG = dict(type='grafana-postgresql-datasource', uid='sql-apm-pg')
SERVICE = dict(type='yesoreyeram-infinity-datasource', uid='sql-apm-fingerprint')
SHARED = dict(type='datasource', uid='-- Dashboard --')  # built in: reuse another panel's result
# The part of an original text shown when the pointer rests on the mark of its example cell.
# The table gives that box neither a height limit nor a scroll bar, so the text is cut to
# what fits a small screen: so many characters, and so many lines as they will be drawn
# (a line longer than HOVER_WRAP width units wraps; a non-ASCII character counts as two).
HOVER_CHARS, HOVER_LINES, HOVER_WRAP = 1600, 24, 75
EDITOR = 110  # pixels of the search input; more text scrolls inside it or is read enlarged
REVISION = 1
SEARCH, LIST, DETAIL = '/d/mpp-search/sql-search', '/d/mpp-list/sql-list', '/d/mpp-detail/sql-detail'
RANGE = '$__timeFrom()::timestamptz,$__timeTo()::timestamptz'
MS = "(extract(epoch FROM {})*1000)::bigint"
TIMINGS = [('请求整体', 'request'), ('Execute 首次', 'execute_first'), ('Execute 续取', 'execute_fetch'),
           ('Parse', 'parse'), ('Bind', 'bind')]


def target(sql, ref='A', form='table'):
    return dict(datasource=PG, editorMode='code', format=form, rawQuery=True, rawSql=sql, refId=ref)


class Layout:
    """Stack panels top to bottom; each call to row() starts a new line."""
    def __init__(self):
        self.panels, self.y, self.next_id, self.collapsed = [], 0, 1, None

    def identify(self, panel):
        """Give the panel its number now, so that another panel can refer to it before it is placed."""
        if 'id' not in panel:
            panel['id'] = self.next_id
            self.next_id += 1
        return panel

    def add(self, panel, x, w, h):
        self.identify(panel)
        panel['gridPos'] = dict(x=x, y=self.y, w=w, h=h)
        (self.collapsed['panels'] if self.collapsed is not None else self.panels).append(panel)
        return panel

    def line(self, height, *cells):
        """cells: (panel, width); placed left to right on one line."""
        x = 0
        for panel, width in cells:
            self.add(panel, x, width, height)
            x += width
        self.y += height

    def beside(self, panel, width, *stacked):
        """One panel on the left and, to its right, (panel, height) pairs stacked to the same total height."""
        self.add(panel, 0, width, sum(height for _, height in stacked))
        for other, height in stacked:
            self.add(other, width, 24 - width, height)
            self.y += height

    def row(self, title, collapsed=False):
        self.collapsed = None
        row = self.add(dict(type='row', title=title, collapsed=collapsed, panels=[]), 0, 24, 1)
        self.y += 1
        self.collapsed = row if collapsed else None


def override(name, **properties):
    return dict(matcher=dict(id='byName', options=name),
                properties=[dict(id=key.replace('__', '.'), value=value) for key, value in properties.items()])


def table(title, sql, columns, description='', footer=None, links=None):
    """columns: (field, display name, extra properties); a None display name hides the field.

    ``links`` makes every cell of a row a link. A column whose extra properties say
    ``plain=True`` is left out of it: Grafana adds a link given for one field to the
    links of all fields instead of replacing them, so the row link is then given to
    every other field by name pattern.
    """
    overrides, plain = [], []
    for field, display, *extra in columns:
        properties = dict(extra[0]) if extra else {}
        if properties.pop('plain', False):
            plain.append(field)
        if display is None:
            properties['custom__hideFrom__viz'] = True
        else:
            properties['displayName'] = display
        overrides.append(override(field, **properties))
    panel = dict(type='table', title=title, description=description, datasource=PG, targets=[target(sql)],
                 fieldConfig=dict(defaults=dict(custom=dict(align='auto', cellOptions=dict(type='auto'), inspect=True, filterable=False, minWidth=60)),
                                  overrides=overrides),
                 options=dict(showHeader=True, cellHeight='sm'))
    if links and plain:
        overrides.append(dict(matcher=dict(id='byRegexp', options='/^(?!(' + '|'.join(plain) + ')$).*$/'), properties=[dict(id='links', value=links)]))
    elif links:
        panel['fieldConfig']['defaults']['links'] = links
    return panel


def link(title, url):
    return [dict(title=title, url=url, targetBlank=False)]


def stat(title, sql, description='', color_mode='none', transformations=None):
    return dict(type='stat', title=title, description=description, datasource=PG, targets=[target(sql)],
                transformations=transformations or [],
                fieldConfig=dict(defaults=dict(color=dict(mode='fixed', fixedColor='text'), mappings=[], noValue='—',
                                               thresholds=dict(mode='absolute', steps=[dict(color='text', value=None)])), overrides=[]),
                options=dict(reduceOptions=dict(values=False, calcs=['lastNotNull'], fields=''), orientation='auto',
                             textMode='value_and_name', colorMode=color_mode, graphMode='none', justifyMode='center',
                             wideLayout=True, showPercentChange=False))


def total(title, source, field, description=''):
    """One number shown once, taken from another panel's result so that nothing is queried twice.

    A total of the whole list does not belong in every row of it; the field stays
    in that panel's result and is only hidden from its table.
    """
    return dict(type='stat', title=title, description=description, datasource=SHARED,
                targets=[dict(datasource=SHARED, panelId=source['id'], refId='A', withTransforms=False)],
                fieldConfig=dict(defaults=dict(color=dict(mode='fixed', fixedColor='text'), unit='locale', noValue='0', mappings=[],
                                               thresholds=dict(mode='absolute', steps=[dict(color='text', value=None)])), overrides=[]),
                options=dict(reduceOptions=dict(values=False, calcs=['lastNotNull'], fields='/^' + field + '$/'), orientation='auto',
                             textMode='value', colorMode='none', graphMode='none', justifyMode='center', wideLayout=True,
                             showPercentChange=False))


def text(title, content, mode='markdown', **extra):
    options = dict(mode=mode, content=content)
    if mode == 'code':
        options['code'] = dict(language='sql', showLineNumbers=True, showMiniMap=False)
    return dict(type='text', title=title, options=options, **extra)


def variable(kind, name, label='', hide=0, **extra):
    base = dict(type=kind, name=name, label=label or name, hide=hide)
    if kind == 'query':
        base.update(datasource=PG, query=extra.pop('sql'), refresh=1, sort=0, multi=False, includeAll=False,
                    options=[], current={})
        base['definition'] = base['query']
    elif kind == 'textbox':
        value = extra.pop('value', '')
        base.update(query=value, current=dict(text=value, value=value), options=[dict(selected=True, text=value, value=value)])
    elif kind == 'custom':
        pairs = extra.pop('pairs')
        chosen = extra.pop('default', pairs[0][1])
        base.update(query=','.join(text + ' : ' + value for text, value in pairs), multi=False, includeAll=False,
                    options=[dict(text=text, value=value, selected=value == chosen) for text, value in pairs],
                    current=dict(text=dict(pairs_swap(pairs))[chosen], value=chosen))
    base.update(extra)
    return base


def pairs_swap(pairs):
    return [(value, text) for text, value in pairs]


def multi(name, label, pairs, selected):
    """Multi-value custom variable; its csv form is passed to the query functions."""
    chosen = [(text, value) for text, value in pairs if value in selected]
    return dict(type='custom', name=name, label=label, hide=0, multi=True, includeAll=False,
                query=','.join(text + ' : ' + value for text, value in pairs),
                options=[dict(text=text, value=value, selected=value in selected) for text, value in pairs],
                current=dict(text=[text for text, _ in chosen], value=[value for _, value in chosen]))


def filters():
    """Cluster, database and user dropdowns. Values are tokens; '*' means all.

    Grafana quotes the values of a variable that offers "All", so the choice is
    an ordinary first option instead.
    """
    def one(name, label, column, source):
        return variable('query', name, label, current=dict(text='全部', value='*'),
                        sql="SELECT '全部' AS __text, '*' AS __value, 0 AS seq UNION ALL "
                            "SELECT DISTINCT {0}, mpp_view_encode({0}), 1 FROM {1} WHERE {0} IS NOT NULL ORDER BY 3, 1".format(column, source))
    return [one('cluster', '集群', 'scope_id', 'scope'),
            one('database', '数据库', 'database', 'mpp_baseline_group'),
            one('user', '执行用户', 'execution_user', 'mpp_baseline_group')]


FILTER_ARGS = ("mpp_view_decode(nullif('${cluster}','*')),mpp_view_decode(nullif('${database}','*')),"
               "mpp_view_decode(nullif('${user}','*'))")


def tag(uid, panels, variables, annotations):
    """Start every query with a comment naming its dashboard and panel.

    The database administrator can tell where a statement comes from, and the
    end-to-end check can tell which panel a result belongs to.
    """
    def mark(item, label):
        for query in item.get('targets', []):
            if 'rawSql' in query:
                query['rawSql'] = '/* ' + uid + ' ' + label + ' ' + query['refId'] + ' */ ' + query['rawSql']
    for panel in panels:
        mark(panel, 'panel ' + str(panel['id']))
        for inner in panel.get('panels', []):
            mark(inner, 'panel ' + str(inner['id']))
    for item in variables:
        if item['type'] == 'query':
            item['query'] = item['definition'] = '/* ' + uid + ' variable ' + item['name'] + ' */ ' + item['query']
    for item in annotations:
        item['target']['rawSql'] = '/* ' + uid + ' annotation */ ' + item['target']['rawSql']


def dashboard(uid, title, description, panels, variables, time, annotations=None, links=None):
    tag(uid, panels, variables, annotations or [])
    return dict(uid=uid, title=title, description=description + '（随包看板 r' + str(REVISION) + '）',
                tags=['sql-apm', 'mpp'], timezone='Asia/Shanghai', editable=True, graphTooltip=1,
                schemaVersion=41, version=REVISION, refresh='', time=time, timepicker={},
                templating=dict(list=variables), panels=panels,
                annotations=dict(list=[dict(builtIn=1, datasource=dict(type='grafana', uid='-- Grafana --'), enable=True,
                                            hide=True, iconColor='rgba(0, 211, 255, 1)', name='Annotations & Alerts',
                                            type='dashboard')] + (annotations or [])),
                links=links or [])


def navigation(*items):
    names = dict(search=('SQL 检索', SEARCH, 'search'), list=('SQL 列表', LIST, 'list-ul'))
    return [dict(type='link', title=names[item][0], url=names[item][1], icon=names[item][2], targetBlank=False,
                 keepTime=False, includeVars=False, asDropdown=False, tags=[], tooltip='') for item in items]


def detail_url(fingerprint, identity, start, end, extra=''):
    """SQL expression for a link into the detail page with an explicit time range."""
    return ("'" + DETAIL + "?var-fp='||" + fingerprint + "||'&var-identity='||" + identity
            + "||'&from='||" + MS.format(start) + "||'&to='||" + MS.format(end) + extra)


# --------------------------------------------------------------------------- SQL 列表
def list_dashboard():
    layout = Layout()
    # The link target sits in its own hidden column: a value mapping on the linked
    # field would replace the address itself with the mapped text.
    layout.line(5, (table('数据的时间范围', """SELECT s.scope_id,min(f.first_log_at) first_at,max(f.last_log_at) last_at,count(*) files,'设为时间范围' AS action,
  '""" + LIST + """?from='||""" + MS.format("max(f.last_log_at)-interval '24 hours'") + """||'&to='||""" + MS.format("max(f.last_log_at)+interval '1 second'") + """ AS url
FROM scope s JOIN source_file f USING(scope_id) GROUP BY s.scope_id ORDER BY s.scope_id""",
        [('scope_id', '集群'), ('first_at', '日志最早时间'), ('last_at', '日志最晚时间'), ('files', '已导入文件数'),
         ('action', '看最后 24 小时', dict(links=link('把时间范围设为该集群日志的最后 24 小时', '${__data.fields.url:raw}'))),
         ('url', None)],
        description='导入的日志往往不是当前时间的。按时间范围现算的排行用右上角的时间范围，点“设为时间范围”可以直接跳到该集群有数据的最后一天。'), 24))
    layout.row('按时间范围现算的排行（右上角所选时间范围内的执行记录）')
    ranking = layout.identify(table('SQL 身份排行：所选时间范围内（最多 ${limit} 行）', """SELECT r.scope_id,r.database,r.execution_user,mpp_view_label('timing',coalesce(r.timing_type,'unknown')) timing,
  r.record_count,r.not_success,r.total_ms,r.mean_ms,r.slowest_ms,r.last_at,r.fingerprint,r.ranked_rows,
  """ + detail_url('r.fingerprint', 'r.identity', '$__timeFrom()::timestamptz', '$__timeTo()::timestamptz',
                   "||'&var-timing='||coalesce(r.timing_type,'unknown')") + """ AS url
FROM mpp_view_ranking('${norm}',""" + RANGE + "," + FILTER_ARGS + """,'${timings:csv}','${rank_order}',${limit}) r""",
        [('scope_id', '集群'), ('database', '数据库'), ('execution_user', '执行用户'), ('timing', '计时类别'),
         ('record_count', '次数'), ('not_success', '未成功次数'), ('total_ms', '总耗时', dict(unit='ms')),
         ('mean_ms', '平均耗时', dict(unit='ms')), ('slowest_ms', '最慢一次', dict(unit='ms')),
         ('last_at', '范围内最近一次'), ('fingerprint', '结构指纹', dict(custom__width=260)),
         ('ranked_rows', None), ('url', None)],
        description='一行是一个 SQL 身份（集群＋数据库＋执行用户＋SQL 结构）的一类计时。“次数”是这一类计时的记录数；Execute、Parse、Bind 是阶段或调用，不相加成执行次数。'
                    '失败、取消、超时的记录没有计时类别，“未成功次数”按 SQL 身份统计。点一行进入详情。',
        links=link('进入 SQL 详情', '${__data.fields.url:raw}')))
    layout.line(3, (total('所选时间范围内，符合筛选的一共有多少行（下表只显示排在最前的 ${limit} 行）', ranking, 'ranked_rows',
                          '一行是一个 SQL 身份的一类计时。这个数随时间范围和顶部的筛选变化。'), 24))
    layout.line(13, (ranking, 24))
    layout.row('按当前基线版本统计的排行（各集群当前生效版本里已算好的整体基线，与时间范围无关）')
    baseline = layout.identify(table('SQL 身份排行：当前基线版本（最多 ${limit} 行）', """SELECT r.scope_id,r.database,r.execution_user,mpp_view_label('timing',r.timing_type) timing,
  r.included_count,r.active_days,r.p50_ms,r.p95_ms,r.p99_ms,r.max_ms,r.mean_ms,
  CASE WHEN r.p99_met THEN '三项都满足' WHEN r.p95_met THEN 'P99 样本不足' WHEN r.basic_met THEN 'P95、P99 样本不足' ELSE '样本不足' END conditions,
  r.fingerprint,r.ranked_rows,
  """ + detail_url('r.fingerprint', 'r.identity', "h.last_at-interval '7 days'", "h.last_at+interval '1 millisecond'",
                   "||'&var-timing='||r.timing_type") + """ AS url
FROM mpp_view_baseline_ranking('${norm}',""" + FILTER_ARGS + """,'${timings:csv}','${base_order}',nullif('${min_samples}','')::bigint,${limit}) r
CROSS JOIN LATERAL (SELECT max(x.last_at) last_at FROM mpp_query_hits('${norm}',r.fingerprint,r.scope_id,r.database,r.execution_user) x) h""",
        [('scope_id', '集群'), ('database', '数据库'), ('execution_user', '执行用户'), ('timing', '计时类别'),
         ('included_count', '样本数'), ('active_days', '活跃天数'), ('p50_ms', 'P50', dict(unit='ms')),
         ('p95_ms', 'P95', dict(unit='ms')), ('p99_ms', 'P99', dict(unit='ms')), ('max_ms', '最大', dict(unit='ms')),
         ('mean_ms', '平均', dict(unit='ms')), ('conditions', '样本条件'),
         ('fingerprint', '结构指纹', dict(custom__width=260)), ('ranked_rows', None), ('url', None)],
        description='数值来自各集群当前生效版本保存的整体基线，不重新计算。某项样本条件不满足时，对应的数值仅供参考。点一行进入详情，时间范围为该 SQL 最近一次执行往前 7 天。',
        links=link('进入 SQL 详情', '${__data.fields.url:raw}')))
    layout.line(3, (total('当前基线版本里，符合筛选的一共有多少行（下表只显示排在最前的 ${limit} 行）', baseline, 'ranked_rows',
                          '一行是一个 SQL 身份的一类计时。这个数随顶部的筛选变化，与时间范围无关。'), 24))
    layout.line(13, (baseline, 24))
    layout.row('版本列表')
    layout.line(8, (table('已发布的基线版本', """SELECT v.scope_id,v.published_at,v.built_at,v.window_start,v.window_end,v.window_days,
  CASE WHEN v.is_current THEN '当前生效' ELSE '历史版本' END current,
  CASE WHEN v.results_cleaned THEN '已清理（'||to_char(v.cleaned_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')||'）' ELSE '保留' END cleaned,
  CASE WHEN v.rules_match THEN '与当前规则相同' ELSE '规则版本不同' END rules,v.build_id
FROM mpp_query_versions('${norm}',mpp_view_decode(nullif('${cluster}','*'))) v ORDER BY v.scope_id,v.published_at DESC""",
        [('scope_id', '集群'), ('published_at', '发布时间'), ('built_at', '构建完成时间'), ('window_start', '训练窗口开始'),
         ('window_end', '训练窗口结束'), ('window_days', '窗口天数'), ('current', '是否当前生效'), ('cleaned', '结果是否已清理'),
         ('rules', '规则'), ('build_id', '版本标识')]), 24))
    variables = [variable('query', 'norm', hide=2, skipUrlSync=True, sql='SELECT mpp_view_rules()')] + filters() + [
        multi('timings', '计时类别', TIMINGS, ('request', 'execute_first')),
        variable('custom', 'rank_order', '时间范围排行按', pairs=[('次数', 'count'), ('总耗时', 'total'), ('平均耗时', 'mean'),
                                                              ('最慢一次', 'slowest'), ('未成功次数', 'not_success')]),
        variable('custom', 'base_order', '基线排行按', pairs=[('P95', 'p95'), ('P50', 'p50'), ('P99', 'p99'), ('最大', 'max'),
                                                           ('平均', 'mean'), ('样本数', 'samples')]),
        variable('textbox', 'min_samples', '基线样本数不少于', value=''),
        variable('custom', 'limit', '行数', pairs=[('50', '50'), ('100', '100'), ('200', '200'), ('500', '500')], default='100')]
    return dashboard('mpp-list', 'SQL 列表', '按时间范围现算的排行、按当前基线版本统计的排行和版本列表',
                     layout.panels, variables, dict({'from': 'now-24h', 'to': 'now'}), links=navigation('search'))


# --------------------------------------------------------------------------- SQL 详情
ARGS = "'${norm}','${fp}','${identity}'"
RECORDS = ARGS + ",'${timing}'," + RANGE + ",'${sqlid}'"
KEPT = ['fp', 'identity', 'timing', 'version', 'sqlid', 'status', 'dmin', 'dmax', 'list_order', 'text_order',
        'layer', 'mode', 'q', 'hit']


def same_page(**changed):
    """Link to the detail page itself with the current state except the given variables."""
    parts = ['${__url_time_range}'] + ['${' + name + ':queryparam}' for name in KEPT if name not in changed]
    parts += ['var-' + name + '=' + value for name, value in changed.items()]
    return DETAIL + '?' + '&'.join(parts)


STATISTIC = "mpp_view_statistics(" + ARGS + ",'${version}'"
NUMBER = "nullif(btrim('{}'),'')::numeric"
METRICS = [('included_count', '样本数'), ('active_days', '活跃天数'), ('excluded_count', '被排除的样本数'),
           ('min_ms', '最小'), ('p25_ms', 'P25'), ('p50_ms', 'P50'), ('p75_ms', 'P75'), ('p90_ms', 'P90'),
           ('p95_ms', 'P95'), ('p99_ms', 'P99'), ('max_ms', '最大'), ('mean_ms', '平均'), ('stddev_ms', '标准差'),
           ('cv', '变异系数'), ('mad_ms', '绝对中位差'), ('iqr_ms', '四分位距'), ('log_median', '对数中位数'),
           ('log_mad', '对数绝对中位差'), ('p95_p50', 'P95 与 P50 之比'), ('p99_p50', 'P99 与 P50 之比')]
CONDITION = """CASE WHEN (s.sufficiency->'{0}'->>'met')::boolean THEN '满足' ELSE '不满足：'||(SELECT string_agg(mpp_view_label('reason',r),'、')
    FROM jsonb_array_elements_text(s.sufficiency->'{0}'->'reasons') r) END"""


def chart(kind, title, sql, description='', x=None, unit='ms', form='table', **extra):
    panel = dict(type=kind, title=title, description=description, datasource=PG, targets=[target(sql, form=form)],
                 fieldConfig=dict(defaults=dict(unit=unit, min=0, color=dict(mode='palette-classic'), custom={}), overrides=[]),
                 options=dict(legend=dict(displayMode='list', placement='bottom', showLegend=True),
                              tooltip=dict(mode='multi', sort='none')))
    overrides = extra.pop('overrides', None)
    if overrides:
        panel['fieldConfig']['overrides'] = overrides
    if x:
        panel['options'].update(xField=x, orientation='vertical', barWidth=0.9, groupWidth=0.75, stacking='none',
                                showValue='never', xTickLabelRotation=0, xTickLabelSpacing=100)
    panel.update(extra)
    return panel


def pattern(title, layer, label, description):
    """P50 and P95 per bucket of one stored layer; never depends on the time range."""
    return chart('barchart', title, "SELECT " + label + """ AS bucket,s.p50_ms AS "P50",s.p95_ms AS "P95"
FROM """ + STATISTIC + ",'" + layer + """') s WHERE s.timing_type='${timing}' AND s.included_count>0
ORDER BY s.bucket_date,s.bucket_number""", description, x='bucket',
                 overrides=[override('bucket', unit='string')])


def detail_dashboard():
    layout = Layout()
    layout.line(9,
        (text('SQL 原文：${sql_note}', '${sql_text:raw}', mode='code',
              description='选了某一份原文时显示它，否则显示同一结构的一份示例。原文按原样保存，带实际取值。'), 14),
        (table('当前查看的内容', """SELECT * FROM (
SELECT 1 seq,'SQL 身份' item,(SELECT i.label FROM mpp_view_identities('${norm}','${fp}') i WHERE i.identity='${identity}') content,NULL::text url
UNION ALL SELECT 2,'计时类别',mpp_view_label('timing','${timing}')||CASE WHEN '${timing}' IN ('request','unknown') THEN '' ELSE '：是阶段或调用的记录，不加总成执行次数' END,NULL
UNION ALL SELECT 3,'原文范围',CASE WHEN '${sqlid}'='' THEN '同一结构的全部原文（共 '||(SELECT structure_texts FROM mpp_view_sql_text('${norm}','${fp}'))||' 份）'
  ELSE '只看一份原文 ${sqlid}；点这里回到全部原文' END,CASE WHEN '${sqlid}'<>'' THEN '""" + same_page(sqlid='') + """' END
UNION ALL SELECT 4,'时间范围','点这里设为这条 SQL 最近一次执行往前 7 天',
  (SELECT '""" + DETAIL + """?'||'${fp:queryparam}&${identity:queryparam}&${timing:queryparam}&${version:queryparam}&${sqlid:queryparam}&${mode:queryparam}&${q:queryparam}&${hit:queryparam}'
     ||'&from='||""" + MS.format("i.last_at-interval '7 days'") + """||'&to='||""" + MS.format("i.last_at+interval '1 millisecond'") + """
   FROM mpp_view_identities('${norm}','${fp}') i WHERE i.identity='${identity}')
UNION ALL SELECT 5,'失败、取消、超时','没有计时类别和耗时，在每个计时类别下都列出',NULL
UNION ALL SELECT 6,'提示',m.n||' 份原文尚未按当前规则生成指纹，其执行记录不在结果中',NULL
  FROM (SELECT mpp_query_missing_rules('${norm}') n) m WHERE m.n>0
UNION ALL SELECT 7,'返回','回到 SQL 检索（填回上一次的输入）','""" + SEARCH + """'
) x WHERE x.content IS NOT NULL ORDER BY x.seq""",
            [('seq', None), ('item', '项目', dict(custom__width=130)),
             ('content', '内容', dict(custom__wrapText=True, links=link('打开', '${__data.fields.url:raw}'))), ('url', None)],
            description='时间范围只影响对比、执行历史、按原文拆开和明细，不影响基线。'), 10))

    layout.row('基线（取决于所选的基线版本，与右上角的时间范围无关）')
    layout.line(4, (stat('关键数值：${timing} 的整体基线', """SELECT m.name||CASE WHEN m.met THEN '' ELSE '（样本不足，仅供参考）' END AS name,m.value,m.unit,
  CASE WHEN m.met THEN 'text' ELSE '#8e8e8e' END AS color
FROM """ + STATISTIC + """) s CROSS JOIN LATERAL (VALUES
  (1,'样本数',s.included_count::numeric,'none',true),(2,'活跃天数',s.active_days::numeric,'none',true),
  (3,'P50',s.p50_ms,'ms',(s.sufficiency->'basic'->>'met')::boolean),(4,'P95',s.p95_ms,'ms',(s.sufficiency->'p95'->>'met')::boolean),
  (5,'P99',s.p99_ms,'ms',(s.sufficiency->'p99'->>'met')::boolean),(6,'最大',s.max_ms,'ms',(s.sufficiency->'basic'->>'met')::boolean)
) m(ord,name,value,unit,met) WHERE s.timing_type='${timing}' AND s.sample_state='available' ORDER BY m.ord""",
        description='P50 和最大跟随基础样本条件，P95、P99 各自跟随自己的样本条件。条件不满足时数值变灰并标明，仅供参考。没有数值表示所选版本里没有这一计时类别的基线。',
        color_mode='value',
        transformations=[dict(id='rowsToFields', options=dict(nameField='name', valueField='value',
            mappings=[dict(fieldName='unit', handlerKey='unit'), dict(fieldName='color', handlerKey='color')]))]), 24))
    layout.line(8,
        (table('三项样本条件', """SELECT mpp_view_label('condition',c.name) condition,
  CASE WHEN (c.detail->>'met')::boolean THEN '满足' ELSE '不满足' END met,
  (c.detail->>'actual_count')||' / '||(c.detail->>'required_count') samples,
  CASE c.detail->>'coverage_kind' WHEN 'none' THEN '不要求' ELSE (c.detail->>'actual_coverage')||' / '||(c.detail->>'required_coverage')
    ||CASE c.detail->>'coverage_kind' WHEN 'active_weeks' THEN ' 周' ELSE ' 天' END END coverage,
  CASE WHEN (c.detail->>'met')::boolean THEN '' ELSE (SELECT string_agg(mpp_view_label('reason',r),'、') FROM jsonb_array_elements_text(c.detail->'reasons') r) END reason
FROM """ + STATISTIC + """) s CROSS JOIN LATERAL (VALUES ('basic',s.sufficiency->'basic'),('p95',s.sufficiency->'p95'),('p99',s.sufficiency->'p99')) c(name,detail)
WHERE s.timing_type='${timing}'""",
            [('condition', '条件'), ('met', '是否满足', dict(custom__cellOptions=dict(type='color-text'),
                mappings=[dict(type='value', options={'满足': dict(color='green', index=0), '不满足': dict(color='orange', index=1)})])),
             ('samples', '样本数：实际 / 要求'), ('coverage', '活跃天数：实际 / 要求'), ('reason', '不满足的原因')],
            description='三项条件各自决定对应的数值能不能作参照：基础条件对应 P50 等，P95、P99 条件对应各自的分位值。'), 11),
        (dict(type='bargauge', title='耗时分位：${timing} 的整体基线', datasource=PG, description='所选版本整体基线保存的分位值。',
              targets=[target("""SELECT s.min_ms AS "最小",s.p25_ms AS "P25",s.p50_ms AS "P50",s.p75_ms AS "P75",s.p90_ms AS "P90",
  s.p95_ms AS "P95",s.p99_ms AS "P99",s.max_ms AS "最大" FROM """ + STATISTIC + """) s WHERE s.timing_type='${timing}' AND s.sample_state='available'""")],
              fieldConfig=dict(defaults=dict(unit='ms', min=0, color=dict(mode='fixed', fixedColor='blue'),
                                             thresholds=dict(mode='absolute', steps=[dict(color='blue', value=None)])), overrides=[]),
              options=dict(reduceOptions=dict(values=False, calcs=['lastNotNull'], fields=''), orientation='horizontal',
                           displayMode='basic', valueMode='color', showUnfilled=True, minVizHeight=14, minVizWidth=8,
                           namePlacement='left', sizing='auto', legend=dict(showLegend=False, displayMode='list', placement='bottom'))), 13))
    layout.line(6, (table('五类计时一览（整体基线；点一行切换计时类别）', """SELECT mpp_view_label('timing',s.timing_type)||CASE WHEN s.timing_type='request' THEN '' ELSE '（阶段或调用）' END timing,
  s.included_count,s.active_days,s.p50_ms,s.p95_ms,s.p99_ms,s.max_ms,
  CASE WHEN (s.sufficiency->'p99'->>'met')::boolean THEN '三项都满足' WHEN (s.sufficiency->'p95'->>'met')::boolean THEN 'P99 样本不足'
    WHEN (s.sufficiency->'basic'->>'met')::boolean THEN 'P95、P99 样本不足' ELSE '样本不足' END conditions,s.timing_type code
FROM """ + STATISTIC + """) s WHERE s.sample_state='available'
UNION ALL
SELECT '没有样本的类别：'||string_agg(mpp_view_label('timing',s.timing_type),'、' ORDER BY array_position(ARRAY['request','execute_first','execute_fetch','parse','bind'],s.timing_type)),
  NULL,NULL,NULL,NULL,NULL,NULL,NULL,'${timing}'
FROM """ + STATISTIC + """) s WHERE s.sample_state='no_samples' HAVING count(*)>0""",
            [('timing', '计时类别', dict(custom__width=520, custom__wrapText=True)), ('included_count', '样本数'), ('active_days', '活跃天数'),
             ('p50_ms', 'P50', dict(unit='ms')), ('p95_ms', 'P95', dict(unit='ms')), ('p99_ms', 'P99', dict(unit='ms')),
             ('max_ms', '最大', dict(unit='ms')), ('conditions', '样本条件'), ('code', None)],
            description='有样本的计时类别各一行；没有样本的类别合成最后一行文字。扩展协议的 SQL 没有“请求整体”，各阶段不相加。',
            links=link('切换到这一计时类别', same_page(timing='${__data.fields.code}'))), 24))
    layout.line(5, (table('这个集群已发布的版本', """SELECT CASE WHEN v.build_id='${version}' THEN '● 正在查看' ELSE '' END chosen,v.published_at,v.window_start,v.window_end,
  v.window_days,v.rules,v.status,v.build_id FROM mpp_view_versions('${norm}','${identity}') v""",
            [('chosen', '查看', dict(custom__width=100)), ('published_at', '发布时间'), ('window_start', '训练窗口开始'), ('window_end', '训练窗口结束'),
             ('window_days', '窗口天数', dict(custom__width=90)), ('rules', '规则版本'), ('status', '能否选作参照'), ('build_id', '版本标识')],
            description='默认查看当前生效的版本。已清理的版本和规则版本不同的版本列在这里，但不能在顶部选作参照。查看不改变当前生效版本，也不重算。'), 24))
    layout.line(8,
        (pattern('一天内各小时的规律：P50 和 P95', 'hour', "lpad(s.bucket_number::text,2,'0')||' 时'",
                 '训练窗口内按开始时间的小时归并，共 24 个分桶。来自所选版本，不随时间范围变化。'), 12),
        (pattern('训练窗口内每一天：P50 和 P95', 'day', "to_char(s.bucket_date,'MM-DD')",
                 '训练窗口内每个日期各一个分桶。来自所选版本，不随时间范围变化。'), 12))

    layout.row('基线明细（展开后查询：星期几和每周的规律、被排除的样本、全部指标和分层明细）', collapsed=True)
    layout.line(8,
        (pattern('星期几的规律：P50 和 P95', 'weekday', "(ARRAY['周一','周二','周三','周四','周五','周六','周日'])[s.bucket_number]",
                 '训练窗口内所有周一、所有周二……各归并成一个分桶。'), 8),
        (pattern('每周：P50 和 P95', 'week', "to_char(s.bucket_date,'MM-DD')||' 起'", '每个自然周（周一起）一个分桶。'), 8),
        (table('被排除的样本及原因：${timing} 的整体基线', """SELECT coalesce(mpp_view_label('reason',e.key),e.key) reason,e.value::bigint samples
FROM """ + STATISTIC + """) s CROSS JOIN LATERAL jsonb_each_text(s.exclusions_by_reason) e WHERE s.timing_type='${timing}'
UNION ALL SELECT '（没有被排除的样本）',0 FROM """ + STATISTIC + """) s WHERE s.timing_type='${timing}' AND s.exclusions_by_reason='{}'
ORDER BY 2 DESC""", [('reason', '排除原因'), ('samples', '样本数')]), 8))
    layout.line(11, (table('五类计时 × 全部指标（整体基线）', "SELECT m.label,\n" + ",\n".join(
        "  max(m.value) FILTER (WHERE s.timing_type='" + code + "') AS \"" + code + "\"" for _, code in TIMINGS) + """
FROM """ + STATISTIC + """) s CROSS JOIN LATERAL (VALUES
""" + ",\n".join("  (" + str(n) + ",'" + label + "',s." + column + "::numeric)" for n, (column, label) in enumerate(METRICS)) + """
) m(ord,label,value) GROUP BY m.ord,m.label ORDER BY m.ord""",
        [('label', '指标', dict(custom__width=180))] + [(code, label, dict(noValue='无样本')) for label, code in TIMINGS],
        description='耗时类指标的单位是毫秒；比值、变异系数和对数指标没有单位。没有样本的计时类别显示“无样本”。'), 24))
    layout.line(11, (table('分层明细：${timing}，${layer}（每个分桶一行）', "SELECT " + """CASE s.layer WHEN 'overall' THEN '整体' WHEN 'day' THEN to_char(s.bucket_date,'YYYY-MM-DD')
    WHEN 'week' THEN to_char(s.bucket_date,'YYYY-MM-DD')||' 起的一周' WHEN 'weekday' THEN (ARRAY['周一','周二','周三','周四','周五','周六','周日'])[s.bucket_number]
    ELSE lpad(s.bucket_number::text,2,'0')||' 时' END bucket,
  """ + CONDITION.format('basic') + " basic,\n  " + CONDITION.format('p95') + " p95,\n  " + CONDITION.format('p99') + " p99,\n  "
        + ",".join('s.' + column for column, _ in METRICS) + """
FROM """ + STATISTIC + """,'${layer}') s WHERE s.timing_type='${timing}' AND s.included_count+s.excluded_count>0
ORDER BY s.bucket_date,s.bucket_number""",
        [('bucket', '分桶', dict(custom__width=150)), ('basic', '基础条件'), ('p95', 'P95 条件'), ('p99', 'P99 条件')]
        + [(column, label, dict(unit='ms') if column.endswith('_ms') else {}) for column, label in METRICS],
        description='在顶部“分层明细”里选时间层次。每个分桶列出保存的全部 17 个指标、样本数、活跃天数、被排除数和它自己的三项样本条件。'), 24))

    layout.row('对比（所选时间范围内的执行，对所选版本的整体基线）')
    layout.line(5, (table('超过基线的执行：${timing}', """SELECT c.reference,c.baseline_ms,c.known_executions,c.above,c.above_share,c.expected_share,coalesce(c.note,'') note
FROM mpp_view_compare(""" + ARGS + ",'${timing}','${version}'," + RANGE + ",'${sqlid}') c",
        [('reference', '参照'), ('baseline_ms', '基线值', dict(unit='ms')), ('known_executions', '范围内有耗时的次数'),
         ('above', '超过基线值的次数'), ('above_share', '占比', dict(unit='percentunit', decimals=2)),
         ('expected_share', '正常时的大致占比', dict(unit='percentunit', decimals=0)), ('note', '说明', dict(custom__width=300))],
        description='如实比较，不下“异常”的结论。某项样本条件不满足时，对应的一行不计算，并写明“基线样本不足，不作参照”。'), 24))

    layout.row('执行历史（只看右上角所选的时间范围）')
    layout.line(4, (stat('范围内的记录：${timing}', """SELECT count(*) AS "合计",count(*) FILTER (WHERE o.outcome='success') AS "成功",
  count(*) FILTER (WHERE o.outcome='failed') AS "失败",count(*) FILTER (WHERE o.outcome='cancelled') AS "取消",
  count(*) FILTER (WHERE o.outcome='timed_out') AS "超时",count(*) FILTER (WHERE o.outcome='unknown') AS "结果未知"
FROM mpp_view_records(""" + RECORDS + ") o",
        description='计时类别是“请求整体”时，这是执行次数；是 Execute、Parse、Bind 时，这是阶段或调用的记录数，不等于执行次数。失败、取消、超时的记录没有计时类别，在每个类别下都计入。'), 24))
    main = chart('timeseries', '每次执行的耗时：${timing}（每格 $__interval；一格里画最慢和最快各一次）', """SELECT p.end_at AS time,
  CASE p.kind WHEN 'slowest' THEN p.duration_ms END AS "每格最慢的一次（一格只有一次时就是它）",
  CASE p.kind WHEN 'fastest' THEN p.duration_ms END AS "同一格里最快的一次"
FROM mpp_view_points(""" + ARGS + ",'${timing}'," + RANGE + ",$__interval_ms,'${sqlid}'," + NUMBER.format('${dmin}') + "," + NUMBER.format('${dmax}') + ") p ORDER BY 1",
        description='每个点都是一次真实的执行，画在它的结束时间上。点密时按图的宽度自动分格，每格只画最慢和最快各一次；在图上横向拖动可以放大，格子变小，直到每次执行都是单独的点；双击缩小。'
                    '水平虚线是所选版本整体基线的 P50、P95、P99，只在对应样本条件满足时画出。失败、取消、超时没有耗时，画成竖线标记。',
        interval='1ms')
    main['targets'].append(target("""SELECT t.at AS time,c.reference||' 基线' AS metric,c.baseline_ms AS value
FROM mpp_view_compare(""" + ARGS + ",'${timing}','${version}'," + RANGE + """) c
CROSS JOIN (VALUES ($__timeFrom()::timestamptz),($__timeTo()::timestamptz)) t(at)
WHERE c.condition_met ORDER BY 1,2""", ref='B', form='time_series'))
    main['fieldConfig']['defaults']['custom'] = dict(drawStyle='points', pointSize=4, showPoints='always', lineWidth=1,
                                                     axisLabel='耗时', scaleDistribution=dict(type='linear'), spanNulls=False)
    main['fieldConfig']['overrides'] = [
        dict(matcher=dict(id='byRegexp', options='/基线/'), properties=[
            dict(id='custom.drawStyle', value='line'), dict(id='custom.lineStyle', value=dict(fill='dash', dash=[8, 6])),
            dict(id='custom.showPoints', value='never'), dict(id='custom.lineWidth', value=1)]),
        dict(matcher=dict(id='byRegexp', options='/P50 基线/'), properties=[dict(id='color', value=dict(mode='fixed', fixedColor='green'))]),
        dict(matcher=dict(id='byRegexp', options='/P95 基线/'), properties=[dict(id='color', value=dict(mode='fixed', fixedColor='orange'))]),
        dict(matcher=dict(id='byRegexp', options='/P99 基线/'), properties=[dict(id='color', value=dict(mode='fixed', fixedColor='red'))]),
        dict(matcher=dict(id='byRegexp', options='/最慢/'), properties=[dict(id='color', value=dict(mode='fixed', fixedColor='blue'))]),
        dict(matcher=dict(id='byRegexp', options='/最快/'), properties=[dict(id='color', value=dict(mode='fixed', fixedColor='light-blue'))])]
    layout.line(11, (main, 24))
    main_id = main['id']
    counts = chart('timeseries', '记录数：${timing}（每格 $__interval，按状态堆叠）', """SELECT c.slot_at AS time,mpp_view_label('outcome',c.outcome) AS metric,c.record_count AS value
FROM mpp_view_counts(""" + ARGS + ",'${timing}'," + RANGE + ",$__interval_ms,'${sqlid}') c ORDER BY 1,2",
        description='与上图使用同一个步长。“耗时不低于／不高于”两个筛选不作用于这张图。', unit='none', form='time_series', interval='1ms')
    counts['fieldConfig']['defaults']['custom'] = dict(drawStyle='bars', fillOpacity=80, lineWidth=0, showPoints='never',
                                                       stacking=dict(mode='normal', group='A'), axisLabel='记录数')
    counts['fieldConfig']['overrides'] = [
        dict(matcher=dict(id='byRegexp', options='/' + name + '/'),
             properties=[dict(id='color', value=dict(mode='fixed', fixedColor=color))])
        for name, color in (('成功', 'green'), ('失败', 'red'), ('取消', 'orange'), ('超时', 'purple'), ('结果未知', 'gray'))]
    layout.line(6, (counts, 24))

    layout.row('按原文拆开（所选时间范围内，同一结构下取值不同的原文）')
    texts = layout.identify(table('范围内出现过的原文，按${text_order}排，取前 10 份（点一行只看这一份原文）', """SELECT t.rank,CASE WHEN t.search_hit THEN '命中' ELSE '' END hit,t.differing,t.executions,t.median_ms,t.slowest_ms,
  t.above_p95_share,t.range_texts,t.sql_id
FROM mpp_view_texts(""" + ARGS + ",'${timing}','${version}'," + RANGE + ",'${text_order}','${mode}',mpp_view_decode('${q}'),'${hit}') t",
        [('rank', '#', dict(custom__width=50)), ('hit', '检索', dict(custom__width=70, custom__cellOptions=dict(type='color-text'),
            mappings=[dict(type='value', options={'命中': dict(color='green', index=0)})])),
         ('differing', '取值不同的那一段', dict(custom__width=420)), ('executions', '执行次数'), ('median_ms', '中位耗时', dict(unit='ms')),
         ('slowest_ms', '最慢一次', dict(unit='ms')), ('above_p95_share', '高于基线 P95 的占比', dict(custom__width=170, unit='percentunit', decimals=1, noValue='基线样本不足，不作参照')),
         ('range_texts', None), ('sql_id', '原文标识')],
        description='从检索页带过来的输入命中的原文排在最前并标出。“取值不同的那一段”是比较所列原文的文本、去掉共同的开头和结尾后剩下的部分，是近似值。'
                    '只有同一份原文重复出现时拆开才有意义；参数化的 SQL 拆不出信息。点一行后，汇总、对比、两张图和明细只算这一份原文，基线仍是整个结构的。',
        links=link('只看这一份原文', same_page(sqlid='${__data.fields.sql_id}'))))
    layout.line(3, (total('所选时间范围内一共出现过多少份不同的原文（下表只显示前 10 份）', texts, 'range_texts'), 24))
    layout.line(9, (texts, 24))
    layout.row('按原文着色的点图（展开后查询）', collapsed=True)
    colored = chart('timeseries', '每格最慢的一次，按原文着色（前 10 份原文）', """SELECT p.end_at AS time,'#'||t.rank||' '||left(t.differing,40) AS metric,p.duration_ms AS value
FROM mpp_view_texts(""" + ARGS + ",'${timing}','${version}'," + RANGE + """,'${text_order}','${mode}',mpp_view_decode('${q}'),'${hit}') t
CROSS JOIN LATERAL mpp_view_points(""" + ARGS + ",'${timing}'," + RANGE + """,$__interval_ms,t.sql_id) p
WHERE p.kind='slowest' ORDER BY 1""", description='与原文表的前 10 份对应，每份原文一种颜色。点很密时不容易看清，可以先在原文表里点一行只看一份。',
        form='time_series', interval='1ms')
    colored['fieldConfig']['defaults']['custom'] = dict(drawStyle='points', pointSize=4, showPoints='always', axisLabel='耗时')
    layout.line(10, (colored, 24))
    layout.row('明细（所选时间范围内，每次执行一行）')
    listed = layout.identify(table('明细：${timing}，${list_order}，最多 200 条', """SELECT e.end_at,e.duration_ms,mpp_view_label('outcome',e.outcome) outcome,e.comparison,
  mpp_view_label('shape',e.request_shape) shape,e.sql_id,e.source_file,e.source_lines,e.training,e.matching
FROM mpp_view_executions(""" + ARGS + ",'${timing}','${version}'," + RANGE + ",'${sqlid}','${status:csv}'," + NUMBER.format('${dmin}') + "," + NUMBER.format('${dmax}') + ",'${list_order}',200) e",
        [('end_at', '结束时间', dict(custom__width=190)), ('duration_ms', '耗时', dict(unit='ms', noValue='未知')), ('outcome', '状态'),
         ('comparison', '与基线的比较'), ('shape', '单条或整批'),
         ('sql_id', '原文', dict(custom__width=300, links=link('只看这一份原文', same_page(sqlid='${__data.fields.sql_id}')))),
         ('source_file', '来源文件'), ('source_lines', '行号'), ('training', '训练判定（所选版本）', dict(custom__width=260)), ('matching', None)],
        description='默认最新的在前，可在顶部改为最慢的在前；两种排序都是先在整个时间范围内排好再取前 200 条。耗时未知的显示“未知”，不补零。'
                    '可按状态筛选；“耗时不低于／不高于”同时作用于这张表和上面的耗时图。'))
    layout.line(3, (total('所选时间范围内，符合状态和耗时筛选的一共有多少条（下表最多显示 200 条）', listed, 'matching'), 24))
    layout.line(13, (listed, 24))

    variables = [
        variable('query', 'norm', hide=2, skipUrlSync=True, sql='SELECT mpp_view_rules()'),
        variable('textbox', 'fp', '指纹'),
        variable('query', 'identity', '身份', sql="SELECT label AS __text, identity AS __value FROM mpp_view_identities('${norm}','${fp}')"),
        variable('query', 'timing', '计时类别', sql="SELECT label AS __text, timing AS __value FROM mpp_view_timings('${norm}','${fp}','${identity}')"),
        variable('query', 'version', '基线版本', sql="SELECT label AS __text, build_id AS __value FROM mpp_view_versions('${norm}','${identity}') WHERE selectable"),
        dict(variable('custom', 'status', '状态', pairs=[('成功', 'success'), ('失败', 'failed'), ('取消', 'cancelled'), ('超时', 'timed_out'), ('结果未知', 'unknown')]),
             multi=True, includeAll=True, allValue='', current=dict(text=['All'], value=['$__all'])),
        variable('textbox', 'dmin', '耗时不低于（毫秒）'), variable('textbox', 'dmax', '耗时不高于（毫秒）'),
        variable('custom', 'list_order', '明细排序', pairs=[('最新的在前', 'latest'), ('最慢的在前', 'slowest')]),
        variable('custom', 'text_order', '原文排序', pairs=[('执行次数', 'count'), ('中位耗时', 'median'), ('最慢一次', 'slowest')]),
        variable('custom', 'layer', '分层明细', pairs=[('整体', 'overall'), ('逐天', 'day'), ('逐周', 'week'), ('星期几', 'weekday'), ('各小时', 'hour')]),
        variable('textbox', 'sqlid', hide=2), variable('textbox', 'mode', hide=2), variable('textbox', 'q', hide=2),
        variable('textbox', 'hit', hide=2),
        variable('query', 'sql_text', hide=2, skipUrlSync=True, sql="""SELECT CASE WHEN length(t.sql_text)>200000 THEN left(t.sql_text,200000)||E'\\n-- （原文共 '||length(t.sql_text)||' 个字符，这里只显示前 200000 个）' ELSE t.sql_text END
FROM mpp_view_sql_text('${norm}','${fp}','${sqlid}') t
UNION ALL SELECT '-- 请在顶部“指纹”里粘贴一个结构指纹值，或从 SQL 检索、SQL 列表进入' WHERE NOT EXISTS (SELECT FROM mpp_view_sql_text('${norm}','${fp}','${sqlid}'))"""),
        variable('query', 'sql_note', hide=2, skipUrlSync=True, sql="""SELECT CASE WHEN t.selected THEN '所选的一份原文 '||t.sql_id ELSE '同一结构的一份示例（共 '||t.structure_texts||' 份原文）' END
FROM mpp_view_sql_text('${norm}','${fp}','${sqlid}') t UNION ALL SELECT '没有找到这个指纹' WHERE NOT EXISTS (SELECT FROM mpp_view_sql_text('${norm}','${fp}','${sqlid}'))""")]
    marks = dict(datasource=PG, enable=True, hide=False, name='失败、取消、超时', iconColor='red',
                 filter=dict(exclude=False, ids=[main_id]),
                 target=target("""SELECT min(o.end_at) AS time,mpp_view_label('outcome',o.outcome)||' '||count(*)||' 次' AS text,mpp_view_label('outcome',o.outcome) AS tags
FROM mpp_view_records(""" + RECORDS + """) o WHERE o.outcome IN ('failed','cancelled','timed_out')
GROUP BY o.outcome,floor(extract(epoch FROM o.end_at)*200/greatest(extract(epoch FROM ($__timeTo()::timestamptz-$__timeFrom()::timestamptz)),1))
ORDER BY 1""", ref='Anno'),
                 mappings=dict(time=dict(source='field', value='time'), text=dict(source='field', value='text'),
                               tags=dict(source='field', value='tags')))
    return dashboard('mpp-detail', 'SQL 详情', '一条 SQL 的原文、基线、与基线的对比、执行历史和按原文拆开',
                     layout.panels, variables, dict({'from': 'now-7d', 'to': 'now'}), annotations=[marks],
                     links=navigation('search', 'list'))


# --------------------------------------------------------------------------- SQL 检索
# The form's code is run through Grafana's variable substitution first, so it must
# not contain a dollar sign or doubled square brackets. Input text never travels in
# a variable as typed: words and passages go as base64url, complete SQL goes in the
# body of the request that Grafana's backend forwards to the fingerprint service.
FORM_COMMON = r"""
const element = (id) => context.panel.elements.find((item) => item.id === id);
const variables = {};
context.grafana.templateService.getVariables().forEach((item) => {
  variables[item.name] = item.current && typeof item.current.value === 'string' ? item.current.value : '';
});
const toToken = (bytes) => {
  let binary = '';
  for (let start = 0; start < bytes.length; start += 32768) {
    binary += String.fromCharCode.apply(null, bytes.subarray(start, start + 32768));
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
};
const fromToken = (token) => {
  const binary = atob(token.replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new TextDecoder().decode(bytes);
};
const remembered = () => {
  try {
    return JSON.parse(window.sessionStorage.getItem('sql-apm-search') || 'null');
  } catch (error) {
    return null;
  }
};
const remember = (state) => {
  try {
    window.sessionStorage.setItem('sql-apm-search', JSON.stringify(state));
  } catch (error) {
    // Without session storage only the refill after a complete-SQL search is lost.
  }
};
// Set the box and the switch directly: the plugin's patchFormValue rewrites every
// line break of a code element into a backslash and an n.
const fill = (text, mode) => {
  context.panel.onChangeElements(context.panel.elements.map((item) => {
    if (item.id === 'sql') {
      return Object.assign({}, item, { value: text });
    }
    if (item.id === 'mode') {
      return Object.assign({}, item, { value: mode });
    }
    return item;
  }));
};
const STATE = ['mode', 'q', 'qd', 'fp', 'xstate', 'xreason', 'xsql', 'xhints'];
const apply = (state) => {
  const query = {};
  STATE.forEach((name) => {
    query['var-' + name] = state[name] || '';
  });
  context.grafana.locationService.partial(query, true);
};
"""
FORM_INITIAL = FORM_COMMON + r"""
// Fill the box again: from the address when it carries a search, otherwise from
// the last search of this browser tab (the way back from the detail page).
const kept = remembered();
const inAddress = variables.q !== '' || variables.fp !== '' || variables.xstate !== '';
let text = '';
let mode = variables.mode || 'words';
if (inAddress) {
  if (variables.q !== '') {
    text = fromToken(variables.q);
  } else if (kept && kept.qd !== '' && kept.qd === variables.qd) {
    text = kept.text || '';
  }
} else if (kept && typeof kept.text === 'string' && kept.text !== '') {
  text = kept.text;
  mode = kept.mode || 'words';
  apply(kept);
}
fill(text, mode);
context.panel.enableSubmit();
"""
FORM_UPDATE = FORM_COMMON + r"""
// The editor keeps one line-break form for the whole text, and which one depends
// on the browser's platform. Line breaks are therefore always sent as LF.
const text = String(element('sql').value || '').replace(/\r\n/g, '\n');
const mode = String(element('mode').value || 'words');
const bytes = new TextEncoder().encode(text);
const state = { mode: mode, text: text, q: '', qd: '', fp: '', xstate: '', xreason: '', xsql: '', xhints: '' };
const finish = () => {
  remember(state);
  apply(state);
  context.panel.enableSubmit();
};
if (mode !== 'exact') {
  if (bytes.length > 262144) {
    context.grafana.notifyError(['输入太长', '按词和整段方式的输入不能超过 256 KB；更长的完整语句请用“完整 SQL”方式。']);
    context.panel.enableSubmit();
    return;
  }
  state.q = toToken(bytes);
  finish();
  return;
}
// Complete SQL: at most one byte over the 512 KB limit is sent, which is enough
// for the service to answer exactly like the command line does.
const body = JSON.stringify({ sql_b64: toToken(bytes.length > 524288 ? bytes.subarray(0, 524289) : bytes) });
state.qd = String(Date.now()) + '-' + String(bytes.length);
return context.grafana.backendService
  .post('/api/ds/query', {
    from: 'now-1m',
    to: 'now',
    queries: [{
      refId: 'A',
      datasource: { type: 'yesoreyeram-infinity-datasource', uid: 'sql-apm-fingerprint' },
      type: 'json', source: 'url', format: 'table', parser: 'backend', root_selector: '', columns: [],
      url: '/v1/exact',
      url_options: { method: 'POST', body_type: 'raw', body_content_type: 'application/json', data: body },
    }],
  }, { showErrorAlert: false })
  .then((response) => {
    const frame = response && response.results && response.results.A && response.results.A.frames && response.results.A.frames[0];
    const result = frame && frame.schema && frame.schema.meta && frame.schema.meta.custom && frame.schema.meta.custom.data;
    if (!result || typeof result.state !== 'string' || result.state === 'failed') {
      throw new Error('unexpected service answer');
    }
    state.fp = result.fingerprint || '';
    state.xstate = result.state;
    state.xreason = result.reason || '';
    state.xsql = result.exact_sql_id || '';
    if (Array.isArray(result.statement_hints) && result.statement_hints.length > 0) {
      const hints = result.statement_hints.filter((item) => item.fingerprint).map((item) => ({ statement: item.statement, fingerprint: item.fingerprint }));
      state.xhints = toToken(new TextEncoder().encode(JSON.stringify(hints)));
    }
    if (result.rules && result.rules.normalization_id !== variables.norm && variables.norm !== '') {
      context.grafana.notifyWarning(['规则不一致', '指纹服务使用的规则与库里当前版本的规则不同：产品升级后需要重启指纹服务，并发布一个新版本。']);
    }
    finish();
  })
  .catch(() => {
    state.xstate = 'service_unavailable';
    context.grafana.notifyError(['指纹服务不可用', '无法按完整 SQL 检索；按词和整段不受影响。请联系管理员检查指纹服务。']);
    finish();
  });
"""
FORM_RESET = FORM_COMMON + r"""
try {
  window.sessionStorage.removeItem('sql-apm-search');
} catch (error) {
  // nothing to forget
}
fill('', 'words');
apply({ mode: 'words' });
context.panel.enableSubmit();
"""
# value, the label on the switch (it states the meaning), and the fuller rule shown on the mark beside the switch
MODES = [('words', '按词：每个词都要出现', '只按空白把输入切成若干个词，引号是普通字符，最多 20 个词；每个词都要出现在原文里，位置和顺序不限。'),
         ('passage', '整段：整个输入连续出现', '不切，整个输入算一段，不受 20 个词的限制；这一段要在原文里连续出现。'),
         ('exact', '完整 SQL：结构相同即命中', '不切，把输入当作一个完整的请求来解析；结构指纹相同即命中，取值可以不同。')]
MODE_NOTE = ('按词和整段都不区分英文字母大小写，比较时忽略空白，符号按字面比较。单独的数字或很短的词会匹配到很多原文，需要精确时用整段。'
             '也可以直接粘贴一个结构指纹值。')
MODE_HELP = """先选方式，再点“检索”。不自动判断方式：同一段内容在不同方式下都可能有效，结果不同。

| 方式 | 输入怎么切 | 命中条件 |
| --- | --- | --- |
| **按词** | 只按空白切成若干个词，引号是普通字符；最多 20 个词 | 每个词都出现在原文里，位置和顺序不限 |
| **整段** | 不切，整个输入算一段；不受 20 个词的限制 | 这一段在原文里连续出现 |
| **完整 SQL** | 不切，当作一个完整的请求解析 | 结构指纹相同，取值可以不同 |

""" + MODE_NOTE + """

输入框只显示几行，内容多时在框里滚动，或点框左上角的图标放大。"""


def form():
    request = dict(method='-', contentType='application/json', getPayload='return {}', payload={}, header=[])
    return dict(type='volkovlabs-form-panel', pluginVersion='6.3.5', title='输入：粘贴一段 SQL，或输入关键词',
        description=MODE_HELP, datasource=PG, targets=[],
        options=dict(sync=False, updateEnabled='manual', elementValueChanged='',
            layout=dict(variant='single', orientation='vertical', padding=10, sectionVariant='default', sections=[]),
            elements=[
                dict(uid='sql', id='sql', title='', type='code', language='sql', height=EDITOR, value='', isEscaping=False,
                     labelWidth=None, width=None, tooltip='', section='', unit=''),
                dict(uid='mode', id='mode', title='方式', type='radio', value='words', optionsSource='Custom',
                     options=[dict(id=value, type='string', value=value, label=label) for value, label, _ in MODES],
                     labelWidth=10, width=None, section='', unit='',
                     tooltip=''.join(label.split('：')[0] + '：' + rule for _, label, rule in MODES) + MODE_NOTE)],
            initial=dict(request, code=FORM_INITIAL, highlight=False, highlightColor='red'),
            update=dict(request, code=FORM_UPDATE, confirm=False, payloadMode='all'),
            resetAction=dict(mode='custom', code=FORM_RESET, confirm=False, getPayload='return {}', payload={}),
            buttonGroup=dict(orientation='left', size='md'),
            submit=dict(variant='primary', text='检索', icon='search', foregroundColor='yellow', backgroundColor='purple'),
            reset=dict(variant='secondary', text='清空', icon='trash-alt', foregroundColor='yellow', backgroundColor='purple'),
            saveDefault=dict(variant='hidden', text='Save Default', icon='save'),
            confirmModal=dict(title='Confirm update request', body='Please confirm to update changed values',
                              columns=dict(include=['name', 'oldValue', 'newValue'], name='Label', oldValue='Old Value', newValue='New Value'),
                              confirm='Confirm', cancel='Cancel', elementDisplayMode='modified')))


SEARCH_FILTERS = (FILTER_ARGS + ","
                  "CASE WHEN '${timefilter}'='on' THEN $__timeFrom()::timestamptz END,CASE WHEN '${timefilter}'='on' THEN $__timeTo()::timestamptz END")
SEARCH_INPUT = "CASE WHEN '${mode}'='exact' THEN '${fp}' ELSE mpp_view_decode('${q}') END"


def search_dashboard():
    # Laid out for a 1920x1080 screen with the browser's own bars: the input, this search's note,
    # the total and the result list (21 grid lines) are all visible without scrolling the page.
    layout = Layout()
    note = table('这次检索', "SELECT n.item,n.content FROM mpp_view_search_note('${norm}',coalesce(nullif('${mode}',''),'words'),mpp_view_decode('${q}'),"
        "'${fp}','${xstate}','${xreason}','${xsql}'," + FILTER_ARGS + ") n\nWHERE '${q}${fp}${xstate}'<>'' ORDER BY n.seq",
        [('item', '项目', dict(custom__width=150)), ('content', '内容', dict(custom__cellOptions=dict(type='auto', wrapText=True)))],
        description='写明这次用的是哪种方式、输入是怎么切的，或者为什么没有检索。完整 SQL 方式写明四种结果中的哪一种。')
    results = layout.identify(table('结果：一行是一个 SQL 结构，最多 50 个（点一行进入 SQL 详情）', """SELECT r.total_structures,r.record_count,r.matched_texts,r.structure_texts,r.identities,r.top_label,r.top_records,
  array_to_string(r.scopes,'、') scopes,array_to_string(r.databases,'、') databases,array_to_string(r.execution_users,'、') users,r.last_at,
  left(regexp_replace(x.sql_text,'\\s+',' ','g'),160) example,r.fingerprint,
  """ + detail_url('r.fingerprint', 'r.top_identity', "r.top_last_at-interval '7 days'", "r.top_last_at+interval '1 millisecond'",
                   "||'&var-sqlid='||coalesce(r.only_sql_id,'')||'&var-mode=${mode}&var-q=${q}&var-hit=${xsql}'") + """ AS url,
  CASE WHEN h.cut THEN h.head||E'\\n……（这份原文共 '||length(x.sql_text)||' 个字符，这里只是开头；点这一行进入详情看全文）' ELSE h.head END example_more
FROM mpp_view_search('${norm}',coalesce(nullif('${mode}',''),'words'),""" + SEARCH_INPUT + "," + SEARCH_FILTERS + """,'${order}','${xsql}') r
LEFT JOIN LATERAL (SELECT t.sql_text FROM mpp_query_text(r.example_sql_id) t) x ON true
LEFT JOIN LATERAL (
  SELECT string_agg(l.line,E'\\n' ORDER BY l.n) FILTER (WHERE l.used<=""" + str(HOVER_LINES) + """ OR l.n=1) head,
         length(x.sql_text)>""" + str(HOVER_CHARS) + """ OR bool_or(l.used>""" + str(HOVER_LINES) + """ AND l.n>1) cut
  FROM (SELECT s.line,s.n,sum(1+(length(s.line)+(octet_length(s.line)-length(s.line))/2)/""" + str(HOVER_WRAP) + """) OVER (ORDER BY s.n) used
        FROM regexp_split_to_table(left(x.sql_text,""" + str(HOVER_CHARS) + """),E'\\r?\\n') WITH ORDINALITY s(line,n)) l) h ON true
WHERE '${q}${fp}'<>''""",
        [('total_structures', None), ('record_count', '记录数', dict(custom__width=80)),
         ('matched_texts', '命中原文数', dict(custom__width=95, noValue='按结构')),
         ('structure_texts', '结构的原文总数', dict(custom__width=115)), ('identities', '身份数', dict(custom__width=70)),
         ('top_label', '记录最多的身份（集群/库/用户）', dict(custom__width=250)), ('top_records', '该身份记录数', dict(custom__width=105)),
         ('scopes', '集群', dict(custom__width=75)), ('databases', '数据库', dict(custom__width=105)), ('users', '执行用户', dict(custom__width=125)),
         ('last_at', '最近一次', dict(custom__width=160)),
         ('example', '原文示例（开头；左上角小三角看更多）', dict(custom__minWidth=320, custom__tooltip__field='example_more', custom__tooltip__placement='left')),
         ('fingerprint', '结构指纹', dict(custom__width=170)), ('url', None), ('example_more', None, dict(custom__width=760, plain=True))],
        description='三种方式的结果都是同样的列表，检索后停在这里，不自动进入详情。“记录数”是命中原文的全部记录，包含各阶段的记录。'
                    '点一行进入 SQL 详情，显示记录最多的那个身份，时间范围是它最近一次执行往前 7 天；只命中一份原文时，进入后只看这一份。'
                    '“原文示例”只显示开头；把鼠标移到这一格左上角的小三角上，弹出这份原文的开头一段（保留换行，长的只显示到一屏以内），点一下小三角可以把它固定住，再点别处收起。看全文请点这一行进入详情。',
        links=link('进入 SQL 详情', '${__data.fields.url:raw}')))
    layout.beside(form(), 12, (total('命中的 SQL 结构总数', results, 'total_structures', '符合这次检索和顶部筛选的 SQL 结构一共有多少个；下表最多显示其中 50 个。'), 3),
                  (note, 5))
    layout.line(13, (results, 24))
    layout.line(7, (table('整批没有命中时的逐条提示（点一行改为查看这条语句）', """SELECT h.statement,h.state_label,h.record_count,h.fingerprint,
  '""" + SEARCH + """?var-mode=exact&var-qd=${qd}&var-xhints=${xhints}&var-xstate='||h.state||'&var-fp='||h.fingerprint AS url
FROM mpp_view_hints('${norm}','${xhints}',""" + FILTER_ARGS + ") h",
        [('statement', '第几条语句', dict(custom__width=110)), ('state_label', '这条语句单独查的结果'), ('record_count', '记录数'),
         ('fingerprint', '结构指纹'), ('url', None)],
        description='粘贴的是多条语句、整体没有命中时，这里列出每条语句单独查的结果（最多前 20 条）。逐条命中不等于整批命中。',
        links=link('查看这条语句', '${__data.fields.url:raw}')), 24))
    hidden = [variable('textbox', name, hide=2) for name in ('q', 'qd', 'fp', 'xstate', 'xreason', 'xsql', 'xhints')]
    variables = [variable('query', 'norm', hide=2, skipUrlSync=True, sql='SELECT mpp_view_rules()'),
                 variable('textbox', 'mode', hide=2, value='words')] + hidden + filters() + [
        variable('custom', 'timefilter', '时间', pairs=[('不限时间', 'off'), ('只看右上角所选的时间范围', 'on')]),
        variable('custom', 'order', '结果排序', pairs=[('记录数多的在前', 'count'), ('最近执行的在前', 'recent')])]
    return dashboard('mpp-search', 'SQL 检索', '粘贴一段 SQL 或输入关键词，找到 SQL 结构', layout.panels, variables,
                     dict({'from': 'now-7d', 'to': 'now'}), links=navigation('list'))


def build():
    return {'mpp-search.json': search_dashboard(), 'mpp-list.json': list_dashboard(), 'mpp-detail.json': detail_dashboard()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只核对已提交的文件与生成结果一致')
    args = parser.parse_args()
    stale = []
    for name, document in build().items():
        content = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
        path = TARGET / name
        if args.check:
            if not path.is_file() or path.read_text() != content:
                stale.append(name)
        else:
            path.write_text(content)
            print('WROTE: ' + str(path.relative_to(ROOT)))
    if stale:
        print('ERROR: regenerate with scripts/grafana/build_dashboards.py: ' + ', '.join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
