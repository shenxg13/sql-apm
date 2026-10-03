# Kylin 执行账号读取权限修正（2026-10-04）

用户明确要求 sfmon 至少可读取 APM_ROOT 下全部目录和文件，包括 private。
先将该要求写入 [Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 的范围及 K6b，
再在已有演练实例修正权限。此项承接[用户独立验证核对](kylin-manual-validation-2026-10-04.md)，
不替代其尚未完成的候选包对齐和四项统计测试补验。

## 改动

- 原 private／pgpass 已归 sfmon、分别为 0700／0600，本次保持；不输出或重新生成密码。
- postgres 所有的目录增加 sfmon 读取／进入 ACL 与读取默认 ACL，普通文件增加读取 ACL。
  PGDATA 属主仍为 postgres，顶层权限为 0750；没有为 sfmon 增加数据写权限。
- PostgreSQL 17 启动时按 PGDATA 的组读取模式选择新目录／文件权限。停机期间补齐 ACL，
  重启后采用 0750／0640，使正常创建的新数据文件可继承有效的 sfmon 读取 ACL。
  新安装手册对应采用 [`initdb --allow-group-access`](https://www.postgresql.org/docs/17/app-initdb.html)。
- socket 目录采用 postgres 属主、sfmon 主组和 setgid（2755），不设默认 ACL，
  不授予 sfmon 目录写权限。锁文件继承 sfmon 组及 0640；socket 节点保持原 0777，
  认证仍由既有 HBA/SCRAM 控制。普通文件读取 ACL 不适用于 socket 连接权限。
- 手册新增既有部署修正与无 sudo 检查方法。显式 0600、覆盖 ACL 或移入文件可能限制继承，
  外部维护后须复查；本次不宣称能覆盖任意工具主动撤销权限的行为。

## 实测与失败处理

变更前已确认没有业务任务和活动查询；pgAdmin 存在空闲连接。备份原 ACL 后短暂停机，
修改权限并重启。首次将只读默认 ACL 同时用于 socket 目录，导致 sfmon 连接返回
`Permission denied`。自动审批拒绝扩大 socket 默认 ACL 写权限，理由是可能让未来目录可写；
随后改用上面的组继承方式，清除该目录默认 ACL，并仅恢复当时 socket 节点的连接权限。
再次正常重启后，新 socket 为 0777、新锁文件为 0640，socket 目录仍不可写，连接恢复。

最终验证均由 sfmon 原身份执行；对每个普通文件仅以只读方式打开并立即关闭，
没有读取或返回密码、SQL 正文或数据文件内容。

| 检查 | 实际结果 |
| --- | --- |
| APM_ROOT 目录遍历 | 1,699 个目录全部可读、可进入 |
| 普通文件路径打开 | 27,432 个全部成功；包含内部文件链接指向的普通文件 |
| 符号链接／socket | 70 个链接均指向 APM_ROOT 内部；1 个 socket 单独按连接验证 |
| 不可读路径／sfmon 可写 PGDATA 路径 | 均为 0 |
| private／pgpass | sfmon 属主，0700／0600 |
| PGDATA／socket 目录 | postgres 属主，0750／2755；sfmon 均无写权限 |
| 新数据文件 | 通过临时表创建真实关系文件，0640、sfmon 可读不可写；随后事务回滚清理 |
| socket／跨机器 TCP | 重启后均以原密码连接成功；TCP 查询为只读，开发机临时凭据副本已删除 |
| 业务状态 | 无运行中任务；119 五版、120 四版，当前 build_id 与修正前完全相同 |

临时表探针不写业务表，不生成 SQL APM 任务或发布版本；本次未重跑九任务。
目录／文件计数是检查时观察值，随运行变化，不作为固定验收门槛。

## 证据与边界

脱敏[机器记录](data/kylin-read-access-2026-10-04.json)保存实际计数、模式、当前版本和连接结果。
原 ACL、操作日志及检查输出留在受保护忽略目录 `var/issue31/read-access-20261004/`；
ACL 备份 SHA-256 为 `7634a70f5d67b038527f65fe3278cd83402cadd540862a775a2d04b3545feac3`。
原九任务及包摘要沿用上次核对记录，本报告仅证明权限修正与相关连接行为。
安装手册同步修改，HTML 由固定提交重新生成；完整包对齐、独立评审和正式发布仍按原契约完成。
