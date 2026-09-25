# 首版语句类别黑名单：日志核查与规则验证

日期：2026-09-26。业务规则由[训练资格主题](../../.project-wiki/contracts/training-eligibility.md#首版类别边界与保守维护规则)
单处维护。本报告保存调查方法、观察、候选依据与验证证据，不作为 Issue 状态镜像。

## 结论与证据边界

按用户“尽量全面核查、名单保守、不确定不加入”的要求，对本地 119／120
全部 46 个 CSV 文件逐条扫描。首版排除名单保持已有七项；日志中新观察到的
类别、尚未确认业务覆盖的别名及特殊设置均未自动加入。报告中的计数是**词法
类别出现次数或日志记录数**，包括同一 SQL 在请求、阶段、调用、内部路径的
重复出现，不能解释为独立执行数、成功数、被过滤数或训练覆盖率。

输入范围与[2026-09-24 调查](cluster-log-analysis-2026-09-24.md)一致；46 个
SHA-256 全部匹配原清单。本次读到每个文件 EOF，扫描字节数等于文件大小，
前后大小和 mtime 一致，全部记录为 30 列，未发生 CSV 解析失败或遗漏文件。
119 的来源日期空档、120 仅约四天的覆盖限制仍沿用原调查；不声称已取得源端
所有日志。旧测试目录中的样本及 `/tmp` 中的历史结果不作为额外生产样本计入。

机器可复核的[汇总证据](data/statement-census-2026-09-26.json)保存文件清单、
字节数、校验值、CSV 记录数、按集群／来源／字段的完整汇总及代表性定位。
这是约 150 KB 的无原文诊断附件，可进入 Git；生产日志、SQL 原文、绑定参数、
账号、数据库名、地址及完整逐文件临时结果均不进入 Git。

## 覆盖与计数口径

| 指标 | 119 | 120 |
| --- | --- | --- |
| 文件数 | 30 | 16 |
| 输入字节 | 4,302,628,352 | 7,022,201,699 |
| CSV 记录 | 5,728,640 | 6,730,129 |
| duration 消息记录 | 2,854,821 | 2,929,866 |
| 非空 SQL 字段 | 4,907,172 | 6,281,510 |
| SQL 字段为空 | 821,468 | 448,619 |
| 词法单条文本记录 | 4,714,580 | 6,046,899 |
| 词法多语句文本记录 | 140,562 | 131,619 |
| 仅注释／空分号文本记录 | 44,854 | 76,138 |
| 词法／编码不确定记录 | 7,176 | 26,854 |
| 与 SQL 字段相同的内联文本 | 1,460,076 | 1,941,821 |
| 单列核查的不同内联文本 | 71,662 | 109,145 |

- 主计数使用 CSV 第 25 列 SQL 文本；同时检查第 22 列内部查询，本批均为空。
- 对消息中的 `statement`、`parse`、`bind`、`execute`／`execute fetch from`
  及 duration 后的相应内联形式进行补充核查。内联文本与 SQL 字段字节相同的
  只记一致计数；不同的作为独立证据通道，不补写 SQL 字段，也不合并为执行样本。
- 内联差异可能来自前缀、尾随内容、记录路径或截断，不能直接当成不同执行或
  恢复出的原文。未匹配上述消息形态的消息不尝试当作 SQL；它们的 SQL 字段仍扫描。
- 词法诊断跳过普通注释、嵌套块注释、字符串、引号标识符和美元引用体，检查
  括号闭合，按顶层分号观察语句序列。只有整段无诊断问题时才统计其类别；
  编码、词法或普通字符串反斜杠模式不确定时，整段记入异常，不保留局部命中。
- `WITH`、`DO`、`EXPLAIN` 等按外层标签保留，不递归把内部语句当成独立执行。
  `CREATE EXTERNAL` 是外部表前缀观察，不证明已解析整个 HashData 语法。
  引号或表达式开头及诊断词表外的文本可能为 `UNKNOWN`。
- 空文本、只有注释／空分号、未知类别、词法异常分别计数；词法可读不代表
  SQL 语法合法、文本完整或执行成功。不根据 `LOG/00000` 推定可训练。

### 主 SQL 字段的全部观察类别

每个无词法问题的文本可贡献多个子语句类别；下表不是互斥的日志记录分类。

| 词法标签 | 119 | 120 |
| --- | --- | --- |
| `ALTER TABLE` | 28,850 | 38,785 |
| `ANALYSE` | 84 | 31,867 |
| `ANALYZE` | 35,034 | 72,710 |
| `BEGIN` | 119,945 | 259,366 |
| `COMMENT` | 1,642 | 570 |
| `COMMIT` | 88,038 | 212,022 |
| `COPY` | 33,073 | 14,212 |
| `CREATE EXTERNAL` | 65,462 | 95,476 |
| `CREATE INDEX` | 0 | 24 |
| `CREATE SEQUENCE` | 0 | 111 |
| `CREATE TABLE` | 47,043 | 219,230 |
| `CREATE VIEW` | 0 | 48 |
| `DEALLOCATE` | 49,455 | 381,563 |
| `DELETE` | 177,227 | 209,121 |
| `DO` | 84 | 355 |
| `DROP EXTERNAL` | 6,526 | 48,655 |
| `DROP SCHEMA` | 0 | 51 |
| `DROP SEQUENCE` | 0 | 56 |
| `DROP TABLE` | 21,939 | 122,434 |
| `DROP VIEW` | 0 | 24 |
| `END` | 55,327 | 57,841 |
| `GRANT` | 488 | 6,775 |
| `INSERT` | 772,786 | 840,107 |
| `LOCK` | 0 | 37,580 |
| `ROLLBACK` | 0 | 65,332 |
| `SELECT` | 2,103,206 | 2,778,734 |
| `SET` | 1,134,051 | 499,703 |
| `SET ROLE` | 0 | 12 |
| `SET TRANSACTION` | 6,534 | 30,447 |
| `SHOW` | 172,041 | 64,038 |
| `TRUNCATE` | 51,067 | 89,735 |
| `UNKNOWN` | 1 | 20 |
| `UPDATE` | 10,737 | 152,866 |
| `VACUUM` | 34,611 | 35,285 |
| `WITH` | 31,608 | 9,100 |

### 来源与异常覆盖

下表按原日志源码位置统计主 SQL 字段。完整类别交叉表见 JSON 的 `groups`。
源码位置解释沿用[计时专项报告](hashdata-duration-source-mapping-2026-09-24.md)，
本次没有进一步归并 Execute 首次与续取，也没有将 Parse／Bind 合计为请求数。

| 集群 | 来源位置 | 记录 | 空 SQL | 词法批次 | 诊断异常 |
| --- | --- | --- | --- | --- | --- |
| 119 | `COptTasks.cpp:299` | 338,761 | 0 | 702 | 0 |
| 119 | `COptTasks.cpp:769` | 1,358 | 0 | 0 | 0 |
| 119 | `OTHER` | 22,004 | 21,973 | 2 | 4 |
| 119 | `aclchk.c:3914` | 11 | 0 | 0 | 0 |
| 119 | `analyze.c:311` | 28 | 0 | 0 | 0 |
| 119 | `appendonlywriter_hdw.c:82` | 2,046 | 0 | 1,364 | 0 |
| 119 | `auth.c:315` | 49,020 | 49,020 | 0 | 0 |
| 119 | `autostats.c:334` | 9,296 | 0 | 2,050 | 0 |
| 119 | `bgworker.c:376` | 1 | 1 | 0 | 0 |
| 119 | `bgworker.c:562` | 2 | 2 | 0 | 0 |
| 119 | `bgworker_hotrange.c:128` | 1 | 1 | 0 | 0 |
| 119 | `bgworker_hotrange.c:424` | 13,243 | 13,243 | 0 | 0 |
| 119 | `bgworker_hotrange.c:93` | 2 | 2 | 0 | 0 |
| 119 | `bgworker_io.c:145` | 1 | 1 | 0 | 0 |
| 119 | `bgworker_io.c:147` | 1 | 1 | 0 | 0 |
| 119 | `catalogproxy.c:83` | 693,591 | 680,216 | 5,328 | 1,360 |
| 119 | `cdbgang.c:750` | 20 | 20 | 0 | 0 |
| 119 | `cdbgang_async.c:219` | 4 | 0 | 0 | 0 |
| 119 | `cdbutil.c:1845` | 2 | 0 | 0 | 0 |
| 119 | `copy_hdw.c:153` | 30,549 | 0 | 0 | 0 |
| 119 | `csm.c:59` | 1 | 1 | 0 | 0 |
| 119 | `execUtils.c:1902` | 1 | 0 | 0 | 0 |
| 119 | `gophermeta.c:207` | 1 | 1 | 0 | 0 |
| 119 | `hba.c:2086` | 2,879 | 2,879 | 0 | 0 |
| 119 | `heap.c:1486` | 19,640 | 0 | 0 | 0 |
| 119 | `heap.c:587` | 1,222 | 0 | 0 | 0 |
| 119 | `index_cache_shm_impl.c:399` | 1 | 1 | 0 | 0 |
| 119 | `invalmsg_shared.c:258` | 1 | 1 | 0 | 0 |
| 119 | `metacache_mgr.c:808` | 1 | 1 | 0 | 0 |
| 119 | `misc.c:116` | 1 | 0 | 0 | 0 |
| 119 | `namespace.c:259` | 1 | 0 | 0 | 0 |
| 119 | `namespace.c:2951` | 2 | 0 | 0 | 0 |
| 119 | `namespace.c:424` | 4 | 0 | 0 | 0 |
| 119 | `namespace.c:429` | 1 | 0 | 0 | 0 |
| 119 | `nodeMotion.c:197` | 50 | 0 | 0 | 29 |
| 119 | `numeric.c:1066` | 6 | 0 | 0 | 0 |
| 119 | `parse_agg.c:1112` | 3 | 0 | 0 | 0 |
| 119 | `parse_coerce.c:1421` | 1 | 0 | 0 | 0 |
| 119 | `parse_coerce.c:576` | 1 | 0 | 0 | 0 |
| 119 | `parse_expr.c:2468` | 7 | 0 | 0 | 0 |
| 119 | `parse_func.c:529` | 2 | 0 | 0 | 0 |
| 119 | `parse_relation.c:1040` | 63 | 0 | 0 | 0 |
| 119 | `parse_relation.c:1061` | 3 | 0 | 0 | 0 |
| 119 | `parse_relation.c:3351` | 14 | 0 | 0 | 0 |
| 119 | `parse_relation.c:3376` | 11 | 0 | 0 | 0 |
| 119 | `parse_relation.c:3390` | 15 | 0 | 0 | 0 |
| 119 | `parse_type.c:269` | 1 | 0 | 0 | 0 |
| 119 | `postgres.c:1239` | 18,664 | 0 | 5,445 | 2,845 |
| 119 | `postgres.c:1455` | 18,663 | 0 | 5,445 | 2,844 |
| 119 | `postgres.c:1685` | 841,452 | 0 | 56,725 | 47 |
| 119 | `postgres.c:1946` | 819,577 | 0 | 55,359 | 47 |
| 119 | `postgres.c:2219` | 654,414 | 9,637 | 0 | 0 |
| 119 | `postgres.c:2224` | 109 | 0 | 0 | 0 |
| 119 | `postgres.c:2603` | 681,297 | 23,514 | 0 | 0 |
| 119 | `postgres.c:2764` | 671,520 | 0 | 0 | 0 |
| 119 | `postgres.c:2843` | 671,465 | 0 | 0 | 0 |
| 119 | `postgres.c:3835` | 5,245 | 5,245 | 0 | 0 |
| 119 | `postgres.c:3851` | 20 | 20 | 0 | 0 |
| 119 | `postgres.c:3885` | 83 | 3 | 0 | 0 |
| 119 | `postgres.c:3955` | 44 | 0 | 0 | 0 |
| 119 | `postgres.c:4013` | 37 | 1 | 0 | 0 |
| 119 | `postmaster.c:2567` | 1 | 1 | 0 | 0 |
| 119 | `postmaster.c:2713` | 2 | 2 | 0 | 0 |
| 119 | `postmaster.c:2721` | 25 | 25 | 0 | 0 |
| 119 | `postmaster.c:3108` | 1 | 1 | 0 | 0 |
| 119 | `postmaster.c:3136` | 1 | 1 | 0 | 0 |
| 119 | `postmaster.c:3315` | 1 | 1 | 0 | 0 |
| 119 | `postmaster.c:3317` | 1 | 1 | 0 | 0 |
| 119 | `postmaster.c:3737` | 1 | 1 | 0 | 0 |
| 119 | `postmaster.c:4004` | 4 | 4 | 0 | 0 |
| 119 | `postmaster.c:4250` | 1 | 1 | 0 | 0 |
| 119 | `postmaster.c:4425` | 9 | 9 | 0 | 0 |
| 119 | `postmaster.c:6235` | 8 | 8 | 0 | 0 |
| 119 | `pqcomm.c:1572` | 20 | 20 | 0 | 0 |
| 119 | `pqcomm.c:935` | 46 | 46 | 0 | 0 |
| 119 | `resscheduler.c:264` | 1 | 1 | 0 | 0 |
| 119 | `rpc.c:158` | 3 | 3 | 0 | 0 |
| 119 | `syslogger.c:742` | 42 | 42 | 0 | 0 |
| 119 | `syslogger.c:756` | 1 | 1 | 0 | 0 |
| 119 | `tablecmds.c:1195` | 1 | 0 | 1 | 0 |
| 119 | `tablecmds.c:1214` | 24 | 0 | 1 | 0 |
| 119 | `tablecmds.c:15822` | 5,388 | 0 | 0 | 0 |
| 119 | `variable.c:605` | 2,178 | 0 | 0 | 0 |
| 119 | `varsup.c:79` | 137,046 | 7,340 | 8,112 | 0 |
| 119 | `xact.c:4615` | 9,195 | 0 | 28 | 0 |
| 119 | `xlog_kv.c:194` | 8,176 | 8,176 | 0 | 0 |
| 120 | `COptTasks.cpp:299` | 755,724 | 0 | 795 | 3,374 |
| 120 | `COptTasks.cpp:769` | 59,492 | 0 | 32 | 7,928 |
| 120 | `OTHER` | 23,510 | 22,486 | 0 | 33 |
| 120 | `aclchk.c:3914` | 25 | 0 | 0 | 2 |
| 120 | `analyze.c:2720` | 1 | 0 | 0 | 0 |
| 120 | `analyze.c:3090` | 1 | 0 | 0 | 0 |
| 120 | `analyze.c:311` | 60 | 0 | 0 | 0 |
| 120 | `analyze.c:868` | 2 | 0 | 0 | 0 |
| 120 | `auth.c:315` | 6,912 | 6,912 | 0 | 0 |
| 120 | `autostats.c:334` | 67,338 | 0 | 918 | 277 |
| 120 | `bgworker_hotrange.c:424` | 1,258 | 1,258 | 0 | 0 |
| 120 | `bool.c:151` | 1 | 0 | 0 | 0 |
| 120 | `catalogproxy.c:83` | 355,765 | 351,826 | 744 | 330 |
| 120 | `cdbdisp.c:285` | 1 | 1 | 0 | 0 |
| 120 | `cdbgang.c:750` | 40 | 40 | 0 | 0 |
| 120 | `cdbgang.c:816` | 2 | 2 | 0 | 0 |
| 120 | `cdbgang_async.c:219` | 6 | 1 | 0 | 0 |
| 120 | `cdbsreh.c:403` | 2 | 0 | 0 | 0 |
| 120 | `cdbutil.c:1845` | 4 | 2 | 0 | 0 |
| 120 | `copy.c:5313` | 85 | 0 | 85 | 0 |
| 120 | `copy_hdw.c:153` | 31,655 | 0 | 0 | 0 |
| 120 | `dbsize_hdw.c:110` | 18 | 0 | 0 | 0 |
| 120 | `dbsize_hdw.c:81` | 18 | 0 | 0 | 0 |
| 120 | `dbsize_hdw.c:87` | 18 | 0 | 0 | 0 |
| 120 | `deadlock.c:956` | 12 | 0 | 0 | 0 |
| 120 | `execUtils.c:1902` | 2 | 0 | 0 | 0 |
| 120 | `formatting.c:2223` | 2 | 0 | 0 | 0 |
| 120 | `formatting.c:3524` | 3 | 0 | 0 | 0 |
| 120 | `guc.c:6049` | 12 | 0 | 0 | 0 |
| 120 | `guc.c:9584` | 3 | 0 | 0 | 0 |
| 120 | `hba.c:2086` | 6,148 | 6,148 | 0 | 0 |
| 120 | `heap.c:1486` | 17,720 | 0 | 0 | 0 |
| 120 | `heap.c:587` | 1,446 | 0 | 0 | 156 |
| 120 | `ic_udpifc.c:5909` | 1 | 0 | 0 | 0 |
| 120 | `indexcmds.c:735` | 8 | 0 | 0 | 0 |
| 120 | `invalmsg_shared.c:282` | 1 | 0 | 0 | 0 |
| 120 | `misc.c:116` | 3 | 0 | 0 | 0 |
| 120 | `namespace.c:259` | 2 | 0 | 0 | 0 |
| 120 | `namespace.c:2951` | 7 | 0 | 0 | 0 |
| 120 | `namespace.c:424` | 1,954 | 0 | 0 | 0 |
| 120 | `nodeHash.c:868` | 5 | 0 | 0 | 5 |
| 120 | `nodeModifyTable.c:923` | 3 | 0 | 0 | 0 |
| 120 | `nodeMotion.c:197` | 15 | 0 | 0 | 0 |
| 120 | `numutils.c:101` | 1 | 0 | 0 | 1 |
| 120 | `numutils.c:59` | 60 | 0 | 0 | 0 |
| 120 | `orca.c:62` | 25 | 0 | 0 | 0 |
| 120 | `parse_agg.c:1112` | 18 | 0 | 0 | 0 |
| 120 | `parse_agg.c:367` | 3 | 0 | 0 | 0 |
| 120 | `parse_clause.c:3047` | 1 | 0 | 0 | 0 |
| 120 | `parse_coerce.c:1421` | 4 | 0 | 0 | 0 |
| 120 | `parse_coerce.c:576` | 7 | 0 | 0 | 0 |
| 120 | `parse_expr.c:1749` | 2 | 0 | 0 | 0 |
| 120 | `parse_func.c:529` | 3 | 0 | 0 | 0 |
| 120 | `parse_oper.c:716` | 4 | 0 | 0 | 0 |
| 120 | `parse_relation.c:1040` | 626 | 0 | 0 | 0 |
| 120 | `parse_relation.c:1061` | 638 | 0 | 0 | 0 |
| 120 | `parse_relation.c:3351` | 44 | 0 | 0 | 6 |
| 120 | `parse_relation.c:3376` | 50 | 0 | 0 | 1 |
| 120 | `parse_relation.c:3390` | 101 | 0 | 0 | 0 |
| 120 | `parse_relation.c:571` | 6 | 0 | 0 | 0 |
| 120 | `parse_relation.c:686` | 1 | 0 | 0 | 0 |
| 120 | `parse_type.c:269` | 1 | 0 | 0 | 0 |
| 120 | `pg_appendonly.c:422` | 632 | 0 | 0 | 0 |
| 120 | `pl_exec.c:3060` | 13,447 | 0 | 0 | 0 |
| 120 | `postgres.c:1239` | 20,889 | 0 | 789 | 5,924 |
| 120 | `postgres.c:1455` | 20,887 | 0 | 789 | 5,921 |
| 120 | `postgres.c:1685` | 1,573,118 | 0 | 58,105 | 879 |
| 120 | `postgres.c:1946` | 1,551,840 | 0 | 58,011 | 869 |
| 120 | `postgres.c:2219` | 359,675 | 1,683 | 0 | 137 |
| 120 | `postgres.c:2224` | 18 | 0 | 0 | 0 |
| 120 | `postgres.c:2603` | 472,981 | 16,216 | 0 | 137 |
| 120 | `postgres.c:2608` | 54 | 0 | 0 | 0 |
| 120 | `postgres.c:2764` | 457,096 | 0 | 0 | 137 |
| 120 | `postgres.c:2843` | 457,073 | 0 | 0 | 137 |
| 120 | `postgres.c:3835` | 9,611 | 9,607 | 0 | 0 |
| 120 | `postgres.c:3851` | 58 | 41 | 0 | 0 |
| 120 | `postgres.c:3885` | 87 | 29 | 0 | 2 |
| 120 | `postgres.c:3955` | 19 | 0 | 0 | 0 |
| 120 | `postgres.c:3999` | 2 | 0 | 0 | 2 |
| 120 | `postgres.c:4013` | 37 | 0 | 0 | 0 |
| 120 | `postgres.c:446` | 4 | 4 | 0 | 0 |
| 120 | `postmaster.c:2209` | 2 | 2 | 0 | 0 |
| 120 | `postmaster.c:2713` | 1 | 1 | 0 | 0 |
| 120 | `postmaster.c:2721` | 95 | 95 | 0 | 0 |
| 120 | `postmaster.c:2737` | 10 | 10 | 0 | 0 |
| 120 | `pqcomm.c:1572` | 59 | 35 | 0 | 0 |
| 120 | `pqcomm.c:935` | 42 | 42 | 0 | 0 |
| 120 | `rpc.c:246` | 1 | 0 | 0 | 0 |
| 120 | `syslogger.c:742` | 1 | 1 | 0 | 0 |
| 120 | `tablecmds.c:15822` | 2,550 | 0 | 1,530 | 0 |
| 120 | `varchar.c:620` | 2 | 0 | 0 | 0 |
| 120 | `variable.c:605` | 10,149 | 0 | 0 | 0 |
| 120 | `varsup.c:79` | 412,468 | 31,025 | 7,785 | 596 |
| 120 | `xact.c:4440` | 24 | 0 | 0 | 0 |
| 120 | `xact.c:4615` | 35,165 | 0 | 2,036 | 0 |
| 120 | `xlog_kv.c:194` | 1,152 | 1,152 | 0 | 0 |

下列诊断原因互斥：探测器对一个文本只报告首次发现的问题；这不是所有问题的
完整清单。主 SQL 字段与内联通道分别记录，不求和成被排除执行数。

| 原因 | 119 主 SQL | 120 主 SQL |
| --- | --- | --- |
| `ambiguous_string_escape` | 94 | 2,471 |
| `invalid_encoding_or_nul` | 14 | 451 |
| `unbalanced_bracket` | 6,015 | 17,655 |
| `unclosed_comment` | 116 | 287 |
| `unclosed_string` | 937 | 5,990 |

剩余 `UNKNOWN` 在 119 为 1 次、120 为 20 次；未知标签不视作已识别业务类别，
不因类别排除。内联通道的诊断异常分别为 33 和 308 条，详见附件。

批次观察覆盖纯控制、控制＋查询／写入、DDL＋DML、维护＋业务及未知子句。
机器附件的回放标签保存观察到的序列前缀；它们不是结构指纹，不输出对象或值。
“全部初始类别”与“混合批次”的业务预期通过主题页 C08—C10、C24—C25 核对，
诊断器自身不执行黑名单判定，也不产生过滤准确率。

## 候选与业务决定

以下正例表示“该候选类别的合成例子”，**不表示已决定排除**；反例用于限制
潜在覆盖。表内暂缓均依据用户本次通用的保守指示，不冒称逐项得到用户采纳。
全部正反例为人工生成，未运行数据库 SQL。是否在日志观察到可对照上表；
未观察到的项是语法／业务相邻边界，不能写成实际发生过。

| 候选及依据 | 若考虑排除的理由与保留理由 | 合成正例／反例与适用边界 | 首版决定 |
| --- | --- | --- | --- |
| `END`：两集群观察到；上游事务语法与 COMMIT 同类 | 可统一事务提交别名；但扩大别名覆盖未逐项确认 | `END WORK;`／`DO $$BEGIN PERFORM 1; END$$;`，只考虑顶层控制命令 | 暂缓，不默认启用 |
| `ANALYSE`：两集群观察到；上游 `analyze_keyword` | 可统一统计维护拼写；业务覆盖仍需确认 | `ANALYSE demo;`／`EXPLAIN ANALYZE SELECT 1;`，后者是 EXPLAIN | 暂缓，不默认启用 |
| `START TRANSACTION`：上游 BEGIN 等效形式，本批未观察到 | 可统一事务开始；本批证据不足且业务未确认 | `START TRANSACTION;`／过程体 `BEGIN`，不扫描过程体控制词 | 暂缓，不默认启用 |
| 特殊 `SET`：观察到 `SET TRANSACTION`、`SET ROLE`；其他来自上游语法 | 可能是执行上下文，但身份／隔离／约束有独立语义 | `SET ROLE NONE;`、`SET TRANSACTION READ ONLY;`／`SET work_mem = '16MB';`，普通参数设置已在原名单 | 暂缓特殊形式，不按 SET 前缀概括 |
| `ROLLBACK`／`ABORT`、保存点：观察到 ROLLBACK，其余是相关语法 | 可排除事务控制步骤；回滚、锁及恢复耗时也可能有分析价值 | `ROLLBACK;`、`SAVEPOINT demo_s;`／`DELETE FROM demo;`，回滚动作与业务 DML 分开 | 暂缓；执行失败资格另行应用 |
| 两阶段事务：上游独立语法，本批未观察到 | 管理事务但可能是分布式业务提交关注对象 | `COMMIT PREPARED 'demo_tx';`／`COMMIT;`，不得按首词合并 | 暂缓，不默认启用 |
| `SHOW`：两集群观察到 | 可视作配置查询，也可能用于运行状态分析 | `SHOW work_mem;`／`SELECT * FROM demo;`，不扩大到所有查询 | 暂缓，不默认启用 |
| `DEALLOCATE`：两集群观察到；PREPARE／EXECUTE 为相关语法 | 释放预备语句是管理动作；执行预备语句仍承载业务 | `DEALLOCATE ALL;`／`EXECUTE demo_plan;`，不能连同 SQL EXECUTE 或日志 execute 记录一起排除 | 暂缓，不默认启用 |
| 游标、RESET、DISCARD：相关客户端／会话管理语法，本批未观察到 | 管理与清理可能无需基线；取数和重置也可能被关注 | `CLOSE demo_cursor;`、`RESET ALL;`／`FETCH 10 FROM demo_cursor;`，不统一排除游标取数 | 暂缓，不默认启用 |
| `LOCK`：120 观察到 | 显式控制操作；等待时长可能正是业务分析目标 | `LOCK TABLE demo IN ACCESS SHARE MODE;`／`SELECT 1;`，不能推定所有锁相关 SQL 都排除 | 暂缓，不默认启用 |
| 其他维护、诊断：REINDEX／CLUSTER／CHECKPOINT／EXPLAIN，本批未观察到 | 与维护有关，但诊断、等待和实际执行可能有基线价值 | `REINDEX TABLE demo;`、`EXPLAIN SELECT 1;`／`EXPLAIN ANALYZE SELECT 1;`，后者实际执行，不能当 ANALYZE 维护 | 暂缓，不默认启用 |
| CREATE／DROP 对象、GRANT、COMMENT：多类已观察到 | 某些是管理步骤，也可能构成 ETL 业务流程 | `CREATE TABLE demo AS SELECT 1;`、`GRANT SELECT ON demo TO PUBLIC;`／已确认 `CREATE INDEX` | 未采纳全部 DDL／管理语句排除 |
| TRUNCATE、COPY、DML、WITH、DO：已观察到 | 可属于正常数据装载及业务过程，不能按成本或关键字决定无价值 | `TRUNCATE TABLE demo;`、`COPY demo FROM STDIN;`／`VACUUM demo;`，业务动作与已确认维护分开 | 未采纳按整个类别排除 |
| 监控查询／函数：仅为模板维护边界，本次不做来源归因 | 某条查询可不关心，但不能推出整个 SELECT／函数类别应排除 | `SELECT 1;`／`SELECT * FROM demo;`，个别模板由用户后续维护 | 不制定模板条目，不排除整个查询类别 |

## 语法来源与兼容限制

已查看 PostgreSQL 9.4 官方文档，以及既有计时调查使用的固定
[Greenplum 上游语法源码](https://github.com/greenplum-db/gpdb-archive/blob/9a08259bd1836f0cf5ba935e7e0030a5a9c0a54b/src/backend/parser/gram.y)。
它属于 GP 6.20.3 开发谱系，不能冒称用户 HashData 3.13.13 的完整源码或现场
验证。源码只支持“该语法如何分类”，不授权增加业务黑名单。

- [SET](https://www.postgresql.org/docs/9.4/sql-set.html)：运行参数及 SESSION／LOCAL 写法；函数效果相近不改变 SELECT 类别。
- [BEGIN](https://www.postgresql.org/docs/9.4/sql-begin.html)与[COMMIT](https://www.postgresql.org/docs/9.4/sql-commit.html)：普通事务命令及 WORK／TRANSACTION 修饰。
- [COMMIT PREPARED](https://www.postgresql.org/docs/9.4/sql-commit-prepared.html)：独立的两阶段提交命令。
- [VACUUM](https://www.postgresql.org/docs/9.4/sql-vacuum.html)与[ANALYZE](https://www.postgresql.org/docs/9.4/sql-analyze.html)：回收及统计维护；VACUUM ANALYZE 是组合选项。
- [CREATE INDEX](https://www.postgresql.org/docs/9.4/sql-createindex.html)与[ALTER TABLE](https://www.postgresql.org/docs/9.4/sql-altertable.html)：指定 DDL 的范围。选项在上游语法中存在不代表目标分布式构建支持成功执行。
- [EXPLAIN](https://www.postgresql.org/docs/9.4/sql-explain.html)、[RESET](https://www.postgresql.org/docs/9.4/sql-reset.html)、[ROLLBACK](https://www.postgresql.org/docs/9.4/sql-rollback.html)：相邻候选与反例的语义依据。
- 固定 `gram.y` 的 `VariableSetStmt`／`set_rest`、`TransactionStmt`、`IndexStmt`、`AnalyzeStmt`／`analyze_keyword` 明确提供特殊 SET、END、两阶段提交和 ANALYSE 分支。

## 回放、验证与重现

最终选择 140 个“文件／记录／字段”定位（119：61，120：79），全部回放通过。
覆盖两集群实际观察到的每个类别和每种诊断原因，并包含两类 SQL 证据通道。

回放按 CSV 逻辑记录定位，复核 SQL 字节摘要、物理行范围、来源及诊断结果；
遇到每个文件最后一个选定记录即停止。覆盖每个观察类别／异常原因和证据通道，
并按固定排序每通道至多抽取 12 种批次前缀。不是逐条人工审查所有 SQL，
也不是独立 SQL 解析器验证；诊断边界另有合成回归，业务预期人工走查 C01—C25。

使用项目 Python 3.9.5 和标准库，无新增依赖。以下命令仅读取本地忽略日志，
输出目录必须在输入目录之外；不会连接数据库或执行 SQL：

```bash
.venv/bin/python scripts/diagnostics/statement_census.py --root raw/inbox/hashdata/119 --output /tmp/sql-apm-category-census/119
.venv/bin/python scripts/diagnostics/statement_census.py --root raw/inbox/hashdata/120 --output /tmp/sql-apm-category-census/120
.venv/bin/python scripts/diagnostics/statement_census.py --root raw/inbox/hashdata --output /tmp/sql-apm-category-replay --replay docs/reports/data/statement-census-2026-09-26.json
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

每个扫描结果按原文件名保存一个 JSON。附件的 `files` 取这些结果的文件名、
字节数、SHA-256 和计数；`groups` 对同集群、字段、来源逐项求和。回放候选按
文件名字典序取每个标签的首次定位，再对每通道批次标签限取 12 个，按
文件／逻辑记录／字段去重并保留标签。没有语句级去重或执行事件合并。

合成回归覆盖注释／字符串／过程体中的伪关键字与分号、普通提交与两阶段提交、
未知／畸形文本、CSV 多行及错误、输入不改写、输出不含样例对象名，以及回放
摘要不一致时报错。产品规则尚无过滤实现，因此 C01—C25 是人工规则验收，
不是以诊断程序的词法分类代替黑名单引擎测试。

验证结果：31 项 Python 单元测试通过，其中 6 项诊断回归；140 个生产定位的
有界回放全部通过；C01—C25 已逐项人工走查。完整离线 Harness 检查通过，
包括 Markdown、链接／知识引用完整性及五组流程回归；PR 在线契约检查随交接记录。

## 调查成本与保留限制

全文件扫描的目的，是避免只看常见前缀或小样本而遗漏低频类别；这是本次用户
明确要求。复用旧汇总或抽样更省成本，但无法回答本次所有可用日志中有哪些类别。
采用两集群各一个流式扫描进程，按文件输出；缓存最多 100,000 条文本摘要对应的
诊断结果，不缓存全部原文。失败可按文件重新执行，不修改输入。内存还受单个
CSV 字段（读取上限 128 MiB）、最长批次和汇总大小影响，不宣称固定峰值内存。

首轮回放发现诊断词表遗漏 TRUNCATE，以及 READABLE／WRITABLE EXTERNAL
的修饰形式，已修正并重新扫描全部文件；普通 `COMMIT TRANSACTION` 的诊断
归类也由合成回归锁定。最终附件来自修正后的同一诊断版本。这里修正的是调查
类别计数，不是新增排除规则。

最终扫描按文件累计耗时实测为 119：106.883 秒，
120：170.591 秒；两个进程并行，二者相加不是墙钟总时长。
这只是本次证据采集成本，不是产品性能承诺；未测峰值内存。

未测过滤准确率、归一化后训练覆盖率、数据库容量或生产运行性能；本次无数据库
迁移、历史重写、自动清理、模板名单维护或产品过滤代码交付。
