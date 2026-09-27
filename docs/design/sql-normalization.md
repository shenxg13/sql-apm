# SQL 归一化与结构指纹接口

首版 `sql-normalization/1` 实现完整输入的解析、上下文归一化、确定性结构编码和结构指纹，
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

engine = Normalizer()  # 默认 rules/functions/v1.0.1.json；构造时验证并固定快照
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
| `fingerprint.value` | 仅 reliable 非空，格式为 `struct:sql-normalization/1:` 加 SHA-256 |
| `fingerprint.reason` | reliable 时 null，否则固定诊断，不包含解析器错误原文 |
| `approximate` | 只在解析明确拒绝时尝试；结构异常、归一化异常、配置和资源错误不回退 |
| `diagnostics` | 替换节点数、函数选择原因计数；不含对象名、参数值或异常原文 |

`context` 可映射 [Normalization](offline-data-contract/fields.md#normalization-与-fingerprint)，
`fingerprint` 可映射 Fingerprint 的状态、值及原因。数据库 ID、SqlText、Group、训练资格和
执行状态由后续程序建立；本模块不因可解析或有指纹而断言执行成功或允许训练。
空输入返回诊断，调用方不得据此伪造完整 SqlText／Fingerprint 实体；非 str／bytes 参数抛出
TypeError。不完整文本的观察结果遵守独立近似契约，不填入可靠 Fingerprint.value。

规则依据包括算法版本、适配器 `mpp-adapter-probe/5`、固定 pglast 版本、字典规范内容摘要
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
| INSERT 的直接 VALUES、UPDATE 的直接 SET（含多列赋值） | 同上；算术表达式中的常量保留；原生参数号在允许位置不构成身份 |
| 函数参数 | 字典逐参数动作；允许参数中的直接业务值可替换，已知嵌套调用用自己的策略 |
| 控制参数、未知／歧义／停用／待核实函数 | 整个受保护子树保留，外层 WHERE 或内层已知函数不能绕过 |
| 显式转换 | 保留转换节点与类型修饰；只有已确认标量类型允许深入，bool／oid／reg*／JSON／数组／未知类型等整体保护 |
| SQL 特殊函数、命名参数、VARIADIC | 整体保留；不把 EXTRACT、TRIM、COALESCE、NULLIF 等当成普通同名函数 |
| SELECT 普通常量、CASE 结果、HAVING、JOIN 条件 | 未确认的非函数常量保留；不扩大 WHERE 规则 |
| SET 配置、LIMIT／OFFSET、数组下标、窗口定义 | 整个控制子树保留，包括里面的函数及子查询 |
| CTE、子查询、COPY 查询、CTAS／VIEW 查询、完整批次 | 进入各自查询上下文；WHERE 不泄漏到内层投影或控制位置；语句顺序保留 |
| DDL 定义、存储／外表选项、MPP 扩展 | 保留完整结构和参数；不将对象名、分区边界、配置当业务值 |

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
[归一化验证报告](../reports/sql-normalization-2026-09-27.md)。源码中 `probe/5` 的名字保留用于
历史解析证据和能力版本追溯；产品入口为 Normalizer，不把旧探测返回 AST 当产品指纹。

普通注释由扫描器确认后忽略；仅以 `/*+`、`--+` 开始的注释作为 Hint，内容原样保留，
位置为去普通注释后的 token 间隙。普通空白、说明性注释及关键字大小写变化有稳定性验证。
Hint 的内容、空白和位置不推断等价；嵌套歧义或 Hint 落在合并字符串 token 内明确拒绝。
这是保守位置编码：额外括号、分号或正负数字 token 数变化可能造成拆组，尤其 Hint 在
后续位置时；不宣称所有语义等价写法都能合并。此版本未做查询优化、交换律、绑定关系、
目录／会话重建或隐含对象解析。

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
