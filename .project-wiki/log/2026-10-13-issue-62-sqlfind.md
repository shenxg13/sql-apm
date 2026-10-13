# sqlfind 运维检索入口

依据 [Issue #62](https://github.com/shenxg13/sql-apm/issues/62) 的已确认契约，新增中文短命令、
三种输入、候选和详情摘要、终端交互、账号 home 配置与撤销，以及开发机部分样本试用环境。

规则分别归入[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)和
[检索主题](../features/sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)；
[开发说明](../../docs/design/sql-search.md#面向运维的-sqlfind)解释既有查询函数复用，
[试用文档](../../docs/runbooks/sqlfind-trial.md)提供 PuTTY 操作和撤销。
自动验证不代替 C28 用户确认；数据库结构、Grafana 与离线包清单不变，后续发版另行负责上线。
