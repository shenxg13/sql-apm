# sqlfind 原文列表显示不同部分

用户试用反馈原文摘要的开头一致、无法据此选择，明确要求展示不同部分。
[Issue #62 已同步第 17b 项及 C33](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6162958350)。
原文列表改为比较完整原文后显示去掉共同前后缀的差异，包含零记录项，较长差异保留首尾并标记省略；
空白可见，编号、历史和基线不变。仅查看列表时计算，复用现有共同前缀函数，无数据库变更。

规则由[检索主题](../features/sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护，
试用步骤见[操作文档](../../docs/runbooks/sqlfind-trial.md)。
