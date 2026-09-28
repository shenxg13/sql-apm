# 归一化 R1 整改验证（2026-09-28）

本次修复 Issue #9 的两个 R1 阻断：可靠指纹的 Hint 位置碰撞，以及近似路径被 Hint 绕过
函数／类型转换保护。用户授权“进行R1整改”；没有改变 A1–A10 的验收范围。
这是实现方修复验证，独立 R2 仍须复核退出条件，不能据此宣称评审通过或 Issue 完成。

## 固定来源与版本

- [R1 完整问题账本](https://github.com/shenxg13/sql-apm/pull/10#issuecomment-5857840098)。
- [明确退回整改](https://github.com/shenxg13/sql-apm/issues/9#issuecomment-5857865167)，所有者继续为 `codex-parser-probe-20260927`。
- R1 固定 head／initial head：`e9f8a7fc14e9d65121e4c5f98a3f0136462ec398`。
- 固定 target tip／merge base：`a69d3d1a9a36550b78402e826920e2bf466c240e`。
- 冻结 Issue 契约 SHA-256：`e3a1a11c7e622ecba25124853b65692a64c0657ffa826a7652fa598a6835e1ce`。
- 新代码身份由 PR 修复提交和[新回放](data/sql-normalization-r1-2026-09-28.json)的逐文件源码摘要固定，不覆盖 R1 或历史证据。

| 上下文 | 原版本 | 当前版本 |
| --- | --- | --- |
| 可靠归一化 | sql-normalization/1 | sql-normalization/2 |
| 解析能力 | mpp-adapter-probe/5 | mpp-adapter-probe/6 |
| 观察用近似 | sql-approximate/1 | sql-approximate/2 |
| 依赖／字典 | pglast 7.18／1.0.1 | 不变 |

算法规则及快照内容摘要同步改变。所有新可靠值均带新版本上下文；比较回放时按组内输入成员
比较分区，不直接拿新旧指纹字符串判断语义变化。字典规范摘要仍为
`74ee855341d41f9fb6df217bd351222806f0f11a8c19d2d2b777138f9c824f45`。

## I9-R1-F001：Hint 位置碰撞

原算法只保存全局 token 间隙。下面两个合成输入的 AST 相同，Hint 的 gap 都是6，旧可靠
指纹也相同，实际 Hint 已跨越语句边界：

```sql
SELECT (a) FROM t /*+ H */; SELECT b FROM u
SELECT a FROM t; SELECT /*+ H */ b FROM u
```

修复为在完整 AST 之外记录 Hint 所属语句、语句内 token 间隙和保留括号的完整有序 token
种类序列摘要；空语句间及末尾分号后的 Hint 单独标记批次边界。原 `gap` 保留，但不再单独
承担位置身份。仅增加语句序号和局部 gap 仍不足，测试还覆盖了同语句、相同 token 总数、
不同括号位置的碰撞。实现与精确字段说明见[接口](../design/sql-normalization.md)。

有序序列只作完整 AST 的位置补充，不是原文哈希或解析失败降级。普通格式及说明性注释
不进入序列；值类 token 统一为 VALUE，是否保留其内容由完整 AST 和既有归一化规则决定。
因此相同 Hint 位置的业务字面量／原生参数仍能归并。Hint 内容、次序、位置改变仍区分。
每个含 Hint 的语句最多计算一次序列摘要；间隙使用有序端点二分查找，无 Hint 时跳过新增计算。

## I9-R1-F002：Hint 绕过近似保护

原算法把 Hint 保留为 token 后，只检查紧邻括号的 token 来识别函数或转换。下面输入中，
10 改为20会得到相同近似值、替换1处，违反整个函数／转换应保留的规则：

```sql
SELECT * FROM t WHERE custom /*+ H */ ((SELECT x FROM u WHERE id = 10)) AND (
SELECT * FROM t WHERE (id = 10) /*+ H */ ::boolean AND (
```

修复使用排除 Hint 的语法视图统一判断上下文和保护范围，再将可替换位置映射回原 token
序列。Hint 内容及输出位置保持原样。上述两种输入现在均替换0处，10／20近似值不同；
原结构状态仍为 unsupported_syntax，可靠值为空，拒绝原因保留且 observation_only=true。
明确的直接业务值两侧有 Hint 时仍按原业务规则替换，新增正向用例验证了映射和 Hint 顺序。

## 验证结果

Python 3.9.5 下完成以下检查，结果及源摘要见[补充证据](data/sql-normalization-r1-checks-2026-09-28.json)：

| 检查 | 结果 |
| --- | --- |
| 原始 R1 复现 | F001四组、F002四组均修复；四个无 Hint／普通注释对照通过 |
| 普通测试 | 45项通过 |
| 解析及归一化专项 | 107项通过，包含578例完整树／拒绝矩阵和格式对照 |
| 扩展语法矩阵 | 81例及关系断言通过 |
| Hint 同类扫描 | 9种等价 AST 括号写法 × 所有间隙 × 块／行 Hint，共198个位置组合，身份均区分 |
| 近似保护同类扫描 | 7类函数／转换 × 无注释／普通注释／块 Hint／行 Hint／多个 Hint，共35组，保护值均保留 |
| Normalizer 近似回退 | 函数／转换 × 块／行／多个 Hint，共6组，可靠失败与观察隔离保留 |
| MPP／批次／续接字符串 | MPP及嵌套查询 Hint、语句及空批次边界、格式归并、合并字符串内 Hint 拒绝均通过 |

对应测试为 [Hint 边界](../../tests/parser_probe/test_hint_boundaries.py)、
[近似保护](../../tests/test_approximate.py)及已有解析／归一化测试。新回放固定复用914条历史
样本，再取原文索引首尾各512条，精确去重后1,832条；来源库、缓存和源码在回放前后摘要相同。
4个隔离进程、每进程512 MiB、每输入验证组合20秒、单阶段5秒预算；工作进程回放16.874秒
（不包含前后文件摘要核验）。未执行源 SQL、连接业务数据库或重扫115万条日志。

| 回放项目 | 结果 |
| --- | --- |
| reliable／unsupported_syntax | 1,810／22，与原样本逐条相同 |
| 可靠组 | 1,566，新旧组的输入成员集合完全一致 |
| 来源保留、重复稳定、可靠格式稳定、完整结构变化守恒 | 全部通过；failures为空 |
| 拒绝中的近似状态 | 20个可用、2个不可用；原拒绝原因及可靠结果为空保留 |
| 逐条公开审计字段 | 排除可靠指纹值及耗时后，与旧回放完全一致 |

真实回放只有5个可靠输入含 Hint；拒绝样本仅22条。它不能证明全部旧失败的新版近似分组
不变，也不能证明 Hint 的全量语义准确率；本次缺陷退出条件主要由定向反例和同类扫描验证。
新规则仍可能因括号、正负数字或分号差异保守拆组，不做 Hint 语义等价推断。
旧全量解析和9,293条近似报告保持不变，旧近似重放工具继续校验其版本5来源，不能用当前
代码放宽校验再复用旧结论。观察统计、持久化、生产部署和数据库语义验证不在本次范围。

## 复核命令

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_matrix \
  --output var/parser-probe/r1-expansion-rerun.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_replay \
  --output var/parser-probe/r1-normalization-rerun.json
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --pr 10 --repo shenxg13/sql-apm
```

回放输出路径必须不存在；真实输入留在本地忽略目录。新报告只导出固定诊断、结构字段路径、
计数、指纹和来源定位；补充证据中的 SQL 全为 R1 合成反例。交付检查、修复 head 与 R2
独立复核交接保存在同一 PR／Issue 的审计评论，正式轮次不因修复提交重置。
