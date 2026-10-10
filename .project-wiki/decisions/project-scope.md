---
id: decision.project-scope
type: decision
status: active
owners:
  - .project-wiki/decisions/project-scope.md
updated: 2026-10-05
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/33
    status: current
  - path: docs/reports/build-publication-2026-10-01.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/29
    status: current
  - path: .project-wiki/log.md
    status: historical
  - path: docs/reports/knowledge-reorganization-2026-09-25.md
    status: current
related:
  - decision.runtime-and-components
  - feature.operator-cli
  - feature.sql-search-and-views
  - feature.log-ingestion
  - contract.timing-and-grouping
  - contract.sql-fingerprints
  - contract.offline-data-contract
confidence: high
---

# 项目范围、资料来源与交付顺序

## Summary

产品已进入按已确认 Issue 实施的阶段；本文的已确认范围与建议、原始输入分开保存。
首次了解项目、确认范围、设计后续接入或核对原始资料时阅读。

## Source Of Truth

本页保存已确认的范围与来源，包括原知识索引的确认记录及后续补充；
各节保留用户依据、日期、来源状态、证据限制和待定事项。
这些条款定义目标或事实，不表示相关产品功能已经实现；较早条款中的待定表述
应结合明确链接的后续确认阅读，不能覆盖后续已确认规则。
[知识变更记录](../log.md)用于追溯；涉及 Issue 时以其在线正文和评论核对任务契约。

## Contracts

### SQL APM 项目知识

原始资料先保存，需求通过讨论逐项确认；已确认且获实施授权的 Issue 进入开发与独立评审。
后文保留早期需求会话的阶段说明，具体功能现状以各主题最新实施记录为准。

### 已确认的生产系统称谓

