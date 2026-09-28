# MPP SQL 解析器结构保真探测

> 目录调整说明：本文源码链接已更新为[当前目录布局](parser-layout-2026-09-27.md)。历史机器证据中的路径和摘要保持原样，对应当轮源码。

日期：2026-09-27（Asia/Hong_Kong）。对应 [Issue #9](https://github.com/shenxg13/sql-apm/issues/9)
的解析器选型步骤。用户本轮授权先执行代表性 MPP SQL 结构保真验证；完整归一化模块、
业务指纹、数据库读写及全量日志验收不属于本次交付。

## 结论

**两个候选都不能直接作为满足现有契约的完整 MPP 解析器。** 建议继续以 pglast
作为普通 PostgreSQL SQL 的解析基础候选，先验证 MPP 专属语法适配，再决定生产依赖。
本次固定的是实验依赖，不是已通过完整验收的产品选型。

- pglast 在合成用例中保留普通函数名称及控制文本、显式 schema、嵌套转换、窗口
  和批次结构；但拒绝分布子句、外部表及 `COPY ... ON SEGMENT` 等实际来源形式。
- SQLGlot 的部分不支持语法返回 `Command` 文本节点；它还会改写函数和格式模板。
  `to_date('20260101','YYYYMMDD')` 与模板改为 `yyyymmdd` 的调用被转换为同一 AST，
  不符合本项目保留函数控制文本的要求。
- 两者的默认 AST 都不能直接满足 Hint 的内容和位置要求。pglast 的词法评论接口
  可以支持额外保留；本次最小词法间隙方案通过了所列合成关系核对，但未交付产品识别器。
- pglast v7.18 对含中文错误输入返回的位置索引存在不一致，不能直接用于生产语法定位。
  最终探测仅从错误消息匹配预定语法词，不输出消息或依赖该索引归因。

这些结论不更改已确认支持范围。尚无证据表明关键 MPP 语法无法实现；下一步应证明
适配能完整表示专属子句，而不是删除子句、接受文本回退或缩小 Issue 验收范围。

## 环境与可复现材料

| 项目 | 本次实测 |
| --- | --- |
| Python | 项目解释器 3.9.5，Linux x86_64 |
| pglast | 7.18；内置解析器报告 PostgreSQL 17.7 |
| SQLGlot | 30.19.0，纯 Python，显式 `read='postgres'`、`ErrorLevel.RAISE` |
| 安装位置 | 本地忽略的 `var/parser-probe/site-packages`，未加入项目虚拟环境 |
| 依赖依据 | [固定版本及 wheel 摘要](../../tests/parser_probe/requirements.txt) |
| 诊断程序 | [parser_fidelity.py](../../sql_apm/diagnostics/parser_fidelity.py) |
| 合成输入 | [67 个案例、17 个成对关系及 15 个结构路径检查](../../tests/parser_probe/fixtures/parser-fidelity-cases.json) |
| 最终结果 | [机器证据](data/parser-fidelity-2026-09-27.json) |
| 补充人工复现 | [两条 MPP 合成输入及 Unicode 错误索引](data/parser-fidelity-manual-2026-09-27.json) |

候选版本与发行包来源：[pglast 7.18](https://pypi.org/project/pglast/7.18/)、
[SQLGlot 30.19.0](https://pypi.org/project/sqlglot/30.19.0/)。本次核对下载 wheel 的
SHA-256 与官方 PyPI 元数据一致；pglast 8.4 的发布文件没有本次使用的 CPython 3.9 wheel，
选择具有对应 wheel 的 7.18，未尝试源码构建 8.4，也不据此断言源码不兼容。

MPP 来源是 PostgreSQL 9.4.26／Greenplum 6.20.3／HashData 3.13.13。
解析器内核版本和来源版本不同；成功解析不证明该 SQL 能在来源数据库执行。
不调用数据库、不执行 SQL、不恢复会话设置或绑定值。

## 探测方法与结果含义

1. 每个候选在新进程中解析完整 SQL／批次。单输入上限 512 KiB、单次子进程等待上限
   5 秒，仅作为本次探测停止边界；超限、超时单列，不能记成语法不支持。本次未触发。
2. pglast 使用完整 JSON AST，只去除 `location`、`stmt_location`、`stmt_len`。
   SQLGlot 递归读取完整 `args`，排除源坐标及缓存类型，分别比较不带评论和带评论的结构。
   任一位置出现 `Command` 时，整个输入记为 `opaque_command`。
3. `parsed` 只表示返回 AST；`parse_error` 是候选拒绝；`input_encoding_error` 是编码异常。
   这些是实验状态，**不是**产品的 `reliable`、`unsupported_syntax` 或
   `normalization_failed`。未生成任何业务分组指纹。
4. 机器证据中的 `ast_digest` 等值仅用于实验结构比较。未替换业务值、未应用函数字典，
   也没有把这些摘要映射到 `Fingerprint.value`。
5. SQLGlot 日志和子进程 stderr 不向报告转发，异常只保留类型与固定语法词。
   生产输入只输出来源定位、摘要、固定节点种类和计数；合成输入可以保存 SQL 及检查路径。

## 合成结构核对

67 个固定案例包含正常语法、MPP 扩展、Hint、空输入、畸形输入及部分批次失败，
不是 67 条必须成功的 SQL。pglast：50 个 AST、15 个拒绝、2 个空结果；
SQLGlot：51 个 AST、11 个文本回退、4 个拒绝、1 个空结果。

| 具体语法／行为 | pglast 7.18 | SQLGlot 30.19.0 |
| --- | --- | --- |
| SELECT、WITH、JOIN、INSERT VALUES／SELECT、UPDATE FROM、DELETE USING | 所列案例返回结构 | 所列案例返回结构 |
| 普通建表、CTAS、ALTER ADD COLUMN、CREATE INDEX、DROP TABLE | 所列案例返回结构 | 所列案例返回结构 |
| `WITH (appendonly=true, orientation=column)` | 作为关系选项保留；不证明运行支持 | 返回结构 |
| `DISTRIBUTED BY`／`RANDOMLY`／`REPLICATED` | 拒绝 | `Command` 回退 |
| 带 GP 风格 RANGE 分区及分布子句的建表 | 拒绝，首先遇到分布子句 | `Command` 回退；未单独证明每个分区分支 |
| 简单 `CREATE EXTERNAL ... LOCATION ... FORMAT` | 拒绝 | 返回结构；不代表复杂外部表支持 |
| READABLE／WRITABLE、WEB EXECUTE 外部表、DROP EXTERNAL | 拒绝 | `Command` 回退 |
| `COPY ... ON SEGMENT`（补充合成） | 拒绝 | 返回 Copy；完整字段保真尚未专门验收 |
| 临时表＋选项＋ON COMMIT＋分布子句（补充合成） | 拒绝 | `Command` 回退 |
| to_date 普通调用及原始格式模板 | 保留名称及模板 | 改为 StrToDate，模板转为 `%Y%m%d` |
| 显式函数 schema、命名参数、嵌套调用 | 所列案例可检查其结构 | 普通及带 schema 调用存在不同表示，需适配 |
| 显式转换、自定义限定类型、类型修饰符 | 所列路径保留 | 有结构；regclass／text 转成 REGCLASS／TEXT，不能把大小写变化误称丢失 |
| DISTINCT、FILTER、OVER、窗口边界 | 所列路径保留 | 返回结构，产品规则适配另行验证 |
| 分号在字符串、美元引用体、注释内部 | 所列批次为两条语句 | 所列批次为两条语句 |
| DO 过程体＋SELECT | 外层为 DoStmt 和 SelectStmt，过程体仍为受保护文本 | 整批含 `Command` |
| 部分批次含不支持的分布表 DDL | 整批拒绝 | 返回 Select＋Command，必须整体视为未结构化 |
| 仅说明性注释和分号 | 空语句列表 | 返回 Semicolon，调用方须排除空语句节点 |

15 项 AST 路径核对全部符合记录的预期：13 项检查 pglast 的函数名／控制文本、schema、
嵌套 cast、类型修饰符、特殊调用标记、聚合修饰及批次边界；2 项核实 SQLGlot
实际的模板转换和 REGCLASS 表示。它们不是笼统的“两个候选都保真通过”。

17 项成对关系中，pglast 仅 AST 满足 13 项，缺的 4 项都涉及 Hint；加入本次实验
Hint 附件后满足 17 项。SQLGlot 去掉普通评论或保留全部评论的两种方案分别满足
12 项，失败集合不同，均不足以直接使用。4 个案例各对两候选做新进程复测，8 项一致。

### Hint 和位置

pglast 扫描器区分 `C_COMMENT`／`SQL_COMMENT` 和字符串。实验只把完整评论 token
以 `/*+` 或 `--+` 开头的形式视为 Hint 候选，保存其原始内容和之前的有效 token 数；
普通评论不计入间隙，字符偏移不进入比较。Unicode 输入的评论切片核对正确。
字符串／美元引用体中的伪 Hint、说明性嵌套评论中的伪 Hint 不被提取。
这不证明现场启用了任何 Hint 扩展，也不覆盖所有 Hint 写法、AST 节点关联或异常评论。

SQLGlot 保留全部评论时，Hint 内容差异可区分，但 `SELECT /*+ ... */` 与
`/*+ ... */ SELECT` 在本例挂到同一节点；普通评论还会影响格式归并。
后续两条路线都需要明确的 Hint 识别及独立位置保留。

含中文的错误反例：`SELECT '中文'; CREATE TABLE t(id int) DISTRIBUTED BY (id);`
中 DISTRIBUTED 的字符索引是 36、UTF-8 字节索引是 40，pglast 两种解析入口实测均返回 32。
这是该固定版本的观测，不把这个 index 当可信结构位置；后续 AST／词法／错误坐标要分别验证。

## 有界生产日志重放

复用[已有类别调查](statement-category-census-2026-09-26.md)附件中全部 140 个固定定位：
119 为 61、120 为 79；主 SQL 字段 92、不同内联 SQL 48；涉及 11 个文件。
沿用原调查按类别、词法异常和批次前缀选择的样本，不是随机抽样，也不是独立执行数。

每个文件流式读取至最后目标记录就停止。核对文件大小、前后大小与 mtime、CSV 30 列、
每条 SQL SHA-256、物理行范围、来源字段／源码位置及原词法类别／诊断；140 个定位全部匹配。
保存实际读取前缀的字节数和 SHA-256，读缓冲可能越过最后目标少量字节。
附件保留原调查的完整文件 SHA-256，但本次**没有重新计算完整文件摘要**。

最终受控运行用时 43.815 秒，包含合成探测、新进程检查、读取和候选解析；
读取前缀共 2,090,754,048 字节，SQL 输入长度为 4—80,959 字节。
为修正编码分类和 Unicode 错误定位问题，另有两次相同样本的中间运行，均不并入分母。
未测峰值内存或全量吞吐。这些耗时是实验实测，不能外推产品性能。

| 样本 | pglast AST | pglast 语法拒绝 | pglast 编码异常 | SQLGlot AST | SQLGlot 文本回退 | SQLGlot 语法／词法拒绝 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 119：61 | 38 | 22 | 1 | 35 | 21 | 5 |
| 120：79 | 58 | 19 | 2 | 55 | 17 | 7 |
| 总计：140 | 96 | 41 | 3 | 90 | 38 | 12 |

pglast 的 41 个拒绝中，固定消息明确指向 DISTRIBUTED 的 18 个、EXTERNAL 的 5 个，
其余 18 个仅记为其他语法拒绝，不用不可靠的位置索引猜测原因。SQLGlot 的 38 个文本回退
按完整输入计数，可能同一批次包含多个 Command；不能把局部成功计为整批成功。

原调查有 15 个词法／编码不确定输入。pglast 对其中 3 个、SQLGlot 对其中 4 个仍返回 AST，
说明“解析器接受”不能覆盖来源完整性与编码判断。其余 125 个无既有词法诊断的输入中，
pglast 为 93 个 AST／32 个拒绝；SQLGlot 为 86 个 AST／37 个回退／2 个拒绝。
无词法诊断也不证明来源 SQL 合法或执行成功。

人工核对最早文件的固定定位：记录 162 为 EXTERNAL WEB／EXECUTE 形式，204 含
COPY ON SEGMENT，463 含分布建表，9038 的批次含临时表选项／ON COMMIT／分布子句，
9044 的批次含 CTAS／DISTRIBUTED RANDOMLY。仅观察词法结构并用合成 SQL 复现，
生产名称、值、地址和脚本内容未写入仓库。

成功样本中，人工对照记录 3、6、14、15、21、43、56、84 的外层语句种类和顺序；
其中 43 的 SET＋INSERT 保留两节点，WITH 保持在 SelectStmt 中。
所有无既有词法异常且 pglast 返回 AST 的样本，AST 语句数与原类别序列长度相同。
SQLGlot 有一处数量不同（119 首文件记录 2515 的 inline_distinct），包含 Semicolon
等节点；这属于适配时需核对的空语句边界，不据此断言错误拆分了 SQL。
生产样本未逐字段人工审查整个 AST，成功计数不等于结构保真率或指纹可靠率。

## 下一步实施建议与未完成项

优先验证 `pglast + 明确的 MPP 语法适配 + Hint 附件`：

- 针对分布表／CTAS、外部表 LOCATION／EXECUTE／FORMAT／错误处理、COPY ON SEGMENT
  建立完整节点，保留对象、参数及子句组合；任何子句不确定时整体拒绝。
- 专属语法适配要消费和验证完整输入，不能仅去掉 EXTERNAL／DISTRIBUTED 让普通解析通过。
- 普通 PostgreSQL AST 到项目结构的转换需要明确保留特殊函数标记、显式限定、类型、
  窗口和批次信息；补齐坐标转换及 Hint 边界。
- 接入函数字典 1.0.1 后再验证业务值归并与保护优先级；本次不宣称字典接入已通过。

SQLGlot 可扩展方言，但要同时修复已观察到的函数模板转换、Hint 定位和文本回退。
本次证据支持优先探测 pglast 适配路线；尚未测量两条路线的开发成本，也未完成 MPP 适配原型。

## 复现与检查

从仓库根目录，使用已准备好的 Python 3.9.5。依赖安装需要获准的网络访问；
生产日志仅从本地忽略目录读取。

```bash
.venv/bin/python -m pip --isolated install --index-url https://pypi.org/simple \
  --only-binary=:all: --no-deps --require-hashes \
  --target var/parser-probe/site-packages \
  -r tests/parser_probe/requirements.txt
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.parser_fidelity \
  --root raw/inbox/hashdata --output var/parser-probe/reproduction
```

省略 `--root` 只运行合成案例，不访问生产日志。比较机器证据时排除 `seconds`。
`script_sha256`、`fixture_sha256`、来源附件摘要及固定依赖共同界定实验版本。
进程退出 0 仅表示探测完成；候选缺口以结果字段表示，不把退出成功解释为全部案例支持。

补充人工复现 JSON 中的两条 `synthetic_sql` 可传给脚本的 `probe(name, sql, True)`，
Unicode 反例可分别调用 `pglast.parse_sql` 和 `pglast.parser.parse_sql_json` 核对异常参数。

实际检查：Python 3.9.5 下既有 31 项单元测试通过，函数字典 1.0.1 严格校验及覆盖审计通过；
完整离线 `scripts/quality/check.sh` 通过，包含 Markdown、链接／知识引用及五组流程回归。
脚本、用例和来源证据摘要已再次核对；15 项结构路径检查及 8 项新进程复测通过。
首次 Harness 因新文件未跟踪而拒绝引用，标记本次新增文件为待添加后完整重跑通过。
本次不创建完整模块 PR，不宣称 Issue #9 A1—A9 完成，不运行在线 PR 契约验收。
