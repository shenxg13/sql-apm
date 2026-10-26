# Issue #72 R1 整改

- 按 [R1 台账 F-72-001](https://github.com/shenxg13/sql-apm/pull/75#issuecomment-6419461856)，
  训练补指纹的短事务先取导入共用的元数据锁，避免指纹唯一键与计数行形成跨集群锁环。
  范围见[存储主题](../architecture/postgresql-storage.md#查询读取结构-1130-2026-10-26)，解析仍在锁外。
- 按[维护者纳入决定](https://github.com/shenxg13/sql-apm/issues/72#issuecomment-6419732118)，
  验证报告补充详情及列表整页的生产估计，澄清看板设计中的旧扫描耗时；#72 契约不变。
