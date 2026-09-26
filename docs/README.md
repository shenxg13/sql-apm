# SQL APM 文档导航

这里按阅读目的汇总仓库中的文档。每项提供中文名称、资料类型和用途，点击名称进入正文。
[返回项目首页](../README.md)；按知识主题查找可使用[项目知识索引](../.project-wiki/index.md)。

“需求说明”记录业务约定，“设计说明”解释结构与边界，“操作说明”提供实际用法，
“分析证据”记录特定时间和范围的调查或验证，“协作说明”描述工作流程，
“历史参考”用于追溯或讨论。类型不表示功能已经实现；确认边界和交付情况以对应正文及关联 Issue 为准。

## 推荐阅读顺序

1. 先读[项目范围与交付顺序](../.project-wiki/decisions/project-scope.md)，了解要解决的问题和首期边界。
2. 再读[日志导入](../.project-wiki/features/log-ingestion.md)、[计时与分组](../.project-wiki/contracts/timing-and-grouping.md)和[统计指标](../.project-wiki/contracts/baseline-statistics.md)，理解数据如何形成基线。
3. 接着读[基线构建与版本](../.project-wiki/features/baseline-versions.md)、[SQL 原文与明细](../.project-wiki/contracts/sql-storage.md)，了解结果如何保存、更新和追溯。
4. 准备开发时读[源码布局](../.project-wiki/architecture/source-layout.md)和[本地开发说明](runbooks/local-development.md)；参与需求或交付时读[Issue／PR 工作流](../.harness/workflows/github-planning.md)。

## 了解项目

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [项目首页](../README.md) | 项目概览 | 项目简介、目录职责及主要入口。 |
| [项目范围、资料来源与交付顺序](../.project-wiki/decisions/project-scope.md) | 需求说明 | 首期范围、交付先后、确认依据及未来多类型系统接入边界。 |
| [运行环境、组件与资源边界](../.project-wiki/decisions/runtime-and-components.md) | 需求说明 | Python、PostgreSQL、Grafana 的职责及开发／生产环境约束。 |

## 理解业务规则

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [日志导入、来源与异常处理](../.project-wiki/features/log-ingestion.md) | 需求说明 | 文件获取、来源登记、批次完整性、去重、重试及导入问题处理。 |
| [计时分类、分组与时间归属](../.project-wiki/contracts/timing-and-grouping.md) | 需求说明 | 五类计时的统计单位、SQL 分组和推算开始时间。 |
| [SQL 结构指纹与归一化](../.project-wiki/contracts/sql-fingerprints.md) | 需求说明 | SQL 结构如何归并，哪些常量保留，以及函数字典的衔接。 |
| [训练资格、黑名单与排除时段](../.project-wiki/contracts/training-eligibility.md) | 需求说明 | 哪些样本参与训练，单条／整批 SQL 及排除原因如何处理。 |
| [统计指标、训练窗口与样本门槛](../.project-wiki/contracts/baseline-statistics.md) | 需求说明 | 五个时间统计层次、主要指标、计算口径和样本不足的表达。 |
| [基线构建、版本与发布](../.project-wiki/features/baseline-versions.md) | 需求说明 | 固定构建输入、规则变更、发布检查、版本切换与历史保留。 |
| [SQL 原文、明细与留存](../.project-wiki/contracts/sql-storage.md) | 需求说明 | 原文去重、执行／调用明细与留存边界。 |
| [命令行与本地配置操作](../.project-wiki/features/operator-cli.md) | 需求说明 | 导入、构建、查询任务和诊断的预期操作方式。 |
| [SQL 检索、Grafana 与历史展示](../.project-wiki/features/sql-search-and-views.md) | 需求说明 | SQL 检索、分组选择、基线版本与执行历史的展示约定。 |

