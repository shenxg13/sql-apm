# 归一化 R2 整改验证（2026-09-28）

用户授权“进行R2整改”。本次修复 `I9-R2-F001`：含 Hint 时，正负数字业务值因锚点编码
差异不能归并。已确认应归并的业务值恢复归并，同时保留 R1 的 Hint 位置及近似保护修复。
这是实施方验证，不能替代独立 R3 对退出条件、全部验收和最新差异的复核。

## 固定来源

- [R2 完整结论与问题账本](https://github.com/shenxg13/sql-apm/pull/10#issuecomment-5861815973)。
- [R2 明确退回整改](https://github.com/shenxg13/sql-apm/issues/9#issuecomment-5861820485)，所有者继续为 `codex-parser-probe-20260927`。
- 固定 target／merge base：`a69d3d1a9a36550b78402e826920e2bf466c240e`。
- initial head：`e9f8a7fc14e9d65121e4c5f98a3f0136462ec398`；R2 head：`68fe15a8a98192e3e2c0fe20438b12f06e47ba0e`。
- Issue #9 的 A1–A10 未变，正文 SHA-256 仍为 `e3a1a11c7e622ecba25124853b65692a64c0657ffa826a7652fa598a6835e1ce`。

R2 独立评审已将 `I9-R1-F001`、`I9-R1-F002` 标为 fixed；新问题来源为 repair_delta，
严重性P2。正式轮次已消耗2，修复后准备最后普通轮次 R3，不重置轮次或改写历史结论。

## 原因与修复

以下合成输入无 Hint 时可以归并；在 R2 head 加上相同 Hint 后却得到不同可靠值：

```sql
/*+ H */ SELECT * FROM t WHERE x = 1
/*+ H */ SELECT * FROM t WHERE x = -1
```

PG AST 已将 `-1` 折叠为一个数值常量，既有归一化规则将它和 `1` 替换为同一业务标记。
但旧 Hint 锚点仍把负号作为独立 token 计入摘要；Hint 位于值后时，全局／局部间隙也随之
变化。仅修改摘要或仅修正位置计数都不够。原文档“正负数字可能保守拆组”违反 A3，已纠正。

本次在清理 PG AST 源位置前，依据数值 `A_Const` 节点确认折叠的一元负号，再同时从 Hint
序列摘要、全局 gap、局部 token_gap 中去掉这些负号的独立计数。PG 保留的二元减号、
未折叠的符号表达式、括号和转换结构继续参与锚点；受保护值仍由完整 AST 区分。
不靠相邻关键字推断一元／二元角色，不补 SQL，也不改变原始字节或业务值替换策略。

具体边界：

- 数值常量中的连续负号可折叠，括号保持位置；相同结构边界的正负业务值、字符串和参数仍归并。
- 源位置是 UTF-8 字节偏移，扫描器使用字符偏移；显式换算，并回映 ROW／OIDS 兼容替换造成的长度变化。MPP 扩展子树照旧完整保留。
- 原全局 gap 也改用规范单元计数，包括 Hint 位于批次后续语句、空语句间或末尾分号后的情况。
- `x=- /*+ H */ 1` 中 Hint 位于被折叠负号之后，规范间隙无法区分负号前后，整批明确拒绝为 `hint_inside_folded_sign`；可靠值为空。`x=y - /*+ H */ 1` 的二元减号未折叠，仍可可靠锚定。
- 无法映射常量位置、跨度或适配替换位置时返回固定诊断，不猜定锚点。没有 Hint 或减号时不进入新增位置采集。

实现复用已有 PG 解析结果，仅在需要时追加一次扫描和显式栈遍历；位置回映只利用已有适配
替换区间。同一语句的 Hint 摘要仍只计算一次。新增开销随 token／AST 数量增长；这是
实现层面的定性分析，不是吞吐承诺。接口及输出细节见[设计说明](../design/sql-normalization.md)。

| 上下文 | R2 head | 当前修复 |
| --- | --- | --- |
| 可靠归一化 | sql-normalization/2 | sql-normalization/3 |
| 解析能力 | mpp-adapter-probe/6 | mpp-adapter-probe/7 |
| 观察用近似 | sql-approximate/2 | 不变 |
| 字典／依赖 | 1.0.1／pglast 7.18 | 不变 |

规则摘要和内容寻址快照随新版本更新；不能直接跨版本比较指纹字符串。旧报告保持原样。
R2 的非阻断建议也已处理：补充可靠路径只识别紧接加号、近似路径还识别 `/* + … */`／
`-- + …` 的既有差异及正反测试，不修改近似算法。

## 合成与回归验证

Python 3.9.5；[补充证据](data/sql-normalization-r2-checks-2026-09-28.json)保留原72组
合成用例的结果、新测试源码摘要及两份真实回放的摘要。

| 验证 | 结果 |
| --- | --- |
| 原 R2 72组反例 | 72组无 Hint／有 Hint 均归并，因 Hint 拆组为0 |
| 新增190组归并对照 | Hint 在值前／后、后续语句、批次边界；连续负号、零、浮点、参数、字符串、UTF-8、CTE、COPY和MPP兼容位置回映，全部通过 |
| 运算符与保护边界 | 二元减号位置改变、不同运算符、未折叠正号／转换保留；SELECT投影、LIMIT、未知函数、保护转换、DDL常量的正负值区分 |
| 明确拒绝 | 四组 Hint 位于折叠负号内部的输入明确拒绝，可靠值为空、近似观察隔离保留 |
| 旧 Hint 位置回归 | 原 R1 四组反例及198个组合通过；R2的728例六类位置扫描无错误合并 |
| 完整测试 | 46项普通、114项解析／归一化专项通过，包含578例广覆盖矩阵 |
| 扩展语法矩阵 | 81例及关系断言通过 |
| 字典 | validate／coverage通过；784条规则，1.0.1规范摘要不变 |

新用例位于[带符号业务值测试](../../tests/parser_probe/test_hint_signed_values.py)。运行：

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_matrix --output var/parser-probe/r2-expansion-rerun.json
```

## 真实输入验证

[固定1,832条回放](data/sql-normalization-r2-2026-09-28.json)复用914条历史样本及原文索引
首尾各512条，精确去重；选择与 R1／R2 验证完全相同。来源库、缓存和源码前后摘要一致，
仍为1,810条可靠、22条拒绝，其中20条近似可用、2条不可用；1,566个可靠组的成员集合与
上一版一致。排除版本化可靠值和计时后，逐条审计字段完全相同，failures为空。

使用既有4个隔离进程、每进程512 MiB、每输入验证组合20秒／单阶段5秒预算，工作进程
回放17.272秒，不含前后文件摘要核验。来源保留、重复稳定、格式稳定及完整结构变化守恒
全部通过。复核时须使用新的输出路径：

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_replay --output var/parser-probe/r2-normalization-rerun.json
```

另复用 R2 的只读 `norm_hint.py` 对同一原文索引内含 `/*+` 或 `--+` 字节标记的5,333条
不同输入进行当前版本归一化；这些标记可能位于字符串中。脚本 SHA-256 与 R2 留存值一致：
`fd7f75e9678a76216c8cf33a45ba848eff471f05e1074b146e015426b38d7631`。
报告记录其执行方法、固定源库摘要、每条原文字节摘要和版本上下文，不导出 SQL 或 Hint 原文。
单进程、地址空间2 GiB、每输入20秒闹钟，核心输入上限512 KiB；循环8.5秒，不含额外文件
摘要核验。实际结果没有超时、内存失败或处理异常；此预算复用独立 R2 的验证方法。

[含 Hint 标记的回放](data/sql-normalization-r2-hints-2026-09-28.json)结果：

- 5,317条可靠、16条结构拒绝；状态、原因、实际 Hint 数与 initial head／R2 head 逐条一致。
- 4,323个可靠组，分别与 initial head 和 R2 head 的输入成员集合完全一致，无新合并或拆分。
- 5,333个ID的选择完全相同，每条SQL字节摘要与只读源库一致；源库摘要与旧证据一致并在核验后再次确认。
- 两个历史对照输出的 SHA-256 与 R2 账本一致，未覆盖原输出。

R3 可从报告的 records 键取得固定输入ID，在同一只读源库中按ID读取并调用 Normalizer，
比较状态／原因／Hint 数和分组成员；避免直接比较不同上下文的指纹字符串。
原验证脚本保留在 R2 本地临时证据目录，报告的 script_sha256 可核对；源库留在本地忽略目录。

真实语料没有复现此次带符号业务值缺陷，因此“组数不变”不证明缺陷修复，退出条件主要
由72组原反例、190组补充及位置扫描证明。本次没有再次解析115万条全量输入，也没有重跑
8,909条近似差分；近似源码／版本未变，R2 的独立证据与本次回退测试分别保留。没有执行源
SQL、连接业务数据库或宣称全量语义准确率、生产吞吐达标。

## 交付门槛

原始需求快照、字典、锁定依赖和历史验证报告保持不变。交付前执行完整 Harness 和在线
PR 契约检查；结果、修复提交、GitHub CI 与 R3 待接收交接保存在 PR／Issue 审计评论。

```bash
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --pr 10 --repo shenxg13/sql-apm
```

独立 R3 仍须复核 `I9-R2-F001` 退出条件、两个已修复 R1 问题的交互回归、完整修复增量、
全部 A1–A10 及不可跳过门槛。实施方记录“已修复，待独立验证”，不签发 R3 结论或合并 PR。
