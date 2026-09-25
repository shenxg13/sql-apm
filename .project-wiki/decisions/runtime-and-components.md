---
id: decision.runtime-and-components
type: decision
status: active
owners:
  - .project-wiki/decisions/runtime-and-components.md
updated: 2026-09-25
sources:
  - path: .project-wiki/log.md
    status: historical
  - path: docs/reports/knowledge-reorganization-2026-09-25.md
    status: current
  - path: docs/reports/python-environment-2026-09-25.md
    status: current
related:
  - decision.project-scope
  - contract.log-evidence
  - feature.sql-search-and-views
confidence: high
---

# 运行环境、组件与资源边界

## Summary

保存用户确认的运行约束、现网事实和组件分工；资源参考不是硬门禁。
准备环境、选择依赖或讨论组件职责时阅读。

## Source Of Truth

以下保留原知识索引各节的用户确认、日期、来源状态、证据限制和待定事项。
这些条款定义目标或事实，不表示相关产品功能已经实现；较早条款中的待定表述
应结合明确链接的后续确认阅读，不能覆盖后续已确认规则。
[知识变更记录](../log.md)用于追溯；涉及 Issue 时以其在线正文和评论核对任务契约。

## Contracts

### 已确认的当前开发环境与生产部署安排

- 确认日期：2026-09-25。
- 来源：用户说明生产环境为 Kylin V10 SP2、完全离线，要求当前先以可联网的
  Alma 环境为准，后续通过专门 Issue 实施 Kylin 离线安装方案。
- 来源状态：current；已确认环境事实与阶段边界。2026-09-25 已完成下述 Python
  开发环境准备，数据库和展示组件仍未部署。
- 当前开发及验证以现有 Alma 环境为准。2026-09-25 只读检查
  `/etc/os-release` 得到 **AlmaLinux 9.8 (Olive Jaguar)**，`uname -m` 得到
  **x86_64**；当时仅由用户提供可联网条件，后续 Python 环境准备已验证官方
  下载源访问及 HTTPS 证书校验，详见下述环境记录。
- Python **3.9.5**、项目本地 `.venv/` 和 Baseline 存储 **PostgreSQL 17** 的
  已确认安排不变，不因采用 Alma 而改用系统默认 Python 版本。
- 生产环境 **Kylin V10 SP2、完全离线** 作为用户提供的事实记录。其 CPU 架构
  尚未提供，不能由开发机架构推定；相关生产安装细节留待后续专项工作核实。
- Kylin 离线安装及适配方案由用户后续通过专门 Issue 实施，不作为当前 Alma
  开发与离线基线交付的前置条件。本次不创建该 Issue，不开展离线打包或生产部署。
- Alma 上的开发验证不代表已经验证 Kylin 兼容性；具体依赖、部署方式和各项
  验收仍在相应实施任务中落实。

### 已确认的现网产品版本

- 确认日期：2026-09-23；来源状态：current，用户明确指出版本已记录在
  [原始需求文档](../raw/sql-baseline.md)的“已确认现网环境”中，本次已核对原文。
- PostgreSQL **9.4.26**、Greenplum Database **6.20.3**、HashData Warehouse
  **3.13.13**。
- 以上为用户指明的现网版本事实，不表示原始文档中的其他设计建议同时获确认。
  尚未取得与该 HashData 构建对应的日志源码映射。用户随后提供下述 psql
  多语句测试的终端照片，支持该案例按整批计时；不能将其或 PostgreSQL 的
  通用行为推广为生产日志中每条 duration 的计时范围。

