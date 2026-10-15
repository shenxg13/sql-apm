# sqlfind 仓库入口移至 bin

用户明确要求把仓库入口移至 `bin/sqlfind` 并调整相关代码，不讨论后续发版位置。
[Issue #62 第 1／27 项及 C2／C27 已同步](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6183765468)。
启动器从 `bin` 上一级定位程序，根目录不留旧入口；配置工具支持定向迁移原有账号链接并保留配置与撤销记录。

路径归属由[源码布局](../architecture/source-layout.md)维护，
账号链接规则由[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)维护。
