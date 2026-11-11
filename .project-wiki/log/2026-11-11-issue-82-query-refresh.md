# 带历史导入的增量维护

[#82](https://github.com/shenxg13/sql-apm/issues/82) 修复派生维护的历史全扫描，开发结构升至 1.15.0，
只替换函数并保留持久对象与数据。导入性能验收改为同源历史库副本配对，并记录大表扫描计数；
Issue #73 的 +18.3% 明确限定为空库测量。完整条款见[存储主题](../architecture/postgresql-storage.md#查询派生增量维护-1150-2026-11-11)，
实测、模型及后续生产核对见[验证报告](../../docs/reports/query-refresh-2026-11-11.md)。
