---
id: architecture.postgresql-storage
type: architecture
status: active
owners:
  - sql_apm/storage/
  - scripts/db/
  - tests/database/
  - docs/design/postgresql-storage.md
updated: 2026-09-26
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/7
    status: current
  - path: sql_apm/storage/schema.sql
    status: current
  - path: docs/reports/postgresql-storage-2026-09-26.md
    status: historical
  - path: docs/reports/mpp-storage-migration-2026-09-26.md
    status: current
  - path: docs/reports/postgresql-storage-r1-remediation-2026-09-26.md
    status: current
related:
  - contract.offline-data-contract
  - decision.runtime-and-components
  - architecture.source-layout
  - contract.sql-storage
  - feature.baseline-versions
confidence: high
---

# PostgreSQL 存储结构与初始化

## Summary

当前物理结构版本 1.1.0 承接离线逻辑契约 1.0.0，保留初版 DDL 及显式升级路径。
交付范围为项目账号、库、schema、表、约束、索引、结构版本、MPP 专属表改名迁移及临时实例验证；
业务导入、指纹、统计、写入接口、发布编排和 Grafana 尚未实现。

## Source Of Truth

用户于 2026-09-26 核对初始化、统计明细及首版普通表方案后授权实施；
随后确认各系统独立统计表，并核对 14 张 MPP 专属表前缀、2 张公共表引用及版本升级后指示“开始调整”。
在线 Issue 负责交付契约；[DDL](../../sql_apm/storage/schema.sql)负责实际结构，
[物理设计](../../docs/design/postgresql-storage.md)负责对象映射与数据库／应用责任，
[初版记录](../../docs/reports/postgresql-storage-2026-09-26.md)与
[MPP 迁移记录](../../docs/reports/mpp-storage-migration-2026-09-26.md)分别说明实际证据及限制；
[R1 整改记录](../../docs/reports/postgresql-storage-r1-remediation-2026-09-26.md)补充外键内部触发器的兼容性验证。

## Contracts

- 已有 PG17 实例，由已有管理员首次引导，一个普通项目角色拥有库、schema 及对象。
  三个名称默认 sql_apm，可分别配置；初始化保留已有密码和数据，不接管不兼容对象。
- 管理员 bootstrap 与项目账号 schema 阶段分开。库内 DDL／结构版本同事务，
  建库在事务块外；失败后按完成阶段修正重跑。
- 各系统统计结果独立保存，共享适用计算代码及构建／发布框架。当前 41 张表中
  14 张 MPP 专属表及其索引／约束使用 mpp_ 前缀，其他系统接入时再交付自身结构。
  原名清单、公共表残留 MPP 关联及边界见物理设计；不引入公共统计／分组表。
- 冻结的 1.0.0 DDL 保持字节不变；upgrade 先完整校验旧结构及摘要，在一笔事务中
  改名并登记 1.1.0，保留旧版本时间与业务数据。新库及升级库按同一目标 catalog 检查。
  需要维护窗口停写及串行操作，DDL 锁等待 5 秒，失败回滚后可重试，无自动降级。
- 原文、执行、解释、规则、输入及构建身份分开；引用结构保证关键集群和规则上下文。
  完整内容复用、解释可靠性、不可变快照和发布原子性仍须后续应用实现。
- 统计每行对应构建、分组和时间桶，17 指标为 numeric 列；
  空值原因、门槛细节及排除原因用结构化 JSONB，零样本／零分母与真实零分开。
- 已知分组五层覆盖使用显式计算键及空桶键；稀疏存储不表示漏算。
  首版普通表，按构建与分组索引；分区、真实容量及留存期限待实际数据评估。
- 重跑比较实际 catalog 与事务内创建的预期空结构，并核对脚本 SHA-256。
  项目 schema 专用于版本化对象；兼容表子集可补全，结构漂移明确失败。
  引用表及被引用表的外键内部触发器须保持默认 O 模式，D／R／A 均拒绝；
  仅核对系统目录，不自动启用触发器，也不证明异常期间写入的历史行有效。
- 通用构建核心允许非 SQL profile 不具备 normalization_id；当前 HashData
  执行／指纹／分组／统计结构不冒充未来所有系统的共同模型。

## Workflows

执行身份、认证前提、精确命令、版本演进和阶段恢复见
[初始化操作说明](../../docs/runbooks/database-initialization.md)。
新增物理对象先对应逻辑契约，明确 CHECK／外键与应用职责，并补充合成存储用例。

## Failure Modes

- 跳过 catalog 校验直接运行 IF NOT EXISTS，静默接受同名错误结构。
- 只核对外键定义却忽略内部触发器状态，让已停用的外键被误判为有效结构。
- 把 SQL 摘要或结构指纹当作实际执行身份，丢失真实重复执行。
- 将合法 NULL 或样本不足当作零，将五层样本数量相加。
- 修改既有 DDL 字节后仍使用相同已部署结构版本。
- 把合成存储验证当作生产容量、统计公式或完整业务流程验收。

## Update Rules

实际结构变化同步物理设计、初始化／恢复说明、测试和本主题；
业务规则变化回到其所属主题及在线 Issue，不复制原有需求全文。
索引只维护导航，日志只记录摘要；原始需求快照与生产输入保持原样。

## Open Questions

业务写入事务、来源解析、完整发布编排、真实容量及后续留存／分区迁移由后续工作落实。
