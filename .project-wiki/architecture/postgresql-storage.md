---
id: architecture.postgresql-storage
type: architecture
status: active
owners:
  - sql_apm/storage/
  - scripts/db/
  - tests/database/
  - docs/design/postgresql-storage.md
updated: 2026-10-02
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/29
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/27
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/25
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/21
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/17
    status: current
  - path: docs/reports/approximate-storage-2026-09-29.md
    status: current
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

当前物理结构版本 1.6.0 承接离线逻辑契约 1.0.0，保留初版 DDL 及显式升级路径。
交付范围为项目账号、库、schema、表、约束、索引、结构版本、MPP 专属表改名迁移及临时实例验证；
新增独立近似观察持久化结构；指纹模块已有独立实现，Issue #18 已提供[导入与写入接口](../../docs/design/log-ingestion.md)，③统计由 #25 实现；发布编排见 #29，Grafana 尚未实现。

## Source Of Truth

用户于 2026-09-26 核对初始化、统计明细及首版普通表方案后授权实施；
随后确认各系统独立统计表，并核对 14 张 MPP 专属表前缀、2 张公共表引用及版本升级后指示“开始调整”。
2026-09-29 用户确认 #17 在导入前补齐近似观察结构，#18 同次写入可靠与近似结果，观察统计另行处理。
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
- 各系统统计结果独立保存，共享适用计算代码及构建／发布框架。当前 56 张父表／普通表（不含动态分区）中
  26 张 MPP 专属表及其索引／约束使用 mpp_ 前缀，其他系统接入时再交付自身结构。
  原名清单、公共表残留 MPP 关联及边界见物理设计；不引入公共统计／分组表。
- 冻结的 1.0.0／1.1.0／1.2.0／1.3.0／1.4.0／1.5.0 DDL 保持字节不变；upgrade 先完整校验各步旧结构及摘要，
  一笔事务完成 1.0.0 → 1.1.0 → 1.2.0 → 1.3.0 → 1.4.0 → 1.5.0 → 1.6.0 或中间版本起步的升级，保留旧版本时间与业务数据。
  新库直接登记 1.6.0；升级与新库使用同一目标 catalog 检查。
  需要维护窗口停写及串行操作，DDL 锁等待 5 秒，失败回滚后可重试，无自动降级。
- 近似观察以独立规则、原字节、结果和证据／事件关联表保存。bytea 保留残片与非法编码，
  JSON 文本保留近似表示中的转义；规则和结构拒绝原因参与复用，事件次数不合并。
  可靠指纹拒绝 approx:，Group／Decision 外键不能引用近似结果。具体字段与写入方责任见物理设计。
- 原文、执行、解释、规则、输入及构建身份分开；引用结构保证关键集群和规则上下文。
  完整内容复用与导入解释由 #18 实现；②快照、原文级规则缓存和统一数据库判定由 #21 实现，
  不逐条持久化 Decision；发布原子性由 #29 共用任务连接实现。
- 统计每行对应构建、分组和时间桶，17 指标为 numeric 列；
  空值原因及排除原因用结构化 JSONB，零样本／零分母与真实零分开；
  门槛细节按 Build 的封存配置与公式版本由 `mpp_statistic_sufficiency` 派生，不逐行物理保存。
- 已知分组五层覆盖按窗口与实际统计推导计算键及空桶键，构建级计数核对丢行。
  1.4.0 两张结果父表按集群＋构建月份分区，按构建与分组索引；留存清理另行设计。
- 重跑比较实际 catalog 与事务内创建的预期空结构，并核对脚本 SHA-256。
  项目 schema 专用于版本化对象；兼容表子集可补全，结构漂移明确失败。
  引用表及被引用表的外键内部触发器须保持默认 O 模式，D／R／A 均拒绝；
  仅核对系统目录，不自动启用触发器，也不证明异常期间写入的历史行有效。
- 通用构建核心允许非 SQL profile 不具备 normalization_id；当前 HashData
  执行／指纹／分组／统计结构不冒充未来所有系统的共同模型。

1.3.0 新增规则／原文缓存、输入解释清单和配置补充表；快照及缓存有不可变触发器。
数据库函数是唯一资格推导，其定义与触发器启用状态均进入结构检查；
[设计](../../docs/design/training-decisions.md)说明物理映射与成本。确认来源为
[Issue #21](https://github.com/shenxg13/sql-apm/issues/21)，旧 Decision 表保留但首期不写入。

1.4.0 由 [#25](https://github.com/shenxg13/sql-apm/issues/25) 实现两张结果表的分区与瘦身；
新增分区登记和构建分组关系表，Build 保存分区引用与脱敏诊断。旧结果表非空则拒绝迁移，
不自动删除。每次构建先自动创建所需月份分区；catalog 同时核对分区边界、父子关系、叶表、索引及门槛派生函数定义。
[统计设计](../../docs/design/baseline-statistics.md)说明计算与资源成本。

1.5.0 由 [#27](https://github.com/shenxg13/sql-apm/issues/27) 增加独立观察分组、构建关系和统计表。
正式 Group／Statistic 与观察表通过不同外键隔离；观察分组只能引用非空 available 近似结果。
沿用同一集群／构建月份登记，新增第三张分区父表，不另存观察覆盖索引。
1.4.0→1.5.0 为增量迁移，已有统计、覆盖、分区和历史 receipt 均保留；
[统计设计](../../docs/design/baseline-statistics.md#观察统计实施计划与边界)记录未知时刻和身份的诊断归属。

### 编排结构 1.6.0（2026-10-01）

[Issue #29](https://github.com/shenxg13/sql-apm/issues/29)确认删除覆盖表；冻结 1.5.0 DDL 并增加
1.5.0→1.6.0 迁移。`mpp_build_layer_count` 保存两种结果各五层行数及分组数，
`mpp_coverage(build_id, observation, group_id)` 从窗口和统计按需返回计算键／空键。
Task 增加时间、阶段耗时、snapshot／statistics 模式及 snapshot／check 阶段。
共用会话锁及原子发布由[编排设计](../../docs/design/build-publication.md)负责。

新月份以独立空表加 ATTACH 创建叶分区，普通读取无需等待父表 ACCESS EXCLUSIVE 锁；
构建前确保当月，次月预建遇共享分组表锁忙时跳过，后续构建再试；分区 DDL 单独提交。
当月首次创建仍可能等待另一集群构建事务结束，锁及发生时机见[编排设计](../../docs/design/build-publication.md#覆盖分区与成本)。
正式／观察两个分区父表继续按集群及构建月份分区。
迁移保留统计行，按旧结果回填构建层计数；在当前无生产部署的边界内删除覆盖表。
历史任务的新增开始时间只能表示迁移时间，结束时间保持未知，不伪造旧任务阶段耗时。

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

导入事务与来源解析见 #18 设计；发布编排见 #29；留存清理由后续工作落实。
