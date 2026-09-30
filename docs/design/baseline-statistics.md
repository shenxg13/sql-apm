# 统计计算设计

[Issue #25](https://github.com/shenxg13/sql-apm/issues/25) 已确认目标、范围及 D1–D8，
本设计落实其实施选择。统计口径见[统计契约](../../.project-wiki/contracts/baseline-statistics.md)。

## 实施顺序与验证计划

1. 保存冻结的 1.3.0 DDL，新增 1.4.0 分区、自然键及迁移；用空表升级、非空拒绝、
   连续升级、重跑及跨月份写入验证。
2. 实现独立于数据库的五层指标计算；逐项独立核对指标、时间边界、空值和多原因计数。
   门槛由数据库派生，独立 oracle 核对五层数量／覆盖边界、完整原因及历史快照隔离。
3. 引用封存快照创建构建，临时推导一次判定，流式逐组计算并批量写入；验证成功、失败、
   中断和重新计算。CLI 仅输出原因码、计数和不透明标识。
4. 在私有 PG17 重导 55 文件，按两个截止日创建快照；验证逐层守恒、与判定对账、
   固定种子抽样至少 1,000 组的独立复算和同快照重复一致，实测资源。
5. 更新知识、操作说明和验证报告，执行产品、数据库、Harness 及在线 PR 契约检查。

## 计算、成本与恢复

算术、分位数、总体方差及平方根采用 Decimal 40 位精度；对数采用 binary64 的 `math.log1p`。原始 numeric 毫秒值仍完整
保存在执行明细。验证容差为绝对 `1e-9` 加相对 `1e-10`，计数、桶及标记须精确一致。
五层直接消费样本；不对分位数再次聚合。排除包括可归组的 excluded／unresolved；
outside_window 不计入，无法归组的计数保留构建诊断。

每次计算临时物化一次判定，随后按分组排序流式读取；内存随最大单组和固定写入批量增长，
不会把全部分组载入 Python。临时表和数据库排序可能使用磁盘。全量扫描与排序用于保证
完整统计，已由 D5 明确要求；避免五层重复推导是更低成本的执行方式。
结果在一个事务内保存并置 calculated，失败整体回滚。构建登记单独提交，故失败可保留
原因。started_at 来自数据库 `clock_timestamp()`，Build ID 日期前缀和分区月份均由该时间
转换到北京时间生成；finished_at 也使用数据库时钟，避免客户端时钟领先导致终态约束失败。
SIGINT／SIGTERM 转入中断；强杀或连接丢失由独立监护连接检测工作进程退出后记录
中断，恢复不续用半成品。任务串行、发布检查和版本切换由④交付。

## 门槛结果的数据库派生

`sufficiency` 是逻辑结果，物理统计行仅保存数量、覆盖数组及指标，不保存重复 JSONB。
调用 `mpp_statistic_sufficiency(statistics_version text, thresholds jsonb, layer text,
included_count bigint, active_dates date[], active_week_starts date[])` 返回 basic／p95／p99，
每项包含 required_count、actual_count、coverage_kind、required_coverage、actual_coverage、met、reasons。
数量不足原因在前，覆盖不足原因在后；同时不足时两项都返回。day 不检查覆盖，weekday 用活跃周，
其余层用活跃日；空样本仍按相同规则产生完整结果。

查询将统计行关联到 Build 及其封存 ConfigSnapshot，传入该快照的门槛和版本；
不读取当前全局默认值，也不由函数逐行重复查配置。函数为 immutable／parallel safe，
仅显式支持 baseline-formulas/1，未知版本报固定原因 unsupported_statistics_version；
未来新增版本须保留旧版本分支，以免历史构建被新规则重解释。
[手册](../runbooks/baseline-statistics.md#查询门槛结果)提供规范关联示例。

推导有查询 CPU 成本；按构建／分组过滤后仅投影需要的结果，全量分布验收每行调用一次。
不另建永久缓存或物化视图。独立 Python oracle 只在 tests，验证全部字段和原因，
包括新快照门槛变化后旧 Build 仍按原门槛解释。函数定义及属性进入 catalog 漂移检查。

## 分区与关联

每个集群和北京时间构建开始月份在 `mpp_result_partition` 登记一个 bigint 分区编号，
由组合内容 SHA-256 的前 60 位导出；检测到身份冲突时报错。`mpp_ensure_result_partition`
在开始构建前创建两张父表相应 LIST 分区，短暂 advisory 事务锁仅保护该月份的 DDL。
这不实现同集群任务互斥。首次创建月分区需要父表 DDL 锁，会与已有读写事务互相等待，
等待中的 DDL 也可能排住后续普通查询；advisory 锁不消除这一成本。跨集群并发和月分区预建由④评估。统计和覆盖均保留单表，层次仍为键的一部分。

`mpp_build_group` 每构建每组一行，集中检查 Build／Group 集群、规则和 profile 相符；
统计与覆盖通过其复合外键引用，避免逐指标行保存三个重复文本。父对象已有结果关联后
不能改变上下文。Build 分区所属月份必须与 started_at 一致。统计自然键支持构建读取，
group/build/layer 索引支持历史查询；已知组含仅排除的组，未知归属不创建分组。

分区元数据、父表、叶表、边界、父子关系、索引、外键与触发器一起进入完整 catalog 对比，
动态分区不会被通配忽略。升级要求旧统计与覆盖为空，只重建这两张表；原始证据、执行、快照、
旧 Build 与其发布记录保留。没有生产部署，非空目标需要另议保留数据的迁移方案。

## 进程退出边界

单独提交的 running 构建始终 results_saved=false。监护子进程通过父进程管道 EOF 检测退出，
仅更新仍为 running 的该次构建，并记录 worker_disconnected；正常异常由主连接先记录具体固定码。
数据库连接失败时监护有限重连。整机宕机、监护同时被杀或数据库持续不可用无法即时写入终态；
此时记录仍为 running／false，不会作为完整结果。④后续负责整体任务恢复和占用管理。
