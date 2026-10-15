# sqlfind 交互历史设置

用户于 2026-10-14 确认在详情逐项调整历史条数和时间范围，先更新
[Issue #62 契约与确认记录](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6159484696)，再实施。
新增 `h`、1–1000 条、自定义起止时间、会话内保留、取消与恢复默认；不改变基线或候选检索范围。

规则由[检索主题](../features/sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护，
交互入口见[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)，
操作与人工验收见[试用步骤](../../docs/runbooks/sqlfind-trial.md)。
