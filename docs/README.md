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
| [SQL 归一化与结构指纹接口](design/sql-normalization.md) | 设计／已实现接口 | 核心调用、规则快照、支持矩阵、命令与失败边界 |
| [SQL 近似指纹接口与边界](design/sql-approximate.md) | 接口说明 | 观察用词法归一化、原文保留、近似结果隔离及命令。 |
| [离线基线逻辑数据契约](../.project-wiki/contracts/offline-data-contract.md) | 设计说明 | 三个交接边界、对象关系、身份、训练、统计、发布与演进约束。 |
| [契约字段字典](design/offline-data-contract/fields.md) | 设计说明 | 字段类型、必填与空值、枚举、引用和全部统计指标。 |
| [HashData 来源映射](design/offline-data-contract/hashdata-mapping.md) | 设计说明 | 30 列输入与五类计时映射，观察、推导及未知信息的边界。 |
| [契约样例与需求对应](design/offline-data-contract/README.md) | 设计说明 | 需求到设计条款及正反样例的对应、合成数据与核对方法。 |
| [构建编排与发布](design/build-publication.md) | 设计／已实现接口 | 集群任务、恢复、六项检查、覆盖推导和原子发布。 |
| [统计计算设计](design/baseline-statistics.md) | 设计／已实现接口 | 正式及观察五层统计、分区自然键、原子保存与失败恢复。 |
| [训练样本判定设计](design/training-decisions.md) | 设计／已实现接口 | 不可变快照、原文缓存、数据库统一推导及物理边界。 |
| [日志导入设计](design/log-ingestion.md) | 设计说明 | 文件事务、Execute 单次配对、原文和近似入库、恢复与冲突检测边界。 |
| [PostgreSQL 物理结构](design/postgresql-storage.md) | 设计说明 | 逻辑映射、MPP 专属表、独立统计存储及版本迁移边界。 |
| [PostgreSQL 存储知识](../.project-wiki/architecture/postgresql-storage.md) | 设计说明 | 已实现结构、初始化边界和维护入口。 |
| [数据契约设计验证](design/offline-data-contract/verification.md) | 分析证据 | 文档及合成样例的实际检查结果和未覆盖的产品运行边界。 |
| [数据契约设计范围](../.project-wiki/decisions/project-scope.md#已确认的数据契约设计范围) | 需求说明 | 逻辑数据契约的交付边界及关联 Issue；具体设计以该任务的交付为准。 |
| [通用工程原则](../.project-wiki/decisions/engineering-principles.md) | 设计说明 | 数据正确性、恢复、资源成本与证据边界等设计约束。 |

## 开发与操作

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [Duration 覆盖与合并耗时诊断](runbooks/duration-dispersion.md) | 操作说明 | 只读提取、真实维度覆盖、合并子组中位数倍数及脱敏边界。 |
| [本地开发说明](runbooks/local-development.md) | 操作说明 | 当前环境准备情况、Python 用法、本地样本位置及检查入口。 |
| [Kylin 离线部署与九任务验证](runbooks/kylin-offline-deployment.md) | 操作说明 | 离线包、项目自带解释器、PG17.10、SCRAM、日志传输及 Alma 比对。 |
| [精简程序包与预发布交付](runbooks/program-release.md) | 操作说明 | 必要文件打包、离线 HTML、独立验收及用户确认后发布。 |
| [Kylin 人工验证记录](runbooks/kylin-validation-record.md) | 记录模板 | 快照恢复后的独立执行、DBA 查询、每步实际输出与结论。 |
| [质量工具安装与运行](runbooks/issue-pr-quality-tooling.md) | 操作说明 | 仓库检查工具的版本、安装步骤和运行方式。 |
| [完整流程与版本操作](runbooks/build-publication.md) | 操作说明 | full、rebuild、status、history 及端到端验收。 |
| [统计计算操作](runbooks/baseline-statistics.md) | 操作说明 | 快照计算、观察诊断、重试引用、分区维护和真实验收。 |
| [训练快照与判定诊断](runbooks/training-decisions.md) | 操作说明 | 本地配置、快照／汇总命令、逐条查询和私有全量验收。 |
| [完整日志批次导入](runbooks/log-ingestion.md) | 操作说明 | 来源／清单 JSON、只导入命令、幂等重试和私有实例验收。 |
| [数据库初始化与恢复](runbooks/database-initialization.md) | 操作说明 | 单账号引导、认证、参数、兼容重跑、MPP 版本升级及失败恢复。 |
| [仓库脚本清单](../scripts/README.md) | 操作说明 | 质量、GitHub 流程和函数字典工具的用途及调用入口。 |
| [函数参数规则维护](../rules/functions/README.md) | 操作说明 | 字典文件、字段、匹配边界、验证命令和来源重建方法。 |

## 查看分析依据

报告的结论受各自数据、版本和时间范围限制；实现验证与只读日志调查的用途在正文中分别说明。

| 文档 | 类型 | 读它了解什么 |
| --- | --- | --- |
| [近似分组观察统计验证](reports/observation-statistics-2026-10-01.md) | 实施验证报告 | 55 文件重导、全部观察组复算、正式结果逐行对照、重复一致与资源实测。 |
| [统计 R2 整改](reports/baseline-statistics-r2-remediation-2026-10-01.md) | 整改验证报告 | 批量门槛查询一致性、单组／聚合实测与统计容量接受同步。 |
| [构建编排与完整离线流程验收](reports/build-publication-2026-10-01.md) | 实施验证报告 | 九次真实任务、原子发布、覆盖迁移、独立复算与每版资源实测。 |
| [Kylin 离线部署验证](reports/kylin-offline-deployment-2026-10-02.md) | 实施验证报告 | 离线编译、自检、认证和与 Alma 比对的实测及未完成验收边界。 |
| [精简候选包与 HTML 准备验证](reports/slim-release-preparation-2026-10-03.md) | 实施验证报告 | 程序精简、逐文件证据继承、隔离验证、HTML 检查及目标机确认点。 |
| [精简候选包 Kylin 试跑](reports/slim-release-kylin-trial-2026-10-03.md) | 实施验证报告 | 快照恢复后的完整重装、独立验收、四进程超时与单进程复跑、性能对照。 |
| [Kylin 用户独立验证核对](reports/kylin-manual-validation-2026-10-04.md) | 验收核对报告 | 用户九任务等值、正常 yum 源和 DBA 验证，以及早期候选包误选与最小补验范围。 |
| [Kylin 执行账号读取权限修正](reports/kylin-read-access-2026-10-04.md) | 验证报告 | sfmon 全目录读取、private 和 PGDATA 权限、新文件继承及 socket／TCP 复验。 |
| [构建编排 R1 整改](reports/build-publication-r1-remediation-2026-10-02.md) | 整改验证报告 | 次月预建并发、未登记集群拒绝、知识同步与 R1 退出条件。 |
| [统计范围变更与 R1 整改验收](reports/baseline-statistics-2026-10-01.md) | 整改验证报告 | 派生门槛、数据库时钟修复、新结构 55 文件全量验收与容量对比。 |
| [统计计算原验收](reports/baseline-statistics-2026-09-30.md) | 实施验证报告 | 55 文件重导、两集群五层守恒、独立复算、重复一致与资源实测。 |
| [类别别名验证](reports/training-category-aliases-2026-09-30.md) | 实施验证报告 | 三个别名、快照兼容及 55 文件执行级差分。 |
| [训练判定 R1 整改验证](reports/training-decisions-r1-remediation-2026-09-30.md) | 整改验证报告 | 事务 SET 暂缓边界、规则缓存版本和真实输入影响核对。 |
| [训练样本判定验证](reports/training-decisions-2026-09-29.md) | 实施验证报告 | 合成规则、结构迁移、55 文件判定守恒及实测耗时。 |
| [结构指纹 v5 验证](reports/sql-normalization-v5-2026-09-29.md) | 实施验证报告 | SELECT／JOIN 常量与集合分支恢复、149 万原文独立差分及 745 万耗时记录的覆盖／离散。 |
| [七天日志门槛 R1 整改验证](reports/log-supplement-r1-remediation-2026-09-28.md) | 整改验证报告 | 美元引号非法编码／NUL 统一拒绝、分母与活跃日回归、真实语料影响及新代码证据。 |
| [日志导入 R2 整改验证](reports/log-ingestion-r2-remediation-2026-09-29.md) | 整改验证报告 | 单输入有界重试、持续失败记录隔离与基础设施故障回滚的验证。 |
| [日志导入 R1 整改验证](reports/log-ingestion-r1-remediation-2026-09-29.md) | 分析证据 | 工作进程故障恢复、成功清单成员保护、Analysis 解释版本核验及回归。 |
| [完整日志导入验证](reports/log-ingestion-2026-09-29.md) | 分析证据 | 55 文件正式入库、五类计时、历史原文差异、重复导入及资源实测。 |
| [120 七天日志补充分析](reports/cluster-log-supplement-2026-09-28.md) | 分析证据 | 55 文件覆盖、实际记录日期、七天类别对照与候选方案门槛覆盖。 |
| [归一化 v4 R1 整改验证](reports/sql-normalization-v4-r1-remediation-2026-09-28.md) | 整改验证报告 | Hint 同桶例外确认、FILTER 边界修复及重新采集的冻结版本差分。 |
| [归一化 v4 验证](reports/sql-normalization-v4-2026-09-28.md) | 实施验证报告 | IN 粗分桶、Hint gap 清理、冻结版本分组差分及逐条结构审计。 |
| [归一化收敛裁决整改验证](reports/sql-normalization-adj-remediation-2026-09-28.md) | 实施验证报告 | PG 合成常量源位置修复、裁决退出条件及历史分组差分，一次性终验依据。 |
| [归一化 R2 整改验证](reports/sql-normalization-r2-remediation-2026-09-28.md) | 实施验证报告 | 含 Hint 的正负业务值归并、位置保真回归及5,333条历史 Hint 对照。 |
| [归一化 R1 整改验证](reports/sql-normalization-r1-remediation-2026-09-28.md) | 实施验证报告 | Hint 位置碰撞与近似保护绕过修复、新版本及1,832条回放对照。 |
| [归一化与结构指纹验证](reports/sql-normalization-2026-09-27.md) | 实施验证报告 | 合成回归、1,832条有界重放及真实归组结构核对 |
| [近似指纹与旧失败重放](reports/mpp-approximate-2026-09-27.md) | 验证报告 | 8,909个拒绝输入的近似结果及384个已修复对照，保留观察用途。 |
| [全量解析问题修复](reports/mpp-full-repair-2026-09-27.md) | 分析证据 | 递归深度与四类真实语法修复、384个恢复输入及全部115万原文的结构回归。 |
| [MPP日志全量解析覆盖](reports/mpp-full-scan-2026-09-27.md) | 分析证据 | 46个完整文件、115万不同原文的解析状态、真实语法缺口、深度异常及914项旧结果对照。 |
| [解析源码目录调整](reports/parser-layout-2026-09-27.md) | 分析证据 | 核心与诊断模块归位、测试夹具迁移、入口兼容及完整结果等价验证。 |
| [MPP解析整体扩展验证](reports/mpp-broad-validation-2026-09-27.md) | 分析证据 | 整体结构矩阵、新增564个真实输入、COPY位置修复及ANALYZE ROOTPARTITION缺口。 |
| [混合 ALTER 完整结构修复](reports/mixed-alter-2026-09-27.md) | 分析证据 | 普通动作与 MPP SET 动作的有序保留、原碰撞修复、字段断言及350个输入回归。 |
| [MPP 解析器扩展验证](reports/mpp-expanded-validation-2026-09-27.md) | 分析证据 | 新形态有界抽样、混合 ALTER 结构丢失修复、格式参数及 ROW 兼容、回归与剩余缺口。 |
| [MPP 解析适配与 Hint 原型验证](reports/mpp-adapter-probe-2026-09-27.md) | 分析证据 | 显式 MPP 扩展节点、Hint 锚点、格式回归及同组日志重放，保留原型限制。 |
| [MPP SQL 解析器结构保真探测](reports/parser-fidelity-2026-09-27.md) | 分析证据 | 两个候选的固定版本、MPP 语法／Hint／函数保真缺口、合成核对与有界重放。 |
| [HashData 日志事实与证据边界](../.project-wiki/contracts/log-evidence.md) | 分析证据 | 已知日志配置、样本来源、调查结果及其适用限制。 |
| [PostgreSQL 结构 R1 整改验证](reports/postgresql-storage-r1-remediation-2026-09-26.md) | 分析证据 | 外键内部触发器模式漂移的复现、轻量目录检查及恢复／迁移回归。 |
| [MPP 结构升级验证](reports/mpp-storage-migration-2026-09-26.md) | 分析证据 | 1.0.0 到 1.1.0 的数据／对象保留、失败回滚、重跑和自定义名称验证。 |
| [近似观察存储 1.2.0 验证](reports/approximate-storage-2026-09-29.md) | 实施验证报告 | 升级、隔离和全部10,902条 v5 拒绝的接口往返；不含 SQL 原文。 |
| [PostgreSQL 结构验证](reports/postgresql-storage-2026-09-26.md) | 分析证据 | 临时 PG17 实例的存储约束、真实账号、重跑和清理实测。 |
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
| [日志补充分析与门槛诊断](runbooks/log-supplement.md) | 操作说明 | 新清单、全量索引、类别回放、候选方案及脱敏门槛命令的复现与边界。 |
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
