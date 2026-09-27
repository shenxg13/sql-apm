# MPP 解析适配与 Hint 保留原型验证

> 目录调整说明：本文源码链接已更新为[当前目录布局](parser-layout-2026-09-27.md)。历史机器证据中的路径和摘要保持原样，对应当轮源码。

日期：2026-09-27（Asia/Hong_Kong）。承接[解析器结构保真探测](parser-fidelity-2026-09-27.md)，
对应 [Issue #9](https://github.com/shenxg13/sql-apm/issues/9) 中用户授权继续验证的步骤。

## 结论与交付边界

**pglast 加显式 MPP 扩展节点、独立 Hint 附件的路线，在本次范围内可行。**
同一组 140 个固定日志定位中，原型返回 124 个完整批次结构，明确拒绝 16 个输入。
其中 31 个输入此前被原始 pglast 拒绝；此前返回 AST 的 3 个词法不确定输入现在保守拒绝。
这个结果支持继续产品实现，不表示已完成 MPP 方言覆盖或生成了可靠业务指纹。

本次交付诊断目录下的适配原型、专项回归及重放证据。没有替换业务常量、接入函数字典、
定义正式接口或最终指纹编码，也没有连接数据库、执行 SQL、创建完整模块 PR 或完成
Issue #9 验收。已确认的功能范围保持有效；下面的原型限制不是删除验收要求。

## 材料、版本与语法依据

| 材料 | 用途 |
| --- | --- |
| [适配原型](../../sql_apm/sql/mpp_parser.py) | 显式 MPP 子句结构、普通 PG AST、Hint 附件及整批拒绝 |
| [有界验证程序](../../sql_apm/diagnostics/mpp_adapter_probe.py) | 固定定位重放、格式扰动、结构关系及新进程复测 |
| [17 项专项测试](../../tests/parser_probe/test_mpp_adapter.py) | 多组字段变异、格式、Hint、失败边界及输入保留断言 |
| [机器证据](data/mpp-adapter-probe-2026-09-27.json) | 140 个定位结果、67 个合成案例、17 组关系、4 项新进程检查与源码摘要 |
| [原合成案例](../../tests/parser_probe/fixtures/parser-fidelity-cases.json) | 沿用上一阶段的固定输入与关系，未改写此前分母 |
| [固定依赖](../../tests/parser_probe/requirements.txt) | 沿用已核对摘要的实验环境；没有新增产品依赖 |

环境是 CPython 3.9.5、pglast 7.18，原型版本为 `mpp-adapter-probe/1`。
pglast 内核为 PostgreSQL 17.7；来源系统为 PostgreSQL 9.4.26／Greenplum 6.20.3／
HashData 3.13.13。结构解析不验证对象存在性、权限、协议、现场扩展或 SQL 可执行性。

专属子句依据为固定提交的
[Greenplum 6 语法源文件](https://github.com/greenplum-db/gpdb-archive/blob/9a08259bd1836f0cf5ba935e7e0030a5a9c0a54b/src/backend/parser/gram.y)：
ALTER SET WITH／分布策略约 2997—3025 行，COPY ON SEGMENT 约 4022 行，分布子句约
4675—4840 行，外部表约 5415—5800 行，DROP EXTERNAL 约 7729 行。
本地核对的源文件 SHA-256 为
`3e20b140f6bfd3ab7e7a6abbcd20f00b7e3aef728167e22b74921d78bc0aa49c`。
它是上游语法依据，不是 HashData 私有源码或现场执行验证。生产版本差异仍需保留。

## 实现方式与已验证形式

先进行编码、词法完整性检查；使用 pglast 扫描 token，保留 Hint 内容和位置。
已识别的 MPP 子句逐项解析为有类型的扩展节点，再把对应源片段替换成等长空白，
由 pglast 验证其余标准语法。对象、列、选项、表达式等使用 PG AST 表示。
扩展解析必须消费整个子句，剩余词、未知分支或任何批次成员失败时，整个输入拒绝。
不返回部分成功结果，不回退到原始文本节点或文本哈希。

| 形式 | 原型保留的结构与核对重点 |
| --- | --- |
| CREATE TABLE／CTAS 的分布子句 | BY 的有序列及可选限定 opclass，RANDOMLY／REPLICATED；表／查询／临时属性／关系选项／ON COMMIT 保留于基础 AST |
| CREATE [READABLE／WRITABLE] EXTERNAL [WEB] TABLE | 显式模式、WEB 标志、列／LIKE 定义、LOCATION 有序列表或 EXECUTE 字符串 |
| 外部表执行与格式 | ON ALL／HOST／MASTER／SEGMENT／数量；FORMAT、旧式格式选项、有限 name=value 格式项、OPTIONS、ENCODING |
| 外部表错误处理及分布 | LOG ERRORS [PERSISTENTLY]、SEGMENT REJECT LIMIT、单位及是否显式指定、分布策略；拒绝上游禁止的组合与非法阈值 |
| DROP EXTERNAL [WEB] TABLE | 普通 DropStmt 的目标／限定／行为，加独立外部表标志 |
| COPY … ON SEGMENT | 查询或表、方向、文件／命令、选项保留于 CopyStmt，另存 ON SEGMENT 标志 |
| ALTER TABLE … SET WITH (…) [DISTRIBUTED …]／SET DISTRIBUTED … | 目标、ONLY 等基础信息，加独立选项／分布策略；只覆盖单个 MPP action |
| 普通查询、写入及混合批次 | 原始完整 PG 结构仅去坐标，保留语句顺序、函数控制模板、schema、转换、窗口及配置等 |

专项测试对分布列顺序／opclass、外部地址／命令／模式／格式／编码／阈值、COPY 目的地、
ALTER 选项和 Hint 内容／位置做应区分断言；普通注释、关键字大小写及空白做应相同断言。
还检查尾随垃圾、重复子句、非法列定义、未闭合输入及部分批次错误不会变成成功。
这些是逐字段合成证据，不能把表中每种形式外推成完整方言支持。

### 两个新发现的格式边界

固定版本 pglast 直接拒绝 `SELECT a NOT /* ordinary */ IN (1)`，也拒绝
`NULLS /* ordinary */ FIRST`；没有注释时接受。这造成初次格式扰动有 14 个输入失败。
原型现在仅对扫描器已确认的完整注释做等长空白替换，保留换行，再进行解析。
Hint 在替换前保存；字符串内的注释样式文本不被删改。NOT IN／LIKE／ILIKE／BETWEEN、
NULLS FIRST／LAST、行注释、字符串续接和 Hint 回归均通过。

Hint 只识别完整注释 token 以 `/*+` 或 `--+` 起始的形式，保留原始内容和有效 token 间隙。
空白化注释后再次扫描，以免普通注释把跨行续接字符串拆成不同 token 数，改变后续 Hint
的锚点。`SELECT 'a'\n'b' /*+ H */` 与在两个字符串片段间加入普通注释的结构一致。
位于续接字符串内部、无法用稳定间隙定位的 Hint 明确返回 `hint_inside_combined_token`；
有嵌套注释的 Hint 返回 `ambiguous_nested_hint`。没有尝试解释 Hint 语义或推断现场启用情况。

## 有界重放结果

沿用[类别调查附件](data/statement-census-2026-09-26.json)中的全部 140 个固定定位：
119 为 61、120 为 79，SQL 字段 92、不同内联 SQL 48，来自 11 个文件。
不是随机样本、唯一 SQL 数或独立执行数；本次没有扩大抽样或重新统计生产执行。

首次准备缓存时流式读取至各文件最后一个目标，核对文件大小及前后 stat、CSV 30 列、
SQL 摘要、行范围、来源和既有词法诊断，140 个定位全部匹配。
实际读取前缀共 2,090,754,048 字节，记录前缀摘要；沿用历史完整文件摘要，
**没有重算整个文件 SHA-256**。原文缓存只写入本地忽略的 `var/`。
后续修复重放只读该缓存，重新检查完整定位清单、证据摘要及每条 SQL 摘要，未反复读取原日志。

| 样本 | 原型返回完整结构 | 明确拒绝 |
| --- | ---: | ---: |
| 119：61 | 54 | 7 |
| 120：79 | 70 | 9 |
| 总计：140 | 124 | 16 |

16 个拒绝是：非法编码 3、括号不平衡 4、字符串未闭合 3、注释未闭合 2、普通字符串
反斜杠解释有歧义 3、基础语法拒绝 1。前五类共 15 个，与既有不确定输入全部对应。
余下 1 个在原调查中已标为 UNKNOWN，局部核对为单个标识符文本，未猜测或修复 SQL。
125 个没有既有词法诊断的输入中，124 个返回结构、1 个拒绝。

与上一阶段 pglast 直接解析相比：93 个原有成功输入继续成功，其每条基础 AST 与原解析
去坐标后的 AST 完全相同；31 个原语法拒绝输入通过专属结构适配；3 个原有成功输入因
来源词法不确定而拒绝。不能把简单的 96 → 124 差额解释为可靠率提升。

124 个成功输入的语句数均与原类别序列一致；其中扩展节点实例为分布子句 80、
CREATE EXTERNAL 5、DROP EXTERNAL 5、COPY ON SEGMENT 1、ALTER 分布／存储 2。
它们是节点实例计数，含重复定位及一个批次多个节点，不是执行数。
两个 ALTER 输入分别位于原定位的记录 213516／inline_distinct 和 541784／sql；
检查到 SET WITH 形式后补充了明确结构与反例，没有去掉该 action 后宣称成功。

### 合成、变异与稳定性

- 17 项专项测试通过，内部包括多组正反输入和字段断言。
- 既有 67 个合成案例：60 个返回结构、7 个拒绝。拒绝项为 GP 风格分区、空输入、
  仅注释、截断、不闭合字符串、含错误成员的批次及未知语法；没有把负例算成缺陷。
- 原 17 组成对结构关系全部符合预期。4 个指定合成案例在新进程中重复结果一致。
- 124 个成功生产输入逐一保持原 token 内容，仅在间隙加入换行和普通注释，
  124 个均继续解析并得到相同结构摘要。字面量及 Hint 内部文本没有被扰动。
- 每输入 512 KiB、每子进程 5 秒的探测停止边界均未触发。最终重放、格式变异、
  合成及新进程检查共 21.732 秒，不含首次日志读取和专项单元测试；未测峰值内存或全量吞吐。

`prototype_parsed` 只是实验状态；`comparison_digest` 仅用于结构比较，未应用常量规则，
不是产品指纹或可靠率。生产树没有逐字段人工全面审核，抽样成功不能代替完整验收。

## 当前限制与下一步

原型尚未适配 GP 风格分区、复杂自定义格式参数、旧式 LOG ERRORS INTO、多个 ALTER action
混合等分支；无法可靠锚定的 Hint 整批拒绝。普通 PG 解析基于较新内核，仍可能接受来源
版本不具备的语法，不能将本原型当成 HashData 语法合法性验证器。

下一阶段可将已验证的结构转换整理为产品解析层，继续完善支持清单与失败诊断，再接入
函数字典 1.0.1 和已确认的业务常量／参数规则。必须验证函数及类型保护优先级、规则版本、
可靠／不支持／失败状态和整批原子性；最终指纹表示与业务归并测试仍待实现。

## 复现与检查

使用已准备的项目 Python 3.9.5 和上一阶段固定实验依赖，从仓库根目录运行。
首次需要本地原日志；省略 `--root` 表示使用已核对的本地缓存，不表示只跑合成。

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_adapter_probe --root raw/inbox/hashdata \
  --output var/parser-probe/mpp-reproduction.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_adapter_probe --output var/parser-probe/mpp-reproduction.json
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
git diff --check
```

机器证据记录原型、验证程序、专项测试、复用函数和来源调查代码的摘要，以及案例、缓存和
来源附件摘要。对比复现结果时排除耗时。退出码 0 表示实验完成，具体拒绝仍须读取结果。
新增实验依赖不加入默认测试环境；专项测试通过独立命令运行，默认业务测试保持无此依赖。

本轮最终检查：新增 17 项专项测试、既有 31 项单元测试、完整离线 Harness 及
`git diff --check` 均通过；源码摘要与最终附件已核对。首次 Harness 因当前 PATH 未含
质量工具而未运行，改用已有 `var/harness-tools/bin` 后全部通过，未安装工具或降低版本。
原日志和 SQL 缓存仍被忽略，第一阶段脚本、案例及证据保持原字节。
本次未运行在线 PR 验收，也未把诊断阶段结论表述为 Issue 完成。
