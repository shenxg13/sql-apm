# sqlfind 交互帮助

用户讨论后确认最外层、候选列表和详情菜单增加 `?` 帮助。
[Issue #62 第 17c 项及 C34 已同步](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6163225929)。
帮助按操作位置分组，每命令一行，说明检索到执行历史的流程、身份和计时含义、输入与取消。
显示后保留当前菜单及选择，不访问数据库；检索输入中的问号仍按字面保留。

规则由[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)维护，
操作见[试用步骤](../../docs/runbooks/sqlfind-trial.md)。
