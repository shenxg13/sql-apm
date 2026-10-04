# SQL 归一化与结构指纹接口

当前 `sql-normalization/5` 实现完整输入的解析、上下文归一化、确定性结构编码和结构指纹，
并接入独立的[观察用近似路径](sql-approximate.md)。核心位于
[normalization.py](../../sql_apm/sql/normalization.py)，业务边界由
[SQL 指纹契约](../../.project-wiki/contracts/sql-fingerprints.md)定义。
不执行 SQL，不连接数据库，不恢复绑定值，不补取或猜补完整 SQL。

## 依赖与调用

Python 3.9.5、pglast 7.18；[运行依赖](../../requirements.txt)锁定 CPython 3.9 Linux x86_64
wheel 摘要。SQLGlot 仅用于旧候选解析实验，不是产品模块依赖。首次在仓库根目录安装：

```bash
.venv/bin/python -m pip install --require-hashes -r requirements.txt
```

无网络且已有本地 wheel 时可用 `--no-index --find-links var/parser-probe/wheels`。
本次验证另装于忽略的 `var/normalization-runtime`，命令通过
`PYTHONPATH=var/normalization-runtime` 使用；这不是正式部署路径。
当前提供仓库内 Python 包与命令，尚未打包发布安装制品。

```python
from sql_apm.sql.normalization import Normalizer

engine = Normalizer()  # 默认 rules/functions/v1.0.2.json；构造时验证并固定快照
one = engine.normalize('SELECT * FROM orders WHERE id = 1001')
two = engine.normalize('SELECT * FROM orders WHERE id = $2')
assert one['fingerprint']['state'] == 'reliable'
assert one['fingerprint']['value'] == two['fingerprint']['value']
assert one['approximate'] is None

snapshot = engine.rule_snapshot()  # 调用方保存，用 context.rules_ref 核验
```

自定义规则使用 `Normalizer(FunctionDictionary.load(path))`。构造失败时抛出配置／依赖异常，
不能沿用部分字典或降级近似。构造后使用独立快照，修改原字典对象、输出上下文或快照副本
不会影响引擎。普通调用不读文件、不访问外部服务；可复用同一实例，没有跨调用结果缓存。

## 输出与逻辑字段映射

| 字段 | 约定 |
| --- | --- |
| `source` | 输入字节长度、精确 SHA-256、base64 原字节；哈希只用于来源核验，不是结构指纹 |
| `context` | algorithm_version、parser_version、parser_dependency、profile、dictionary_schema_version、dictionary_rules_version、dictionary_digest、rules_digest、rules_ref |
| `normalized` | 完整有类型结构与 Hint；可靠时非空，不是可执行 SQL，也不能替代原文 |
| `fingerprint.kind` | 固定 structural，和 approximate 明确分开 |
| `fingerprint.state` | reliable／unsupported_syntax／normalization_failed |
| `fingerprint.value` | 仅 reliable 非空，格式为 `struct:sql-normalization/5:` 加 SHA-256 |
| `fingerprint.reason` | reliable 时 null，否则固定诊断，不包含解析器错误原文 |
| `approximate` | 只在解析明确拒绝时尝试；结构异常、归一化异常、配置和资源错误不回退 |
| `diagnostics` | 替换节点数、IN 分桶列表数、函数选择原因计数；不含对象名、参数值或异常原文 |

