# sqlfind 跳过无输出操作的边界

用户试用后指出方式选择产生空输出段，并确认只给有实际输出的操作加编号及起止标记。
[Issue #62 第 17d 项及 C35 已修订](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6166482422)。
有效方式选择、新检索及退出直接跳转或退出，不占编号；`b` 重显列表、错误和取消提示仍有边界。
当前规则由[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)维护。
