---
id: decision.project-scope
type: decision
status: active
owners:
  - .project-wiki/decisions/project-scope.md
updated: 2026-09-25
sources:
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
confidence: high
---

# 项目范围、资料来源与交付顺序

## Summary

产品仍处于需求沟通阶段；本文的已确认范围与建议、原始输入分开保存。
首次了解项目、确认范围、设计后续接入或核对原始资料时阅读。

## Source Of Truth

本页保存已确认的范围与来源，包括原知识索引的确认记录及后续补充；
各节保留用户依据、日期、来源状态、证据限制和待定事项。
这些条款定义目标或事实，不表示相关产品功能已经实现；较早条款中的待定表述
应结合明确链接的后续确认阅读，不能覆盖后续已确认规则。
[知识变更记录](../log.md)用于追溯；涉及 Issue 时以其在线正文和评论核对任务契约。

## Contracts

### SQL APM 项目知识

当前处于需求沟通阶段。先保存原始资料，后续通过讨论逐项确认具体需求。

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

2026-09-25 用户明确“目前不需要确认实施细节”（来源状态：current）。当前继续
围绕业务需求及范围沟通，任务拆分、具体技术方案及实现级验收用例留待后续
对应 Issue。此前提出的五部分实施任务划分尚未确认，停止推进该项确认；
既有业务规则、技术栈和首期交付先后等已确认决定保持有效。

后续确认的需求与决策另行记录，并引用本快照；原始资料保持原样以便追溯。

### 已确认的首期交付顺序

- 确认日期：2026-09-25。
- 来源：用户对“先完成离线基线流程，再接入 SQL 检索和 Grafana 展示，检索与
  展示仍属于首期范围”的建议回复“确认”。
- 来源状态：current；已确认交付先后，尚未开展产品实施。
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

### 已确认的数据契约设计范围

- 确认日期：2026-09-25；来源状态：current。
- 来源：用户确认先完成数据契约设计，并明确要求“确认，开始创建issue”。
- 通过[Issue #3：首期离线基线数据契约设计](https://github.com/shenxg13/sql-apm/issues/3)
  落实日志输入、规范化执行／调用记录、基线统计及构建／诊断输出之间的数据约定。
  交付逻辑对象与关联、字段语义和约束、来源映射、版本兼容规则及正反样例；
  PostgreSQL 物理表结构、索引和建表脚本由后续 Issue 落实。
- 首期完整定义 HashData 的数据语义，并遵守下节多类型接入扩展约束；
  函数参数规则及语句类别黑名单继续由各自 Issue 负责，不借设计扩大其职责。
- 本次确认设计边界及 Issue 创建，不表示字段设计、数据契约或产品实现已经完成。
  详细交付契约和验收项以 Issue 实时正文与评论为准，按既有流程收敛确认；
  本页不维护 Issue 正文或执行状态的副本。

### 已确认的多类型系统接入扩展约束

- 确认日期：2026-09-25。
- 来源：用户说明当前仅作为 MPP 数据库 HashData 的 baseline，后续可能接入其他
  数据库，也可能接入跑批系统等非数据库对象，明确要求“在设计的时候请保留多端接入的可能性”。
- 来源状态：current；已确认设计扩展目标，尚未确定或实现具体扩展接口。
- 首期以 HashData 的 SQL／请求及阶段／调用为基线对象。设计须保留将来接入
  其他数据库和非数据库系统执行记录的空间；这里的“多端”指不同业务系统或
  来源类型，不只是同一类 HashData 的多个集群。
- 落实该目标时，应使来源特有的读取、解析、对象识别和计时解释能够独立扩展，
  避免将 HashData 日志格式、SQL 文本、数据库／执行用户字段及五类计时固定为
  所有未来来源都必须具备的前提。具体模块、接口、字段和扩展机制留待设计落实。
- 统计计算、训练窗口、基线版本等可复用能力，应与来源特有的解释规则保持清晰
  边界。未来来源的对象身份、执行单位、耗时语义、分组和训练资格需分别确认，
  不因同名对象或字段相似就混合样本，也不默认沿用所有 HashData 指标与样本门槛。
- 跑批系统可按其自身的任务或作业定义识别统计对象，具体身份规则待实际接入时
  确认；保留该扩展空间不要求把非 SQL 对象伪装为 SQL 或生成 SQL 结构指纹。
- 已确认的 HashData 五项分组、五类计时、时间统计层次及其他业务规则继续作为
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
- 当前不展开产品实施细节的约定继续适用；本次用户单独授权的 Agent 流程优化不改变产品交付范围。

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
后续接入的具体数据库／业务系统及其执行语义、分组与接入方式尚未选定。
