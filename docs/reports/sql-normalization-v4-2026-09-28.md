# 结构指纹 v4 实施与验证（2026-09-28）

按 [Issue #11](https://github.com/shenxg13/sql-apm/issues/11) 的已确认 B1–B8 契约，
实现 WHERE IN／NOT IN 列表粗分桶、全局 Hint gap 移出哈希、正式解析能力名称，
并交付可复现的脱敏分组差分命令。本文记录实施方验证，独立评审及合并由在线 PR 交接。

## 固定依据与实现

- 实施基线为 PR #10 合并后的 `a921dbbebbfb2863bb6969b043c48017ddfb7ab0`。
- 真实对照固定为 `bd62856921aa806e109490c199482d099d560557`，
  使用 `sql-normalization/3`／`mpp-adapter-probe/8`。
- 新版为 `sql-normalization/4`／`mpp-adapter/9`；Python 3.9.5、pglast 7.18，
  字典版本1.0.1及其内容摘要保持不变。规则上下文见下方机器证据。
- 归一化器沿用已有上下文遍历，先执行业务值规则，再将全部为裸业务值标记的
  IN 右侧 `List.items` 转为四种长度桶。混合列表及显式转换节点保留。
- 解析器仍提供全局 gap 供诊断；归一化结构排除它，完整 Hint anchor 原样参与身份。
  保留同语句词法结构、局部位置、带符号业务值处理及已确认的明确拒绝边界。
- 差分工具复用现有隔离进程，核心不依赖诊断模块；完整命令、数据格式和失败恢复见
  [接口说明](../design/sql-normalization.md#规则变更分组差分)。

新旧指纹都含规则上下文，不能直接比较字符串。差分按同一原文 ID 集合比较两侧分组分区。
冻结 v3 的实际规范结构由独立诊断投影仅应用 IN 四桶与 gap 移除，逐条与 v4 的完整结构摘要
核对；其他字段、列表、顺序和 Hint anchor 均不得变。这比只比较最终组数更严格。

[检查摘要](data/sql-normalization-v4-checks-2026-09-28.json)保留版本、源码、日志及完整回放文件摘要；
约3.9 MB的逐条回放中间结果留在本地，报告提交脱敏汇总及独立分区差分。

## 合成与既有回归

| 验证 | 实测结果 |
| --- | --- |
| 普通测试 | 46项通过 |
| 完整解析专项 | 141项通过 |
| 分桶边界 | IN／NOT IN、字面量／参数混用，1、2、10、11、100、101及同桶／跨桶组合通过 |
| 不适用位置 | 69条合成输入的完整结构摘要与冻结 v3 相同，涵盖混合列表、转换、函数、投影、JOIN／HAVING、控制位置及非目标 |
| 独立投影 | 8条实际冻结 v3 完整合成树与 v4 结果逐字段相同，含各桶边界及 Hint 批次 |
| Hint 位置保护 | R1位置反例、198例及728例位置扫描、R2的72组、裁决57及180例全部通过，错误合并为0 |
| 既有广覆盖 | 专项包含原578例结构矩阵；独立81例扩展矩阵及关系断言通过 |
| 函数字典 | 784条规则 validate及coverage通过，规则1.0.1未改 |
| 工具隐私／完整性 | 全部、标记、ID选择；文本及JSON；状态、合并、拆分、上下文；不完整快照、缺ID、摘要错误、旧输出保护均有测试 |

69条保护场景的冻结结构摘要和8条完整人工结构保存在
[合成夹具](../../tests/parser_probe/fixtures/normalization-v3-preserved.json)；不含生产 SQL。
测试分别位于[分桶测试](../../tests/parser_probe/test_normalization_v4.py)、
[差分工具测试](../../tests/parser_probe/test_normalization_diff.py)及既有 Hint／回放专项。

实施中，首轮固定回放发现新增审计错误地假定 IN 右侧直接是列表，实际容器为 `List.items`；
据此修正审计并增加冻结完整树测试，重新生成快照。该失败未计入通过证据，未修改产品的
既有保护规则，旧不完整快照留在本地并被比较命令拒绝。最终固定回放结果如下。

## 真实选择集与差分

来源为本地119／120原文索引，所有访问只读。需求阶段原脚本实际将输入ASCII大写折叠后，
匹配 `b' IN '`、`b' IN('`（含前导空格）、`VALUES` 或 `ARRAY`。本次已逐ID核对 Python 字节过滤与
原脚本的 SQLite 条件一致，得到相同200,390条；原脚本SHA-256为
`741b49f13f2a6dced4355f026f4d41a4cd410db86a352381e102a59395c6b0ac`。

初次采集的第二个标记缺少前导空格，得到更广的337,416条完整快照（334,381条可靠、3,035条拒绝）。
最终比较用 `compare --ids` 在两份完整快照内选择精确契约ID集合；所有请求ID必须存在、原文摘要必须一致，
不重新解析，也不把额外输入计入验收分母。快照采集工具绑定提交 `1317c96071791dd5bf5f0df340e8b9f746fccecd`，
后续仅新增离线比较的ID选择，产品核心不变；比较工具自己的源码摘要写入结果。
复现可直接用准确标记capture，或重建ID集合后复用广快照，命令见接口说明。
每份快照验证源库 SHA-256、选中输入原文字节
摘要和运行源码摘要；索引包含1,156,621个不同输入。标记是字节选择，可能出现在字符串内，
不等同于语法节点计数。生产原文、快照及运行日志均保留忽略的 `var/`。

| 选择集 | 输入数 | 可靠／拒绝 | 旧／新可靠组 | 合并／拆分／状态变化 |
| --- | --- | --- | --- | --- |
| IN／VALUES／ARRAY 契约字节子集 | 200,390 | 197,371／3,019 | 71,026／69,243 | 1,615／0／0 |
| `/*+` 或 `--+` Hint 字节标记 | 5,333 | 5,317／16 | 4,323／4,323 | 0／0／0 |
| 既有固定回放 ID 集合 | 1,832 | 1,810／22 | 1,566／1,565 | 1／0／0 |

- [IN 子集差分](data/sql-normalization-v4-in-2026-09-28.json)：197,371条可靠结构逐条审计一致，
  66,175条输入包含可分桶 IN；可靠组减少1,783，有4,287条输入进入合并组，
  共对应21,915次日志字段出现。全部合并都符合独立投影，无拆分、新拒绝或原因变化。
- [Hint 差分](data/sql-normalization-v4-hints-2026-09-28.json)：5,317条可靠结构逐条审计一致，
  14条包含可分桶 IN；没有新增拒绝、原因变化或分组合并／拆分。
- [固定集合差分](data/sql-normalization-v4-fixed-2026-09-28.json)：1,810条逐条审计一致。
  唯一合并是输入ID 916与917，两者各有1个业务值 IN 列表且都没有 Hint，证明该变化仅来自分桶；
  它们共出现174个日志字段，不能解读为174次执行。
- 固定回放同时通过原文保留、重复稳定、格式稳定和完整结构变化守恒检查，failures为空；
  4个隔离进程、每进程512 MiB，组合看门狗20秒、单阶段5秒。

## 资源、恢复与证据边界

分桶沿用显式栈及线性列表检查，不新增产品服务或重算编排（qualitative）。
真实差分用于识别误合并、意外拆分或新拒绝；单靠合成测试或比较总组数不能满足 B6 的
每次合并可解释要求。最终验收限定在契约字节子集、Hint集合及固定ID；意外扩大选择的采集成本和337,416条分母如实保留，
没有重跑115万条全量解析。

较大的两份快照各使用2个隔离进程，总计4个工作进程；每进程512 MiB、每输入5秒、512 KiB。
Hint及固定ID快照各使用1个进程。完整性核验包含前后读取约2.1 GB原文索引的摘要成本。最终子集比较复用已完成快照，
避免为同一原文再次执行归一化；每处合并仍逐条核对完整结构。
完成前不会把快照标为complete；中断重试必须选新输出，已有原文和旧快照不覆盖。
本次实测（measured）两份337,416条快照分别用时1451.256／1356.632秒，计入结束时索引核验，
不含初始索引摘要；期间并行执行了小集合验证，不能直接与需求阶段原型计时比较。
这些是本地诊断资源安排，不构成产品吞吐门槛或生产环境性能证明。

同桶列表可能产生不同执行计划或耗时分布，此权衡已获确认，本次未验证执行计划相近性。
Hint anchor保留原有词法位置身份；没有改成AST节点锚定。完整SQL语义等价、生产部署、
基线落库、训练窗口重算、日志导入及检索服务不在本次范围。

## 验证命令与交接

```bash
.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m sql_apm.diagnostics.mpp_expansion_matrix --output var/parser-probe/issue11-expansion.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m sql_apm.diagnostics.normalization_replay --output var/parser-probe/issue11-replay-verified.json
.venv/bin/python -m sql_apm.sql.function_dictionary validate rules/functions/v1.0.1.json
.venv/bin/python scripts/functions/coverage.py
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --pr PR --repo shenxg13/sql-apm
```

完整 Harness 与在线 PR #12 契约检查均通过，日志摘要保存在检查证据中。
差分的capture／compare和冻结版本重现命令见接口说明；复跑选用新的输出名称。
指纹契约、设计、源码布局、命令说明、知识日志及文档导航已同步；原始需求快照、
`rules/`、近似核心、依赖锁定和既有历史报告保持原样。PR评论将绑定固定提交、完整检查、
CI结果及独立评审交接；实施方证据不代替独立评审结论。