2026-10-02 用户确认，来源为 [Issue #33](https://github.com/shenxg13/sql-apm/issues/33)：
MPP 是公司内部对这套生产系统的统称，当前包括 **HashData Warehouse 3.13.13**、
**Greenplum Database 6.20.3**、**PostgreSQL 9.4.26** 三个版本。它是具体业务系统的称谓，
不泛指所有采用 MPP 架构的数据库；`mpp_` 表限于这一系统。

项目正文统一写 MPP，模块、表和技术标识统一写小写 `mpp`；标识不带厂商产品版本。
HashData 仅用于本段组成说明、来源声明中的构建串，以及厂商产品或上游源码的事实陈述。
其他主题引用本条款，不另行定义组成版本。

| 旧标识（历史追溯） | 当前标识 |
| --- | --- |
| `system_kind=hashdata` | `system_kind=mpp` |
| `hashdata-csv/1` | `mpp-csv/1` |
| `hashdata-csv-reader/1` | `mpp-csv-reader/1` |
| `hashdata-3.13.13/1` | `mpp-mapping/1` |
| `hashdata-pg94` | `mpp-sql` |

`/1` 表示标识自身版本；函数字典以自己的 schema_version／rules_version 管理版本。
2026-09-26 原“MPP（HashData）称谓与旧技术标识保留”条款被本决定取代；
[当时的知识日志](../log.md)及既有报告保持原样，旧 ID 不能与新库直接比较。
现有数据库须重建，指纹和分组按新上下文重新生成；不提供原地换算或旧 profile 兼容层。
业务算法、五项分组和统计口径不因改名而变化。

- 用户于 2026-09-26 补充“我们有两个跑批系统，分别是luban和baichuan”；记录 `luban` 和
  `baichuan` 为两个不同的现有跑批系统。本次确认系统名称，不表示已经完成接入。
- 两个跑批系统的任务身份、运行／重试单位、计时及统计规则需分别确认；不因同属
  跑批系统就默认一致。用户随后选择各系统独立统计结果，并核对 MPP 调整清单后
  指示“开始调整”；实现与版本迁移见[存储结构](../architecture/postgresql-storage.md)。
- 已确认本次为 14 张 MPP 专属表增加 `mpp_` 前缀，同步 2 张公共表关联及结构版本，
  总计仍为 41 张表；未来系统按自身接入工作交付结构，不预建额外表。此前公共统计／
  分组表及跑批新增 8 表方案是讨论建议，本次采用各系统独立结果结构。

### 原始资料

- [SQL Baseline 系统方法论](../raw/sql-baseline.md)：原文快照，作为需求讨论的参考。
- 来源：[shenxg13/chat-the-best · topics/sql-baseline.md](https://github.com/shenxg13/chat-the-best/blob/main/topics/sql-baseline.md)。
- 固定版本：[a1290c3c76afc074e93e37b9acc970d450d3337b](https://github.com/shenxg13/chat-the-best/blob/a1290c3c76afc074e93e37b9acc970d450d3337b/topics/sql-baseline.md)。
- 原文件 Git blob SHA：`d5b8c6e4ef8d317110aec7737d49088d4a9308e4`。
- 保存日期：2026-09-22。
- 获取状态：固定版本已读取，原文件 Git blob SHA 已核对。
- 处理状态：raw，作为原始输入保留，尚未提升为已确认的产品契约。

### 资料状态与使用约定

原始文档按上述版本原样保存。文中的“已确认”“推荐”“第一版”等表述保留其
来源语境，不自动成为 sql-apm 项目已经确认的需求或决策。

除下述已确认事项外，其余业务架构、技术选型、实施范围、阈值参数和验收标准
均留待后续沟通确认。
先前讨论中的实施建议也不视为已接受方案。

2026-09-25 用户明确“目前不需要确认实施细节”（当次会话边界）。当时继续
围绕业务需求及范围沟通，任务拆分、具体技术方案及实现级验收用例留待后续
对应 Issue。此前提出的五部分实施任务划分尚未确认，停止推进该项确认；
既有业务规则、技术栈和首期交付先后等已确认决定保持有效。

后续确认的需求与决策另行记录，并引用本快照；原始资料保持原样以便追溯。

### 已确认的首期交付顺序

- 确认日期：2026-09-25。
- 来源：用户对“先完成离线基线流程，再接入 SQL 检索和 Grafana 展示，检索与
  展示仍属于首期范围”的建议回复“确认”。
- 来源状态：current；交付先后保持有效，当次确认尚未开展产品实施；后续交付按各 Issue 验收记录核对。
- 先交付离线基线流程：日志导入、指纹生成、五类计时及
  [已确认的时间统计层次](../contracts/baseline-statistics.md#已确认的时间统计粒度)、
  PostgreSQL 存储、版本管理和命令行诊断，沿用已经确认的各项业务规则与流程用法。
- 使用现有 119／120 日志核对统计结果，并测量实际耗时、峰值内存和数据库
  增长，为后续调整提供依据。现有日志调查不等同于实现验收；资源参考不因此
  变成固定性能门槛，也不能假定所有分组都具备足够样本。
- 再接入完整 SQL／批次输入检索，以及 Grafana 的基线与执行历史展示。
  这些功能继续属于首期范围，既有匹配、分组、版本与历史查询规则保持适用。
- 实时 activity 对比及定时 SCP 继续按此前的后续阶段安排；本次不提前引入。
- 具体实施任务、依赖选型、接口和验收用例在对应 Issue 中落实；本次确认
  交付顺序，不代表单项 Issue 的完整实施契约已经确认。

### 首期离线流程交付（2026-10-01）

[Issue #29](https://github.com/shenxg13/sql-apm/issues/29)把既有导入、快照和统计连接到发布检查、
当前版本切换及任务／版本查询，首期离线链路已完整实现；实际命令见[操作说明](../../docs/runbooks/build-publication.md)。
实施验收与资源实测由[验证报告](../../docs/reports/build-publication-2026-10-01.md)保存，评审及合并状态以在线 Issue／PR 为准。

本地 120 只有七天日志，生产首批三十天成本尚未实测；不把本地验证等同于生产部署。
版本保留与清理单独处理，SQL 检索和 Grafana 展示继续属于首期后续交付，定时 SCP 与 activity 安排不变。
（2026-10-10：定时传输和每日自动运行已由 [#54](https://github.com/shenxg13/sql-apm/issues/54) 实现，见[运行决定](runtime-and-components.md#每日运行的运行方式2026-10-10)；activity 安排不变。）

### 已确认的数据契约设计范围

- 确认日期：2026-09-25；来源状态：current。
- 来源：用户确认先完成数据契约设计，并明确要求“确认，开始创建issue”。
- 通过[Issue #3：首期离线基线数据契约设计](https://github.com/shenxg13/sql-apm/issues/3)
  落实日志输入、规范化执行／调用记录、基线统计及构建／诊断输出之间的数据约定。
  交付逻辑对象与关联、字段语义和约束、来源映射、版本兼容规则及正反样例；
  PostgreSQL 物理表结构、索引和建表脚本由后续 Issue 落实。
- 首期完整定义 MPP 的数据语义，并遵守下节多类型接入扩展约束；
  函数参数规则及语句类别黑名单继续由各自 Issue 负责，不借设计扩大其职责。
- 本次确认设计边界及 Issue 创建，不表示字段设计、数据契约或产品实现已经完成。
  详细交付契约和验收项以 Issue 实时正文与评论为准，按既有流程收敛确认；
  本页不维护 Issue 正文或执行状态的副本。

2026-09-26 按已确认 Issue 契约产出的[离线逻辑数据契约](../contracts/offline-data-contract.md)
包含字段、来源映射和合成样例，供后续模块实现及独立评审核对；当时产品解析、统计、
存储和运行时校验器尚未交付。后续各模块的实施见对应主题，此前确认记录保留作为设计边界的来源。

### 已确认的多类型系统接入扩展约束

- 确认日期：2026-09-25。
- 来源：用户说明当前仅作为 MPP 系统的 baseline，后续可能接入其他
  数据库，也可能接入跑批系统等非数据库对象，明确要求“在设计的时候请保留多端接入的可能性”。
- 来源状态：current；已确认设计扩展目标，尚未确定或实现具体扩展接口。
- 首期以 MPP 的 SQL／请求及阶段／调用为基线对象。设计须保留将来接入
  其他数据库和非数据库系统执行记录的空间；这里的“多端”指不同业务系统或
  来源类型，不只是同一类 MPP 的多个集群。
- 落实该目标时，应使来源特有的读取、解析、对象识别和计时解释能够独立扩展，
  避免将 MPP 日志格式、SQL 文本、数据库／执行用户字段及五类计时固定为
  所有未来来源都必须具备的前提。具体模块、接口、字段和扩展机制留待设计落实。
- 统计计算、训练窗口、基线版本等可复用能力，应与来源特有的解释规则保持清晰
  边界。未来来源的对象身份、执行单位、耗时语义、分组和训练资格需分别确认，
  不因同名对象或字段相似就混合样本，也不默认沿用所有 MPP 指标与样本门槛。
- 跑批系统可按其自身的任务或作业定义识别统计对象，具体身份规则待实际接入时
  确认；保留该扩展空间不要求把非 SQL 对象伪装为 SQL 或生成 SQL 结构指纹。
- 已确认的 MPP 五项分组、五类计时、时间统计层次及其他业务规则继续作为
  首期契约；扩展接入目标不增减现有分组维度、不修改计时含义，也不改变 Python、PostgreSQL
  和 Grafana 的既有安排。
- 本次确认保留设计上的扩展可能性，不要求首期交付其他数据库／跑批系统适配器、
  通用插件平台或额外采集方式。未来具体接入对象、范围和验证在对应工作中确认，
  当前不提前固定实现细节。

关联条款：[日志导入](../features/log-ingestion.md)、[计时与分组](../contracts/timing-and-grouping.md)、
[SQL 指纹](../contracts/sql-fingerprints.md)。

### 已采用的开发基础

- [Agent 开发流程](../architecture/agent-development-harness.md)。
- [通用工程原则](engineering-principles.md)。
- [本地初始化范围](../../.harness/plans/local-bootstrap.md)。
- [本地开发与检查](../../docs/runbooks/local-development.md)。
- [模板来源及本地定制](../../docs/provenance.md)。
- [本地初始化验证](../../docs/reports/local-bootstrap-validation.md)。
- [GitHub 首次发布范围与验证](../../docs/reports/initial-publication.md)。
- [知识更新记录](../log.md)。

上述内容描述开发协作方式，不定义 SQL APM 的业务架构。
项目运行模型暂未确认。

### 知识规范与模板

阅读[实体规范](../schema.md)，在具体需求确认后按需建立有来源的项目知识。

- [Architecture](../templates/architecture.md)。
- [Module](../templates/module.md)。
- [Feature](../templates/feature.md)。
- [Contract](../templates/contract.md)。
- [Decision](../templates/decision.md)。
- [Method](../templates/method.md)。
- [Operating model](../templates/operating-model.md)：尚未采用的运行模型模板。

### 需求沟通节奏

- 来源：2026-09-24 用户要求后续完成一项确认后继续下一项；迁移前 Agent 入口保存了此约定。
- 用户确认一项后，记录决定，并在同一轮提出下一项实质未决需求；不重复询问已确认事项，
  没有实质未决事项时说明当前边界。新建议在回答前保持未确认状态。
- 需求会话不因讨论而自动获得产品实施授权；用户后续明确指定 Issue 实施时，按其已确认契约执行。

## Workflows

按任务涉及的边界补读：

- [运行环境、组件与资源边界](runtime-and-components.md)。
- [命令行与本地配置操作](../features/operator-cli.md)。
- [SQL 检索、Grafana 与历史展示](../features/sql-search-and-views.md)。

## Failure Modes

不能把候选建议、历史观察或待验证实现当成已确认且已交付的行为；
本页各节保留的限制及关联条款共同约束相应任务。

## Update Rules

本主题的确认内容只在本页维护；其他入口保留链接。跨主题变更同步实际受影响的
条款，按[知识维护方法](../methods/knowledge-maintenance.md)记录来源与变更。

## Open Questions

具体产品实施任务、验收用例和运行模型留待对应 Issue；本次知识整理不确认它们。各节已有待定说明继续有效。
后续接入的数据库类型、各系统的接入优先级、具体执行语义、分组与接入方式尚未确定。
