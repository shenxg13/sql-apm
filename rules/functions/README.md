# 函数参数规则

本目录提供 HashData 首期指纹处理的函数规则，版本为 `1.0.0`。
规则选择器接受结构化调用描述；不解析 SQL、不查询数据库、不生成完整 SQL 指纹。
人工 SQL 的归并预期仍需后续主引擎接入验证。

## 文件与来源

| 文件 | 用途 |
| --- | --- |
| [v1.json](v1.json) | 可加载的版本字典，逐重载参数动作及依据 |
| [review-policies.json](review-policies.json) | 按函数维护的语义审查策略，生成字典的维护输入 |
| [postgres-9.4.26-inventory.json](postgres-9.4.26-inventory.json) | 固定上游源码的签名事实、文档条目和 SHA-256 |
| [special-syntax.json](special-syntax.json) | 特殊语法、表格片段和历史／其他系统名称的处置 |
| [examples.json](examples.json) | 人工 SQL 预期和结构化表达式正反例 |
| [POSTGRESQL-LICENSE.txt](POSTGRESQL-LICENSE.txt) | 上游签名资料的许可证 |

覆盖分母为 PostgreSQL 9.4.26 `func.sgml` 的第 9 章表格及函数引用，
再用同版本初始目录展开重载。共 409 个函数名、775 个签名；
176 个签名允许至少一个业务参数归一化，599 个签名明确保留。
目录另外保留 1,805 个章外签名事实用于审计，未把它们计为已审查。
这不是 PostgreSQL 所有扩展或 HashData 现场函数的穷尽目录。

Greenplum v6 官方资料补充 4 条明确保留关系 OID 的规则；另有 5 条只有用途说明、
缺精确类型证据的条目标为 `pending`。尚未取得 HashData 3.13.13 现场函数目录及
自定义函数定义，不能把上游资料当成现场验证。来源和实测指标见
[覆盖报告](../../docs/reports/function-dictionary-2026-09-25.md)。

## 字典字段

顶层字段只有 `schema_version`、`rules_version`、`profile`、`rules`。
当前格式版本为整数 1，规则版本采用 `major.minor.patch`，profile 为 `hashdata-pg94`。

| 规则字段 | 含义 |
| --- | --- |
| `id` | 稳定规则 ID；上游 OID 只作本版本来源定位，不作为跨集群身份 |
| `schema`、`name` | 规范化名称；显式 schema 必须精确匹配 |
| `types` | 输入参数的上游内部类型名，排除 OUT 参数 |
| `defaults` | 末尾可省略参数的数量，不把省略参数补写进表达式 |
| `variadic` | 可变参数元素类型，普通函数为 null |
| `kind` | function、aggregate 或 window，调用方须可靠分类 |
| `allow_unqualified` | 是否允许未限定名称使用此内置规则假设 |
| `decision` | normalize、preserve 或 pending；pending 不计审查完成 |
| `arguments` | 完整逐参数列表：从 1 开始的 position、action、role |
| `rationale`、`sources` | 本项目处理理由与官方语义／签名来源 |
| `enabled` | 是否启用；停用条目仍保留来源，调用回退保留 |

`normalize` 只表示字面业务叶子可以替换，不能把整个函数或表达式删成占位符。
`preserve` 是经过审查后明确保留；`pending` 表示证据不足。
字典格式、重复键、重复签名、字段或动作非法时，整个加载失败，不使用部分规则。
摘要对规则 ID 和参数位置排序后计算规范 JSON 的 SHA-256；输入列表重排不改变摘要。

## 匹配和表达式边界

1. 未引用的 ASCII 名称折叠为小写；双引号名称保持大小写并处理转义双引号。
   schema 和函数名分开传递，带点的单一名称不会被猜测拆分；其他未支持的标识符
   拼写回退保留。源 SQL 的显式限定仍须由主引擎保留。
2. 未限定名称仅匹配允许该形式的 pg_catalog 规则。它是已声明的上游内置假设，
   不能证明真实解析对象；首期不恢复 search_path，同名自定义函数存在误匹配限制。
3. 参数类型使用目录内部名称，例如 int4、float8、timestamp、_text。
   类型来自调用方的可靠信息；不从字符串外观猜类型，不实现隐式转换或类型别名解析。
   所有类型已知时精确签名优先；只有部分类型或无类型时，候选动作必须一致。
