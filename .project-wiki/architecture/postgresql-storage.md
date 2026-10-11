---
id: architecture.postgresql-storage
type: architecture
status: active
owners:
  - sql_apm/storage/
  - scripts/db/
  - tests/database/
  - docs/design/postgresql-storage.md
updated: 2026-10-11
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/52
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/54
    status: current
  - path: docs/reports/python313-upgrade-2026-10-08.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/47
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/43
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/35
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/33
    status: current
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

当前物理结构版本 1.11.0 承接离线逻辑契约 1.0.0，保留初版 DDL 及显式升级路径。
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
- 各系统统计结果独立保存，共享适用计算代码及构建／发布框架。当前 57 张父表／普通表（不含动态分区）中
  27 张 MPP 专属表及其索引／约束使用 mpp_ 前缀，其他系统接入时再交付自身结构。
  原名清单、公共表残留 MPP 关联及边界见物理设计；不引入公共统计／分组表。
- 冻结的 1.0.0／1.1.0／1.2.0／1.3.0／1.4.0／1.5.0／1.6.0／1.7.0／1.8.0／1.9.0／1.10.0 DDL 保持字节不变；upgrade 先完整校验各步旧结构及摘要，
  一笔事务完成 1.0.0 → 1.1.0 → 1.2.0 → 1.3.0 → 1.4.0 → 1.5.0 → 1.6.0 → 1.7.0 → 1.8.0 → 1.9.0 → 1.10.0 → 1.11.0 或中间版本起步的空库升级，保留旧版本登记时间；1.7.0／1.8.0／1.9.0／1.10.0 可带数据升级，更早非空库须重建。
  新库直接登记 1.11.0；升级与新库使用同一目标 catalog 检查。
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
  1.4.0 两张结果父表按集群＋构建月份分区，按构建与分组索引；留存清理见下述 1.9.0。
- 重跑比较实际 catalog 与事务内创建的预期空结构，并核对脚本 SHA-256。
  项目 schema 专用于版本化对象；兼容表子集可补全，结构漂移明确失败。
  引用表及被引用表的外键内部触发器须保持默认 O 模式，D／R／A 均拒绝；
  仅核对系统目录，不自动启用触发器，也不证明异常期间写入的历史行有效。
- 通用构建核心允许非 SQL profile 不具备 normalization_id；当前 MPP
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

### 观察组代表引用的跨库边界（observed，2026-10-08）