`context` 可映射 [Normalization](offline-data-contract/fields.md#normalization-与-fingerprint)，
`fingerprint` 可映射 Fingerprint 的状态、值及原因。数据库 ID、SqlText、Group、训练资格和
执行状态由后续程序建立；本模块不因可解析或有指纹而断言执行成功或允许训练。
空输入返回诊断，调用方不得据此伪造完整 SqlText／Fingerprint 实体；非 str／bytes 参数抛出
TypeError。不完整文本的观察结果遵守独立近似契约，不填入可靠 Fingerprint.value。

规则依据包括算法版本、适配器 `mpp-adapter/9`、固定 pglast 版本、字典规范内容摘要
及算法规则摘要。`rules_ref` 是规范规则快照的内容地址；**必须连同 `rule_snapshot()` 的
内容保存**，只有摘要不能解释历史规则。该快照含完整字典和算法规则说明，字典规则／参数
列表按已有摘要语义排序；不把字典文件排版摘要误作 dictionary_digest。算法实现由版本化
源码定义，发布时须一并保留对应代码。改变上下文产生不同身份，不直接跨版本比较指纹。

指纹输入是带 `sql-apm-structural` 类型域、固定 context 和 normalized 的规范 JSON：
键排序、ASCII 转义、无多余空白、列表保留顺序。序列化和 AST 处理均可使用显式栈处理深层
结构，不提高进程全局递归限制。原文、诊断计数和计时不参与指纹。

## 归一化规则

只在明确的 AST 位置替换完整常量／参数节点，统一使用 `SQLAPMBusinessValue` 标记；
不通过文本正则枚举完整 SQL。语法不支持由解析层明确拒绝，未知节点保留整个子树。

| 位置 | 行为 |
| --- | --- |
| SELECT／UPDATE／DELETE 的 WHERE | 数字、字符串、原生 `$n` 使用统一标记；NULL／布尔保留 |
| WHERE、SELECT 列表及 JOIN ON 的 IN／NOT IN 列表 | 全部元素归一化为裸业务值标记时，以 1／2–10／11–100／>100 桶代替元素序列；Hint anchor 仍可能区分同桶长度 |
| 聚合 FILTER | 保持 v3 业务值替换与列表长度，不对 FILTER 条件分桶；可遍历子查询的独立 WHERE 按自身上下文处理 |
| ON CONFLICT 的两种 WHERE | DO UPDATE WHERE 按更新条件分桶；冲突目标的推断谓词整体保护 |
| INSERT 的直接 VALUES、UPDATE 的直接 SET（含多列赋值） | 数字、字符串、原生 `$n` 使用业务值标记；算术表达式中的常量保留 |
| 函数参数 | 字典逐参数动作；允许参数中的直接业务值可替换，已知嵌套调用用自己的策略 |
| 控制参数、未知／歧义／停用／待核实函数 | 整个受保护子树保留，外层 WHERE 或内层已知函数不能绕过 |
| 显式转换 | 保留转换节点与类型修饰；只有已确认标量类型允许深入，bool／oid／reg*／JSON／数组／未知类型等整体保护 |
| SQL 特殊函数、命名参数、VARIADIC | 整体保留；不把 EXTRACT、TRIM、COALESCE、NULLIF 等当成普通同名函数 |
| SELECT 目标列表、JOIN ON | 与 WHERE 相同，包含算术表达式常量和 IN 分桶；USING 列表保留 |
| CASE 结果、HAVING、ORDER／GROUP BY、DISTINCT ON | 沿用 v4；非函数字面量保留，函数仍按字典 |
| SET 配置、LIMIT／OFFSET、数组下标、窗口定义 | 整个控制子树保留，包括里面的函数及子查询 |
| CTE、子查询、COPY 查询、CTAS／VIEW 查询、完整批次 | 进入各自查询上下文；保护子树不被外层上下文绕过；语句顺序保留 |
| UNION／INTERSECT／EXCEPT 分支 | 每个未包装的 SelectStmt 分支使用独立 SELECT 规则；包括 WHERE、函数及 FILTER；集合分支中的 VALUES 是查询 VALUES，不作为 INSERT 直接值 |
| DDL 定义、存储／外表选项、MPP 扩展 | 保留完整结构和参数；不将对象名、分区边界、配置当业务值 |

IN 分桶在已有业务值归一化后执行，编码为 `{"SQLAPMInBucket":"2-10"}` 等四种标记；
IN／NOT IN 运算符、左侧表达式和其余结构继续保留。列、NULL／布尔、表达式、函数或
显式转换节点均不是裸业务值标记，混合列表保留原有长度及元素结构，仍执行既有常量规则。
CTE／子查询及集合运算各分支的 WHERE、SELECT 列表和 JOIN ON 适用同一规则；
HAVING、函数参数、聚合 FILTER 及受保护子树不扩大。
FILTER 继承冻结 v3 的业务值替换能力，但保留 IN 元素序列；进入内部子查询时重新建立查询上下文。
`INSERT … ON CONFLICT … DO UPDATE … WHERE` 属更新条件；冲突目标的 `ON CONFLICT (…) WHERE`
属于受保护的推断谓词，不因 WHERE 关键字相同就纳入分桶。
`IN (子查询)`、多行 VALUES 和 ANY／ARRAY 不折叠。桶边界写入规则快照；以后修改必须升级版本。
同桶归并不保证执行计划或耗时相近，实际执行样本仍分别计数。

函数名使用 AST 已解码标识符，保留引号大小写与显式 schema。只依据显式的已知类型转换
帮助匹配；不从普通字符串、数字或列名猜函数重载、隐式转换及数据库目录。普通调用按
函数／聚合候选策略共识选择；OVER／聚合语法限定相应候选。字典不支持或策略有歧义则
保留，不把未知函数一律视为解析失败。默认内置函数及 search_path 假设沿用现有字典契约。

## 语法支持矩阵与限制

解析沿用已验证的完整 AST 与有类型 MPP 适配，依赖其固定能力版本。支持表示取得结构，
不表示本模块验证了数据库中的对象存在、类型正确或语句能够执行。

| 语法类别 | 当前处理及证据 |
| --- | --- |
| 查询、写入、事务、SET、常见 PG DDL、批次 | PG AST 保留；归一化按上表位置执行 |
| DISTRIBUTED BY／RANDOMLY／REPLICATED、CTAS | 类型化 distribution 扩展，保留分布键、策略及查询结构 |
| 外部／可写外表、FORMAT／EXECUTE／LOCATION | 既有 external 节点，保留执行文本、位置、格式和配置 |
| COPY ON SEGMENT、ANALYZE ROOTPARTITION | 类型化扩展，不丢失源特有语义 |
| 混合 ALTER、ALTER TRUNCATE PARTITION | 保留动作顺序及完整对象；无法支持的子动作导致整批拒绝 |
| 范围分区建表、旧式 WITH OIDS | 保留边界、显式类型、分区名及存储选项 |
| LIST／复杂子分区、部分 ADD／EXCHANGE／SPLIT、部分 COPY 选项、U& 字符串 | 仍有明确拒绝；不承诺全部 MPP 方言 |

完整解析证据与拒绝原因见[全量修复报告](../reports/mpp-full-repair-2026-09-27.md)、
[广覆盖矩阵](../reports/mpp-broad-validation-2026-09-27.md)；本模块真实归组证据见
[v4 验证报告](../reports/sql-normalization-v4-2026-09-28.md)。正式解析能力为 `mpp-adapter/9`；
历史报告中的 `mpp-adapter-probe/*` 及旧探测模块保留原样，用于重现当时结果。产品入口为 Normalizer。

普通注释由扫描器确认后忽略；仅以 `/*+`、`--+` 开始的注释作为 Hint，内容原样保留。
`/* + … */`、`-- + …` 在此可靠路径属于普通注释；近似路径的更保守识别边界见其接口。
解析结果仍提供全局 `gap` 供诊断；版本4的 `normalized.hints` 排除该字段，
位置身份只使用既有 `anchor`：所属非空语句的零起始序号 `statement_index`、
语句内 `token_gap` 和按常量边界规范化的有序 token 种类序列摘要 `syntax_sha256`。
该序列保留括号、关键字、分隔符及真正的运算符；普通字面量和参数的种类统一为 `VALUE`，
值是否受保护仍由完整 AST 与归一化规则决定。摘要使用规范 JSON 的 SHA-256，只补充 Hint 位置，不能独立替代 AST
或作为原文哈希降级。没有 Hint 时不计算该序列摘要；同一语句的多个 Hint 复用一次摘要。

版本3使用 PG 已解析的数值 `A_Const` 源位置，确认哪些一元负号已折叠进常量。
适配器版本8仅对 `location` 为非负整数的常量做源 token 映射。PG 为无长度的 char／bit、
默认 FETCH 数量、substring FOR 起点和 interval 修饰生成的常量可能没有位置或位置为负；
它们没有源 token，不能携带源码中被折叠的负号，因此跳过源位置映射，AST 中的值仍完整保留。
这些负号不再单独计入序列、全局 `gap` 或语句内 `token_gap`，同一边界的 `1`、`-1`、
`- -1` 等业务值因而仍可归并，包括 Hint 在值之后或批次后续语句的情况。
二元减号、未折叠的正负号表达式、括号及类型转换继续保留；保留值的正负仍由完整 AST 区分。
来源位置在清理 AST 前取得，显式转换 UTF-8 字节与字符偏移，并回映 ROW／OIDS 兼容替换的
长度变化；不按相邻关键字猜测一元／二元角色，也不修改原 SQL。没有 Hint 或减号时跳过此处理。

Hint 恰好位于被折叠负号之后时，规范间隙无法区分该负号前后的位置，返回
`hint_inside_folded_sign`，整批不产生可靠指纹；例如 `x=- /*+ H */ 1`。
二元运算符后的 `x=y - /*+ H */ 1` 仍可锚定。以下三项是源文本、AST 和适配替换映射
不一致时的防御性不变量，不是受支持的有效 SQL 因包含合成常量而应被拒绝的语法边界：

- `hint_constant_location_unmapped`：非负整数位置没有对应源 token。
- `hint_constant_span_unmapped`：映射出的折叠负号没有数值常量跨度。
- `hint_sign_inside_adapter_replacement`：负号落在适配器生成的替换文本中。

这三项由构造不一致内部数据的辅助函数测试验证，仍明确拒绝而不猜定锚点；有效输入由
定向、广覆盖及历史回放验证。证据见[收敛整改报告](../reports/sql-normalization-adj-remediation-2026-09-28.md)。
上述版本3／适配器版本8的负号规则由版本4保留；本次新增 IN 分桶并移除全局 gap，
当时升级为 `sql-normalization/4`／`mpp-adapter/9`。
当前 v5 继承上述 Hint 编码，新增 SELECT 列表、JOIN ON 和集合分支恢复。
v4 与 v5 的规则摘要、快照及全部指纹身份不同，不能直接比较指纹字符串；
当前没有持久化基线，不执行历史基线迁移。

`anchor.kind=statement` 表示 Hint 位于语句开始前、内部或终止分号前。
位于空语句间或最后一个分号后的 Hint 使用 `batch_boundary`，记录下一非空语句序号
（末尾为语句总数）、全批次间隙和包含分号的全批次序列摘要，避免猜定其语句归属。
原始边界以屏蔽注释后的重新扫描结果定位，再统一排除已确认折叠的负号；
嵌套歧义或 Hint 落在合并字符串 token 内仍明确拒绝。
普通空白、说明性注释及关键字大小写变化有稳定性验证；Hint 内容、内部空白和位置不推断等价。

版本1仅保存全局 token 间隙，R1 发现冗余括号被 PG AST 清理后，不同语句或子句的 Hint
可产生相同身份；此前“只会保守拆组”的描述不成立。版本2以语句归属和完整词法结构补足
这一位置约束，详见[整改验证](../reports/sql-normalization-r1-remediation-2026-09-28.md)。
R2 进一步确认版本2因正负数字拆组违反业务值归并约定，版本3已修复，不能以文档限制豁免。
版本4不再因前面另一条语句多出冗余括号、导致全局 gap 移动而拆分后续语句的 Hint。
Hint 所属语句的局部间隙和完整词法结构摘要保持原规则；同语句的括号、列表长度或批次边界
仍可因 anchor 不同而区分，不推断 Hint 的位置等价。IN 分桶不改写 anchor。
这是用户在 R1 整改中明确接受的 B1 例外，见[确认记录](https://github.com/shenxg13/sql-apm/issues/11#issuecomment-5865613229)：
Hint 在同语句 IN 列表之前／之后或批次边界时，同桶列表长度不同仍可能不同指纹；
等长业务值替换及原有带符号归并继续适用。定向反例、冻结对照与真实差分见
[v4 R1 整改报告](../reports/sql-normalization-v4-r1-remediation-2026-09-28.md)。
不宣称所有语义等价写法都能合并。
此版本未做查询优化、交换律、绑定关系、目录／会话重建或隐含对象解析。

## 命令、退出码及资源

```bash
.venv/bin/python -m sql_apm.diagnostics.normalize_sql \
  --sql 'SELECT * FROM orders WHERE id = 1001' --include-rules

.venv/bin/python -m sql_apm.diagnostics.normalize_sql --file /tmp/input.sql
```

命令复用核心接口，可通过 `--dictionary` 指定字典。输出 JSON 含原字节和归一化结构，
是诊断输出而非脱敏报告；不要将生产原文输出直接提交。`--include-rules` 输出完整规则依据。
退出码 0 为 reliable，2 为结构拒绝且近似可用，1 为无可用结果、资源／配置／处理失败。
原有 approximate_sql 命令继续维持旧的“解析诊断＋近似”语义，不冒充可靠归一化入口。

输入上限 512 KiB，文件入口只读上限加一个字节，超限不给任何指纹，也不对前缀归一化。
文件超限时 source 只描述实际读取的前缀和超限诊断，不能当完整文件身份。
核心不提供进程超时、并发调度、持久化和恢复；批量调用方负责隔离、保存固定规则和重试。
时间／内存随文本、AST、函数数量增长，深层 JSON 无全局递归设置；不宣称已通过生产吞吐验收。

## 验证入口

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m unittest discover -s tests/parser_probe -p test_normalization.py

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe

.venv/bin/python -m sql_apm.diagnostics.normalization_replay \
  --output var/parser-probe/normalization-rerun.json
```

全体旧解析实验测试需要其独立 requirements；归一化专项只需要运行依赖。重放使用本地
忽略的三个历史样本缓存及全量原文索引，只读复用914条样本，默认再取索引最早／最晚各512条，
按精确 SQL 去重；不是随机抽样或全量归一化验收。每条分别核对来源、重复输出、格式稳定及
AST 变化守恒，报告仅含固定字段路径、计数、指纹和来源定位。4个隔离进程，每进程512 MiB；
每个输入包含最多4次解析的验证组合，总看门狗20秒，各阶段另外核对5秒预算。最终报告
保留实际阶段用时；一个组合超时不能被报告为“核心单次归一化超时”。

## 规则变更分组差分

`python -m sql_apm.diagnostics.normalization_diff` 是本地诊断命令。`capture` 使用当前
checkout 的 Normalizer 处理输入，`compare` 比较输入 ID 对应的分组成员集合；
不跨上下文直接比较指纹字符串。两份快照必须来自同一原文索引，ID、原文摘要和出现次数
逐条一致，否则明确失败。规则上下文即使不同也会在报告中完整标注。

下面保留 v3→v4 的历史选择集复现命令，应在对应冻结 v4 checkout 中运行。
当前 v5 的全量差分命令见本节末尾；输出文件名不会切换算法版本。
从对应 checkout 根目录运行（已有固定依赖时使用以下 PYTHONPATH）：

```bash
# 默认全部输入；重复 --marker 表示字节 OR，不是 SQL 语法过滤。
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff capture \
  --output var/parser-probe/v4-in.sqlite --workers 4 \
  --marker ' IN ' --marker ' IN(' --marker VALUES --marker ARRAY --ignore-ascii-case

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff capture \
  --output var/parser-probe/v4-hints.sqlite --marker '/*+' --marker=--+

# IDs 文件为正整数 JSON 数组；与 --marker 互斥，缺少任何指定 ID 都失败。
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff capture \
  --output var/parser-probe/v4-selected.sqlite --ids var/parser-probe/selected-ids.json

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff compare \
  var/parser-probe/v3-in.sqlite var/parser-probe/v4-in.sqlite \
  --require-v4 --output var/parser-probe/v4-in-diff.json --text
```

`--source` 可指定已有原文 SQLite 索引，默认 `var/parser-probe/full-scan.sqlite`；
读取已有 inputs／occurrences 表，不更改原文索引。标记默认精确匹配 UTF-8 字节，
`--ignore-ascii-case` 只折叠 ASCII 大小写；字符串和注释内标记也可能被选中。
没有 marker 或 IDs 参数即处理全部输入，应按任务契约选择验证规模。

快照只含 ID、原文摘要、固定状态／原因、结构指纹、结构摘要、计数及规则和源码上下文；
不保存 SQL、Hint 原文或 AST 值。标记选择条件也仅保存摘要。出现次数来自日志字段，
不是执行次数。快照强制放在当前 checkout 的 `var/`；输入库在运行前后核验摘要，快照摘要由比较命令记录。
只有完整完成、源库及源码未改变时才标记 complete；中断保留不完整快照，比较命令拒绝使用，
恢复时选择新输出重新执行。已有输出不会覆盖，没有跨版本缓存复用或产品重算编排。

默认4个工作进程，可选1–8；每进程512 MiB、每输入5秒看门狗及512 KiB输入上限，复用
现有隔离进程。资源或内部错误记录固定状态，不转为可靠结果。文本摘要和 JSON 比较结果
只包含计数、版本、摘要及至多10组、每组至多10个 ID 示例；完整快照继续留在本地。
合并／拆分在两侧均可靠的输入上判定；组数另按每侧全部可靠输入计算，状态变化单独报告。

`compare` 默认仅报告差异，成功读取和比较返回0；格式、完整性、来源或选择集不一致返回1。
`--require-v4` 附加检查 frozen v3／adapter8 与 v4／adapter9 的上下文及字典／依赖一致性，
逐条核对预期结构摘要；任何状态／原因变化、拆分或无法解释的结构变化都返回1。
旧版采集时，在原归一化结果的副本上独立替换符合 WHERE 上下文的裸业务值 IN 列表并移除全局 gap，
保留其他每个字段和 Hint anchor，再生成预期摘要。新版实际完整结构必须与其相同，
因此不会仅凭“总组数减少”就声称每次合并正确。FILTER 中的裸业务值不触发分桶；
嵌套查询的 WHERE 与冲突更新条件独立建立资格。投影和回放守恒审计共享诊断侧上下文检查，
不调用产品 walker；冻结 v3 合成结构同时验证该检查和实际结果。该投影是 diagnostics 的验证逻辑，产品不调用它。
其他未来版本仍可用通用 capture／compare；本次 v4 投影不会自动认定未来规则正确。

首次引入工具时，冻结 v3 尚无该模块，按如下方式复现。`V4_COMMIT` 应设置为含工具的固定
交付提交；在两个 checkout 中使用同一份工具，分别使用各自未经修改的产品核心和字典：

```bash
base_dir="$PWD"
git worktree add --detach var/parser-probe/v3-evidence bd62856921aa806e109490c199482d099d560557
git show "${V4_COMMIT}:sql_apm/diagnostics/normalization_diff.py" \
  > var/parser-probe/v3-evidence/sql_apm/diagnostics/normalization_diff.py
(
  cd var/parser-probe/v3-evidence
  PYTHONPATH="$base_dir/var/parser-probe/site-packages" "$base_dir/.venv/bin/python" \
    -m sql_apm.diagnostics.normalization_diff capture \
    --source "$base_dir/var/parser-probe/full-scan.sqlite" --output var/v3-in.sqlite \
    --marker ' IN ' --marker ' IN(' --marker VALUES --marker ARRAY --ignore-ascii-case
)
```

对 Hint 集合改用上述两个 Hint 标记；对固定1,832条回放，从其脱敏报告 records 提取
input_id 为 JSON 数组，并在两份 checkout 上使用相同 `--ids`。比较时直接传入冻结
checkout 内的快照路径即可。工具与核心源码摘要均写入快照；旧报告及其证据不改写。

比较命令也支持 `--ids`，可在两份已完成快照内选择同一子集，复用已计算结果；缺少任何ID仍失败，
完整快照行数仍须与元数据一致。比较输出保存选择集摘要及比较工具源码摘要。这样可以对已有
较广快照逐项审查，无须为改变诊断选择而重复解析原文。

本次200,390条契约集合的准确标记是 `b' IN '`、`b' IN('`、`VALUES`、`ARRAY`，第二项含前导空格；
短写为 `IN(` 会额外选中词内片段。直接使用上面的准确capture命令即可复现。
如复用较广快照，可先从同一只读索引导出ID，再比较：

```bash
.venv/bin/python - <<'PY'
import json
import sqlite3
from pathlib import Path
source = Path('var/parser-probe/full-scan.sqlite').resolve()
markers = (b' IN ', b' IN(', b'VALUES', b'ARRAY')
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as db:
    ids = [uid for uid, raw in db.execute('SELECT id,sql FROM inputs ORDER BY id')
           if any(marker in raw.upper() for marker in markers)]
with Path('var/parser-probe/in-contract-ids.json').open('x') as out:
    json.dump(ids, out)
PY
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff compare \
  var/parser-probe/v3-in.sqlite var/parser-probe/v4-in.sqlite \
  --ids var/parser-probe/in-contract-ids.json --require-v4 --text
```

### v4→v5 全量审计与分类

v5 快照写明两个新增位置、集合分支恢复和全部保护边界。
`compare --require-v5` 要求冻结 v4／当前 v5 均使用 `mpp-adapter/9`，
字典、profile、pglast 一致；逐条实际 v5 结构须等于冻结 v4 的预期结构。
任何状态／原因变化、拆分、未解释结构变化或缺少阶段摘要均使验收失败。

独立验证由 `normalization_v5_audit.py` 完成，不调用 v5 walker 或 `_fields`。
它在冻结 v4 结果副本上依照明确的可遍历字段及保护边界替换 SELECT／JOIN 值；
共享未改变的字典选择和转换分类。集合分支仅接受版本为 v4 的引擎：
把未包装的分支包装为独立 SelectStmt，调用冻结 v4 walker 恢复旧规则，
再应用独立位置投影。嵌套分支逐层处理；扩展结构、Hint 和所有其他字段保持。

冻结快照同时保存“仅 SELECT”和“SELECT＋JOIN”的完整结构摘要。
对每个最终 v5 合并组，若所有成员的仅 SELECT 摘要一致，归入 `select_list`；
否则若 SELECT＋JOIN 摘要一致，归入 `join_on`；其余经最终投影核验的归入
`set_branches`。这是固定顺序的互斥归因，混合原因归入最后所需步骤；
不是三个相互独立的反事实实验。三类组数相加等于最终合并组数。
耗时诊断在实际五维分组内重新执行同样归因，避免跨数据库／用户的合并影响分类。

`V5_COMMIT` 设为本次交付的固定提交；在新 checkout 中只复制两份诊断模块，
产品核心、字典和解析器保持冻结原样。示例输出路径须尚不存在：

```bash
base_dir="$PWD"
git worktree add --detach var/parser-probe/issue15-v4 9a0f9f507a4e068f32c8704da28c761d6e65ca14
git show "${V5_COMMIT}:sql_apm/diagnostics/normalization_diff.py" \
  > var/parser-probe/issue15-v4/sql_apm/diagnostics/normalization_diff.py
git show "${V5_COMMIT}:sql_apm/diagnostics/normalization_v5_audit.py" \
  > var/parser-probe/issue15-v4/sql_apm/diagnostics/normalization_v5_audit.py
(
  cd var/parser-probe/issue15-v4
  PYTHONPATH="$base_dir/var/parser-probe/site-packages" "$base_dir/.venv/bin/python" \
    -m sql_apm.diagnostics.normalization_diff capture \
    --source "$base_dir/var/parser-probe/issue13/full-scan.sqlite" \
    --output var/v4.sqlite --workers 8
)
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff capture \
  --source var/parser-probe/issue13/full-scan.sqlite \
  --output var/parser-probe/issue15/v5.sqlite --workers 4
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_diff compare \
  var/parser-probe/issue15-v4/var/v4.sqlite var/parser-probe/issue15/v5.sqlite \
  --require-v5 --output var/parser-probe/issue15/full-diff.json --text
```

全量使用 #13 的完整七天索引，不按关键词缩小分母。v4 每输入含归一化及三个投影，
隔离任务总预算为 20 秒；v5 单次采集仍为 5 秒，输入／内存上限保持 512 KiB／512 MiB。
这不是将组合任务时间称为产品单次耗时。两侧同时运行时本轮冻结 v4 用 8 个、v5 用 4 个进程（本机 12 核／24 GiB）；
前者增加了独立投影工作，单进程内存上限保持不变。
源码摘要含审计模块，运行前后核验；所有快照必须完整，不能混用修改前后结果。

合成冻结证据位于 `tests/parser_probe/fixtures/normalization-v4.json`；
记录固定 v4 提交、上下文、输入、原结构和仅恢复集合分支后的 v4 结构，
仅含人工 SQL。专项测试核对结构摘要、独立投影及当前 v5，旧 Hint 回归全部保留。
真实耗时命令、参照分母及分类解释见[Duration 诊断说明](../runbooks/duration-dispersion.md)。
