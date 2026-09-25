# 函数字典 R1 整改验证

本报告记录 [R1 评审](https://github.com/shenxg13/sql-apm/pull/4#issuecomment-5834511907)
的两个 P2 阻塞项及实施者修复证据；原评审头为 `96ae6da`。
用户已授权整改，状态和独立复核结论以 [Issue #1](https://github.com/shenxg13/sql-apm/issues/1)
及 PR 评论为准。本报告不是独立 R2 通过结论。

## 修复与退出条件

| 条目 | 实施修复 | 对应验证 |
| --- | --- | --- |
| R1-F001 | 显式转换仅对明确业务标量继续遍历；对象身份、布尔、JSON、数组及未知类型转换的整个子树保留，外层 text 无法绕过；移除 anyelement 标量白名单 | 直接／嵌套 regclass、regtype 及同类类型保留；已知参数类型及仅转换提供类型的输入；参数／嵌套调用保护；text/numeric 正向归一化 |
| R1-F002 | 用有限内置类型事实检查多态类别及数组／元素、范围／子类型、重复位置关联；未知类型与混合伪类型返回 polymorphic_requires_resolver | 原五个非法调用不再 matched；合法数组及范围签名、精确 text 优先、无类型共识、未知自定义类型保守回退；全部多态家族的关系反例 |

对象身份依据为 [PostgreSQL 9.4 对象标识类型](https://www.postgresql.org/docs/9.4/datatype-oid.html)；
多态类别及关联依据为 [PostgreSQL 9.4 类型系统](https://www.postgresql.org/docs/9.4/extend-type-system.html)。
这些上游约束不代表 HashData 现场函数或自定义类型已经验证。

当前 `sql_apm/sql/type_policy.py` 仅识别列明的 37 个非数组内置类型、对应 37 个数组类型
及 6 个内置范围子类型关系；对未支持类型不猜测。已逐项对照固定 PostgreSQL 9.4.26
的 `pg_type.h` 与 `pg_range.h`；前者摘要在既有来源清单中，后者 SHA-256 为
`9b95bb2f1dcace247dbfbd3e64cb2484a4508ed080145e37f78fc689f2ebd4b6`。
类型类别事实不自动授予参数归一化权限；不解析别名、domain、隐式转换或现场枚举。

## 规则与历史追溯

新规则文件为 [v1.0.1.json](../../rules/functions/v1.0.1.json)，共 784 条规则，
格式版本 1、规则版本 1.0.1，摘要为
`74ee855341d41f9fb6df217bd351222806f0f11a8c19d2d2b777138f9c824f45`。

- `pg94-1285` / `pg94-1290`：quote_literal／quote_nullable 的 anyelement 重载从
  normalize 改为 preserve；text 精确重载仍可归一化。
- `pg94-3176`：to_json 的 preserve 动作不变，只更新不能套用标量规则的理由。
- 其余规则逐字节字段比较相同；规则列表与总数不变。
- PostgreSQL 文档相关 775 个签名中，174 个 normalize、601 个 preserve；
  Greenplum 扩展仍为 4 个 preserve、5 个 pending。
- 原 [v1.json](../../rules/functions/v1.json) 及其摘要、原覆盖／抽样 JSON、固定来源
  清单和原始需求快照保持原字节。旧版本留作追溯，新构建使用 1.0.1。
- 当前维护输入及生成器重建 1.0.1；历史算法行为还需固定原代码提交，不能只加载
  旧 JSON 就声称恢复了旧算法。未改写任何历史基线或生产 SQL。

完整新版计数见[覆盖 JSON](function-dictionary-coverage-2026-09-25-r1.json)。
原始实现及迁移证据仍在[原报告](function-dictionary-2026-09-25.md)，其 176／599、
20 组样例及 1.0.0 摘要属于原版本，不作为本轮当前结果。

## 验证结果

解释器为本地精确 Python 3.9.5，验证均由实施者执行：

- 使用 R1 评论的 11 个独立人工复现，对照旧头源码及 1.0.0 数据：11 项均失败；
  修复实现配合 1.0.1 数据：11 项均通过。只使用人工数据，不执行数据库函数。
- 25 个 unittest 方法通过，包括原 775 个文档签名可达性、新增 8 个类型边界测试
  方法及全部 26 组人工样例；后者仍是结构化策略预览，不是 SQL 端到端指纹验证。
- 新增六组人工样例覆盖对象、类型身份、JSON、数组、布尔及嵌套对象转换。
- 字典重新生成与 v1.0.1.json 逐字节相同；覆盖生成结果可复核。
- 多态类型族检查覆盖 anyelement、anyarray、anynonarray、anyenum、anyrange 与
  独立的 any；不再使用任意 `any` 前缀通配。完整目录伪类型声明匹配只作签名审计。
- 保留现有精确类型优先、无类型动作共识、未知／停用／pending／可变参数回退、
  子树保留、SET／LIMIT／OFFSET、严格配置及快照摘要回归。
- 完整 Harness、文档链接与离线工作流检查通过；在线 PR 契约及远程 CI 在交接
  评论记录具体提交结果。

```bash
.venv/bin/python -m sql_apm.sql.function_dictionary validate rules/functions/v1.0.1.json
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/functions/build_dictionary.py --output /tmp/v1.0.1-rebuilt.json
.venv/bin/python scripts/functions/coverage.py --output /tmp/function-coverage-r1.json
.venv/bin/python scripts/functions/sample_logs.py --root raw/inbox/hashdata \
  --output /tmp/function-sample-r1.json
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

## 有界日志重放与隐私

按原预算读取 119／120 各两个文件、每文件最多 2,000 条 CSV 记录和 8 MiB，
没有扩大为全量扫描。新版[抽样 JSON](function-dictionary-sample-2026-09-25-r1.json)
除规则版本与摘要外，其余内容与原报告相同：输入前缀摘要、计数、原因分类及回放定位
均相同，人工业务身份与生产 SQL 未进入报告。

| 集群 | CSV 记录 | 调用候选 | normalize 命中 | preserve 命中 |
| --- | --- | --- | --- | --- |
| 119 | 4,000 | 8,765 | 300 | 875 |
| 120 | 4,000 | 7,128 | 3,080 | 322 |

观察：本次样本未发生计数变化。解释：词法探测不提供具体类型，且本次两个动作变化
未影响该有界样本计数；不能据此推断类型修复对全部生产输入没有影响。
词法候选不是已解析调用，命中比例不是最终指纹覆盖率。

## 独立复核边界

两项退出条件已有实施测试证据，仍需独立 R2 检查完整修复差异、两项修复的交互、
规则与报告版本、同类边界及回归。R1 已完整结束并消耗 1 轮，不重置轮次。
未获得现场目录、未实现完整 SQL 引擎、未进行 Kylin 部署或生产兼容实测。