`observations.py` 从同组近似结果按 `result_id` 排序选取一个代表，冲突时保留首次引用。
该身份依赖导入时生成的随机近似输入身份；从该机制推断，独立新建库不保证选中同一原文的代表。
这两处逻辑在解释器升级前已经存在，不是本次新增行为。
[#49 实测](../../docs/reports/python313-upgrade-2026-10-08.md#逐表差异定位measured--inferred)
发现 1,074 组中有 11 个代表引用映射至不同原文；分组其余字段、代表结果的非标识字段及
全部统计摘要一致。没有据此认定解释器是原因。

2026-10-08 用户选择“采纳实施方建议”，[#49 已确认契约](https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6056030974)
只对 `mpp_observation_group.result_id` 采用等价比较，三项须同时成立：

- 两库引用均真实存在、available、规则／近似值与组相同，并关联到同组维度下的真实近似事件。
- 被引用近似结果除 `result_id`、`input_id` 外的全部字段相同。
- 该表其余八列及其他 56 表继续精确比较；原文、近似输入／结果及事件关联仍按原文身份对应。

不得删列或仅检查外键。比较器须有正反例，记录代表不同的组数及各组候选数，并保留首次失败。
此规则仅用于本次升级的跨库验收，不改变产品代表选取逻辑或其他逻辑契约。
候选数按保留事件与组的集群、profile、数据库、用户、规则、近似值和计时类型关联后统计不同结果；
未知事件计时对应 `unknown`。该数量不回溯首次构建时的候选集，实测结果见上述报告。

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

### 文件日志时间结构 1.8.0（2026-10-05）

[Issue #35](https://github.com/shenxg13/sql-apm/issues/35) 增加 `source_file.first_log_at` 和
`last_log_at` 两个可空 timestamptz 列，约束两列同时为空或同时有值且前者不晚于后者。
全部记录第 0 列的可解析时间由来源读取器聚合，文件成功事务同时保存；失败回滚，
无可解析时间保持 NULL，成功重复文件保留首次值。

1.7.0 DDL 按字节冻结，既有 DDL 与迁移保持原摘要。1.7.0→1.8.0 可带数据，
只增加上述列及约束，不回填、不重写业务值；已有文件为 NULL，所在批次不可排除。
更早的非空库仍受 #33 的重建要求约束；空库可从 1.0.0 连续升级至 1.8.0。
新库、迁移及重跑共用结构检查。逻辑契约仍为 1.0.0，两列属于导入派生物理元数据，
可供后续留存清理与导入侧大表分区复用，本次不实现这两项能力。

### 版本结果留存结构 1.9.0（2026-10-06）

[Issue #43](https://github.com/shenxg13/sql-apm/issues/43) 实现
[按月清理](../contracts/sql-storage.md#版本结果按月留存2026-10-06)。
mpp_result_partition 增加 cleaned_at 与 groups_cleaned_at，mpp_cleanup_month 保存每次任务
各月份的处理结果、构建数、空间和分组删除计数；task 增加 cleanup 模式及阶段。
结构 1.9.0 可从含数据 1.8.0 升级；全部旧月份初始未清理，现有内容及旧 receipt 保留。
逻辑 contract_version 仍为 1.0.0。新 DDL、迁移和冻结 1.8.0 同步进入结构检查，旧文件字节不变。

两张统计分区的删除与月份标记原子提交；随后用现有索引按行批量删除两张分组关联表。
排他锁只覆盖两张统计父表及两张目标叶表，不申请分组关联表排他锁，因此分组表上的
VACUUM／autovacuum 不阻挡移除。排他事务只处理 DDL 和固定数量的元数据，不扫描结果
或逐构建更新。分区移除与分组收尾共用每月 10 秒锁等待预算；分组批次遇锁回滚后重试，
预算用尽报告 cleanup_groups_pending，保留已提交批次，重跑继续。游标只在提交后推进。
依据：[R1 缺陷与维护者确认的整改](https://github.com/shenxg13/sql-apm/issues/43#issuecomment-6019720422)。
清理时间通过 build.partition_id 关联，build.results_saved 保持历史含义。
已清理月份的统计叶表不再创建；分区构建与分组写入守卫拒绝此类写入。
查询函数在读锁下先检查留存，正式／观察覆盖与门槛推导对已清理构建报 results_cleaned。

旧迁移包含的 schema.sql 相对引用由初始化入口绑定到各自冻结目标，源 catalog 临时
DDL 在 savepoint 内读取后回滚释放锁，避免连续空库升级累计耗尽默认锁表；实际升级
仍单事务提交。阶段边界、成本和验证入口见[物理设计](../../docs/design/postgresql-storage.md#190-版本结果留存)。

### 检索查询结构1.10.0（2026-10-07）

[Issue #47](https://github.com/shenxg13/sql-apm/issues/47) 增加数据库查询函数和原文表唯一的预处理生成列，
表总数仍为57，无扩展和新增索引。1.9.0带数据升级重写原文表；既有列、内容、行数与回执保持不变。
旧DDL／迁移保持字节，版本排序改按整数数组；查询层可继续筛选聚合并复用现有统计读取守卫。
命令行只读事务保护业务数据，长统计读锁可能使清理按预算退出，结束读取后可重试。
稳定函数契约、并发行为与显式全量验证见[检索开发说明](../../docs/design/sql-search.md)。
随包用户结构说明本次仍保留1.9.0，补齐由后续Grafana工作交付；不以旧文档否定新DDL。

### 看板查询与只读授权结构 1.11.0（2026-10-09）

来源：[Issue #51](https://github.com/shenxg13/sql-apm/issues/51) 范围第九节；已实现，合成与全量真实数据验证见
[实测报告](../../docs/reports/grafana-dashboards-2026-10-09.md)。内容限于三类：

- 增改查询函数：切词规则改为只按空白、新增整段入口、模糊检索候选增加原文总数与身份信息，
  以及一组供看板使用的 `mpp_view_*` 函数；函数清单由[开发说明](../../docs/design/grafana-dashboards.md#看板用的查询函数)维护。
- 只读账号的授权：schema 的 USAGE 和每张非分区表的 SELECT。分区经父表读取。授权进入结构检查，
  角色名默认是项目账号加 `_ro`，可配置；账号本身由管理员在 bootstrap 创建，项目账号只负责授权。
- 没有新增表、列、约束、索引和扩展；表总数仍为 57。实测未发现需要新增索引。

1.10.0 可带数据原地升级：只执行函数定义和授权，不重写任何表；升级前须由管理员再执行一次 bootstrap 创建只读账号。
既有内容、行数与回执保持不变。2026-10-11 已由 [#52](https://github.com/shenxg13/sql-apm/issues/52) 将随包结构说明更新至 1.12.0；目标机验证另行记录。

### 每日运行记录结构 1.12.0（2026-10-10）

来源：[Issue #54](https://github.com/shenxg13/sql-apm/issues/54) 范围第八节；已实现，合成数据上的验证见
[实测报告](../../docs/reports/daily-run-2026-10-10.md)。

- 新增五张表：`mpp_daily_run`（每日运行的一次执行）、`mpp_daily_cluster`（其中一个集群的结果）、`mpp_daily_day`（某来源某一天的导入结果）、
  `mpp_daily_problem`（留给人处理的事和接收目录里不合命名的文件）、`mpp_daily_file`（已导入日期的每个文件：对应的导入内容、
  最近一次核对时的文件状态、删除的决定和进度）。表总数由 57 变为 62。记录里不含 SQL 原文、库名和用户名。
- 新增 `mpp_daily_*` 查询函数和供看板使用的 `mpp_view_daily_*` 函数；只读账号对新表只有 SELECT，授权进入结构检查。
  既有表、列、约束、索引和函数没有改动，任务的模式和阶段取值不变。
- 同一时间只允许一个每日运行，用会话级咨询锁实现；仍标为运行中、却没有会话持有该锁的记录由查询函数显示为未正常结束。
- 1.11.0 可带数据原地升级：只建新表、函数和授权，不重写、不回填任何既有表；既有内容、行数与回执保持不变。
  随包[结构说明](../../docs/design/database-structure.md)已包含 62 张表、更新后的总览与版本沿革，
  [检索指南](../../docs/runbooks/search-guide.md)列出查询函数；自动检查核对表／列、55 个查询函数签名和反例。
  表和函数的口径由[开发说明](../../docs/design/daily-run.md)维护。

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

导入事务与来源解析见 #18 设计；发布编排见 #29；版本结果清理由 #43 实现，其他数据留存继续另行确认。

## MPP 标识统一（2026-10-05）

依据 [Issue #33](https://github.com/shenxg13/sql-apm/issues/33) 的 2026-10-02 确认，
1.7.0 使用[统一标识](../decisions/project-scope.md#已确认的生产系统称谓)，
逻辑契约仍为 1.0.0，profile 独立于契约版本，语义与字段类型不变。
1.6.0 的 DDL、此前迁移和旧字典保持原字节；空库可连续升级，已有导入数据的库必须
重新初始化、重新导入和构建。入口在迁移前拒绝非空业务表，固定原因
`mpp_naming_requires_empty_schema`，不执行旧 ID 换算。
实施与验收见[物理设计](../../docs/design/postgresql-storage.md#170-来源标识统一)；
原有带数据迁移保留测试针对冻结的 1.6.0，不能据此声称可带数据进入 1.7.0。
