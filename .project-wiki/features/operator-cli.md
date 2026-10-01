---
id: feature.operator-cli
type: feature
status: active
owners:
  - .project-wiki/features/operator-cli.md
updated: 2026-10-01
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/29
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/27
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/25
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/21
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/18
    status: current
  - path: .project-wiki/log.md
    status: historical
  - path: docs/reports/knowledge-reorganization-2026-09-25.md
    status: current
related:
  - feature.log-ingestion
  - feature.baseline-versions
  - contract.training-eligibility
  - feature.sql-search-and-views
confidence: high
---

# 命令行与本地配置操作

## Summary

完整流程、只导入和重新构建是已确认用法，配置由本地文件维护。
设计导入、构建、状态或诊断命令及本地配置时阅读。

## Source Of Truth

以下保留原知识索引各节的用户确认、日期、来源状态、证据限制和待定事项。
这些条款定义目标或事实，不表示相关产品功能已经实现；较早条款中的待定表述
应结合明确链接的后续确认阅读，不能覆盖后续已确认规则。
[知识变更记录](../log.md)用于追溯；涉及 Issue 时以其在线正文和评论核对任务契约。

## Contracts

### 已确认的首期操作入口

- 确认日期：2026-09-25。
- 来源：用户对日志导入、基线构建、任务状态及诊断结果查询统一提供命令行入口，
  来源映射、批次文件清单、黑名单及排除时段使用本地配置文件维护，Grafana
  继续承担既有 SQL 检索与展示范围的建议，选择“命令行与本地配置文件”。
- 来源状态：current；#18 已交付导入命令及 JSON 配置，②训练快照／判定和③统计命令已交付；#29 实现完整编排、发布与版本查询，界面仍待后续实现。
- 首期为日志导入、基线构建、任务状态和诊断结果查询提供命令行入口。
  日志来源映射、导入批次文件清单、黑名单和故障／维护排除时段通过本地配置
  文件维护；具体文件格式、字段及目录在实施时确定。
- 命令行及配置处理沿用既有来源登记、完整文件、批次完整性、同集群串行、
  安全重试、固定构建输入及发布检查规则；选择命令行不改变这些业务约束。
  构建仍记录实际使用的配置依据，配置变更按已确认规则用于后续构建。
- Grafana 继续承担 SQL 检索、基线与执行历史展示；不增加导入、构建、黑名单
  和排除时段的网页管理要求。任务管理 API 不作为本次确认的首期必要入口；
  SQL 检索服务接口与 Grafana 具体插件仍按原范围另行选型。
- 命令、参数、输出格式及配置关联见本页各实施节和对应操作说明。
  完整流程与分步使用的业务范围已按下述规则确认；本次不创建可执行命令、
  实际配置项或定时任务，后续 SCP 及其触发方式仍按既定阶段另行实施。

