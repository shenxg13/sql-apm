# sqlfind 非交互查询对齐与帮助分组

用户认可当前交互体验后，确认重整启动帮助，并明确要求同时对齐交互与非交互查询功能。
[Issue #62 第 17e 项、C36 及帮助条款已同步](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6183022357)。
非交互新增历史时间／排序、原文范围与编号、详情／原文列表／历史／全文四种输出，复用既有查询和显示，保持旧默认。
启动帮助分通用参数、非交互查询参数与交互入口，详细菜单操作由 `?` 提供。

查询规则由[检索主题](../features/sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护，
帮助分工由[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)维护。
