---
id: architecture.source-layout
type: architecture
status: active
owners:
  - sql_apm/
  - tests/
  - scripts/functions/
  - sql_apm/storage/
  - scripts/db/
  - tests/database/
updated: 2026-09-26
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/1#issuecomment-5834054457
    status: current
  - path: sql_apm/sql/function_dictionary.py
    status: current
  - path: sql_apm/diagnostics/function_probe.py
    status: current
  - path: sql_apm/diagnostics/statement_census.py
    status: current
related:
  - decision.project-scope
  - decision.runtime-and-components
  - contract.sql-fingerprints
  - contract.offline-data-contract
  - architecture.postgresql-storage
  - feature.log-ingestion
  - feature.operator-cli
confidence: high
---

# Python 源码布局与模块职责

## Summary

Python 产品源码放在仓库下的 `sql_apm/` 包内，按职责组织子包；仓库根目录不放
业务源码，包根目录尽量只保留包声明和薄入口。目标结构按对应功能逐步落实，
不为尚未实现的功能预建空目录。

## Source Of Truth

2026-09-25 用户在目录规划讨论后明确要求：“把建议目录结构写入项目知识，并且
开始迁移”，并引用“先迁移现有两个模块，同步导入路径、命令、测试和 CI”。
[在线确认记录](https://github.com/shenxg13/sql-apm/issues/1#issuecomment-5834054457)
保存本次范围及影响；Issue 实时契约负责实施验收。

本文记录已确认目标结构；代码路径说明当前实现，不表示规划中的导入器、完整
SQL 引擎、统计构建器、存储和统一 CLI 已经交付。

## Contracts

### 目标结构

```text
sql-apm/
├── sql_apm/
│   ├── __init__.py
│   ├── __main__.py             # 后续：python -m sql_apm 的薄入口
│   ├── cli/                    # 后续：参数、输出、业务流程调用
│   ├── contracts/              # 后续：跨模块数据对象与约束
│   ├── ingestion/              # 后续：批次、来源追踪、异常处理
│   │   └── hashdata/           # 后续：来源日志解析与计时解释
│   ├── sql/
│   │   ├── __init__.py
│   │   ├── function_dictionary.py
│   │   ├── type_policy.py       # 有限内置类型事实与保守匹配约束
│   │   ├── parsing.py          # 后续：SQL 解析适配
│   │   ├── normalization.py    # 后续：归一化
│   │   └── fingerprint.py      # 后续：结构指纹
│   ├── baseline/               # 后续：训练筛选、窗口、统计、构建、版本
│   ├── storage/                # 已有：DDL／初始化及迁移 SQL；业务读写接口后续实现
│   └── diagnostics/
│       ├── __init__.py
│       ├── function_probe.py
│       └── statement_census.py  # 只读类别调查及来源回放，非训练过滤器
├── tests/                      # 随模块增长按对应业务职责组织
├── rules/                      # 版本化规则数据
├── scripts/                    # 开发、维护、规则生成、验证工具
├── docs/
└── pyproject.toml              # 后续：包安装与工具配置
```

当前保留仓库下直接放置 `sql_apm/` 的布局；本次不迁入 `src/sql_apm/`。
安装包与发布方案实施时再统一评估 `src` 布局。各普通子包使用 `__init__.py`；
图中尚未实现子包的包声明随功能创建。

### 模块职责与依赖

- `__main__.py`、`cli/` 接收参数、调用流程并展示结果；业务判断留在对应模块。
- `contracts/` 后续承载跨模块的数据对象与约束，遵循
  [离线数据契约设计](../contracts/offline-data-contract.md)；当前交付文档及合成样例，
  尚未创建该产品子包或运行时校验器。
- `ingestion/hashdata/` 封装 HashData 特有格式、对象识别和计时解释；通用导入流程
  管理批次、来源和异常。来源数据经明确契约进入后续处理。
- `sql/` 只负责 SQL 专属处理；非 SQL 来源接入基线无需生成 SQL 指纹。
- `baseline/` 消费约定的数据对象，负责可复用统计及构建能力；来源特有的分组、
  资格和耗时解释必须有独立约定，不把 HashData 五类计时强加给所有未来来源。
- `storage/` 集中数据库读写与事务；规则和统计计算不直接执行数据库操作，
  由上层流程组织读取、计算和保存。
- `diagnostics/` 放可复用诊断逻辑；当前词法候选探测是诊断用途，不作为完整解析器。
- 核心规则、统计和数据契约不依赖 CLI；CLI 和维护脚本调用产品模块，产品核心
  不反向依赖 `scripts/`。通过清楚的数据接口避免循环导入。
- 辅助逻辑先放在所属模块；出现明确复用需求时再提取，不提前建设泛化的
  `utils/`、`common/` 或插件平台，不为每个小文件额外创建一层子包。

这些边界遵守[多类型接入约束](../decisions/project-scope.md#已确认的多类型系统接入扩展约束)，
不提前交付其他数据库或跑批系统适配器。

### 当前迁移与兼容边界

| 原路径 | 当前路径 | 用途 |
| --- | --- | --- |
| `sql_apm/function_dictionary.py` | [sql_apm/sql/function_dictionary.py](../../sql_apm/sql/function_dictionary.py) | 字典校验、摘要、规则选择、结构化预览及现有模块命令 |
| `sql_apm/function_probe.py` | [sql_apm/diagnostics/function_probe.py](../../sql_apm/diagnostics/function_probe.py) | 有界词法候选诊断 |

当前只创建 `sql/`、`diagnostics/` 两个子包。现有测试仍在 `tests/` 下，后续随
测试规模按业务模块组织。现有模块命令入口随文件迁移保留；统一 CLI 实施时再
将参数解析委托给 `cli/`，本次不提前新增 `__main__.py`。

首版合并前统一更新全部仓库调用方，不保留旧模块路径的转发文件。
外部手工调用需改用新路径；维护命令见[脚本说明](../../scripts/README.md)和
[字典维护说明](../../rules/functions/README.md)。迁移提交 `96ae6da` 中两份模块实现按字节搬迁，
规则语义、数据、版本和摘要未变。后续 R1 整改单独新增 `sql/type_policy.py`，
修复既有语义边界并生成规则 1.0.1；原 1.0.0 文件保留，详见
[整改报告](../../docs/reports/function-dictionary-r1-remediation-2026-09-25.md)。

2026-09-26 类别调查新增 `diagnostics/statement_census.py`，仅做本地 CSV 词法
类别清点及有界回放；`scripts/diagnostics/statement_census.py` 为薄入口。该工具
不承载产品导入、语法解析或黑名单判定，证据与限制见
[类别核查报告](../../docs/reports/statement-category-census-2026-09-26.md)。

2026-09-26 新增 `storage/` 的版本化 PostgreSQL DDL、管理员引导和 catalog 核对 SQL；
`scripts/db/` 提供初始化、显式版本升级及临时实例验证，`tests/database/` 映射既有人工样例。
[存储主题](postgresql-storage.md)说明结构、事务边界及实际验证，尚无 Python 业务读写接口。

## Workflows

1. 新模块按职责选择所属子包，先检查现有模块和直接依赖，避免重复抽象。
2. 仅在对应功能实施时创建目录与文件，新增接口遵循已确认数据契约。
3. 目录迁移同步内部导入、模块命令、测试、CI、维护文档及受影响的在线契约。
4. 使用 Python 3.9.5 重跑现有产品回归、字典和覆盖检查，执行 Harness；核对
   模块搬迁前后字节及规则摘要，保证结构调整不夹带语义变化。
5. 更新在线 PR 证据；路径或提交变化后的评审使用新的固定提交和契约。

## Failure Modes

- 把目标布局误认为全部功能已经实现，或为未来功能堆积空目录。
- 将词法候选诊断当作完整 SQL 解析或端到端指纹验证。
- 将来源特有语义放进通用统计对象，导致未来非 SQL 来源必须伪装成 SQL。
- 移动文件却遗留旧导入、命令或 CI 路径，或在搬迁中修改已发布规则语义。
- 业务逻辑堆积在包根入口、CLI、维护脚本或无明确职责的公共目录。

## Update Rules

本页维护源码布局及依赖方向；业务语义继续由对应契约页维护。目录职责发生
持久变化时同步本页、相关调用方及知识日志；索引只负责导航。

## Open Questions

具体跨模块字段、存储接口、统一 CLI 参数及包发布方式在对应 Issue 中落实；
`src` 布局尚未采用。这些事项不阻塞当前两模块迁移。
