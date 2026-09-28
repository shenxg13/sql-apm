# 混合 ALTER 完整结构修复

> 目录调整说明：本文源码链接已更新为[当前目录布局](parser-layout-2026-09-27.md)。历史机器证据中的路径和摘要保持原样，对应当轮源码。

日期：2026-09-27（Asia/Hong_Kong）。用户在讨论混合 ALTER 的保护性拒绝后要求
“继续执行修复”。本轮补齐普通操作与已适配 MPP 操作的有序混合支持，承接
[扩展验证](mpp-expanded-validation-2026-09-27.md)，仍属于
[Issue #9](https://github.com/shenxg13/sql-apm/issues/9) 的解析原型阶段。

## 修复结果

原型版本从 `mpp-adapter-probe/2` 升至 `/3`。以前整批拒绝的六个混合 ALTER 合成案例，
现在都能返回完整结构。普通 ADD／DROP／ALTER COLUMN、约束、SET／RESET 关系选项等，
可与 `SET WITH (...) [DISTRIBUTED ...]`／`SET DISTRIBUTED ...` 交错；顺序和每个节点均保留。
多个 MPP 操作也分别保留，不做合并或去重。

以下原碰撞案例现在能区分，列名和类型均出现在相应普通操作节点中：

```sql
ALTER TABLE t ADD COLUMN x int, SET WITH (reorganize=true);
ALTER TABLE t ADD COLUMN y text, SET WITH (reorganize=true);
```

分别改变列名、类型、关系选项、分布策略或操作顺序的回归也通过，避免仅靠同时改变多个字段
掩盖遗漏。此处比较的是实验结构，尚未生成正式业务指纹。

## 实现及结构约定

[实现](../../sql_apm/sql/mpp_parser.py)先确定共享表目标，再按最外层逗号切分操作。
表目标支持限定名称、引号标识符、IF EXISTS、ONLY 的两种形式及继承星号，由 PG 验证完整语法。
类型修饰、函数、约束中的括号，数组方括号，字符串／美元引用体内逗号不参与操作拆分。

MPP 操作先完整解析为专属节点。为验证整条 ALTER 的组合语法，临时把这些操作替换成标准 PG
helper；PG 返回的动作数量必须与源操作数量一致，表目标必须一致，且各替换位置必须恰好对应
一个预期 helper 节点。随后按**源操作索引**组装结果：

- 普通操作：`postgres_alter` 包装完整的 `AlterTableCmd`，不挑选或删除内部字段。
- MPP 操作：`alter_distribution` 保存选项和分布策略。
- 对已适配 ALTER，`base.AlterTableStmt` 保存共享目标和修饰，`extensions` 是**全部操作的有序列表**。
  单个 MPP 操作继续使用原有结构；纯普通 PG ALTER 仍保留原始 `cmds` 表示。

内部 helper 不进入派生结果。即使用户真实写出与 helper 完全相同的普通 SET 操作，
也按其原索引完整保留，不凭名称识别或删除。这一情形有前后各一个普通操作的专门回归。

任何操作不完整、含未消费文本、属于未适配语法，或者结构核对失败，仍拒绝完整 SQL／批次，
不返回部分动作。原始 SQL 不改写，临时 helper 只供内存解析，未执行任何数据库命令。

语法依据为既有固定提交的
[Greenplum 6 gram.y](https://github.com/greenplum-db/gpdb-archive/blob/9a08259bd1836f0cf5ba935e7e0030a5a9c0a54b/src/backend/parser/gram.y)：
ALTER TABLE 入口约 2516 行，逗号连接的动作列表约 2675 行，MPP SET 分支约 2997 行，
relation_expr 约 12780 行。上游 grammar 与来源系统执行合法性仍须区分。

## 验证证据

[新增专项测试](../../tests/parser_probe/test_mixed_alter.py)覆盖完整普通节点、六种操作排列、
多个 MPP 操作、目标修饰、Unicode／引号对象、嵌套逗号、Hint、helper 同名操作，以及
任意位置错误导致整批失败。旧保护性拒绝测试已升级为正向字段保留断言。

[合成矩阵](../../tests/parser_probe/fixtures/mpp-expansion-cases.json)中的六个原缺口案例改为支持，
增加每个动作的字段断言及原碰撞／操作顺序关系。总案例数保持 81，不用新增分母掩盖原问题。
[机器证据](data/mixed-alter-2026-09-27.json)保存源码摘要、逐定位回归、65 项字段结果、
新旧行为及六个完整合成结构。历史报告和证据不覆盖，版本上下文不能混用。

| 验证 | 本轮结果 |
| --- | --- |
| 解析专项回归 | 33 项通过，其中新增混合 ALTER 专项 10 项；包含多组子用例 |
| 扩展合成矩阵 | 81 个结果符合预期：62 个结构、5 个仍未适配、4 个保护边界、10 个非法输入 |
| 扩展字段／关系检查 | 65 项字段断言、25 组成对关系、62 项格式扰动通过 |
| 混合 ALTER 新进程复测 | 六个案例逐一运行两次，完整结果一致 |
| 原合成矩阵 | 67 个：60 个结构、7 个拒绝；17 组关系和4项新进程复测通过 |
| 原 140 个真实输入 | 124 个结构、16 个拒绝，原因不变；124 项格式扰动通过 |
| 新 210 个真实输入 | 全部返回结构，210 项格式扰动通过 |
| 完整树回归 | 修复前成功的334个真实输入，排除原型版本号后逐字段完全相同 |

真实输入仅从既有忽略缓存读取，核对缓存和 SQL 摘要，没有重新扫描或扩大日志范围。
这350个定位没有提供新增混合 ALTER 的真实正向样本；其作用是验证回归，混合语法正向证据
来自明确列出的合成输入。不能把334个原有成功结构当成334条混合 ALTER 的验收。

原16个拒绝原因仍为编码异常3、括号不平衡4、字符串未闭合3、注释未闭合2、普通字符串
反斜杠歧义3及UNKNOWN文本1，不因本次支持混合操作而放宽词法保护。

## 边界与复现

覆盖的是上述普通 PG 动作与已实现 MPP SET 操作的混合。分区专属 ALTER、其他尚未适配的
MPP 动作、U& 字符串等缺口仍有明确失败；合法性还可能受现场版本、对象状态和组合执行规则
影响，本轮没有数据库执行验证。完整归一化、函数字典接入和正式指纹仍未交付。

从仓库根目录，使用既有 Python 3.9.5 及固定隔离依赖：

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_matrix --output var/parser-probe/mixed-matrix-reproduction.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_adapter_probe --output var/parser-probe/mixed-original-reproduction.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_probe --output var/parser-probe/mixed-expanded-reproduction.json
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
git diff --check
```

需要已有两组本地缓存；首次缓存准备见前轮报告。综合证据以先前证据摘要关联历史定位，
保存每条当前结构摘要及修复前后排除版本号的结构摘要。复现对照相应结果并排除耗时。
这些摘要只用于实验比较，不是产品指纹；生产原文仍留在本地忽略区域。

本轮33项解析器专项测试、31项既有业务测试、完整离线 Harness 检查及 `git diff --check`
均通过；综合证据记录的源文件摘要与当前文件一致。改动尚未提交，Issue #9 继续保持进行中，
本报告仅验收混合 ALTER 解析修复。