关联条款：[已确认的集群标识与日志来源映射](log-ingestion.md#已确认的集群标识与日志来源映射)；[首期导入批次的完整性判定](log-ingestion.md#首期导入批次的完整性判定)；[已确认的同集群任务串行与忙时处理](baseline-versions.md#已确认的同集群任务串行与忙时处理)；[已确认的构建输入与配置固定](baseline-versions.md#已确认的构建输入与配置固定)；[已确认的首期统一入口展示范围](sql-search-and-views.md#已确认的首期统一入口展示范围)。

### 已确认的完整流程与分步使用

- 确认日期：2026-09-25。
- 来源：用户对提供一次执行导入、构建、检查及满足条件后生效的完整流程命令，
  同时保留“只导入”和“复用已完成导入数据重新构建”两种用法的建议回复“确认”。
- 来源状态：current；已确认流程职责；#18 已交付只导入，完整阶段编排尚未实现。
- 完整流程：用户确认批次文件齐全后，显式执行一次命令，依次完成导入、基线
  构建和检查；满足既有发布条件时自动生效。进入构建前，清单中所有文件仍须
  导入成功或符合既有同来源、相同内容且此前已成功导入的跳过规则。
- 只导入：执行日志导入，完成后不构建或切换基线；批次及文件状态仍按既有
  规则记录，失败或未完成的导入不因此成为可用构建输入。
- 重新构建：复用已完整成功导入的数据，为选定训练窗口重新计算基线，通过
  检查且满足发布条件后生效。每次固定实际输入及配置；计算失败后重试仍从头
  计算选定窗口，无需仅因计算失败而重复导入 CSV。
- 同集群串行规则覆盖完整流程及各分步用法，不能利用阶段切换绕过任务互斥。
  某阶段失败时停止后续步骤，保留当前生效版本及诊断记录；可可靠隔离的记录级
  问题仍按既有规则处理，不自动升级为整个导入阶段失败。
- 样本不足不单独阻止生效；整窗五类计时均无有效样本时仍保留当前版本，不能
  因命令流程完成而覆盖原有发布限制。
- 这里的自动生效属于用户显式发起的本次命令流程，不新增定时调度、自动重试
  或排队要求。具体命令、参数及阶段状态见[操作说明](../../docs/runbooks/build-publication.md)。

关联条款：[首期导入批次的完整性判定](log-ingestion.md#首期导入批次的完整性判定)；[已确认的同集群任务串行与忙时处理](baseline-versions.md#已确认的同集群任务串行与忙时处理)；[已确认的基线计算失败重试范围](baseline-versions.md#已确认的基线计算失败重试范围)；[已确认的五类计时统一版本与发布边界](baseline-versions.md#已确认的五类计时统一版本与发布边界)。

### 已实现的“只导入”命令

[Issue #18](https://github.com/shenxg13/sql-apm/issues/18) 提供 `python -m sql_apm import`，
JSON 本地配置、显式 `--source`／`--batch` 和计数／原因码输出。
参数、身份冻结、文件冲突和重试操作见[导入操作说明](../../docs/runbooks/log-ingestion.md)。
此命令只执行导入；完整流程、重新构建及发布由 #29 的独立命令提供。

### 已实现的训练快照与诊断入口（2026-09-29）

[Issue #21](https://github.com/shenxg13/sql-apm/issues/21)落实②的本地 JSON 配置与
`python -m sql_apm training snapshot`／`training summary`。模板示例、可选身份限定、
北京时间半开时段及窗口参数的格式、固定快照 ID 和脱敏输出见
[操作说明](../../docs/runbooks/training-decisions.md)。
此入口固定输入／配置、复用原文缓存及汇总数据库按需判定；统计由③的 statistics 命令交付；完整构建、重新构建和发布
由 #29 的④命令实现。用户维护具体模板条目，初始为空；本次不提供类别增删入口。

## 已实现的统计命令

Issue #25 提供 `python -m sql_apm statistics --cluster ... --input ... --config-id ...`，
对②已封存快照创建构建并保存五层结果；输出仅含计数、固定原因和不透明标识。
可引用同快照失败尝试的 `--retry-of`，从头计算；这不交付④的完整重建流程。
参数、恢复和退出码见[操作说明](../../docs/runbooks/baseline-statistics.md)。

Issue #27 将独立观察统计纳入同一命令和事务；输出增加 `observations` 计数及原因汇总，
参数保持不变。观察结果不带充足性结论，失败与正式结果一起回滚；
[观察操作说明](../../docs/runbooks/baseline-statistics.md#观察统计与全量验收)解释归属及规则间计数边界。

### 完整流程与版本查询（2026-10-01）

来源：[Issue #29](https://github.com/shenxg13/sql-apm/issues/29) 的已确认操作边界。
`full` 依次导入、封存快照、计算、检查和有条件发布；默认截止日取本批声明覆盖日期的最后一天，
`--cutoff-date` 可覆盖。`rebuild` 复用成功导入的数据并重新封存，必须显式给出 `--cutoff-date`。
`status` 显示当前版本、最近任务及最近未发布原因，`history` 显示历史成功版本。

五种写入入口共用集群任务占用，包括独立的 import、training snapshot 和 statistics。
忙时返回 `cluster_busy` 并保存 busy_rejected 任务；不会排队或中断持有者。
任务保留模式、阶段、产物、时间和原因，查询仅输出标识、时间、计数及原因码。
参数及 JSON 示例由[操作说明](../../docs/runbooks/build-publication.md)维护；
失败／零样本发布行为继续由[版本要求](baseline-versions.md)维护。

## Workflows

按任务涉及的边界补读：

- [日志导入、来源与异常处理](log-ingestion.md)。
- [基线构建、版本与发布](baseline-versions.md)。
- [训练资格、黑名单与排除时段](../contracts/training-eligibility.md)。
- [SQL 检索、Grafana 与历史展示](sql-search-and-views.md)。

## Failure Modes

不能把候选建议、历史观察或待验证实现当成已确认且已交付的行为；
本页各节保留的限制及关联条款共同约束相应任务。

## Update Rules

本主题的确认内容只在本页维护；其他入口保留链接。跨主题变更同步实际受影响的
条款，按[知识维护方法](../methods/knowledge-maintenance.md)记录来源与变更。

## Open Questions

完整流程、重新构建和查询参数见上述操作说明；未增加自动调度或网页管理。
SQL 检索与 Grafana 展示继续按后续交付安排落实。