4. 未知函数、未知签名、歧义、停用或 pending 均返回保留及具体原因。
   命名参数、特殊调用形式、可变参数返回保留；可变参数尚需主引擎区分展开与
   `VARIADIC` 数组形式，不以此字典恢复可变参数绑定。
5. 普通已命中函数可用于 SELECT 等位置；已有 SET、LIMIT、OFFSET 保护边界优先。
   未授权的其他位置不会因字典出现而自动归一化。
6. 业务位置直接数字／字符串叶子和原生 `$n` 用统一业务标记；布尔与 NULL 保留。
   显式类型转换保留，转换内部业务叶子可替换。列名、对象、运算结构、批次顺序保留；
   算术表达式内原本未授权的常量继续保留，不扩大为任意深度替换。
7. 业务输入中的已知嵌套函数遵循各自规则。控制参数、未知函数及不支持节点的
   子树整体保留，内层规则不能绕过外层保留要求。递归预览深度上限为 100。
8. 聚合、窗口及 SQL 特殊语法首版明确保留。主引擎必须保留 DISTINCT、ORDER BY、
   FILTER、OVER、窗口边界等结构，不能只用函数名和普通 args 生成整个表达式指纹。

`FunctionDictionary.preview()` 仅对人工树演示上述策略，不是完整 SQL AST 接口。
类型等匹配辅助信息与最终指纹编码的分离留待主引擎；不得直接把预览 JSON 当成
已验证的 SQL 指纹。SET 人工 SQL 与保护上下文的函数树测试属于不同层次的样例。

## 使用和验证

在仓库根目录、Python 3.9.5 环境执行：

```bash
.venv/bin/python -m sql_apm.sql.function_dictionary validate rules/functions/v1.json
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/functions/coverage.py
```

选择规则可使用模块 API，也可将如下调用描述写入本地 JSON 文件：

```json
{"name":"to_date","arity":2,"schema":"pg_catalog","types":["text","text"]}
```

```bash
.venv/bin/python -m sql_apm.sql.function_dictionary select rules/functions/v1.json /tmp/call.json
```

返回 decision、reason、逐参数 actions、命中 rule_ids、rules_version 和 sha256。
载入时校验全部配置并复制为快照；外部修改原对象不会改变已加载实例。

## 来源重建与维护

官方 PostgreSQL 9.4.26 源码包下载后保持原样，解压目录不提交 Git。
本仓库只提交必要签名事实和许可证，不提交源码包或生成的数据库。

```bash
.venv/bin/python scripts/functions/import_postgres.py /tmp/postgresql-9.4.26 \
  --output /tmp/postgres-inventory.json
.venv/bin/python scripts/functions/build_dictionary.py --output /tmp/v1-rebuilt.json
```

导入器读取 `pg_proc.h`、`pg_type.h`、`system_views.sql` 和 `func.sgml`，
其中 SGML 的短结束标签、普通 literal 签名、初始化 SQL 的默认参数和 ts_debug
都必须保留；不能只抓 FUNCTION 标签。固定输入 SHA-256 写入 inventory。

新增或修改规则时，先在 review-policies 中记录函数的业务／控制用途及依据，
必要时更新生成器中的明确重载例外；同时增加独立语义正反例，运行选择测试和覆盖审计。
纯类型资料不自动推出业务动作；不要根据 volatility、strict 或函数名相似性自动归一化。
在同一目标 schema／name／types／kind 下不允许重复定义优先级覆盖。

已发布的 v1.json 必须保留原样；语义更新生成新文件并增加 rules_version，
停用规则通过新版本的 enabled=false 表示，同时保留旧版本可追溯。
新增版本时更新对应维护输入、生成器与验证目标，旧文件仍按原摘要校验。
不要把本地编辑过的字典继续宣称为原版本；将变更理由、来源和测试保存在提交与 PR 中。

每次未来基线构建固定并记录指纹算法版本、字典版本和字典摘要，整个训练窗口使用
同一快照，导入与检索复用同一套规则。历史基线保留当时分组与版本，源 SQL 不改写。
本 Issue 没有实现加载多个基线版本、窗口重建或历史迁移。
