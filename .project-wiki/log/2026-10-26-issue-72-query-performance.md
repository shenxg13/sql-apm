# 查询读取与精确汇总

- 来源：[Issue #72](https://github.com/shenxg13/sql-apm/issues/72) 的已确认范围，开发结构由 1.12.0 升至 1.13.0。
- [存储主题](../architecture/postgresql-storage.md#查询读取结构-11302026-10-26)记录三张事务内精确汇总表、overall 覆盖索引、带数据迁移及月分区生命周期；元数据按每页 100 条写入，避免计数维护造成导入退化。
- [检索主题](../features/sql-search-and-views.md#查询性能修复的兼容边界2026-10-26)记录输出、训练判定、选项与提示含义保持不变。
- [验证报告](../../docs/reports/query-performance-2026-10-26.md)区分开发机 measured、生产 modeled 和尚未执行的生产实测；发布与生产升级仍属 #74。
