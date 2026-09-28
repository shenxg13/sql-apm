# SQL 近似指纹接口与边界

本模块实现[已确认的近似观察规则](../../.project-wiki/contracts/sql-fingerprints.md#已确认的观察用近似指纹)。
版本为 `sql-approximate/2`，只提供观察分组身份，不代表完整 SQL、可靠结构指纹或正常基线。
不执行 SQL，不读数据库，不尝试从其他记录补取完整 SQL，不补括号／引号或恢复绑定值。
完整结构归一化已由[Normalizer](sql-normalization.md)提供；生产日志导入、观察统计及其持久化尚未实现。

## 可调用接口

[sql_apm/sql/approximate.py](../../sql_apm/sql/approximate.py)支持 Python 3.9.5。
`fingerprint` 只依赖标准库；`analyze` 延迟导入现有 MPP 解析器，需要已锁定的 pglast 7.18。
解析依赖已提升至[运行依赖文件](../../requirements.txt)，旧候选对比另有实验依赖；
尚未交付安装包或生产部署配置。

```python
from sql_apm.sql.approximate import analyze, fingerprint

result = analyze("SELECT * FROM orders WHERE id IN (1001, 1002,")
assert result['parser_state'] == 'unsupported'
assert result['structure_fingerprint'] is None
assert result['approximate']['observation_only'] is True

# 低层接口：调用方已可靠取得结构失败原因时使用，不自行证明 SQL 解析失败。
near = fingerprint(b'SELECT * FROM orders WHERE id IN (1001,',
                   structural_reason='lexical_unbalanced_bracket')
```

输入接受 `str` 或原始 `bytes`，当前 profile 固定为 `hashdata-csv/1`。不得把数据库错误消息
原文作为 `structural_reason`，该参数只接受固定诊断码；正常使用优先调用 `analyze`。

| 层次／字段 | 行为 |
| --- | --- |
| `parser_state`、`parser_reason` | 现有解析原型的 parsed／unsupported／failed／not_attempted 及固定原因，不伪写执行结果 |
| `parser_version` | 本次调用的解析能力版本 |
| `structure_fingerprint` | 本步骤始终为空；parsed 只表示取得 AST，不表示已完成结构归一化 |
| `approximate` | 仅原型明确拒绝时尝试生成；解析成功、异常或输入超限不自动回退 |
| 近似 `kind`、`state` | kind 固定 approximate；available／unavailable／failed 区分结果与失败 |
| 近似 `value` | 仅 available 非空，使用 `approx:sql-approximate/2:` 前缀及 SHA-256；不能写入正常 Fingerprint.value／Group |
| `algorithm_version`、`rules_digest`、`rules_ref`、`rules` | 算法版本、内置规则的规范摘要、内置版本引用及独立规则快照；不同规则不得混比 |
| `source` | 已取得输入的字节数、精确 SHA-256 和 base64 原字节；不是重建的完整业务 SQL |
| `normalized` | 有类型的词法序列、词法问题、未闭合括号、原结构失败原因、固定 unverified 完整性标记 |
| `diagnostics`、`structural_reason` | 近似扫描诊断与原结构失败原因分别保留，不因近似成功消失 |
| `replacements`、`observation_only` | 实际替换的业务值个数；用途始终仅限观察 |

同输入、同失败原因、同 profile 和规则版本的近似输出稳定。指纹编码为带类型、版本、
profile、规则摘要及近似表示的规范 JSON，不能与原文 SHA 或结构摘要互换。规则变化应升级
版本并保留历史规则依据。调用方负责来源定位、事件身份及统计，不靠指纹去重真实事件。

## 当前处理范围

扫描与括号处理均使用显式状态／栈；不依赖完整 AST，也不提高全局递归限制。

| 内容／位置 | 当前处理 |
| --- | --- |
| 普通空白、完整普通注释 | 忽略；保留 token 边界 |
| 未加引号的词 | 仅折叠 ASCII 大小写；引号标识符和其他 Unicode 字符保留 |
| Hint | 原样保留内容及输出序列位置；语法上下文识别跳过 Hint，不推断等价写法 |
| 简单 WHERE 直接列比较、直接 IN 常量列表 | 边界及位置可确定的普通数字、无反斜杠普通字符串、原生参数改为统一业务标记 |
| UPDATE 的直接 SET 列赋值 | 同上；表达式、函数或显式类型转换不套用简单值规则 |
| 未闭合字符串、注释、标识符、美元引用或词法歧义 | 保留从不确定位置起的全部原始片段及原因，不继续猜测其中的 SQL token |
| 未闭合／不匹配括号 | 保留已有括号和未闭合标记，不补齐；不匹配处保留剩余片段 |
| 编码异常或 NUL | 保留原字节；遇到异常处保守保留剩余片段，不忽略或替换损坏字节 |
| 输入末尾无后续边界的数字／参数 | 原样保留，避免把被截断的词误当作完整业务值 |
| NULL、布尔、负号表达式、函数参数、类型转换、SELECT 表达式、INSERT VALUES、DDL、SET 配置及 LIMIT／OFFSET | 本近似版本保留；不套用可靠结构路径的全部归一化规则 |

本近似扫描器在 `/*` 或 `--` 后跳过起始空白再检查 `+`，因此也保留 `/* + … */`、
`-- + …`。可靠路径只识别紧接的 `/*+`、`--+`。这是既有的保守差异：近似路径可能因此
多拆观察组，不会把它当可靠语义；两条路径均有明确的正反测试。本次补充说明未改变近似算法版本。

版本2先建立排除 Hint 的语法 token 视图，用同一视图判断直接业务值、函数调用和显式转换，
再把选中位置映射回原输出序列。这样 `custom /*+ H */ (...)` 或 `(...) /*+ H */ ::boolean`
中的 Hint 不会截断保护上下文；块／行 Hint、多个 Hint、未闭合调用和嵌套转换均有回归验证。
整个函数或括号转换内部的业务值仍保留，Hint 自身内容、位置及原结构失败原因不丢失。
版本1存在该保护绕过问题，不能把旧版本结果当作新版本通过证据；见
[整改验证](../reports/sql-normalization-r1-remediation-2026-09-28.md)。

有可识别 SQL 命令 token 才生成近似结果；只有分号、普通注释、从开头即不确定的注释或
无法辨认的文本返回 `no_sql_tokens`。因此并非每个拒绝输入都生成指纹，也并非每个近似
结果都有业务值替换。部分批次仍保留已记录的整体序列，不截取一个子句冒充整批身份。

近似分组可能合并不同的完整 SQL，也可能因为保守保留而拆分同一模板。其正确性承诺是
固定规则的可重复词法处理、原文及不确定性保留、用途隔离，不是语义等价或分组准确率。
只有可靠的事件身份和计时依据才能进入对应的观察耗时统计；日志条数不作为执行次数，
失败／未知状态不变，正常训练资格不因生成近似结果恢复。

## 诊断命令与限制

在仓库根目录运行：

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.approximate_sql --sql 'SELECT * FROM orders WHERE id IN (1, 2,'

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.approximate_sql --file /tmp/input.sql
```

命令输出 JSON，包含输入的原始字节表示；这不是脱敏报告。退出码 0 表示解析原型已取得
结构但本命令未生成产品指纹，2 表示结构失败但近似可用，1 表示输入不可用、两条路径均无
结果或处理失败。不同状态不能只靠“有一个哈希值”判断。

输入上限为 512 KiB；文件入口只读取上限加一个字节，超限即拒绝，不截取前缀生成结果。
低层超限结果不复制完整 base64，但调用方的输入不变。核心调用没有进程超时／内存隔离；
处理不可信批量输入时由调用方提供资源限制。重放工具使用每条 5 秒、每进程 512 MiB 和
持久隔离进程，每 1,000 次调用回收，参数与以往解析探测一致。

## 验证与重放

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.normalization_replay \
  --output var/parser-probe/normalization-rerun.json
```

当前有界重放使用新的输出路径，复用固定历史样本及原文索引的首尾各512条，精确去重后
为1,832条；22条结构拒绝中20条近似可用、2条不可用，可靠结果与近似结果继续隔离。
该样本不覆盖全部旧拒绝，函数／转换间插入 Hint 的保护由定向合成回归补充验证。

[历史重放报告](../reports/mpp-approximate-2026-09-27.md)记录版本1对9,293个旧失败输入的
验证，包含解析版本5剩余8,909个拒绝及384个已修复对照；报告保持原样。
`mpp_approximate_replay` 固定核对版本5源码摘要，只能在对应历史代码与证据下复现；
解析器升至版本6后会按设计拒绝，不能放宽摘要校验把旧全量结论冒充当前验证。
