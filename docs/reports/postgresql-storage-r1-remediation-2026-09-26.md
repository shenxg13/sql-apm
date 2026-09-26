# PostgreSQL 结构 R1 整改验证（2026-09-26）

## 范围与依据

用户指示“进行 R1 整改”，本轮处理 [PR #8 的完整 R1 评审](https://github.com/shenxg13/sql-apm/pull/8#issuecomment-5846074741)
中唯一阻断项 I7-R1-F001（P2）。[Issue #7](https://github.com/shenxg13/sql-apm/issues/7)
A5／A10 的兼容性和失败保留要求继续适用，没有缩减或扩展交付契约。
[原实施者接受交接](https://github.com/shenxg13/sql-apm/issues/7#issuecomment-5846217844)后实施修复；
本报告为实施者证据，独立 R2 的退出条件复核和后续正式评审仍由 Reviewer 承担。

R1 初始 head 为 `0b059ac7eb96339ddcb74e356fad1c75afcb6773`，
固定 target tip／merge base 为 `bbd1899b2684d448e941e324f4778eb35329fd0e`。
本次整改增量基于该初始 head，保留既有 R1 发现来源和轮次，不重新开始 R1。

## 问题复现与修复

原检查排除 `tgisinternal`，因此外键定义仍存在时，内部触发器被禁用也能通过。
在修复代码前运行新增行为测试，私有实例执行 `ALTER TABLE source DISABLE TRIGGER ALL`，
`all` 仍返回成功，测试报 `AssertionError: expected command failure`，随后实例正常清理。
这与 R1 在 check／schema／upgrade 及旧库迁移中观察到的漏报属于同一检查遗漏。

[目录检查](../../sql_apm/storage/catalog.sql)新增 `foreign_key_trigger_overrides`：
读取当前表关联的内部外键触发器，记录非默认启用模式及约束名。
引用表、被引用表都检查；不比较自动生成的触发器名／OID，避免误判预期空 schema
或保留 OID 的表改名。只比较异常模式也允许兼容部分表在后续补建表时获得入向触发器。

PG17 的模式定义为 O（origin/local）、D（禁用）、R（仅 replica）、A（always）。
本版 DDL 声明默认 O，D／R／A 均拒绝；A 的拒绝表示模式不符，不能解读成普通连接中外键失效。
定义依据：[PostgreSQL 17 pg_trigger](https://www.postgresql.org/docs/17/catalog-pg-trigger.html)。

初始化、检查及升级的旧结构／目标结构校验共用该函数。
发现异常即失败，由现有事务回滚，不自动启用触发器、改写数据或登记新版本。
改动仅涉及检查器，41 张表和结构版本 1.1.0 不变；以下文件与 R1 head 字节完全一致：
`schema.sql`、冻结的 `versions/1.0.0.sql`、`migrations/1.0.0-to-1.1.0.sql`。
其摘要沿用 [MPP 升级报告](mpp-storage-migration-2026-09-26.md#环境与输入)。

## 实测结果

环境：Python 3.9.5、PostgreSQL 17.10；私有 `/tmp/sql-apm-pg-*` 目录、Unix socket，
禁用 TCP、socket 端口 55473，全部使用合成样例，未接触既有 5432 实例或生产数据。

| 命令／检查 | 实际结果 |
| --- | --- |
| `.venv/bin/python scripts/db/verify.py --pg-bin /usr/pgsql-17/bin` | 原有存储 58 项、外键触发器兼容性 56 项、迁移 63 项及独立异常清理通过。 |
| `.venv/bin/python -m unittest discover -s tests -v` | 31 项通过。 |
| 默认和自定义库／schema／角色 | 新装和含数据 1.0.0 均覆盖；自定义项目角色实际连接执行入口和写入探针。 |
| 1.1.0 all／schema／check／upgrade | 禁用全部内部触发器、单个 R／A，以及被引用表单个 D／R 均失败；同时验证 O 与异常模式混合的表。 |
| 1.0.0 upgrade | occurrence／sql_text 两侧的同类异常被拒绝；不出现 MPP 改名或 1.1.0 版本记录。 |
| 拒绝后状态 | 业务样例、关系 OID／relfilenode、约束 OID、原版本／时间、触发器 OID／启用模式保持原样，无校验 schema 残留。 |
| 显式恢复与重试 | 测试管理员恢复 O 后，新库四入口重试成功；旧库升级及后续重跑成功，行内容、对象身份和旧版本记录保留。 |
| 实际外键执行 | 恢复后普通项目账号的悬空引用 INSERT／UPDATE、删除仍被引用行的 DELETE 均由外键明确拒绝。 |
| 原有兼容性与恢复 | 正常新装、兼容部分表补齐、MPP 重命名、真实锁超时及注入失败回滚全部通过。 |
| 临时实例 | 本次完整数据库回归的四个实例均停止并删除；修复前复现失败的实例也已清理。 |

新增回归见 [触发器行为测试](../../tests/database/trigger_compatibility.py)，
旧库测试接入 [迁移验证](../../tests/database/migration.py)，统一由 verify.py 运行。
完整 Harness、在线 PR 契约及提交后 CI 的实际结果记录在 PR 整改交接证据中。

## 成本与边界

新增查询仅关联系统目录 pg_trigger／pg_constraint；不扫描生产业务表，也不新增永久对象。
成本判断为 qualitative，未测量生产规模耗时。逐行快照比较只用于私有测试的合成小样例。

恢复触发器只保证之后语句恢复约束执行；检查通过不证明异常期间写入的历史行合法。
若实际库发生此类异常，维护者须核查原因和受影响数据，再按授权恢复并重试；
入口没有自动数据修复或全表验证功能。操作边界见[初始化与恢复说明](../runbooks/database-initialization.md)。
