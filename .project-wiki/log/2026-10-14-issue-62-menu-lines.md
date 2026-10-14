# sqlfind 常驻菜单逐行显示

用户指出候选列表和 SQL 详情的命令列表未像最外层一样换行，要求显示一致。
[Issue #62 第 17c 项及 C34 已补充](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6163590741)。
两处常驻操作提示改为每个命令一行，原文范围各命令分别列出，Ctrl-C 返回提示独立一行；操作行为不变。

规则由[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)维护，
操作见[试用步骤](../../docs/runbooks/sqlfind-trial.md)。
