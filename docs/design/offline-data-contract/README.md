# 离线数据契约设计与样例导航

这是首期离线基线的逻辑设计交付。它定义后续模块共享的对象与数据含义，
不包含数据库 DDL、运行时校验器、导入／配对／指纹／统计引擎或服务界面。
所有样例均为人工合成，SQL、用户、来源、摘要占位及规则版本示例不代表生产对象。
业务要求以所属知识主题为准，设计实施与验收契约见 [Issue #3](https://github.com/shenxg13/sql-apm/issues/3)。

## 阅读与维护入口

| 文档 | 职责 |
| --- | --- |
| [逻辑契约](../../../.project-wiki/contracts/offline-data-contract.md) | C01–C11 关系、一致性、身份、统计、发布和演进约束 |
| [字段字典](fields.md) | 类型、必填／空值、枚举、引用、全部指标和状态 |
| [HashData 来源映射](hashdata-mapping.md) | 30 列来源、五类计时、关联证据、已观察／推导／未知边界 |
| [合成样例](examples.json) | 完整基础实例与 76 个有输入和预期的正反场景 |
| [设计验证记录](verification.md) | 实际检查证据及其限制，区别于后续产品验收 |

修改业务要求时先维护其主题与在线契约；仅设计细节变化时更新相应条款、字段和关联样例。
原文快照不改写，Issue 执行状态不在本目录维护副本。

## 需求到设计及样例对应

以下 A1–A9 按在线 Issue 验收顺序编号，便于引用；不复制完整 Issue 正文。

| 验收 | 需求依据 | 设计与字段 | 样例和核对方式 |
| --- | --- | --- | --- |
| A1 完整逻辑设计及映射 | [项目范围](../../../.project-wiki/decisions/project-scope.md#已确认的数据契约设计范围) | C01–C11、字段字典全部对象、本文对应表 | E01 完整实例；逐条引用走查 |
| A2 三个边界、来源与未知值 | [日志导入](../../../.project-wiki/features/log-ingestion.md)、[日志证据](../../../.project-wiki/contracts/log-evidence.md) | C01–C05；Source 至 Problem；M01–M04 | E01、E11–E21、E45、E58、E60；CSV 往返及人工追踪 |
| A3 单条／批次、五类与可靠配对 | [计时分组](../../../.project-wiki/contracts/timing-and-grouping.md)、[指纹](../../../.project-wiki/contracts/sql-fingerprints.md) | C04–C07；Occurrence、Fingerprint、Group | E02–E16、E63；N02–N05；阶段／请求计数单位核对 |
| A4 五层、指标及独立门槛 | [统计要求](../../../.project-wiki/contracts/baseline-statistics.md) | C08；Bucket、Statistic、ThresholdResult、coverage_index | E01、E20–E21、E28–E40、E59；N07、N11–N12；数值与时间核算 |
| A5 状态／资格／多重原因 | [训练资格](../../../.project-wiki/contracts/training-eligibility.md) | C05–C07；Decision、Problem 与规则评估 | E03–E04、E11–E27、E58、E61–E62；N01、N06；真实保留与不训练分开 |
| A6 导入一致性与发布结果 | [导入](../../../.project-wiki/features/log-ingestion.md)、[基线版本](../../../.project-wiki/features/baseline-versions.md) | C03、C09；Batch、ImportAttempt、Build、Publication、CurrentVersion、Task | E41–E55、E01；N07–N10、N13；保持旧版及五类零样本核对 |
| A7 版本与历史解释 | [存储](../../../.project-wiki/contracts/sql-storage.md)、[版本](../../../.project-wiki/features/baseline-versions.md)、[历史查看](../../../.project-wiki/features/sql-search-and-views.md) | C04、C06、C09–C11；Analysis、Normalization、快照及版本引用 | E02、E53–E54、E57；N08、N13；原文复用及历史规则引用 |
| A8 非数据库边界 | [多类型接入](../../../.project-wiki/decisions/project-scope.md#已确认的多类型系统接入扩展约束) | C10–C11；Source.scope_id 与独立 profile | E56；逐字段核对无伪造 SQL／数据库／用户 |
| A9 规则职责、公开样例和知识同步 | [函数字典](../../../rules/functions/README.md)、[类别黑名单](../../../.project-wiki/contracts/training-eligibility.md#首版类别边界与保守维护规则) | C06–C07、C11；Normalization、ConfigSnapshot | E03–E04、E54、E57、E61、E63；生成数据来源检查及文档链接检查 |

## 样例的读取规则

`examples.json` 的 `contract_version/profile/dataset_id` 为包头。
`base` 以对象类型、对象 ID 组织完整的逻辑实例；它是可读演示载体，
不强制数据库、RPC 或后续序列化采用该容器形式。
`raw_csv_fixture.rows` 是人工输入的全部 30 列；使用逗号、双引号、LF 和 UTF-8
编码可还原 F1 的 225 字节，校验值由这些合成字节得到。

基础路径为：S1 → F1 → I1／B1 → R1 → A1／O1 → Q1／N1／P1／G1 →
IN1／CFG1／V1 → D1 → ST1–ST5 → PUB1 → CL1 当前指针。
顺序不强制落库先后；循环引用（如 Build 与 Decision）表示同一逻辑集合，
交接完成时应可解析。SQL 字段内换行使一条 CSV 记录占两条物理行。

O1 结束为 09:00:00、耗时 10 ms，开始为 08:59:59.990，因此小时键是 **8**，
不能按结束小时写成 9。默认窗口 9 月 1 日至 30 日，逐周键有 5 个，
首周与尾周均为窗口裁剪；O1 所在 9 月 21 日周是完整周。
五层各自包含同一个样本，所以每层有效样本和为 1，而真实请求数仍为 1。
每层全部 17 个指标可算，但 basic／P95／P99 都不足；其余类别零样本也能统一发布。

base 中的解析器、关联和指纹版本以及字典摘要均明确标为 synthetic／example-opaque，
表示设计预期，不声称现有引擎生成了这些值。正式交接必须替换为实际可追溯版本和摘要。
`rule_evaluations` 表示各条不合格条件是否命中，所以正常样例为 not_matched，
不是把“成功”这个事实标为未命中。来源解析器与 SQL 解析器有不同职责及版本字段。

`cases` 是边界场景目录，不是一套可执行测试语言：

- `case_id/title/constraints/boundary` 标识案例、约束编号和本例核对范围。
- `input` 是该范围的明确事实／条件投影，`expected` 是预期数据或处理结果投影。
  E01 明确引用完整 base；其他案例不是对 base 的自动补丁，不能把旧的下游结果直接复用。
- 投影中的对象名（如 FX、RX、V0）只在该案例内有作用，结合给出的局部证据解释；
  它们不宣称组成可导入的完整对象包。接口必填和引用类型从字段字典核对，
  完整包引用检查针对 base，案例语义逐例人工追踪。
- `expected.contract=accept` 表示该边界的事实能被契约如实表达，**不表示可训练或可发布**。
  不完整 SQL、未知耗时及失败执行是合法问题输入。N 系列刻意违反约束，预期 reject，
  仍保留原始证据和诊断；E56 仅接受为扩展边界示例。
- 所有 reliable／success 是合成预设证据，不是产品解析算法的实测结论。
  例如 E05／E06 同次调用配对及 E17–E19 错误归属均须后续真实受控样本验证。
- expected 中的简写（如 `publication`、`decision`、`sample_count`）是说明字段，
  不是另立同名产品字段；对应正式结构分别为 Publication.result、Decision.state、
  Statistic.included_count，单位由该案例的计时类别决定。

## 六组样例走查清单

| 样例组 | 案例 | 人工核对重点 |
| --- | --- | --- |
| 身份与计时 | E01–E12、E60；N02–N05 | 原文复用与实际事件分开；六个来源分支形成五类；未配对不强分；两条配对证据只计一次调用 |
| 不完整信息 | E13–E21、E45、E58；N01、N04、N12 | 合法未知值与契约错误分开；解析失败不伪造执行失败；文件损坏与孤立问题分开 |
| 训练资格 | E03–E04、E17–E27、E58、E61–E63；N06 | 纯／混合批次、多重原因、正耗时端点、零点区间；未命中不绕过其他资格 |
| 时间与统计 | E01、E21、E28–E40、E59；N07、N11 | 开始归属、窗口边缘、17 指标、零分母、独立计数／覆盖；边缘周不足整周仍可达样本条件 |
| 导入与发布 | E41–E55；N07–N10、N13 | 去重证据、安全重试、未完成批次、五类合并发布、零样本、失败保持指针、忙时退出 |
| 历史与扩展 | E02、E53–E57、E63；N08、N10、N13 | 固定输入／规则、规则升级整窗归组、旧版可解释、非 SQL 独立身份 |

E34–E40 为覆盖分布摘要而非展开成数千条样本：数量和覆盖均为合成预设值，
每例要求五类分别应用同一门槛检查，不表示可以把不同类别样本合并。
E33 手算的 [0,10] ms 有 mean=5、总体标准差=5、MAD=5、IQR=5、P95=9.5、P99=9.9；
对数中位数与 MAD 均为 ln(11)/2，示范不能改用秒或先取原始中位数再对数转换。

## 可复核检查

首先检查 JSON 语法和仓库文档／流程：

```bash
.venv/bin/python -m json.tool docs/design/offline-data-contract/examples.json > /dev/null
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

下面是仅针对本静态合成样例的有限核算，可直接在仓库根目录执行。
它不读取生产日志，不解析 SQL，不验证所有契约字段，也不是产品运行时校验器。
其余语义按上表人工逐例核对。

```bash
.venv/bin/python - <<'PY'
import csv
import hashlib
import io
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

p = Path('docs/design/offline-data-contract/examples.json')
x = json.loads(p.read_text())
cases = {c['case_id']: c for c in x['cases']}
assert len(cases) == len(x['cases']) == 76
assert sum(c['expected']['contract'] == 'reject' for c in cases.values()) == 13
assert all(c['input'] and c['expected'] and c['constraints'] for c in cases.values())
assert all(set(c['constraints']) <= {'C%02d' % n for n in range(1, 12)} for c in cases.values())
rows = x['raw_csv_fixture']['rows']
s = io.StringIO(newline='')
csv.writer(s, lineterminator='\n').writerows(rows)
raw = s.getvalue().encode('utf-8')
assert all(len(row) == 30 for row in rows)
assert list(csv.reader(io.StringIO(s.getvalue(), newline=''))) == rows
f = x['base']['files']['F1']
assert len(raw) == f['byte_count'] == 225
assert hashlib.sha256(raw).hexdigest() == f['checksum']['value']
reader = csv.reader(io.StringIO(s.getvalue(), newline=''))
assert next(reader)[24] == x['base']['sql_texts']['Q1']['text']
assert reader.line_num == x['base']['evidence_records']['R1']['line_end'] == 2
for cid in ('E28', 'E29'):
    c = cases[cid]
    start = datetime.fromisoformat(c['input']['end_at']) - timedelta(milliseconds=int(c['input']['duration_ms']))
    e = c['expected']
    assert start == datetime.fromisoformat(e['estimated_start_at'])
    assert (str(start.date()), start.isoweekday(), start.hour) == (e['day'], e['weekday'], e['hour'])
    assert str((start - timedelta(days=start.weekday())).date()) == e['week']
for cid in ('E23', 'E24', 'E25', 'E26', 'E27'):
    c = cases[cid]
    a, b, lo, hi = [datetime.fromisoformat(c['input'][k]) for k in ('sample_start', 'sample_end', 'exclude_start', 'exclude_end')]
    hit = lo <= a < hi if a == b else a < hi and b > lo
    assert hit == c['expected']['interval_matches']
for n in range(34, 41):
    c = cases['E%02d' % n]
    i, t = c['input'], c['input']['thresholds']
    coverage = 0 if t['coverage_kind'] == 'none' else i[t['coverage_kind']]
    for name in ('basic', 'p95', 'p99'):
        assert c['expected']['met'][name] == (i['included_count'] >= t[name+'_count'] and coverage >= t['coverage_min'])
for c in (cases['E21'], cases['E31'], cases['E32'], cases['E33']):
    assert set(c['expected']['metrics']) == set(x['metric_names'])
m = cases['E33']['expected']['metrics']
assert float(m['p95_ms']['value']) == 9.5
assert float(m['p99_ms']['value']) == 9.9
assert math.isclose(float(m['log_median']['value']), math.log(11)/2)
assert math.isclose(float(m['log_mad']['value']), math.log(11)/2)
assert cases['E21']['expected']['metrics']['cv'] == {'value': None, 'reason': 'zero_denominator'}
assert all(v == {'value': None, 'reason': 'no_samples'} for v in cases['E31']['expected']['metrics'].values())
base = x['base']
assert base['decisions']['D1']['build_id'] in base['builds']
assert base['decisions']['D1']['group_id'] in base['groups']
assert base['occurrences']['O1']['sql_id'] in base['sql_texts']
assert base['input_snapshots']['IN1']['selection']['refs'] == [{'analysis_id': 'A1', 'occurrence_id': 'O1'}]
for layer in ('overall', 'day', 'week', 'weekday', 'hour'):
    selected = [v for v in base['statistics'].values() if v['bucket']['layer'] == layer]
    assert sum(v['included_count'] for v in selected) == 1
    for v in selected:
        assert set(v['metrics']) == set(x['metric_names'])
        assert v['build_id'] in base['builds'] and v['group_id'] in base['groups']
for c in base['builds']['V1']['coverage_index']:
    assert not set(c['computed_keys']) & set(c['empty_keys'])
    assert len(c['computed_keys']) + len(c['empty_keys']) == {'overall': 1, 'day': 30, 'week': 5, 'weekday': 7, 'hour': 24}[c['layer']]
assert base['current_versions']['CL1']['build_id'] == base['publications']['PUB1']['build_id']
print('76 cases / 13 negative cases; synthetic CSV, time, thresholds, numeric examples and bounded references passed')
PY
```

## 演进与后续实现交接

- 字段类型、必填、单位和枚举变化遵循 C11；更新时保留旧版的可解释依据。
- 解析和关联实现必须补充真实受控样本，核实 profile、状态判定、跨文件边界和去重，
  不能以本页人工预设的可靠性作为算法证明。
- 物理存储可以选择按需重建派生关系，不要求每个样本在每版都复制一行 Decision；
  但固定原事实、解释、规则和输入后，必须能确定性复现当时的资格与分组，旧统计不被覆盖。
- 资源说明为 qualitative：本次只交付小型文档和合成数据；没有新增后台任务、
  数据库锁或生产扫描。实际行数、容量、吞吐、互斥／恢复和数值容差由后续实现验证。
- 自动异常判定、留存期限、其他来源适配器与前端接口继续遵守既定范围，不从字段名推导新承诺。
