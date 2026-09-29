# 完整日志导入验证（2026-09-29）

本报告是 [Issue #18](https://github.com/shenxg13/sql-apm/issues/18) 的实施方验收证据。
全部 55 个文件已在私有临时 PostgreSQL 17 入库；两集群重复导入均新增 0 条证据和事件。
不代替独立评审、生产部署或后续训练／统计／发布验收。

## 实现与复现

- 产品入口为 `python -m sql_apm import`；JSON 来源与冻结批次、恢复方式、完整命令见
  [操作说明](../runbooks/log-ingestion.md)，事务和冲突检测边界见[设计](../design/log-ingestion.md)。
- Python 3.9.5、PostgreSQL 17.10；pglast 7.18、psycopg2-binary 2.9.10 使用根锁定依赖。
  结构 1.2.0 不变；归一化为 `sql-normalization/5`、`mpp-adapter/9`、字典 1.0.1，近似为 `sql-approximate/2`。
- 输入为既有[55 文件清单](data/log-supplement-manifest-2026-09-28.json)，每文件完整读取、核验 SHA-256／字节数。
  共 15,816,193,813 字节；来源登记为本地 Master 完整拷贝。
- [全量 JSON](data/log-ingestion-full-2026-09-29.json)记录来源摘要、代码摘要、每文件计数与两次运行结果；
  [对账 JSON](data/log-ingestion-reconciliation-2026-09-29.json)记录最终适配器重读和历史集合差异；
  [测试／版本证据](data/log-ingestion-checks-2026-09-29.json)记录测试数量、产品代码摘要和历史索引摘要。
- 全量测量进程从 `f67fbaffe5d6ee44b0035af9cc0392bae57c1af6` 启动。
  随后的 `a054fa1` 保留重复文件的新人工标识，并把 HashData 写入映射移入来源适配目录；
  搬迁函数的 AST 完全相同。`e07381b` 修复连续迁移同时间戳的版本识别；本全量使用全新 1.2.0。
  最终适配器另行重读全部 55 文件核对计数，当前代码通过 49 项数据库测试。
  这些后续验证与原始资源测量分别保留，不声称在后续提交重新完成一次全量入库。

实际全量命令：

```bash
.venv/bin/python scripts/db/verify_ingestion_full.py \
  --root raw/inbox/hashdata \
  --manifest docs/reports/data/log-supplement-manifest-2026-09-28.json \
  --index var/parser-probe/issue13/full-scan.sqlite \
  --output var/ingestion/acceptance
.venv/bin/python scripts/db/reconcile_ingestion.py \
  --root raw/inbox/hashdata \
  --manifest docs/reports/data/log-supplement-manifest-2026-09-28.json \
  --report var/ingestion/acceptance/report.json \
  --audit var/ingestion/acceptance/identity-audit.sqlite \
  --index var/parser-probe/issue13/full-scan.sqlite \
  --snapshot var/parser-probe/issue15/v5.sqlite \
  --output var/ingestion/acceptance/reconciliation.json
```

实例由验证脚本通过本地 Unix socket 创建、停止并删除；生产日志、索引和本地配置均在忽略目录。
命令及提交证据只输出汇总、原因码、摘要和不透明定位，不输出生产 SQL、数据库名或用户名。

## 全量计数与历史差异（measured）

| 项目 | 本次 | 已知参照／解释 |
| --- | ---: | --- |
| 文件 | 55 | 119 为 30，120 为 25；全部成功 |
| CSV 证据记录 | 16,390,146 | 与 16,390,146 完全一致 |
| 带独立 SQL 的 duration | 7,454,611 | 与 7,454,611 完全一致 |
| 无独立 SQL 的 duration | 63,355 | 与 63,355 完全一致 |
| 执行／调用 | 7,424,804 | 五类／未知 duration 调用加带 SQL ERROR；真实重复逐条保留 |
| 正式持久化不同原文（含片段） | 1,396,293 | 历史 1,497,418 减去 101,125 个非导入候选 |
| 可靠完整原文／v5 结果 | 1,385,776 | 历史 1,486,516 减去 100,740 个非导入候选 |
| 片段／结构拒绝原文 | 10,517 | 历史 unsupported_syntax 10,902 减去 385 个非导入候选 |
| 问题记录 | 517,900 | 同一事件可有多个问题，不能作为执行数 |
| 再次导入新增证据／事件 | 0／0 | 两批次均 complete，55 文件全部 duplicate_skipped |

历史索引还收集非事件第 24 列、消息内联文本和内部查询；产品按 duration／带 SQL ERROR 形成候选。
对账按原始字节摘要逐个核验，额外未知原文为 0；
应导入却未保存的原文为 0；已保存原文与 v5 快照的状态／拒绝原因变化为 0。
未纳入正式 SQL／近似表的历史原文仍可通过原文件和已保存的 CSV 证据定位，不推定它们是执行。

| 历史原文未纳入原因组合（不同原文） | 数量 |
| --- | ---: |
| `inline_diagnostic_only` | 101,058 |
| `non_event_primary_sql` | 67 |

`inline_diagnostic_only` 为消息内联文本，`internal_diagnostic_only` 为内部 SQL，
`non_event_primary_sql` 为只出现在非导入候选记录中的独立 SQL。组合表示同一原文曾出现在多个位置。
最终适配器重读 16,390,146 条记录，55 个文件的记录数、duration 来源、计时和状态计数均与原全量一致。

总事件对账：7,517,966 条 duration − 150,601 条范围外 + 57,439 条带 SQL ERROR = 7,424,804。
未知计时 57,685 = 57,439 条无计时 ERROR + 246 次未配对 Execute。

## 计时、配对与状态（measured）

| 计时类别 | 119 | 120 | 合计 |
| --- | ---: | ---: | ---: |
| `request` | 819,577 | 2,343,418 | 3,162,995 |
| `execute_first` | 657,727 | 776,538 | 1,434,265 |
| `execute_fetch` | 13,737 | 318 | 14,055 |
| `parse` | 654,523 | 615,768 | 1,270,291 |
| `bind` | 681,297 | 804,216 | 1,485,513 |
| `unknown` | 21,974 | 35,711 | 57,685 |

unknown 包括未配对 Execute 和无计时依据的 ERROR；失败记录的 duration、推算开始均为 NULL。
Execute 配对单独统计如下，不把失败请求算作未知 Execute：

| Execute 配对 | 119 | 120 | 合计 |
| --- | ---: | ---: | ---: |
| `execute_first` | 657,727 | 776,538 | 1,434,265 |
| `execute_fetch` | 13,737 | 318 | 14,055 |
| `unknown` | 1 | 245 | 246 |

| outcome | 119 | 120 | 合计 |
| --- | ---: | ---: | ---: |
| `success` | 2,826,862 | 4,540,503 | 7,367,365 |
| `failed` | 21,893 | 35,392 | 57,285 |
| `cancelled` | 36 | 54 | 90 |
| `timed_out` | 44 | 20 | 64 |
| `unknown` | 0 | 0 | 0 |

独立重读确认 57014 共 155 条：用户取消 91、超时 64。其中 119 的 1 条用户取消没有独立 SQL，
按契约保留问题而不构造事件，故正式 cancelled 为 90，timed_out 为 64。
需求阶段表中的 119／120 两个 ERROR 数 21,893／35,392 在本次对应带 SQL 的非 57014 ERROR；
再加 154 条带 SQL 的 57014，总失败／取消／超时事件为 57,439。另有 4 条其他 ERROR 无 SQL。
各分类证据见对账 JSON 的 error_evidence；没有把历史临时诊断的粗分口径当作正式入库分母。

| duration 来源（含范围外） | 数量 |
| --- | ---: |
| `autostats.c:334` | 98,057 |
| `postgres.c:1455` | 52,544 |
| `postgres.c:1946` | 3,162,995 |
| `postgres.c:2219` | 1,270,158 |
| `postgres.c:2224` | 133 |
| `postgres.c:2603` | 1,485,428 |
| `postgres.c:2608` | 85 |
| `postgres.c:2843` | 1,448,566 |

`1455` 和 `autostats.c:334` 只保留证据／问题，不构造执行。Parse／Bind 独立于后续阶段结果。
未配对合计 246 次（缺前置 33、上下文不符 213），保留 duration 与成功 outcome、计时类别为 NULL。
这不同于早期 46 文件的候选配对统计：本次使用 55 文件和独立 SQL／内联内容等正式校验。
原因及有界文件／记录定位见全量 JSON 的 association、unpaired_examples；不采用最近时间猜测配对。

## 可靠与近似结果、问题（measured）

可靠原文进入 SqlText／Fingerprint；边界不确定、残缺及编码异常文本保留在证据和独立近似路径。
词法闭合但结构拒绝仍标为 uncertain；完整性不由“解析器拒绝”推定。SQL 占位符原样保存。

| 事件 SQL 状态 | 数量 |
| --- | ---: |
| `complete` | 7,269,390 |
| `incomplete` | 897 |
| `invalid_encoding` | 20 |
| `missing` | 63,355 |
| `uncertain` | 91,142 |

| 近似状态：近似原因：结构拒绝原因（不同结果） | 数量 |
| --- | ---: |
| `available:available:base_parser_rejected` | 1,459 |
| `available:available:invalid_encoding` | 221 |
| `available:available:lexical_ambiguous_string_escape` | 282 |
| `available:available:lexical_unbalanced_bracket` | 5,911 |
| `available:available:lexical_unclosed_comment` | 93 |
| `available:available:lexical_unclosed_string` | 2,270 |
| `available:available:scanner_rejected` | 47 |
| `available:available:unsupported_distribution_statement` | 2 |
| `unavailable:no_sql_tokens:base_parser_rejected` | 7 |
| `unavailable:no_sql_tokens:lexical_unclosed_comment` | 37 |
| `unavailable:no_sql_tokens:scanner_rejected` | 1 |
| `unavailable:no_sql_tokens:sql_missing_or_empty` | 187 |

available 与 unavailable 均保留原字节、规则与拒绝原因；近似结果不进入可靠结构、分组或训练。
“结构拒绝”不改变执行 outcome；多语句只部分可解析时不摘取子语句或分配耗时。

| 问题码 | 数量 |
| --- | ---: |
| `duration_ms_unknown` | 57,439 |
| `error_without_execution` | 83,291 |
| `estimated_start_at_unknown` | 57,439 |
| `execute_context_mismatch` | 213 |
| `execute_start_missing` | 33 |
| `fingerprint_base_parser_rejected` | 1,809 |
| `fingerprint_invalid_encoding` | 486 |
| `fingerprint_lexical_ambiguous_string_escape` | 1,934 |
| `fingerprint_lexical_unbalanced_bracket` | 8,350 |
| `fingerprint_lexical_unclosed_comment` | 305 |
| `fingerprint_lexical_unclosed_string` | 2,902 |
| `fingerprint_scanner_rejected` | 50 |
| `fingerprint_sql_missing_or_empty` | 88,524 |
| `fingerprint_unsupported_distribution_statement` | 2 |
| `record_encoding_invalid` | 1,167 |
| `sql_missing` | 63,355 |
| `timing_out_of_scope` | 150,601 |

## 资源实测（measured）

| 项目 | 本机观测 |
| --- | ---: |
| 首次完整导入 | 3515.239 秒（58.59 分钟） |
| 包括查询对账和重复导入的验收命令 | 3547.418 秒 |
| 导入器及解析进程树 RSS 采样峰值 | 485,764 KiB（0.46 GiB） |
| PostgreSQL 进程树 RSS 采样峰值 | 893,880 KiB（0.85 GiB） |
| 导入前数据库 | 11,138,739 字节 |
| 首次导入后数据库 | 37,433,038,515 字节 |
| 数据库增长 | 37,421,899,776 字节（34.85 GiB） |

归一化使用 4 个子进程，每个 512 MiB 地址空间、单条 5 秒外部 watchdog，批量 COPY 和 64 MiB 热缓存。
RSS 每 0.5 秒求进程树总和，采样覆盖整个验收命令（含入库后查询／对账和重复运行），
并非仅导入阶段的独立峰值；共享页可能重复计算，也可能漏过瞬时峰值，不等于 PSS。
数据库大小采用 pg_database_size，包含索引、TOAST，不包含 WAL、备份和临时实例目录的全部占用；
各表含索引大小见 JSON relation_bytes。上述结果不外推生产容量或作为性能门槛。
文件原子事务中断时重放该文件，SQL 元数据可以独立残留；没有成功文件事务就没有可重复计数的事件引用。

## D1–D9 验收映射与验证

| 验收 | 实现与证据 |
| --- | --- |
| D1 | config.py 的登记／映射校验，importer.py 的冻结清单、文件最终状态核对；缺文件修复、清单缩短拒绝、同内容别名用例 |
| D2 | 同源完整内容幂等、改名跳过、COPY 后故障与真实 SIGKILL、重试恢复；人工 origin 变化、边缘重叠、损坏 CSV 失败用例 |
| D3 | reader.py 与合成 CSV 覆盖五类、首次／续取／未知、范围外、零值、跨北京时间日期、独立原文／会话核对及真实重复 |
| D4 | ERROR／两种 57014／无 SQL FATAL、阶段后取消、拒绝解析而执行成功，数据库验证 NULL 耗时与开始时间 |
| D5 | storage/ingestion.py 的完整内容精确去重、占位符原样、同结构不同原文、规则版本、整批拒绝、近似 available／unavailable 隔离及非法字节往返 |
| D6 | 本报告完整 55 文件、全部计数与历史逐项解释、计时／状态／近似／问题分布、第二遍新增为 0 |
| D7 | 本报告资源测量方法、实际值和局限；私有实例、本地忽略目录和脱敏汇总 |
| D8 | 训练成功、失败明细与日志证据契约、M04、设计、命令、运行时驱动锁、知识日志同步；原始需求与 rules 未改 |
| D9 | 下列普通、解析专项、数据库、迁移、Harness 与在线 PR 检查 |

- `.venv/bin/python -m unittest discover -s tests -v`：57 项通过；宿主 Python 标准库环境同样通过。
- `PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v`：180 项通过。
- `.venv/bin/python scripts/db/verify_ingestion.py`：49 项通过，含实际进程死亡恢复和真实 1.0.0→1.1.0→1.2.0 连续迁移后导入。
- `.venv/bin/python scripts/db/verify.py`：86 项结构、56 项 FK 触发器、63 项迁移和 18 项直接近似迁移检查通过。
- 最终完整 Harness、在线 PR 契约及远程 CI 的提交绑定结果记录在 PR #20 的 R0 交接评论；本报告不复制执行状态。

## 已知边界

Sync 才暴露的失败和跨文件会话不由当前 duration 成功证据覆盖；这是已确认规则的局限。
Execute 仅单文件、单次调用配对，不还原完整 portal；首尾连续记录检测不覆盖任意中间重叠。
仍需人工确认来源与文件齐全。Kylin 安装、生产实例、真实 activity 精度、留存／清理、训练、统计和发布未在本轮验证。
