# sqlfind 操作输出边界与专用全文

用户确认每次操作增加带编号及命令名的起止标记，失败、取消也收尾；`f` 只显示当前原文全文。
[Issue #62 第 17d 项及 C35 已同步](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6164631444)。
结束标记之后再显示逐行菜单，保留终端回滚。`f` 保留原文编号及开始／结束标记，直接使用已取得的全文，
不查询或重刷身份、基线和历史，当前选择与设置不变；一次输出保持原有行为。

交互边界由[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)维护，
全文范围由[检索主题](../features/sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护。
