# 日志补充分析与样本门槛诊断

本工具链用于 [Issue #13](https://github.com/shenxg13/sql-apm/issues/13) 的离线观察。
它不执行 SQL、不筛选训练资格、不构建基线，也不修改正式规则。
[补充报告](../reports/cluster-log-supplement-2026-09-28.md)保存本轮结果。

## 输入与计数口径

- 使用明确指定的历史清单和本地关闭的 CSV 文件。新增清单沿用原条目，追加新文件；
  同时完整读取所有文件，核对 SHA-256、字节数、30 列、记录数和读取前后文件状态。
- 原文索引由 `mpp_full_scan` 新建，SQL 与索引均留在忽略目录。单条记录内相同文本
  的主 SQL、内联及 internal 字段只计一次；不同记录或不同文本分别计出现次数。
  全批次原文字节精确去重，不拆成子语句，不将日志出现次数称为执行次数。
- `--record-dates` 为可选新增参数：按 CSV 第一列的日期记录各输入的出现次数。
  默认扫描行为不采集日期；门槛诊断要求该选项生成的已完成索引。
  文件可能跨午夜，不能按文件数量或文件名计算活跃日期。
- 门槛按“集群＋方案指纹”独立聚合；活跃日取实际日志记录日期的并集。
  同日多个轮转文件、多个字段、多个原文仅贡献一个活跃日。日期没有减去耗时，
  不代表已确认业务契约的推算开始日期；也没有按数据库、执行用户、计时类别细分。
- 同时满足至少 7 个不同日期和至少 30／200／1,000 次出现才计为对应门槛组。
  主覆盖率分母是该集群全部非空输入的出现次数，包含拒绝／失败输入；另提供
  仅以成功生成相应方案指纹的出现次数为分母的辅助比例。分子均为达标组内出现次数。
  全空日期没有 SQL 输入，不能给任何组贡献活跃日。

## 对照方案

`threshold_coverage` 的命令输出格式名为 `threshold-coverage/1`。
正式上下文仍为 `sql-normalization/4`、`mpp-adapter/9` 和函数字典 1.0.1。

| `--schemes` 名称 | 相对现行 v4 的处理 | 结果性质 |
| --- | --- | --- |
| `v4` | 直接调用现行 Normalizer，保留其完整指纹与拒绝结果 | 当前可靠结构结果 |
| `positions` | 在 v4 结构上扩展 JOIN ON、SELECT 列表及 CASE 的条件／结果常量处理 | 候选结构分组 |
| `unqualified_functions` | 在 v4 结构上对未限定名称、普通位置参数调用的参数表达式归一；包括未知函数和字典原本保留的控制参数 | 激进候选，可能错误归并不同控制语义 |
| `positions_functions` | 同时应用上述位置和函数扩展，观察叠加收益 | 激进候选结构分组 |
| `tidb_lexical` | 独立 PG 词法对照，替换数字、字符串、前缀／美元引号字面量及原生参数；裸值 IN 列表统一为一个标记 | 仅词法观察，非可靠结构指纹 |

位置方案将三类位置作为一个组合，不宣称能够分别归因 JOIN、SELECT 或 CASE 的收益。
结构候选复用现行显式遍历边界：对象、Hint 锚点、MPP 扩展、SET、LIMIT／OFFSET、
NULL／布尔、未知节点和受保护类型转换保持；函数限定名、命名参数、variadic 和特殊
语法调用沿用产品策略。新增位置不增加 IN 粗分桶；v4 已有分桶保留。
候选均基于同一次 v4 结果，因此解析／归一化拒绝不会被候选结构方案修复。

TiDB 官方[语句摘要说明](https://docs.pingcap.com/tidb/v8.1/statement-summary-tables/)
描述了常量替换、格式／大小写处理和 IN 列表归并。本对照借用这一思路，复用仓库
PG 词法器；没有运行 TiDB，不保证与任何 TiDB 版本产生相同分组。
本实现保留引号标识符、Hint、运算符、括号、NULL／布尔和批次边界，普通注释忽略；
负号保留，带负号／表达式／显式转换的 IN 元素不进行裸值列表折叠。
所有词法字面量位置均归一，包括 SET、LIMIT、函数控制参数及过程体，可能产生错误合并。
词法边界不确定时拒绝；可分词也不证明语法有效。它可独立处理结构解析拒绝的输入，
因此覆盖率差异同时包含分组变化和可处理输入范围变化。

## 复现命令

在仓库根目录执行，Python 3.9.5、pglast 7.18 已准备。
以下路径是本轮实际使用的路径；重跑时选择新的输出／缓存路径，保留已有证据。

```bash
.venv/bin/python -m sql_apm.diagnostics.log_supplement manifest \
  --root raw/inbox/hashdata \
  --previous docs/reports/data/statement-census-2026-09-26.json \
  --output docs/reports/data/log-supplement-manifest-2026-09-28.json

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m sql_apm.diagnostics.mpp_full_scan \
  --evidence docs/reports/data/log-supplement-manifest-2026-09-28.json \
  --database var/parser-probe/issue13/full-scan.sqlite \
  --output var/parser-probe/issue13/full-scan.json --workers 8 --record-dates

.venv/bin/python -m sql_apm.diagnostics.statement_census \
  --root raw/inbox/hashdata/120 --output var/parser-probe/issue13/census-120
.venv/bin/python -m sql_apm.diagnostics.log_supplement census \
  --directory var/parser-probe/issue13/census-120 \
  --previous docs/reports/data/statement-census-2026-09-26.json \
  --evidence docs/reports/data/log-supplement-manifest-2026-09-28.json \
  --output docs/reports/data/log-supplement-census-2026-09-28.json
.venv/bin/python -m sql_apm.diagnostics.statement_census \
  --root raw/inbox/hashdata --output var/parser-probe/issue13/census-replay \
  --replay docs/reports/data/log-supplement-census-2026-09-28.json

.venv/bin/python -m sql_apm.diagnostics.log_supplement scan \
  --source var/parser-probe/issue13/full-scan.sqlite \
  --output docs/reports/data/log-supplement-dates-2026-09-28.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m sql_apm.diagnostics.threshold_coverage \
  --source var/parser-probe/issue13/full-scan.sqlite \
  --output var/parser-probe/issue13/coverage.json \
  --cache var/parser-probe/issue13/coverage.sqlite --workers 8
```

门槛命令默认执行表中五个方案；可用 `--schemes v4 positions` 选择子集。
输出为汇总 JSON；成功退出 0，诊断检查失败退出 1，命令行解析错误退出 2。诊断异常只输出固定错误码，
不输出 SQL、AST、参数值或任意异常文本。原索引只读，前后核对完整摘要，逐输入核对
精确原文字节摘要；代码摘要、版本、字典／规则引用和隔离限制随输出保存。
`--cache` 可省略，默认取输出路径的 `.sqlite` 后缀，但必须位于仓库忽略的 `var/` 中。
缓存使用新文件，输出聚合前完整核对各集群分母，全部完成后才发布 `complete: true`。

## 资源、失败与验证

复用 `ParserProcess`：最多 8 个隔离子进程，每个地址空间上限 512 MiB，单输入最大
512 KiB，任务超时 5 秒，每 1,000 次调用回收进程；禁用 core dump。五方案在同一任务内
共享一次正式归一化，5 秒限制覆盖整个任务。超时、内存限制、崩溃和内部错误按明确失败
计入分母，不转成成功词法结果。父进程流式读输入，使用磁盘 SQLite 聚合，未测峰值 RSS。

全量读取／计算用于回答全部日志的覆盖率和少见类别，抽样不能满足本次验收。
成本包括 CSV 重读、私有索引与缓存、各方案 AST 遍历和摘要；不涉及生产数据库。
清单和汇总写入使用独占新文件；中断的门槛缓存不可作为完成证据，选新路径重新运行。
全量采集未完成也须使用新索引；已完成采集后的解析可按现有 `--phase parse` 继续，
但必须保持代码、运行时和清单上下文一致。新日期汇总与门槛命令拒绝未完成／无日期索引。
历史工具默认清单与历史数据保留；新增诊断不重建旧临时脚本的源码／编码／协议阶段指标。

```bash
.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --pr PR --repo shenxg13/sql-apm
```

定向用例覆盖新旧清单、完整尾部、摘要变化、跨午夜日期、方案切换、实际产品 v4 等价、
候选保护边界、集群隔离、不同日期并集、29／30、199／200、999／1,000 临界数量、
六／七日条件、拒绝分母、隔离调用、输出不含原文及源索引不变。
