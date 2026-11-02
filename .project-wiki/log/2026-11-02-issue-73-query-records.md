# 事件查询派生层与 sqlfind 缓存

- 来源：[Issue #73 已确认设计](https://github.com/shenxg13/sql-apm/issues/73#issuecomment-6579808798)。
- [存储主题](../architecture/postgresql-storage.md#事件查询派生结构-11402026-11-02)记录 1.14.0 短键、窄事件、精确汇总、事务末尾刷新与迁移边界。
- [检索主题](../features/sql-search-and-views.md#事件查询与会话缓存2026-11-02)记录统计 NULL 组守卫、读取兼容性、固定基线版本与按需 revision 检测。
- 当前为开发实现，生产仍为 1.13.0；不把开发验证及模型当作生产实测。
- [开发机验证报告](../../docs/reports/query-records-2026-11-03.md)保留结果等价性、查询与导入实测、持续／临时空间和生产情景模型。
