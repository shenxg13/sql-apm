# v1.0.0 Kylin 成品首次编译与验证

按 [Issue #59](https://github.com/shenxg13/sql-apm/issues/59) 已确认顺序，在用户提供的初始系统上执行唯一一次成品编译。
Python、PostgreSQL、运行时自检和十个随包入口通过，成品与构建证据已复制回开发机并独立核验。

[运行环境主题](../decisions/runtime-and-components.md#首个生产版本与二进制部署2026-10-12)补记本机 measured 结果，
[编译报告](../../docs/reports/v100-runtime-build-2026-10-12.md)保存包身份、系统包变化和全部嵌入固定前缀的路径。
尚未验证初始系统上的二进制兼容性、完整业务流程、重启或用户安装；没有修改业务或 Harness 规则。
