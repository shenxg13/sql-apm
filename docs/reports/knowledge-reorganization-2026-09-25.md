# 项目知识重组与完整性核对（2026-09-25）

## 授权与范围

用户要求执行 Agent 流程优化，并明确不能丢失已确认需求。本次仅调整文档组织、
阅读路由及知识维护方式；没有变更产品要求、原始日志或 GitHub Issue 契约与状态。
优化前已保存工作区文档快照，包含此前尚未提交的需求确认内容。

## 迁移方法

- 原知识索引按标题分成 75 个区块（含导言），每个区块仅有一个主题归属。
- 正文逐节完整保留，仅重定位相对链接；标题调整为主题内层级，另加明确的关联条款链接。
- 确认日期、用户依据、来源状态、公式、边界、例外、示例和待定事项均随正文迁移。
- 根入口和开发说明不再复制业务全文；入口独有的需求沟通节奏迁入项目范围，
  开发说明中的 Python 实际检查记录、环境准备及可执行质量命令继续保留。
- 旧“上文／下述”等参照若已跨页，通过紧随该节的关联条款定位；更早的待定说明
  不能覆盖这些链接指向的后续确认。没有把待定建议提升为已确认要求。
- 更新原有指向索引章节的链接；历史知识日志正文保留，只修正链接并标明历史性质。
- 原始 SQL Baseline 快照保持字节不变；已有分析报告仅在确有迁移锚点时更新链接。

## 入口规模

| 文件 | 优化前行数 | 优化后行数 |
| --- | ---: | ---: |
| AGENTS.md | 769 | 39 |
| .project-wiki/index.md | 1739 | 32 |
| docs/runbooks/local-development.md | 439 | 83 |

完整正文保存在 12 个业务主题页；新增一页知识维护方法。索引按任务导航，
关联规则按任务边界补读，不把这些主题变成新的全量必读清单。

## 原章节去向

下面按原索引顺序列出迁移位置。[机器可读清单](knowledge-reorganization-2026-09-25.json)
包含原文件 SHA-256、每节起止行、迁移前后正文摘要及入口文件摘要。
标题与链接搬迁不改变业务语义；来源和所有未决事项在目标正文中保留。

