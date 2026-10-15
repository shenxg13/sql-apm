# sqlfind 交互排序与原文历史下钻

用户于 2026-10-14 确认方案并要求做一版试用，先同步
[Issue #62 的第 17b 项与 C32／C33](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6162457097)，再实施。
新增候选 o 排序、v 原文列表与选择、历史原文编号和 h 最新／最慢排序；默认按词／整段历史限定本次命中原文。
原文切换保留时间、排序和条数；基线保持结构整体口径。新增适配查询仍只读，不修改数据库函数或 Grafana。

规则由[检索主题](../features/sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护，
操作及 C28 人工试用见[试用步骤](../../docs/runbooks/sqlfind-trial.md)。
