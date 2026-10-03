---
id: decision.runtime-and-components
type: decision
status: active
owners:
  - .project-wiki/decisions/runtime-and-components.md
updated: 2026-10-04
sources:
  - path: docs/reports/kylin-read-access-2026-10-04.md
    status: current
  - path: docs/reports/kylin-manual-validation-2026-10-04.md
    status: current
  - path: docs/reports/slim-release-kylin-trial-2026-10-03.md
    status: current
  - path: docs/reports/slim-release-preparation-2026-10-03.md
    status: current
  - path: docs/reports/kylin-offline-deployment-2026-10-02.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/31
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/18
    status: current
  - path: .project-wiki/log.md
    status: historical
  - path: docs/reports/knowledge-reorganization-2026-09-25.md
    status: current
  - path: docs/reports/python-environment-2026-09-25.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/7
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

### 已确认的 Kylin 演练与项目自带解释器（2026-10-02）

来源：[Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 的已确认契约及范围变更评论。
Python 3.9.5 解释器属于正式部署流程，从离线源码包构建并安装在项目目录，
不依赖主机已有的 Python，也不使用 Alma 构建目录作为可搬运发行包。
若将来希望改用生产已有解释器路径，另开 follow-up Issue，不恢复此前的路径假设。

本轮先在 Kylin V10 SP2 x86_64 演练机执行：Python、PostgreSQL 17.10、程序、日志和
离线包均位于 `/data/sql-apm/`；PG 实例使用专用 postgres 系统用户及 pg_ctl，
项目账号经本机 socket/SCRAM 登录，DBA 工具使用同一项目账号和受限客户端网段。
实例不做 systemd 托管、不启用 TLS，凭据仅放程序目录外的 0600 文件。
编译依赖根包及完整依赖由演练机初始状态的 yum 源收集、检查 RPM 签名并交付离线包；
正常手册先尝试已有 yum 源，不可用时禁用网络源，仅启用 file:// 离线仓库。

上述为已确认部署决定；实际操作、验证进展和适用限制见
[部署手册](../../docs/runbooks/kylin-offline-deployment.md)和
[验证报告](../../docs/reports/kylin-offline-deployment-2026-10-02.md)。
演练已验证该平台能在项目目录离线构建 Python 3.9.5、PostgreSQL 17.10，并通过
66 项普通测试、266 项数据库检查和 31 项发布检查；该事实不等同于全部演练验收已完成。
首次四进程首批记录到 891 次指纹归一化超时，计数与 Alma 不一致；
部署手册采用单解析进程后，九任务的业务计数、成功导入次数和版本链全部通过 Alma 核对。
中途为 SSD 迁移产生的一次人工中断完整保留并单独核对，不将总尝试数宣称为完全相同。
这些结论来自实际任务验证，不能由环境自检推定业务等值。
用户将演练虚拟机从机械盘迁移到 SSD 后，在同一程序、单解析进程及原数据库参数下，
只读采样与同文件导入实测均显示读取等待显著减少；数据和比较限制由验证报告保存。
该运行观察不新增生产存储要求，也不是其余状态严格相同的硬件基准测试。
用户在实施试跑后恢复快照并独立从头执行，DBA 工具验证须由用户完成。
这不核实生产内网源、不同初始系统上的 RPM 完整性、120 三十天首批成本或 aarch64。
本节更新后，早期条款中“Kylin 留待专项”的表述仅指当时阶段，不覆盖本轮已确认安排。

### 已确认的执行账号读取权限（2026-10-04）

来源：用户明确要求“sfmon用户需要能够至少只读APM_ROOT下所有的目录和文件，包括private目录”，
已同步至 [Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 的范围和 K6b。
sfmon 无需 sudo 即可列出、进入 APM_ROOT 下所有目录并读取全部普通文件，包含 private、
pgdata；已有属主写权限保留。private／pgpass 由 sfmon 拥有，仍为 0700／0600。
postgres 数据保持原属主，以命名读取 ACL、默认 ACL 和 PG 组读取模式支持后续新数据文件，
不增加 sfmon 对 PGDATA 的写权限。socket 目录不可写，通过 sfmon 组继承支持锁文件读取，
不设置只读默认 ACL，以免阻断 socket 连接；连接继续执行既有数据库认证。

目标机已验证全部目录和普通文件可读、PG 新文件继承、重启后 socket／TCP 正常，
原九个发布版本保持不变。实际计数、socket ACL 失败及修正见
[权限修正报告](../../docs/reports/kylin-read-access-2026-10-04.md)，操作方法见
[部署手册](../../docs/runbooks/kylin-offline-deployment.md#71-已有部署补齐读取权限)。
外部工具显式收紧权限或移入文件后需复查，不以默认 ACL 保证任意外部操作后的可读性。

### 已确认的精简预发布与离线手册（2026-10-03）

来源：[Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 的补充确认。
首版固定为 `v0.1.0` GitHub 预发布，不用于生产；#33 将修改落库标识，下一版需要
全新数据库，0.x 预发布之间不保证数据库兼容。首个非预发布版本须待 #33、
版本保留与清理完成，且四进程导入问题已有结论后再确定。项目许可证选择暂缓。

GitHub Release 的自定义附件仅包含精简程序包和 SHA-256 校验文件；完整离线包包含
环境依赖及独立验收资源，继续内网交付。程序包保留必要规则、SQL／迁移、部署工具
和规则随附的 PostgreSQL 许可证；开发与验收材料不装入 `app`。
Markdown 是手册唯一维护原稿，构建期生成单文件离线 HTML 随两类包交付；
版本和摘要锁定的 HTML 工具只在开发机构建环境使用，不增加运行依赖。
自动检查覆盖资源请求、锚点及代码文本，浏览器阅读／复制／搜索／打印由用户确认。

候选包产品文件与原试跑同路径摘要逐一相同时可继承九任务结果；实施方仍须在用户
恢复快照后重走安装、自检、初始化、远程连接及 119 首批，再由用户恢复一次并独立执行。
SSD 上四进程首批专门记录超时、分组、基准和资源；零超时且等值才支持成功结论，
否则保留现场，重置后单进程重跑并经用户确认创建后续 Issue，本 Issue 不修改默认并发或超时。
无论试验结果如何，手册九任务保持单进程且每次导入核对超时计数为 0。
恢复快照后的精简候选包试跑在用户报告的 SSD 环境仍出现四进程超时：882 次，
正式分组 51,152，比 Alma 少 114 个；六项发布检查和版本链仍通过。
全程 CPU I/O 等待仅 0.067%，不能把换 SSD 或发布成功当作归一化完整性的保证。
这些是运行观察，不足以确认全部根因；单进程复跑和性能对照由
[候选包试跑报告](../../docs/reports/slim-release-kylin-trial-2026-10-03.md)保存。

实现入口见[程序发布操作说明](../../docs/runbooks/program-release.md)。
目标机候选包试跑前须先获用户明确确认；打标签和发布在评审合并后另获用户确认。
上述是已确认交付与验证边界；开发机检查不能代替 Kylin 试跑和人工验收。
[开发机准备记录](../../docs/reports/slim-release-preparation-2026-10-03.md)保存候选包、
66 个产品文件等值、独立验收与 HTML 自动检查证据，以及试跑前暂停点。
用户随后确认恢复快照并允许试跑；候选包已实际重走离线系统包、两项源码编译、
独立目录的 66／266／31 项自检、初始化及受限 TCP 验证，55 文件摘要一致。
用户回复 Chrome 离线人工检查正常。保留四进程失败现场后，新实例单进程首批零超时，
正式分组 51,266，全部 Alma 计数和版本链相同；全流程从原机械盘 51.0 分钟降到 44.6 分钟，
改善主要来自数据库快照和统计构建。

2026-10-04 用户独立执行后，九任务业务结果、55 文件输入摘要和版本链再次核对通过，
全部零归一化超时；正常 yum 源事务及后续两项源码构建有记录，用户确认本轮 pgAdmin 成功。
但手册传输示例指向保留的早期候选目录，实际使用 `d49d94b`，与最终候选 `151dfcb`
的运行代码和业务资源相同，手册、版本信息及验收资源不同；普通测试仅 62 项，缺少
四项统计测试。因此保留九任务证据，最终候选对齐和完整 66 项普通测试仍需补验，
不能直接宣称完整人工验收已通过。详见[独立验证核对](../../docs/reports/kylin-manual-validation-2026-10-04.md)。
四进程性能后续问题已由用户在另一需求会话确认创建为
[#34](https://github.com/shenxg13/sql-apm/issues/34)，不再等待创建确认；本轮未实施该优化。

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
  和磁盘增长仅有[55 文件本地导入实测](../../docs/reports/log-ingestion-2026-09-29.md)，
  不据此声称该生产配置已满足完整流程或长期留存容量。

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
  保持原版本；环境准备当时未安装业务依赖，不代表 Issue #1 或生产部署已经完成。
- 2026-09-27，可靠归一化实现将 pglast 7.18 锁定为运行依赖，根目录 `requirements.txt`
  记录 CPython 3.9 Linux x86_64 wheel 哈希，已在 Python 3.9.5 离线验证；SQLGlot 仍只用于
  候选解析实验。[归一化接口](../../docs/design/sql-normalization.md)记录安装及调用边界，
  未据开发环境成功推定生产平台兼容或完成部署。
- 环境准备阶段的实现和可选模块边界见[环境验证记录](../../docs/reports/python-environment-2026-09-25.md)。

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

2026-09-29，#18 锁定 `psycopg2-binary==2.9.10` 的 CPython 3.9 Linux x86_64 wheel，
SHA-256 为 `6b269105e59ac96aba877c1707c600ae55711d9dcd3fc4b5012e4af68e30c648`。
该版本提供兼容 Python 3.9 的预编译 libpq 驱动，避免增加本地编译工具链；官方依据见
[PyPI 文件](https://pypi.org/project/psycopg2-binary/2.9.10/)和
[2.9.10 发布记录](https://www.psycopg.org/docs/news.html#what-s-new-in-psycopg-2-9-10)。
实际验证使用 Python 3.9.5 与 PostgreSQL 17.10 私有临时实例；生产部署与 Kylin 离线适配
仍未完成。驱动只连接 Baseline 存储库，不执行源日志 SQL。

关联条款：[已确认的当前开发环境与生产部署安排](runtime-and-components.md#已确认的当前开发环境与生产部署安排)。

### 已确认的数据库初始化与单账号方案

- 确认日期：2026-09-26；来源状态：current。
- 来源：用户要求后续数据库结构 Issue 包含“创建数据库，schema，用户，角色，
  权限这一套结构”，随后明确“先不要创建issue，我们先讨论需求”，并确认
  “全部使用一个账户即可，这个账户既是schema owner，又是app用户，也是admin
  用户，用户，schema，角色和权限采用最简化设计”。
- 用户进一步明确“把初始化数据库和创建相关物理结构放到这个issue里统一实现”。
  项目数据库、schema、用户、角色及权限的初始化，与业务表、字段、主外键、
  唯一性及其他约束、必要索引等物理结构，由同一个后续 Issue 统一实现和验收。
  物理结构承接[离线逻辑数据契约](../contracts/offline-data-contract.md)，不另拆
  数据库初始化与建表任务。用户确认下述创建前边界后，已通过
  [Issue #7：数据库初始化与物理结构实现](https://github.com/shenxg13/sql-apm/issues/7)
  落实完整任务契约；执行状态以在线标签为准，创建 Issue 时数据库对象尚未实现。
- 项目统一使用一个数据库账号，同时承担 schema owner、应用运行及管理职责。
  后续查询／Grafana 访问也沿用同一项目账号；不采用此前建议的结构维护、
  程序读写、查询只读三类账号拆分。
- 用户与角色采用同一个可登录角色表达，不额外建立权限分组或角色继承体系；
  schema 组织和授权采用满足项目功能的最简方案，不把角色分离作为验收要求。
  PostgreSQL 的用户／角色关系见[官方角色说明](https://www.postgresql.org/docs/17/sql-createrole.html)。
- 用户在询问创建 Issue 前还需确认的内容后，对四项边界及默认命名回复“确认”。
  数据库、schema、账号默认均为 `sql_apm`，允许配置；具体表拆分、字段类型、
  索引及脚本组织属于实施设计选择，不要求用户逐项预先固定。
- 初始化从已有可用的 PostgreSQL 17 实例开始，首次账号及数据库创建由已有
  PostgreSQL 管理员引导完成；同一项目账号拥有数据库、schema 及项目对象，
  负责后续项目管理和应用访问，无需实例级超级用户权限。
- 初始化须可安全重跑并保留已有数据；遇到不兼容的同名对象明确报错，不静默
  接管或删除重建，部分失败后允许修正原因并重试。
- 同一交付包含物理设计、初始化脚本、结构版本记录及操作说明；在 disposable
  实例验证初始化、实际项目账号访问、约束、重跑及失败恢复。Python 业务读写
  接口、导入和统计流程由后续任务交付，实例安装及生产部署不纳入本任务。
  临时实例创建、停止与清理属于验证工具；环境探针不代替本任务实施验收。

2026-09-26 按本条已确认范围实现[初版物理结构与初始化](../architecture/postgresql-storage.md)，
初版采用结构版本 1.0.0，随后按用户确认升级为 MPP 命名的 1.1.0，
保留旧 DDL 及显式迁移，并在 PG17 disposable 实例验证。该交付不表示生产实例已部署，
也不表示 Python 业务读写、导入、统计或 Grafana 已实现。

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
