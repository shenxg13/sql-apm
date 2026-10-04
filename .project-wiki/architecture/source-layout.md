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
  - scripts/deployment/
updated: 2026-10-05
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/33
    status: current
  - path: docs/reports/kylin-delivery-alignment-2026-10-04.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/31
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/27
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/25
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/21
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/15
    status: current
  - path: docs/reports/sql-normalization-v4-2026-09-28.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/1#issuecomment-5834054457
    status: current
  - path: sql_apm/sql/function_dictionary.py
    status: current
  - path: sql_apm/diagnostics/function_probe.py
    status: current
  - path: sql_apm/diagnostics/statement_census.py
    status: current
  - path: sql_apm/sql/mpp_parser.py
    status: current
  - path: sql_apm/sql/lexical.py
    status: current
  - path: docs/reports/parser-layout-2026-09-27.md
    status: current
  - path: docs/design/sql-normalization.md
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

本文记录已确认目标结构；代码路径说明当前实现，不表示规划中的完整
SQL 引擎、统计构建器和完整流程 CLI 已经交付；只导入范围见本页已实现边界。

## Contracts

### 目标结构

```text
sql-apm/
├── sql_apm/
│   ├── __init__.py
│   ├── __main__.py             # 已有：python -m sql_apm 的薄入口
│   ├── cli/                    # 已有：导入、训练快照、统计参数和薄调用
│   ├── contracts/              # 后续：跨模块数据对象与约束
│   ├── ingestion/              # 已有：批次、来源追踪、异常处理
│   │   └── mpp/                # 已有：来源日志解析与计时解释
│   ├── sql/
│   │   ├── __init__.py
│   │   ├── function_dictionary.py
│   │   ├── type_policy.py       # 有限内置类型事实与保守匹配约束
│   │   ├── mpp_parser.py       # 已有：MPP 解析适配，固定能力版本
│   │   ├── lexical.py          # 已有：共用词法边界及粗粒度类别
│   │   ├── approximate.py     # 已有：观察用近似指纹，不提供正常基线身份
│   │   ├── pg_ast.py           # 已有：PG语法树位置字段处理
│   │   ├── structure.py        # 已有：无递归深度依赖的结构编码
│   │   └── normalization.py    # 已有：可靠归一化、结构指纹及规则快照
│   ├── baseline/               # 已有：五层统计核心、进程监护及完整任务编排
│   ├── storage/                # 已有：DDL／迁移、SQL 元数据及批量导入写入
│   └── diagnostics/
│       ├── __init__.py
│       ├── function_probe.py
│       ├── statement_census.py # 只读类别调查及来源回放，非训练过滤器
│       ├── parser_fidelity.py  # 候选解析器比较
│       ├── normalize_sql.py    # 已有：文本／文件薄入口
│       ├── normalization_replay.py # 已有：有界归组验证
│       ├── normalization_diff.py   # 已有：脱敏快照及规则变更分组差分
│       └── mpp_*.py            # 适配验证、矩阵、有界抽样及重放
├── requirements.txt           # 已有：运行解析依赖及哈希锁
├── tests/                      # 随模块增长按对应业务职责组织
│   └── parser_probe/           # 可选解析实验测试、依赖锁定及fixtures/
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
- `ingestion/mpp/` 封装 MPP 特有格式、对象识别和计时解释；通用导入流程
  管理批次、来源和异常。来源数据经明确契约进入后续处理。
- `sql/` 只负责 SQL 专属处理；非 SQL 来源接入基线无需生成 SQL 指纹。
- `baseline/` 消费约定的数据对象，负责可复用统计及构建能力；来源特有的分组、
  资格和耗时解释必须有独立约定，不把 MPP 五类计时强加给所有未来来源。
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

该迁移阶段已有 `sql/`、`diagnostics/`、`storage/` 三个子包；后续新增职责见本页已实现边界。解析专项测试放在
`tests/parser_probe/`，既有测试继续保留原路径。现有模块命令入口随文件迁移保留；统一 CLI 实施时再
将参数解析委托给 `cli/`，本次不提前新增 `__main__.py`。

首版合并前统一更新全部仓库调用方，不保留旧模块路径的转发文件。
外部手工调用需改用新路径；维护命令见[脚本说明](../../scripts/README.md)和
[字典维护说明](../../rules/functions/README.md)。迁移提交 `96ae6da` 中两份模块实现按字节搬迁，
规则语义、数据、版本和摘要未变。后续 R1 整改单独新增 `sql/type_policy.py`，
修复既有语义边界并生成规则 1.0.1；原 1.0.0 文件保留，详见
[整改报告](../../docs/reports/function-dictionary-r1-remediation-2026-09-25.md)。

2026-09-26 类别调查新增 `diagnostics/statement_census.py`，仅做本地 CSV 词法
类别清点及有界回放；当前使用 `python -m sql_apm.diagnostics.statement_census`。该工具
不承载产品导入、语法解析或黑名单判定，证据与限制见
[类别核查报告](../../docs/reports/statement-category-census-2026-09-26.md)。

2026-09-26 新增 `storage/` 的版本化 PostgreSQL DDL、管理员引导和 catalog 核对 SQL；
`scripts/db/` 提供初始化、显式版本升级及临时实例验证，`tests/database/` 映射既有人工样例。
[存储主题](postgresql-storage.md)说明结构、事务边界及实际验证，尚无 Python 业务读写接口。

### 解析原型与诊断工具归位（2026-09-27）

用户指出SQL解析源码与用例数据堆在 `scripts/diagnostics/`，并要求开始优化调整。
已按职责完成[目录迁移](../../docs/reports/parser-layout-2026-09-27.md)：

- `sql/mpp_parser.py` 承载已有MPP解析原型，`sql/lexical.py` 承载原类别调查模块中的共用词法逻辑，
  `sql/pg_ast.py` 承载原解析器比较脚本中的PG树清理逻辑。解析核心不导入诊断模块或脚本。
- 六个解析探测、矩阵、抽样与重放实现归入 `diagnostics/`，调用方直接导入包内实现。用户随后要求
  清理冗余入口，已删除这六个及类别调查共七个包装脚本，统一使用 `python -m sql_apm.diagnostics.<模块>`。
  `scripts/` 继续保留数据库初始化、GitHub工作流、质量检查及规则维护等独立工具。
- 两份案例JSON移至 `tests/parser_probe/fixtures/`，实验依赖锁定移至
  `tests/parser_probe/requirements.txt`；测试直接导入 `sql_apm`。业务规则JSON仍归 `rules/`，
  验证输出JSON仍归 `docs/reports/data/`，生产缓存仍留在忽略目录。
- 子进程使用包模块入口及明确工作目录，模块导入不修改 `sys.path`。诊断命令从仓库根目录运行；
  从其他目录调用时需显式配置源码包和可选依赖路径。
- 原型版本、解析结果、拒绝原因、规则数据及历史证据摘要均保持原有语义。目录归位不表示
  正式归一化模块已完成，不提前创建统一CLI、安装打包配置或空的产品模块。

2026-09-27 用户进一步授权全量解析覆盖核查，新增
`diagnostics/mpp_full_scan.py` 及失败／历史结果核对工具 `diagnostics/mpp_full_audit.py`，
仍通过包模块命令执行。完整原文和临时SQLite去重索引
留在忽略的 `var/`，对外报告只含计数、来源定位、摘要和固定诊断标签。索引是诊断中间产物，
不属于产品持久化实现；核心解析原型版本4未因本次遍历而改变。

随后用户授权修复全量核查发现的问题，解析原型升至版本5。新增 `sql/structure.py`
提供不依赖递归栈的派生结构JSON编解码，`sql/pg_ast.py`使用显式栈清理位置字段；两者均不依赖
诊断模块。新增 `diagnostics/mpp_full_repair.py`复用隔离工作进程，读取旧原文库、将新诊断写入
独立忽略索引，并核对完整结构摘要；它不提供业务数据库持久化或正式指纹。
[修复报告](../../docs/reports/mpp-full-repair-2026-09-27.md)记录源码版本、接口边界和全量回归。

近似能力新增 `sql/approximate.py`，纯词法核心仅依赖标准库，解析拒绝接入延迟导入 MPP
原型；`diagnostics/approximate_sql.py` 为文本／文件薄入口，`mpp_approximate_replay.py`
复用全量工具的隔离进程，仅诊断层读取本地索引。生产近似规则不反向依赖诊断模块，
没有新增 scripts 包装或数据库结构；[接口说明](../../docs/design/sql-approximate.md)记录
近似范围；后续可靠结构归一化已由下述模块交付，#27 的 `storage/observations.py`
已提供随构建保存的五层观察统计，详见下述统计模块职责；展示待交付，④完整构建编排见下节。

### 可靠归一化与依赖（2026-09-27）

`sql_apm/sql/normalization.py` 组合解析适配、函数字典、AST 归一化及确定性指纹，提供可复用
Normalizer 与规则快照；`FunctionDictionary.snapshot()` 提供独立规则副本。
`sql_apm/diagnostics/normalize_sql.py` 是文本／文件薄命令，`normalization_replay.py` 负责
有界只读证据。没有新增 scripts 包装，核心不反向依赖 diagnostics。

根目录 `requirements.txt` 将已验证的 pglast 7.18 提升为正式运行依赖；旧 parser_probe
依赖文件额外包含 SQLGlot，仅供候选解析比较。相关解析和归一化测试仍集中于该专项目录，
普通字典／近似测试不强制安装解析依赖。[接口文档](../../docs/design/sql-normalization.md)
给出调用、资源边界和安装命令；当前不交付安装包、数据库业务读写或统一产品 CLI。

### 规则变更差分工具（2026-09-28）

`diagnostics/normalization_diff.py` 在本地原文索引上选择全部、字节标记或 ID 集合，
生成脱敏 SQLite 快照，并比较分组、状态及原因。它复用 `mpp_full_scan.ParserProcess`
的隔离与资源限制，独立的 v3→v4 结构投影仅用于验证，不参与产品归一化。
v4→v5 投影位于 `diagnostics/normalization_v5_audit.py`；两类新位置独立投影，
集合分支只调用版本检查后的冻结 v4 walker，再投影 v5 新位置，不调用被测 v5 walker。
`diagnostics/duration_dispersion.py` 负责只读提取及脱敏耗时证据，
复用完整快照和分阶段摘要计算五维覆盖与三类合并的离散分布；不实现训练或存储服务。
诊断侧上下文检查同时供投影与固定回放守恒审计使用，区分 FILTER、独立查询 WHERE 和冲突更新条件；
业务边界见[指纹契约](../contracts/sql-fingerprints.md#已确认的-where-in-列表粗分桶与-hint-编码清理)。
快照和生产中间结果保留忽略的 `var/`；报告只提交计数、摘要、版本及 ID 示例。
命令与跨冻结版本复现方式见[接口说明](../../docs/design/sql-normalization.md#规则变更分组差分)。

解析能力采用正式名称 `mpp-adapter/9`；已有候选探测、扩展、扫描及修复模块仍是历史诊断工具，
保留原路径供旧报告追溯。本次没有迁移或删除它们，也没有增加 scripts 包装层、数据库服务或重算编排。

## 已实现的导入边界

Issue #18 新增 `ingestion/config.py`、`ingestion/importer.py`、`ingestion/normalizing.py` 和
`ingestion/mpp/reader.py`，分别负责登记、文件／批次、受限归一化和来源解释。
`ingestion/mpp/persistence.py` 保存 MPP 原始字段到 MPP 表的映射，通用文件流程不解释列号。
`storage/ingestion.py` 负责 PostgreSQL 连接、COPY、精确原文和近似写入；
`cli/ingest.py` 与 `__main__.py` 是薄命令入口。产品模块不依赖 diagnostics 或 tests。
[设计](../../docs/design/log-ingestion.md)和[操作说明](../../docs/runbooks/log-ingestion.md)
说明恢复、配对和证据边界。训练快照和统计分别见下述②③边界；发布由下述④实现。

## 已实现的训练判定边界

Issue #21 新增 `training/categories.py` 和 `training/config.py`，分别负责保守产品类别及本地配置；
`storage/training.py` 管理快照、缓存和数据库查询，`cli/training.py` 是薄命令。
`storage/schema.sql` 的 `mpp_training_decisions` 是唯一资格推导，逐条查询和诊断共用；
产品代码不导入 diagnostics 或 tests。没有另立 Python 资格引擎；③统计见下节，④编排见下节。
[判定设计](../../docs/design/training-decisions.md)与[操作说明](../../docs/runbooks/training-decisions.md)
记录接口、临时汇总和持久化边界。

## 已实现的统计计算边界

Issue #25 新增 `baseline/statistics.py` 的纯计算核心，`storage/statistics.py` 组织快照引用、
临时判定、分组读取和事务写入；`baseline/watchdog.py` 是进程退出监护入口，状态落库委托存储层。
`cli/statistics.py` 提供薄命令；独立指标实现仅在 tests 与验收脚本，产品不依赖测试。
训练判定继续复用②的唯一 SQL 函数，未新增永久 Decision 投影。完整门槛结果由
`storage/schema.sql` 的 `mpp_statistic_sufficiency` 按构建封存配置推导；Python 指标核心不重复实现
门槛规则，也不逐行保存派生 JSONB。构建级批量标记查询位于
`storage/statistics_sufficiency.sql`，复用该函数一次验证每层门槛，再作类型化比较；
操作和成本见[统计手册](../../docs/runbooks/baseline-statistics.md#查询门槛结果)。发布与任务编排由下述④实现。

Issue #27 新增 `storage/observations.py`：从同一构建的临时判定投影观察资格和分组，
复用 `baseline/statistics.py` 五层指标以及 `storage/statistics.py` 的批量写入与事务；
没有第二套训练判定函数、指标公式或门槛路径。独立全分组 oracle 留在 tests 与显式验收脚本。

## 已实现的构建编排边界

Issue #29 新增 `baseline/workflow.py` 连接导入、快照、统计及发布步骤，`storage/tasks.py`
管理同集群会话锁与残留任务恢复，`storage/publication.py` 保存六项检查和原子版本切换。
`cli/workflow.py` 提供 full、rebuild、status、history 薄入口。
原有三个写入模块接受同一任务连接，单独调用时也创建任务；不反向依赖诊断或测试。
覆盖函数和构建层计数由存储层维护，命令、原子性及恢复边界见
[编排设计](../../docs/design/build-publication.md)。

## 已实现的部署与演练辅助工具

Issue #31 的 `scripts/deployment/` 保存离线制品收集／打包、解释器自检、凭据初始化、
固定演练配置和结果核对。程序包由 `package-files.json` 的必要文件规则从固定提交生成，
不再交付整棵仓库。`build_release.py` 生成精简 `app`、独立验收包、逐文件摘要和单文件
HTML；`build_bundle.py` 把同一程序与源码／wheel／RPM 组装为内网包。
`render_manual.py` 仅在独立构建环境使用锁定的 Markdown 工具；目标机不安装它。
`run_verification.py` 显式选择实际程序目录，原三组自检的素材与历史探针在 `app` 外；
业务模块仍从交付程序加载，不复制另一份业务实现作为测试对象。
交付目录按完整提交号区分；部署时同时核对交付记录中的预期提交／外部摘要与包内清单，
程序和验收资源必须属于同一提交。仅包内清单通过不能证明交付身份，操作见
[Kylin 手册](../../docs/runbooks/kylin-offline-deployment.md#3-在开发机生成并传输离线包)。
这不引入 Python 安装包、src 布局或新的产品入口；制包与验证方法见
[发布操作说明](../../docs/runbooks/program-release.md)。
演练通过已有 CLI 执行 full/rebuild/status/history，仅用计数查询核对结果，
不复制产品导入、资格或统计实现；产品模块不反向依赖这些脚本。
目录内脚本的操作契约由[部署手册](../../docs/runbooks/kylin-offline-deployment.md)维护。

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

## MPP 命名边界（2026-10-05）

依据 [Issue #33](https://github.com/shenxg13/sql-apm/issues/33)，来源解释包现为
`ingestion/mpp/`，映射文档现为 `docs/design/offline-data-contract/mpp-mapping.md`；
默认日志位置为 `raw/inbox/mpp/`，应用不自动移动文件。统一命名和系统组成见
[系统称谓](../decisions/project-scope.md#已确认的生产系统称谓)，函数规则的默认文件为 1.0.2。
