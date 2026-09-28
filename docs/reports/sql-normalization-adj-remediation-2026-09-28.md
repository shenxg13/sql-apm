# 归一化收敛裁决整改验证（2026-09-28）

按用户“进行adj整改”及 Maintainer 裁决，完成 B1–B5 的一次批量整改：PG 合成常量没有
源 token，不再因无法映射其位置而误拒绝含 Hint 的有效 SQL。本文是实施方验证；独立
`final_verification` 尚待交接后的评审，不是新增普通轮次或最终通过结论。

## 固定依据与范围

- [裁决 adj-9-10-20260928](https://github.com/shenxg13/sql-apm/issues/9#issuecomment-5862909714)。
- [R3 评审与完整账本](https://github.com/shenxg13/sql-apm/pull/10#issuecomment-5862817526)。
- 固定 target／merge base：`a69d3d1a9a36550b78402e826920e2bf466c240e`。
- 冻结整改起点：`c013dbf098d370872ceb7c1f4a129dda5c239a9a`。
- E2 的 R2 对照：`68fe15a8a98192e3e2c0fe20438b12f06e47ba0e`。
- A1–A10 不变，Issue 正文 SHA-256：`e3a1a11c7e622ecba25124853b65692a64c0657ffa826a7652fa598a6835e1ce`。

正式轮次已消耗3；原 `I9-R1-F001`、`I9-R1-F002` 为 fixed，本次处理
`I9-R2-F001` 残留及 `I9-R3-F001`。新增行为仅是源位置判定，其他增量限于规则说明常量、
对应测试、文档与新证据。旧报告、近似实现、字典、依赖锁定及诊断程序没有修改。
全局 gap、其他语句括号造成的保守拆组、IN 长度归并、AST 节点锚定、命名和近似路径
对空格加号的识别差异均不在本批范围。

## 原因与实现

以下为合成示例：

```sql
/*+ H */ SELECT x::char FROM t WHERE y = 1
/*+ H */ SELECT x::char FROM t WHERE y = -1
```

PG 为 `char` 等类型修饰、默认 FETCH 数量、substring FOR 起点及 interval 修饰生成
数值常量，有的 `A_Const.location` 缺失或为负。这些节点没有源 token，但旧负号映射把
它们当成源位置丢失而拒绝；负号触发映射后，同一语句的业务值便不能可靠归并。

现在只映射 `location` 为非负整数的数值常量。合成常量跳过源位置映射，AST 内容继续
保留，真实源码常量的负号仍按 PG 的折叠结果映射。非负整数位置无法对应 token 时仍拒绝；
没有借此推断、补齐 SQL，也没有修改业务值替换或 Hint 锚点编码方式。

三个防御性诊断各有可复现的辅助函数级测试：

| 诊断 | 测试构造与预期 |
| --- | --- |
| hint_constant_location_unmapped | 为 `SELECT -1` 构造位置99的数值 AST，超出源码映射，明确拒绝 |
| hint_constant_span_unmapped | 给 `SELECT -x` 配上伪造数值 AST，负号后实际为列名，明确拒绝；真实 `SELECT -(1)` 通过 |
| hint_sign_inside_adapter_replacement | 人工把负号偏移放入替换生成区间，明确拒绝；区间外和结束边界正确回映 |

这些是内部数据不一致的防线，不能宣称是有效 SQL 的预期语法拒绝。真实输入由 E1–E3
及历史差分验证；未证明覆盖全部可能 SQL。既有四例 `hint_inside_folded_sign` 明确拒绝
和二元减号正例保留，防止 Hint 落在折叠负号内部时错误合并。

解析能力升级为 `mpp-adapter-probe/8`；`sql-normalization/3`、`sql-approximate/2`、
pglast 7.18 和字典1.0.1保留。归一化规则说明补充合成常量的映射边界，规则摘要及快照
地址随之更新，版本上下文可追溯。新旧指纹字符串不能直接比较：

- rules_digest：`04a423680c02c7b6b8b87295346d9468d089e279cb19183b5587b57bbb51474d`。
- rules_ref：`sha256:d2faed8791aab2845b50ff6c9d356ed2cb7b728d3a53d913da5b3e2da02eaf6f`。
- 字典规范摘要：`74ee855341d41f9fb6df217bd351222806f0f11a8c19d2d2b777138f9c824f45`。

## 批量退出条件验证

Python 3.9.5；[检查证据](data/sql-normalization-adj-checks-2026-09-28.json)记录上下文、
源文件／日志摘要、状态对照及旧账本回归。新增7个测试方法已加入
[带符号 Hint 回归](../../tests/parser_probe/test_hint_signed_values.py)。

| 条件 | 本次实测 |
| --- | --- |
| E1 | 54组1／−1配对加3条 DDL／UPDATE／INSERT，共57例通过；无 Hint 和有 Hint 都 reliable 且配对归并。相同新测试在修复前57例全部失败 |
| E2 | 30例定向加1,143个广覆盖变体，共1,173；与 R2 状态逐条相同，没有 reliable→拒绝；相对冻结 head 恢复12条 reliable |
| E3 | 15种嵌套负号／括号／转换 × 6上下文 × 句首／句尾 Hint，共180例；无 Hint 及有 Hint 均 reliable，覆盖 MPP 分布与 ROW／OIDS 长度回映 |
| B2 | 三个防御诊断、无源位置常量与真实负号共存、四组折叠负号内 Hint 拒绝均通过 |
| R1 已关闭项 | 原8组反例通过：4组可靠指纹继续区分，4组近似保护继续区分且替换数为0 |
| R2 归并 | 原72组和既有190组补充归并通过 |
| 位置保护 | 198例及728例扫描通过，错误合并为0 |
| 完整测试 | 46项普通、121项专项通过；专项包含578例原始广覆盖矩阵 |
| 扩展与字典 | 81例扩展及关系断言通过，784条字典规则 validate／coverage 通过 |

E1 六种形式、三种尾部和三种 Hint 位置均与裁决一致。E3 的具体15种写法及六个上下文
完整固化在测试方法中，包含 CTE、INSERT SELECT、MPP CTAS、ROW／OIDS 兼容替换。
E2 复用 R3 的 `r3sweep.py`：十种形式分别加 `WHERE y = -1`、`WHERE y = z - 1`、
`WHERE y = 1`；对 `mpp_broad_matrix.cases()` 的578条输入分别前置 `/*+ H */`，
以及前置后去除尾部分号，再以空格连接 `; SELECT 1 - 1`，按完整合成文本去重得到1,143变体。
脚本和原始对照输出摘要保留在证据的 E2 字段，逐输入只公开摘要及三版状态。

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_matrix --output var/parser-probe/adj-expansion-rerun.json
.venv/bin/python -m sql_apm.sql.function_dictionary validate rules/functions/v1.0.1.json
.venv/bin/python scripts/functions/coverage.py
```

## 历史输入差分

[1,832条固定回放](data/sql-normalization-adj-2026-09-28.json)继续选取914条历史样本及原文
索引首尾各512条并精确去重。来源库、缓存及源码在前后核验中未变，得到1,810条可靠、
22条拒绝，其中20条近似可用、2条不可用；可靠1,566组。排除版本化指纹值及计时，逐条
审计字段与冻结 head 的 R3 回放和已提交 R2 整改证据相同，组成员集合也相同，failures为空。
来源保留、重复稳定、格式稳定、结构变化守恒均通过。

使用既有4个隔离进程、每进程512 MiB、每输入组合20秒／单阶段5秒预算，工作进程回放
16.993秒，不含前后文件摘要核验。复核使用新的输出路径，不能覆盖旧证据：

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_replay --output var/parser-probe/adj-normalization-rerun.json
```

[5,333条含 Hint 标记输入](data/sql-normalization-adj-hints-2026-09-28.json)复用 R3 的
`norm_hint.py`；选择源库内含 `/*+` 或 `--+` 字节标记的全部不同输入，标记也可能位于
字符串。脚本 SHA-256 为 `fd7f75e9678a76216c8cf33a45ba848eff471f05e1074b146e015426b38d7631`，
未改脚本或覆盖旧输出。结果为5,317条可靠、16条拒绝及4,323个可靠组：

- 与冻结 head 逐条状态、原因和实际 Hint 数相同；组成员集合完全相同，无新合并或拆分。
- 本次选择的5,333个ID与冻结证据相同，各条原文字节摘要与只读源库一致；源库摘要在核验前后不变。
- R3 原始输出摘要为 `2b063f3fa58c5d28c21e49f0a70637eb27cf9c1120b22601d078e0e3d08e04bf`；其 records 与已提交 R2 整改证据相同，提供可持久复核的基线。
- 单进程地址空间2 GiB、每输入20秒闹钟、核心输入上限512 KiB；循环8.9秒，不含文件摘要核验，没有超时、内存失败或处理异常。

复现时对报告 records 中的ID从同一只读 SQLite 源库取原字节，调用 Normalizer，比较
状态／原因／Hint 数及按指纹得到的ID集合分区；不要直接比较新旧指纹字符串。输入库摘要、
每条输入摘要、脚本摘要和源码上下文均在 JSON 中；源 SQL 与 Hint 原文保留本地忽略。

本次没有重跑8,909条近似差分：`approximate.py` 及其他解析分支未改，变动的映射器只在
实际 Hint 和减号同时存在时调用。5,333条标记全集涵盖所有可能进入变更路径的历史输入，
其中16条拒绝的结果未变，其余历史拒绝无法进入变更路径。因此裁决中“近似实现或拒绝
输入解析结果变化”的重跑条件未触发。这一判断针对固定历史索引，不外推新增日志。

本次真实样本仍未复现该合成常量缺陷，不能用历史组数不变替代 E1–E3 的退出验证；
未再次扫描115万条全量输入、执行源 SQL 或访问业务数据库，不宣称生产吞吐或全量语义准确率。

## 全部验收与终验交接

| 契约 | 本批复核依据 |
| --- | --- |
| A1／A6 | Python 3.9.5 核心／文本文件命令、重复和新进程测试；锁定依赖、规则快照和源码摘要 |
| A2 | 578例完整语法矩阵、81例扩展、E1–E3及历史回放 |
| A3／A5 | E1–E3、原72组、190组扩展、R1原反例及198／728位置扫描、明确拒绝与保护正反例 |
| A4 | 字典 validate／coverage和已知、未知、歧义、控制值／转换等完整专项 |
| A7／A10 | 失败／整批隔离与近似保护测试，R1原4组近似反例及16条历史 Hint 拒绝状态未变 |
| A8 | 固定样本来源、结构守恒审计与独立指纹分组差分；仅公开脱敏证据 |
| A9 | 接口、契约、日志、导航和新报告；交付前完整 Harness、在线 PR 契约及远程 quality |

原始需求快照及 `rules/` 对 merge base 不变，依赖锁定对冻结 head 不变，历史报告保持
原样。交付前逐行核对本批增量满足 B5，并执行：

```bash
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --pr 10 --repo shenxg13/sql-apm
```

固定新提交、完整检查及 CI 结果由 PR／Issue 审计评论绑定。完成后恢复 Ready 并交接
`status:needs-review`，由独立 Reviewer 对 A1–A10、不可跳过门槛、四项完整账本、B1–B5
及 `c013dbf..新head` 增量执行一次性 `final_verification`。实施方不签发该终验结论。
裁决要求终验只能 approve 或 fail；若失败，本裁决不允许再修复，须另作收敛裁决。