| 原索引行 | 原标题 | 目标主题 |
| ---: | --- | --- |
| 1 | SQL APM 项目知识 | [项目范围、资料来源与交付顺序](../../.project-wiki/decisions/project-scope.md#sql-apm-项目知识) |
| 5 | 原始资料 | [项目范围、资料来源与交付顺序](../../.project-wiki/decisions/project-scope.md#原始资料) |
| 15 | 资料状态与使用约定 | [项目范围、资料来源与交付顺序](../../.project-wiki/decisions/project-scope.md#资料状态与使用约定) |
| 31 | 已确认的首期交付顺序 | [项目范围、资料来源与交付顺序](../../.project-wiki/decisions/project-scope.md#已确认的首期交付顺序) |
| 48 | 已确认的当前开发环境与生产部署安排 | [运行环境、组件与资源边界](../../.project-wiki/decisions/runtime-and-components.md#已确认的当前开发环境与生产部署安排) |
| 66 | 已确认的现网产品版本 | [运行环境、组件与资源边界](../../.project-wiki/decisions/runtime-and-components.md#已确认的现网产品版本) |
| 77 | 已知的生产资源参考 | [运行环境、组件与资源边界](../../.project-wiki/decisions/runtime-and-components.md#已知的生产资源参考) |
| 88 | 已知日志输入规模 | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#已知日志输入规模) |
| 110 | 已确认的 SQL 原文存储方式 | [SQL 原文、明细与留存](../../.project-wiki/contracts/sql-storage.md#已确认的-sql-原文存储方式) |
| 140 | 已确认的首期留存与自动清理边界 | [SQL 原文、明细与留存](../../.project-wiki/contracts/sql-storage.md#已确认的首期留存与自动清理边界) |
| 159 | 已确认的日志记录口径 | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#已确认的日志记录口径) |
| 182 | 执行关联的测试日志观察 | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#执行关联的测试日志观察) |
| 225 | SQL 文本及绑定参数核查 | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#sql-文本及绑定参数核查) |
| 241 | 生产日志观察（2026-09-23） | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#生产日志观察2026-09-23) |
| 274 | 119、120 两集群日志观察（2026-09-24） | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#119120-两集群日志观察2026-09-24) |
| 314 | 四类 duration 的源码对照与 Execute 续取（2026-09-24） | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#四类-duration-的源码对照与-execute-续取2026-09-24) |
| 373 | psql 多语句计时的现场测试（2026-09-23） | [HashData 日志事实与证据边界](../../.project-wiki/contracts/log-evidence.md#psql-多语句计时的现场测试2026-09-23) |
| 400 | 已确认的日志获取方式 | [日志导入、来源与异常处理](../../.project-wiki/features/log-ingestion.md#已确认的日志获取方式) |
| 413 | 已确认的集群标识与日志来源映射 | [日志导入、来源与异常处理](../../.project-wiki/features/log-ingestion.md#已确认的集群标识与日志来源映射) |
| 436 | 完整文件导入、重复处理与重试 | [日志导入、来源与异常处理](../../.project-wiki/features/log-ingestion.md#完整文件导入重复处理与重试) |
| 457 | 已确认的变更及部分重叠文件处理 | [日志导入、来源与异常处理](../../.project-wiki/features/log-ingestion.md#已确认的变更及部分重叠文件处理) |
| 479 | 首期导入批次的完整性判定 | [日志导入、来源与异常处理](../../.project-wiki/features/log-ingestion.md#首期导入批次的完整性判定) |
| 499 | 导入异常的处理 | [日志导入、来源与异常处理](../../.project-wiki/features/log-ingestion.md#导入异常的处理) |
| 524 | 已确认的指纹生成失败处理 | [日志导入、来源与异常处理](../../.project-wiki/features/log-ingestion.md#已确认的指纹生成失败处理) |
| 550 | 已确认的 SQL 纳入与排除策略 | [训练资格、黑名单与排除时段](../../.project-wiki/contracts/training-eligibility.md#已确认的-sql-纳入与排除策略) |
| 576 | 已确认的黑名单能力与维护分工 | [训练资格、黑名单与排除时段](../../.project-wiki/contracts/training-eligibility.md#已确认的黑名单能力与维护分工) |
| 593 | 首版黑名单规则的后续梳理 | [训练资格、黑名单与排除时段](../../.project-wiki/contracts/training-eligibility.md#首版黑名单规则的后续梳理) |
| 608 | 已确认的单条语句与多语句批次 | [训练资格、黑名单与排除时段](../../.project-wiki/contracts/training-eligibility.md#已确认的单条语句与多语句批次) |
| 634 | 已确认的训练样本基本资格 | [训练资格、黑名单与排除时段](../../.project-wiki/contracts/training-eligibility.md#已确认的训练样本基本资格) |
| 658 | 故障与维护时段的人工排除 | [训练资格、黑名单与排除时段](../../.project-wiki/contracts/training-eligibility.md#故障与维护时段的人工排除) |
| 697 | 成功长耗时执行的处理 | [训练资格、黑名单与排除时段](../../.project-wiki/contracts/training-eligibility.md#成功长耗时执行的处理) |
| 714 | 已确认的基线分组维度 | [计时分类、分组与时间归属](../../.project-wiki/contracts/timing-and-grouping.md#已确认的基线分组维度) |
| 733 | 已确认的五类计时统计与 Parse／Bind 纳入 | [计时分类、分组与时间归属](../../.project-wiki/contracts/timing-and-grouping.md#已确认的五类计时统计与-parsebind-纳入) |
| 774 | 已确认的首期计时覆盖范围 | [计时分类、分组与时间归属](../../.project-wiki/contracts/timing-and-grouping.md#已确认的首期计时覆盖范围) |
| 791 | 已确认的 Execute 单次配对与调用分类统计 | [计时分类、分组与时间归属](../../.project-wiki/contracts/timing-and-grouping.md#已确认的-execute-单次配对与调用分类统计) |
| 826 | 首期 schema / search_path 处理边界 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#首期-schema--search_path-处理边界) |
| 843 | 已确认的首期绑定参数处理边界 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#已确认的首期绑定参数处理边界) |
| 862 | 已确认的业务常量归一化 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#已确认的业务常量归一化) |
| 890 | 已确认的其余非函数参数常量保留规则 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#已确认的其余非函数参数常量保留规则) |
| 910 | 已确认的原生参数与业务常量归并 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#已确认的原生参数与业务常量归并) |
| 934 | 首版函数参数归一化字典的后续维护 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#首版函数参数归一化字典的后续维护) |
| 952 | 已确认的执行配置与取数限制 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#已确认的执行配置与取数限制) |
| 967 | 已确认的普通格式与注释处理 | [SQL 结构指纹与归一化](../../.project-wiki/contracts/sql-fingerprints.md#已确认的普通格式与注释处理) |
| 982 | 已确认的首期主要统计指标 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#已确认的首期主要统计指标) |
| 993 | 三层统一的主要指标清单 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#各统计层次统一的主要指标清单) |
| 1030 | 已确认的耗时单位与统计空值口径 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#已确认的耗时单位与统计空值口径) |
| 1056 | 已确认的主要统计公式 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#已确认的主要统计公式) |
| 1099 | 已确认的排除计数口径 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#已确认的排除计数口径) |
| 1115 | 已确认的时间统计粒度 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#已确认的时间统计粒度) |
| 1141 | 已确认的时间口径 | [计时分类、分组与时间归属](../../.project-wiki/contracts/timing-and-grouping.md#已确认的时间口径) |
| 1171 | 已确认的首期执行时间估算假设 | [计时分类、分组与时间归属](../../.project-wiki/contracts/timing-and-grouping.md#已确认的首期执行时间估算假设) |
| 1196 | 已确认的训练窗口 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#已确认的训练窗口) |
| 1216 | 暂定的样本门槛与不足处理 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#暂定的样本门槛与不足处理) |
| 1246 | 跨天小时画像的独立样本门槛 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#跨天小时画像的独立样本门槛) |
| 1260 | 已确认的阶段与调用样本门槛 | [统计指标、训练窗口与样本门槛](../../.project-wiki/contracts/baseline-statistics.md#已确认的阶段与调用样本门槛) |
| 1291 | 已确认的 activity 基线参照选择 | [SQL 检索、Grafana 与历史展示](../../.project-wiki/features/sql-search-and-views.md#已确认的-activity-基线参照选择) |
| 1309 | 已确认的基线更新与历史版本保留 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的基线更新与历史版本保留) |
| 1358 | 已确认的归一化规则版本与更新处理 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的归一化规则版本与更新处理) |
| 1382 | 已确认的构建输入与配置固定 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的构建输入与配置固定) |
| 1410 | 已确认的同集群任务串行与忙时处理 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的同集群任务串行与忙时处理) |
| 1431 | 已确认的基线计算失败重试范围 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的基线计算失败重试范围) |
| 1454 | 已确认的五类计时统一版本与发布边界 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的五类计时统一版本与发布边界) |
| 1482 | 已确认的按集群独立管理基线版本 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的按集群独立管理基线版本) |
| 1504 | 已确认的首期基线发布检查 | [基线构建、版本与发布](../../.project-wiki/features/baseline-versions.md#已确认的首期基线发布检查) |
| 1536 | 已确认的 Python 运行约束 | [运行环境、组件与资源边界](../../.project-wiki/decisions/runtime-and-components.md#已确认的-python-运行约束) |
| 1552 | 已确认的 PostgreSQL 版本安排 | [运行环境、组件与资源边界](../../.project-wiki/decisions/runtime-and-components.md#已确认的-postgresql-版本安排) |
| 1570 | 已确认的首期操作入口 | [命令行与本地配置操作](../../.project-wiki/features/operator-cli.md#已确认的首期操作入口) |
| 1590 | 已确认的完整流程与分步使用 | [命令行与本地配置操作](../../.project-wiki/features/operator-cli.md#已确认的完整流程与分步使用) |
| 1612 | 已确认的首期组件分工 | [运行环境、组件与资源边界](../../.project-wiki/decisions/runtime-and-components.md#已确认的首期组件分工) |
| 1636 | 已确认的首期统一入口展示范围 | [SQL 检索、Grafana 与历史展示](../../.project-wiki/features/sql-search-and-views.md#已确认的首期统一入口展示范围) |
| 1659 | 已确认的 SQL 检索与分组选择 | [SQL 检索、Grafana 与历史展示](../../.project-wiki/features/sql-search-and-views.md#已确认的-sql-检索与分组选择) |
| 1678 | 已确认的基线版本与执行历史查看 | [SQL 检索、Grafana 与历史展示](../../.project-wiki/features/sql-search-and-views.md#已确认的基线版本与执行历史查看) |
| 1696 | 已确认的执行历史记录范围 | [SQL 检索、Grafana 与历史展示](../../.project-wiki/features/sql-search-and-views.md#已确认的执行历史记录范围) |
| 1715 | 已采用的开发基础 | [项目范围、资料来源与交付顺序](../../.project-wiki/decisions/project-scope.md#已采用的开发基础) |
| 1729 | 知识规范与模板 | [项目范围、资料来源与交付顺序](../../.project-wiki/decisions/project-scope.md#知识规范与模板) |

## 验证记录

| 检查 | 结果 |
| --- | --- |
| 原索引正文完整性 | 75／75 区块通过；从目标正文还原标题与相对链接后，可逐字重建迁移前索引，SHA-256 一致 |
| 独有操作与协作信息 | Python 环境观察、环境准备和质量命令完整保留；需求沟通节奏迁入项目范围 |
| 历史知识记录 | 原有全部变更条目保留，仅更新搬迁链接；新增历史性质说明及本次摘要 |
| 已有调查／验证报告 | 7 份原有报告正文保留，仅必要的索引锚点改为主题链接 |
| 本地链接与锚点 | 458 项通过，目标文件及章节均存在 |
| 原始需求快照 | Git blob SHA 仍为 `d5b8c6e4ef8d317110aec7737d49088d4a9308e4` |
| 补丁空白检查 | `git diff --check` 通过 |
| 完整离线质量入口 | `scripts/quality/check.sh` 通过；包含 Shell 检查、62 份 Markdown、84 个候选文件、15 个知识实体及五组流程回归 |

原索引 SHA-256：`75e2725396a3377f43fbd76ddd460cca46657e00eaf55a91b0940820162c98e7`。
迁移清单保留每节原始标题、正文长度及迁移前后 SHA-256，可用于核对本次搬迁，
不是禁止后续按新确认修改需求的冻结门禁。

重组完成时，新增页面尚未加入实际 Git 暂存区，因此当时的完整质量检查在临时 Git 副本中纳入现有跟踪文件
及本次候选文档执行，复用已有本地工具；未修改实际暂存区，也未安装工具或提交／推送。
验证副本包含本轮之前已存在的未提交文档与报告。检查结果属于本次本地自检，
不作为产品实现、真实数据库容量、远程 CI 或独立评审证据。
