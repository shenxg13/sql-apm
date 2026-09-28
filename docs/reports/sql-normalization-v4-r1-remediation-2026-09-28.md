# 结构指纹 v4 R1 整改验证（2026-09-28）

本报告记录 [Issue #11](https://github.com/shenxg13/sql-apm/issues/11)、
[PR #12](https://github.com/shenxg13/sql-apm/pull/12) 的实施方整改证据。
对应 [R1 统一账本](https://github.com/shenxg13/sql-apm/pull/12#issuecomment-5865451281)
的 I11-R1-F001、I11-R1-F002；独立 R2 负责验证退出条件，本文不代替评审结论。
原 R1 完成全面发现，已消耗一轮；本次没有重置轮次或改写历史报告。

## 依据与处理

- 目标与 merge-base：`a921dbbebbfb2863bb6969b043c48017ddfb7ab0`。
- R1 head／initial head：`ea85d015f30c1e64d4ba44c13484e6907d672c0f`。
- 冻结 v3 对照：`bd62856921aa806e109490c199482d099d560557`。
- 用户选择“接受 Hint 例外，更新 Issue 契约并补齐测试”，经 triage 同步在线正文后恢复实施，
  见[范围修订记录](https://github.com/shenxg13/sql-apm/issues/11#issuecomment-5865613229)。
- 未合并的 v4 继续使用 `sql-normalization/4`／`mpp-adapter/9`，规则快照增加 Hint 例外及
  FILTER／冲突更新位置边界，规则摘要随之改变。字典和解析器实现未变，新旧规则上下文不能直接比较指纹值。

| R1 问题 | 采用的退出路径 | 实施证据 |
| --- | --- | --- |
| I11-R1-F001（P2，B1） | 用户确认 Hint 例外，保留 anchor 原有位置身份 | 在线 B1、指纹契约、设计和规则快照同步；Hint 在列表前、后及批次边界的同桶／等长业务值用例 |
| I11-R1-F002（P2，B2／B8） | 修复 FILTER 越界，保留冻结 v3 行为 | 新增 FILTER 上下文，仍替换业务值但不折叠列表；冻结对照、嵌套 WHERE 及 ON CONFLICT 正反例；同步诊断投影与守恒审计 |

FILTER 的修复适用于投影、HAVING、排序及外层 WHERE 等位置。内部可遍历子查询进入自身
WHERE 时仍可分桶；未知函数的受保护子树继续保留。`ON CONFLICT DO UPDATE WHERE` 属更新条件，
适用分桶；`ON CONFLICT (…) WHERE` 属冲突目标推断谓词，仍整体保护。两者现在在契约和设计中明确区分。

诊断侧 `bucket_context` 独立于产品 walker，由冻结 v3→v4 投影和回放守恒审计共用。
不能仅凭 v3 的裸业务值标记认定可折叠：FILTER 已有这些标记但应保留列表。
合成冻结树同时核对投影与产品实际结果，避免把同一越界行为当作预期。

## 合成与既有回归

| 检查 | 结果 |
| --- | --- |
| 普通测试 | 46 项通过 |
| 全部解析专项 | 146 项通过，包含原有 Hint 反例及 198／728、72、57／180 例套件 |
| 新增冻结保护场景 | 8 类 × 3 变体，共 24 条结构摘要与冻结 v3 相同；原 69 条继续通过 |
| 新增独立投影对照 | 12 条冻结 v3 完整合成结构与整改 v4 实际结果相同；原 8 条继续通过 |
| Hint 例外 | 两种 Hint × 三种位置 × IN／NOT IN，12 组；同桶结构相同但 anchor 不同，指纹可不同；等长业务值替换仍归并，跨桶仍区分 |
| 嵌套与冲突更新 | FILTER 内独立 WHERE、外层 WHERE、DO UPDATE WHERE 分桶；冲突目标推断谓词不分桶 |
| 错误审计拒绝 | 即使 FILTER 中存在裸业务值，投影不折叠；守恒审计拒绝投影／HAVING／WHERE 中的 FILTER 越界桶及无 WHERE 上下文的桶 |
| 扩展矩阵 | 81 例及关系断言通过 |
| 字典 | 784 条规则 validate／coverage 通过，字典摘要未变 |
| 固定回放 | 1,832 条，1,810 可靠／22 拒绝，1,565 组，failures 为空；原文、重复、格式、结构守恒及阶段预算通过 |

新冻结证据为[合成夹具](../../tests/parser_probe/fixtures/normalization-v3-r1.json)，
生成时校验冻结提交和产品核心未修改。只含人工 SQL、结构及摘要，不含生产原文。
测试位于 [v4 边界](../../tests/parser_probe/test_normalization_v4.py)、
[差分投影](../../tests/parser_probe/test_normalization_diff.py)和
[回放守恒](../../tests/parser_probe/test_normalization_replay.py)。

## 真实数据差分

本次修改了归一化器及诊断投影，重新采集冻结 v3 与整改 v4 的完整契约选择集。
原始索引只读，每次运行记录输入库及源码的前后摘要；两侧使用同一份新版差分工具，
冻结侧只复制诊断模块，产品核心和字典保持冻结提交内容。

准确 IN 标记为 `b' IN '`、`b' IN('`（含前导空格）、`VALUES`、`ARRAY`，ASCII 大小写折叠；
Hint 选择 `/*+` 或 `--+`；固定集合使用既有 1,832 个 ID。
每条可靠结构经独立投影核对，不用“总组数没变”替代合并解释。

| 选择集 | 输入数 | 可靠／拒绝 | v3／v4 组数 | 合并／拆分／状态变化 |
| --- | --- | --- | --- | --- | --- |
| IN／VALUES／ARRAY | 200,390 | 197,371／3,019 | 71,026／69,243 | 1615／0／0 |
| Hint 标记 | 5,333 | 5,317／16 | 4,323／4,323 | 0／0／0 |
| 固定 ID | 1,832 | 1,810／22 | 1,566／1,565 | 1／0／0 |

[IN 差分](data/sql-normalization-v4-r1-in-2026-09-28.json)、
[Hint 差分](data/sql-normalization-v4-r1-hints-2026-09-28.json)及
[固定集合差分](data/sql-normalization-v4-r1-fixed-2026-09-28.json)均通过 `--require-v4`。
197,371／5,317／1,810 条可靠结构分别逐条一致，没有拆分、新拒绝或原因变化。
IN 组减少 1,783，4,287 条输入进入合并组，对应 21,915 次日志字段出现；Hint 分组不变。
固定集合唯一合并仍为 ID 916／917，仅由 IN 分桶解释。
[检查摘要](data/sql-normalization-v4-r1-checks-2026-09-28.json)记录最新规则上下文、源码、
输入、运行日志及完整本地回放的摘要，六份采集的选择、规模、资源和时间。
完整 Harness 与在线 PR 契约通过，原始索引及采集期间源码均未变。

两份 IN 契约采集实测耗时：v3 1371.580 秒，v4 1261.888 秒；包含结束时索引核验，
不含启动前的索引摘要。该用时受同机并行检查影响，不能与原型计时直接比较。

## 成本与复现边界

完整重新采集针对本次真实风险：FILTER 规则和独立投影均已变化，需要验证当前实现的每次归并。
单用旧 v4 快照不能代表修复后的上下文；合成测试不能满足 B6 的真实逐条核对。
未扩大为 115 万条全量解析，未执行生产 SQL。
两个大集合采集各用 2 个进程，总计 4 个；Hint／固定集各用 1 个进程。
每进程 512 MiB、单输入 5 秒、512 KiB 上限，沿用既有工具；约 2.1 GB 索引的前后摘要核验也计入成本。
固定回放实测 17.459 秒；采集实际用时保存于机器证据，不构成产品性能门槛。
生产原文、SQLite 快照和完整回放留在忽略的 `var/`，报告仅提交计数、摘要、上下文和输入 ID。

复现核心命令如下；冻结 worktree 的准备与工具复制方式见[接口说明](../design/sql-normalization.md#规则变更分组差分)。
输出须使用新名称，不能覆盖旧证据。两份 checkout 分别运行 capture 后使用 compare。

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff capture \
  --output var/issue11-r1fix-in-v4.sqlite --workers 2 \
  --marker ' IN ' --marker ' IN(' --marker VALUES --marker ARRAY --ignore-ascii-case
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff capture \
  --output var/issue11-r1fix-hints-v4.sqlite --workers 1 --marker '/*+' --marker=--+
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff capture \
  --output var/issue11-r1fix-fixed-v4.sqlite --workers 1 --ids var/parser-probe/issue11-fixed-ids.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff compare \
  var/parser-probe/issue11-frozen/var/issue11-r1fix-in-v3.sqlite \
  var/issue11-r1fix-in-v4.sqlite --require-v4 --text
.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m sql_apm.diagnostics.mpp_expansion_matrix --output var/parser-probe/issue11-r1fix-expansion.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m sql_apm.diagnostics.normalization_replay --output var/parser-probe/issue11-r1fix-replay.json
.venv/bin/python -m sql_apm.sql.function_dictionary validate rules/functions/v1.0.1.json
.venv/bin/python scripts/functions/coverage.py
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --pr 12 --repo shenxg13/sql-apm
```

契约、设计、源码职责、知识日志和文档导航已同步。原始需求、字典、近似算法、解析器及历史报告保持原样。
同桶长度的执行计划差异与 Hint 保守拆组为已确认边界；本轮没有扩展执行计划、生产部署或基线构建范围。