## 理解设计

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [Python 源码布局与模块职责](../.project-wiki/architecture/source-layout.md) | 设计说明 | 目标目录、模块职责、依赖方向，以及当前已实现的部分。 |
| [离线基线逻辑数据契约](../.project-wiki/contracts/offline-data-contract.md) | 设计说明 | 三个交接边界、对象关系、身份、训练、统计、发布与演进约束。 |
| [契约字段字典](design/offline-data-contract/fields.md) | 设计说明 | 字段类型、必填与空值、枚举、引用和全部统计指标。 |
| [HashData 来源映射](design/offline-data-contract/hashdata-mapping.md) | 设计说明 | 30 列输入与五类计时映射，观察、推导及未知信息的边界。 |
| [契约样例与需求对应](design/offline-data-contract/README.md) | 设计说明 | 需求到设计条款及正反样例的对应、合成数据与核对方法。 |
| [数据契约设计验证](design/offline-data-contract/verification.md) | 分析证据 | 文档及合成样例的实际检查结果和未覆盖的产品运行边界。 |
| [数据契约设计范围](../.project-wiki/decisions/project-scope.md#已确认的数据契约设计范围) | 需求说明 | 逻辑数据契约的交付边界及关联 Issue；具体设计以该任务的交付为准。 |
| [通用工程原则](../.project-wiki/decisions/engineering-principles.md) | 设计说明 | 数据正确性、恢复、资源成本与证据边界等设计约束。 |

## 开发与操作

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [本地开发说明](runbooks/local-development.md) | 操作说明 | 当前环境准备情况、Python 用法、本地样本位置及检查入口。 |
| [质量工具安装与运行](runbooks/issue-pr-quality-tooling.md) | 操作说明 | 仓库检查工具的版本、安装步骤和运行方式。 |
| [仓库脚本清单](../scripts/README.md) | 操作说明 | 质量、GitHub 流程和函数字典工具的用途及调用入口。 |
| [函数参数规则维护](../rules/functions/README.md) | 操作说明 | 字典文件、字段、匹配边界、验证命令和来源重建方法。 |

## 查看分析依据

报告的结论受各自数据、版本和时间范围限制；实现验证与只读日志调查的用途在正文中分别说明。

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [HashData 日志事实与证据边界](../.project-wiki/contracts/log-evidence.md) | 分析证据 | 已知日志配置、样本来源、调查结果及其适用限制。 |
| [语句类别黑名单核查（2026-09-26）](reports/statement-category-census-2026-09-26.md) | 分析证据 | 全部日志的类别、别名及异常覆盖，保守名单依据与合成规则验收。 |
| [119、120 集群日志分析（2026-09-24）](reports/cluster-log-analysis-2026-09-24.md) | 分析证据 | 两组日志的覆盖、格式、SQL 文本及计时分类观察。 |
| [HashData duration 源码位置核查（2026-09-24）](reports/hashdata-duration-source-mapping-2026-09-24.md) | 分析证据 | 请求、Execute、Parse、Bind 的计时解释及上游源码对照边界。 |
| [生产 SQL 日志样本分析（2026-09-23）](reports/production-log-analysis-2026-09-23.md) | 分析证据 | 早期生产样本的覆盖范围、文本缺失、编码和多语句现象。 |
| [测试日志文本与绑定参数核查（2026-09-23）](reports/sql-log-text-verification.md) | 分析证据 | 两份测试日志中的 SQL 文本、占位参数和相关记录。 |
| [Python 3.9.5 环境验证（2026-09-25）](reports/python-environment-2026-09-25.md) | 分析证据 | 本地解释器、虚拟环境及标准库检查的实测记录。 |
| [函数字典覆盖与验证（2026-09-25）](reports/function-dictionary-2026-09-25.md) | 分析证据 | 首次字典实施的来源、覆盖和验证范围。 |
| [函数字典 R1 整改验证（2026-09-25）](reports/function-dictionary-r1-remediation-2026-09-25.md) | 分析证据 | 对象身份与多态匹配问题的修复证据及复核边界。 |

## 协作与追溯

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [Issue／PR 工作流](../.harness/workflows/github-planning.md) | 协作说明 | 需求确认、实施、评审、交接和状态转换。 |
| [Agent 开发流程的组成与职责](../.project-wiki/architecture/agent-development-harness.md) | 协作说明 | Agent 入口、Harness、项目知识和检查脚本的分工。 |
| [Agent 入口](../AGENTS.md)与[工作流目录](../.harness/catalog.md) | 协作说明 | Agent 按任务查找规则与工作流的入口。 |
| [工具运行与排障约定](../.harness/tooling-runtime.md) | 操作说明 | 工具发现、网络与认证诊断、检查边界。 |
| [知识维护方法](../.project-wiki/methods/knowledge-maintenance.md) | 协作说明 | 需求正文、导航、证据和日志如何分工维护。 |
| [项目知识实体规范](../.project-wiki/schema.md)与[模板清单](../.project-wiki/templates/) | 协作说明 | 新建或调整知识页面时使用的字段、结构与模板。 |
| [模板人工更新流程](updating.md) | 操作说明 | 如何审阅并采纳 agent-harness 的后续更新。 |
| [需求模板](../.github/ISSUE_TEMPLATE/requirement.md)与[PR 模板](../.github/pull_request_template.md) | 协作说明 | 新建需求和提交变更时需要描述的内容。 |

### 历史与参考

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [SQL Baseline 原始资料](../.project-wiki/raw/sql-baseline.md) | 历史参考 | 最初讨论输入；其中建议是否采纳，以对应需求主题页为准。 |
| [知识变更记录](../.project-wiki/log.md) | 历史参考 | 各次确认、修订和知识更新的时间及依据。 |
| [项目来源与模板采纳记录](provenance.md) | 历史参考 | 固定模板版本、原始资料来源和本地定制。 |
| [Harness 更新记录](../.harness/update-log.md) | 历史参考 | 开发协作流程的历次调整。 |
| [本地初始化计划](../.harness/plans/local-bootstrap.md) | 历史参考 | SQL APM 仓库建立时的范围、计划和验证安排。 |
| [本地初始化验证](reports/local-bootstrap-validation.md) | 历史参考 | 仓库初始化阶段实际完成的检查。 |
| [首次 GitHub 发布记录](reports/initial-publication.md) | 历史参考 | 首次公开同步的范围和验证结果。 |
| [知识重组与完整性核对（2026-09-25）](reports/knowledge-reorganization-2026-09-25.md) | 历史参考 | 需求迁入主题页、入口精简及内容保留的核对记录。 |
| [模板初始化说明](getting-started.md) | 历史参考 | 从模板建立新项目的步骤；SQL APM 的现行操作见本地开发说明。 |
| [上游提取计划](../.harness/plans/initial-extraction.md)与[上游验证入口](reports/initial-validation.md) | 历史参考 | agent-harness 的历史资料及其与本项目证据的边界。 |
| [单维护者运行模型示例](examples/single-maintainer.md) | 历史参考 | 可供讨论的运行模型示例，是否采用以项目决定为准。 |

## 索引维护

新增、移动、删除面向人的文档或改变文档用途时，同步本页对应链接与导读；
详细规则见[知识维护方法](../.project-wiki/methods/knowledge-maintenance.md)。
本页保存阅读导航，需求、设计、操作步骤和 Issue 状态由各自正文或在线任务维护。