关联条款：[psql 多语句计时的现场测试（2026-09-23）](../contracts/log-evidence.md#psql-多语句计时的现场测试2026-09-23)；[四类 duration 的源码对照与 Execute 续取（2026-09-24）](../contracts/log-evidence.md#四类-duration-的源码对照与-execute-续取2026-09-24)。

### 已知的生产资源参考

- 确认日期：2026-09-25；来源状态：current，用户提供的生产环境资源信息。
- 用户原话：“生产环境至少可以提供8c16g的虚拟机，500gb磁盘，这个不是硬门禁”。
- 按虚拟机配置理解为至少可提供 8 核 CPU（vCPU）、16 GB 内存和 500 GB 磁盘，
  作为可调整的生产规划参考，不固定为部署上限、强制配置或性能验收硬门槛。
- 以上是可提供资源的用户说明，尚未分配或独立核验服务器，也未完成项目部署。
  Python 程序、PostgreSQL 与 Grafana 的具体部署位置及资源分配仍待落实。
- 每日导入及默认 30 天窗口重建的期望完成时长尚未指定；实际耗时、峰值内存
  和磁盘增长尚未测量，不据此声称该配置已满足完整数据规模或长期留存容量。

### 已确认的 Python 运行约束

- 确认日期：2026-09-22。
- 来源：用户在本项目需求讨论中的明确确认，按原话记录：

  > 现网的基线是3.9.5，所有服务器都是这个版本，我们后续需要在项目的venv中建立一个3.9.5的环境

- 来源状态：current；这是已确认的环境约束。现网服务器版本为用户提供的事实，
  尚未逐台核验。
- 项目采用 Python，运行及兼容验证目标为精确版本 **3.9.5**。
- 使用仓库根目录的 `.venv/` 隔离项目依赖，由 Python 3.9.5 解释器创建。
- 依赖选型须兼容 Python 3.9.5，实际依赖版本在实现时验证并锁定。
- 2026-09-25 用户暂停 Issue #1 的业务实施，明确要求先完成 Python 环境准备。
  已在本机从官方源码构建 Python 3.9.5 至 `var/python-3.9.5/`，重建 `.venv/`，
  安装固定版本包工具，完成标准库、HTTPS 和包构建安装的有界验证。系统 Python
  保持原版本，业务依赖尚未安装；这不代表 Issue #1 或生产部署已经完成。
- 当前实现和可选模块边界见[环境验证记录](../../docs/reports/python-environment-2026-09-25.md)。

准备环境的说明见[本地开发说明](../../docs/runbooks/local-development.md)。

关联条款：[已确认的当前开发环境与生产部署安排](runtime-and-components.md#已确认的当前开发环境与生产部署安排)。

### 已确认的 PostgreSQL 版本安排

- 确认日期：2026-09-22。
- 来源：本项目需求讨论中，用户对“Baseline 存储采用 PostgreSQL 17，
  PostgreSQL 9.4.26 留作后续实时采集兼容测试”的建议回复“确认”。
- 来源状态：current；这是已确认的数据库选型，尚未部署实例。
- Baseline 数据库采用 **PostgreSQL 17**，用于保存执行记录、SQL 指纹和基线结果。
- 开发与正式部署的 Baseline 数据库保持相同主版本；具体 17.x 补丁版本在部署时
  选定并记录。
- 当前先准备一个 Baseline 存储实例；后续按需准备独立的 **PostgreSQL 9.4.26**
  实例，测试旧版查询状态、开始时间和锁等待等基础接口。
- 普通 PostgreSQL 的验证不能覆盖 GP/HashData 的特有字段和行为；相关字段映射
  使用现场字段定义及脱敏样本验证，后续补充真实环境联调。
- Python 仍固定为 3.9.5；数据库驱动需同时满足 Python 兼容约束和对应数据库的
  连接要求，具体依赖版本另行验证并锁定。

部署方式、具体补丁版本和数据库驱动尚待后续落实。

关联条款：[已确认的当前开发环境与生产部署安排](runtime-and-components.md#已确认的当前开发环境与生产部署安排)。

### 已确认的首期组件分工

- 确认日期：2026-09-22。
- 来源：用户指出基线构建状态属于平台自身监控，activity 汇总已有 HashData
  监控指标覆盖，并对“首期不引入 Prometheus”的组件分工建议回复“确认”。
- 来源状态：current；已确认职责边界，尚未完成业务实现和组件部署。

| 组件 | 首期职责 |
| --- | --- |
| 自研 Python 3.9.5 程序 | 日志解析、SQL 指纹和 Baseline 计算 |
| PostgreSQL 17 | 保存执行明细、SQL 指纹和基线结果 |
| Grafana | 直接查询 PostgreSQL，展示基线总览、单条 SQL 历史执行曲线、耗时分布与基线对比 |
| HashData 现有监控 | 按用户说明继续复用已有的 activity 和数据库运行指标监控 |

首期不引入 Prometheus，也不要求为首期业务建设 Prometheus 指标出口。
后续出现统一指标接入或集中监控的明确需求时再评估。
平台自身的导入进度、构建成功或失败等状态先通过任务记录和日志保留，
必要时由 Grafana 展示。

后续基于自建基线的 SQL 异常判断由自研部分完成，异常事件保存到 PostgreSQL。
通知需求和实现方式另行确认；Grafana Alerting 是可评估方案，尚未选定。
原始资料中 Prometheus / Alertmanager 的建议保留来源语境，不自动成为首期依赖。
Grafana 的版本、部署方式和具体面板设计尚待后续落实。

关联条款：[已确认的首期统一入口展示范围](../features/sql-search-and-views.md#已确认的首期统一入口展示范围)。

## Workflows

按任务涉及的边界补读：

- [项目范围、资料来源与交付顺序](project-scope.md)。
- [HashData 日志事实与证据边界](../contracts/log-evidence.md)。
- [SQL 检索、Grafana 与历史展示](../features/sql-search-and-views.md)。

## Failure Modes

不能把候选建议、历史观察或待验证实现当成已确认且已交付的行为；
本页各节保留的限制及关联条款共同约束相应任务。

## Update Rules

本主题的确认内容只在本页维护；其他入口保留链接。跨主题变更同步实际受影响的
条款，按[知识维护方法](../methods/knowledge-maintenance.md)记录来源与变更。

## Open Questions

具体依赖、部署细节和性能数据尚待落实；Kylin 离线安装由后续专项工作承接。各节已有待定说明继续有效。
