# MPP 解析器扩展验证

> 目录调整说明：本文源码链接已更新为[当前目录布局](parser-layout-2026-09-27.md)。历史机器证据中的路径和摘要保持原样，对应当轮源码。

日期：2026-09-27（Asia/Hong_Kong）。用户在前一轮结果讨论后授权“开始扩展验证”。
本轮承接[MPP 适配原型验证](mpp-adapter-probe-2026-09-27.md)，仍属于
[Issue #9](https://github.com/shenxg13/sql-apm/issues/9) 的解析验证阶段，未交付业务归一化或正式指纹。

## 结论

**扩展验证发现并修复了一处结构丢失、两类真实 MPP 语法缺口和一个零值误拒绝。**
新增 210 个输入在修复前为 170 个结构／40 个拒绝，修复后全部返回完整结构并通过格式扰动。
原 140 个输入仍为 124 个结构／16 个拒绝。合计 350 个定位中 334 个返回结构；
这个分母由两轮定向抽样构成，不能推算生产支持率或可靠指纹率。

本轮最关键发现是混合 ALTER：`ADD COLUMN` 在 MPP action 前面时被遗漏，导致不同结构
得到相同摘要。已修复为整批明确拒绝，后续必须实现完整的有序 action 表示后才能支持。
因此，本轮增强了候选路线的证据，也证明“返回 AST”与“结构完整”必须分别验证。

原型版本升为 `mpp-adapter-probe/2`，实验比较摘要不能跨版本直接比较。
Python 3.9.5／pglast 7.18 及隔离依赖保持不变；未连接数据库或执行 SQL。

## 可复现材料

| 材料 | 用途 |
| --- | --- |
| [扩展采样与重放](../../sql_apm/diagnostics/mpp_expansion_probe.py) | 对此前未覆盖文件做有界前缀读取、形态去重和固定缓存重放 |
| [81 个合成案例及预期](../../tests/parser_probe/fixtures/mpp-expansion-cases.json) | 支持、已知缺口、保护边界、非法输入分开标记；包含字段与成对断言 |
| [合成验证入口](../../sql_apm/diagnostics/mpp_expansion_matrix.py) | 运行所有案例、字段检查、格式扰动与结构关系；预期不符时非零退出 |
| [原型](../../sql_apm/sql/mpp_parser.py) | 本轮缺陷修复与显式适配 |
| [新增专项回归](../../tests/parser_probe/test_mpp_expansion.py) | 丢失 action、零值、格式类型、ROW 边界及采样限制 |
| [综合机器证据](data/mpp-expansion-2026-09-27.json) | 修复前后新样本、原 140 回归、合成矩阵、字段比较、代表性脱敏结构及源码摘要 |

第一阶段脚本、原 67 个案例、以前两轮报告和证据未改写。新证据记录当前源码摘要；
历史报告的旧摘要对应当时原型，不能用新版程序冒称重现旧行为。

## 扩展抽样及证据边界

此前样本来自 46 个日志文件中的 11 个。本轮对其余 35 个文件逐一执行固定边界：
每文件最多读取 8 MiB、检查前 5000 条完整 CSV 记录，最多选择 6 个此前未选形态。
先去除与原 140 及已选输入重复的词法形态，再优先选择新的类别／特征及 MPP 组合。
形态由去掉评论后的 token 类别序列定义，忽略具体对象和值，**只用于取样**，不作为指纹。
它可能把不同语法合到同一取样桶，也可能因列数等差异产生多个桶，不证明语义身份。

实际读取 290,344,484 字节，检查 173,057 条完整记录、158,070 个 SQL／不同内联输入；
153,361 次形态已知或文件内重复，形成 4709 个候选形态，再选取 210 个输入。
候选计数是各文件当时的候选数之和，不是全局唯一结构数。34 个文件触及记录数边界，
另一个文件在字节边界截断，舍弃一条不完整尾部 CSV 记录；没有把采样造成的截断当成坏 SQL。

每个来源核对历史文件大小、读取前后 stat，保存实际前缀摘要、记录和物理行定位、SQL 摘要。
一个小文件的读取前缀覆盖完整文件，并核对了完整 SHA-256；其余 34 个没有重算全文件摘要。
读取边界与检查记录边界分别记录，预读字节并不表示相应 SQL 全部被选取或解析。
新原文缓存仅在本地忽略的 `var/parser-probe/expanded-input-cache.json` 中。
修复后的重放使用同一缓存，核对来源清单及每条 SQL 摘要，没有再次读取全部前缀。

本轮全部 35 个新增文件各选 6 个输入；新旧两轮涉及全部 46 个文件，仍然只是有界样本。
时间分布偏向文件开头，形态优先规则也有选择偏差，不代表全日、全部类别或罕见语法覆盖。

### 词法标签不能代替结构检查

新样本中的 47 个 `PARTITION BY` 标签经结构核对均位于窗口表达式，没有证明分区表已支持。
词法 WITH 标签为 54 个，而 AST 中有 withClause 的输入为 14 个，其他可能是建表选项等。
新样本另有 57 个包含 JOIN、20 个包含子查询表达式；这些指标可以重叠，不能相加当执行数。
报告中的“分区表未适配”继续有效。

## 新发现、修复与字段保留

### 混合 ALTER 的误接受与结构丢失

以下两条合成输入在原型 v1 得到相同结构摘要：

```sql
ALTER TABLE t ADD COLUMN x int, SET WITH (reorganize=true);
ALTER TABLE t ADD COLUMN y text, SET WITH (reorganize=true);
```

原因是用标准 PG helper 解析 ALTER 头部时，也接受了前面的普通 action；随后移除 helper
动作列表时把这些 action 一并丢弃。v2 在移除前验证动作列表只能含一个 helper action，
有其他动作就返回 `unsupported_multiple_alter_commands`。对前置 ADD／DROP／DEFAULT、
类型参数中的逗号及外层多语句批次均补充回归。后置或夹在中间的混合 action 继续整批拒绝。
这修复了误成功，没有把混合 ALTER 支持描述为已完成。

### 22 个外部表的裸格式标识符

旧实现只接受有限字面量参数，拒绝裸标识符。扩展后按上游 `format_def_item`／`def_arg`
建立结构：保留参数名、具体类型和值，限定标识符保留全部名称分量；有符号数字和保留字
参数名也有检查。`name=(column_list)` 单独保存有序列名列表，不回退成文本。

例如 `formatter=s.demo_in` 保留 TypeName 中的 `s`、`demo_in`；改 schema、函数名或
改为字符串 `'s.demo_in'` 都能区分。列列表换序、负值、尾随垃圾／逗号和非法表达式有反例。
该参数保持受保护的配置值，未应用业务常量替换。

### 18 个建表／CTAS 的 orientation=row

上游 `def_arg` 明确包含把裸 `ROW` 映射为 String("row") 的分支，PG 17 解析器缺少该分支。
v2 仅在 CREATE TABLE 头部或已识别的 MPP 选项中，将完整 RHS 的 ROW 转成等价字符串节点；
不处理 CTAS 查询中的 ROW 表达式，也不改字符串内容。基础 AST 保留全部关系选项。

普通建表、临时 CTAS、ALTER WITH、关键字大小写、quoted row 等价和 column 区分均已验证。
`orientation=row+1`、重复 WITH 和其他不合法组合仍拒绝。

### ON SEGMENT 0 的误拒绝

pglast JSON 对整数零省略默认的内部数值字段。旧实现把该省略解释为不支持整数；
v2 正确保留零，并验证 0／1 可区分。该测试验证语法及数值保留，不判断现场执行节点配置。

语法依据仍为固定
[Greenplum 6 gram.y](https://github.com/greenplum-db/gpdb-archive/blob/9a08259bd1836f0cf5ba935e7e0030a5a9c0a54b/src/backend/parser/gram.y)：
关系选项约 3179 行、格式定义约 5560 行、def_arg 约 7336 行；ROW 的专属映射在其中明确给出。
源文件摘要沿用[前轮记录](mpp-adapter-probe-2026-09-27.md#材料版本与语法依据)。
上游语法不是 HashData 私有实现或 SQL 实际执行证据。

## 结果与回归

| 输入集合 | 修复前返回结构 | 修复前拒绝 | 修复后返回结构 | 修复后拒绝 |
| --- | ---: | ---: | ---: | ---: |
| 新增 210 个 | 170 | 40 | 210 | 0 |
| 原 140 个 | 124 | 16 | 124 | 16 |
| 合计 350 个定位 | 294 | 56 | 334 | 16 |

原 16 个仍为编码异常 3、括号不平衡 4、字符串未闭合 3、注释未闭合 2、普通字符串反斜杠
歧义 3、UNKNOWN 文本 1。保护性拒绝不等于源 SQL 确定非法或数据库执行失败。

- 新 210 个全部通过保持 token 原文、只变更间隙空白／普通注释的格式检查；原 124 个也全部通过。
- 修复前已成功的 294 个真实输入，比较完整树并仅排除实验版本号后，294 个逐字段完全相同。
- 新 210 个的语句数全部与词法类别序列长度一致；代表性 CTAS、普通建表、13 语句批次、
  外部表及带 Hint 的批次核对了脱敏 token 形态、节点类别和字段集合。未人工逐值审核全部生产树。
- 新 81 个合成案例中，56 个支持，11 个有效形式仍属已知缺口，4 个属保守边界，10 个为非法输入。
  全部符合各自预期；47 项字段路径断言、23 组应相同／应不同关系及 56 项格式扰动均通过。
  11 个缺口不是“已支持”的通过数，后续需继续实现。
- 原 67 个合成案例仍为 60 个结构／7 个拒绝，17 组关系及 4 项新进程复测通过。
- 原专项 17 项加新增 6 项，共 23 项专项回归通过。

新样本最终重放及格式检查为 22.226 秒，原样本回归及原合成为 20.781 秒，均不含首次前缀
扫描。每次解析沿用 512 KiB／5 秒的停止边界，没有触发超限、超时或非预期异常。
采样耗时、峰值内存及全量吞吐未作为本轮性能验收；没有将局部耗时外推为产品性能。

## 尚未适配形式与具体后续方案

| 形式 | 本轮结论 | 后续实现方向 |
| --- | --- | --- |
| GP RANGE／LIST／子分区、ALTER 分区 | 合成正向语法仍被拒绝；新真实样本没有补足该证据 | 按上游分区 grammar 建立递归节点：键、命名分区、有序边界／值、包含标志、EVERY、默认分区及层级；选项和显式类型全部保留，独立验证嵌套及变异 |
| 普通动作与 MPP 动作混合 ALTER | 已修复丢失动作的问题，当前整批拒绝 | 单独解析共享表目标，顶层逗号拆有序 action；普通动作保留 AlterTableCmd，MPP 动作使用专属节点；任何动作失败则整批失败 |
| Unicode U& 字符串 | 显式转义仍被前置词法检查保守拦截 | 分开识别 U&／UESCAPE、E 字符串和普通字符串的扫描规则，补充边界与版本测试；不能整体放宽反斜杠保护 |
| 普通字符串反斜杠、嵌套 Hint、字符串续接内部 Hint | 继续保守拒绝 | 明确来源解释依据及可稳定表示的锚点；证据不足时保留诊断 |
| 旧式 LOG ERRORS INTO | 继续保守拒绝 | 对照来源版本明确目标对象、错误表行为及需要的上下文，再建立完整节点 |

上述方案没有删减 Issue 契约，也不表示已经实现。核心归一化、函数字典接入、正式接口、
规则快照和业务指纹仍待后续工作；本轮不创建完整模块 PR 或关闭 Issue。

## 复现与检查

固定依赖准备见[首次探测报告](parser-fidelity-2026-09-27.md#复现与检查)。从仓库根目录运行：

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_probe --root raw/inbox/hashdata \
  --output var/parser-probe/expanded-reproduction.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_probe --output var/parser-probe/expanded-reproduction.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_matrix --output var/parser-probe/matrix-reproduction.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_adapter_probe --output var/parser-probe/original-regression.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe -v
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
git diff --check
```

首次准备需要前轮原 140 个输入的本地缓存及全部原日志；后续省略 `--root` 使用新缓存。
综合附件保留各命令结果、修复前结果及补充结构核对；复现对照相应区块并排除耗时。
生产 SQL 不写入综合附件，实验结构摘要不能作为业务指纹。

本轮最终检查：23 项解析专项回归、31 项既有业务测试、完整离线 Harness 及
`git diff --check` 均通过。综合附件与当前源码／案例摘要已再次核对，原文缓存仍被忽略。
工作保留在 `issue-9-parser-fidelity` 分支，未提交／推送，未运行在线 PR 契约验收。
