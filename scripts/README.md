# Repository Scripts

| Command | Purpose | Network |
| --- | --- | --- |
| `scripts/quality/check.sh` | Tools, Shell, Markdown, template integrity and offline regressions | None |
| `scripts/quality/check.sh --check-tools-only` | Required local tool versions | None |
| `scripts/quality/check.sh --pr PR --repo OWNER/REPO` | Local checks plus live PR contract | GitHub |
| `scripts/quality/check_template.sh` | Tracked files, local links, wiki ownership and CI entrypoint | None |
| `scripts/github/issue_status.sh` | Live contract/state checks and audited transitions | GitHub |
| `scripts/github/pr_contract.sh` | Live PR structure, branch and Issue association | GitHub |
| `scripts/github/review_convergence.sh RECORD.json` | Local review evidence structure | None |
| `scripts/quality/install_ci_tools.sh` | Explicit Linux x86_64 CI tool provisioning into runner temporary storage | Official downloads and npm |

The quality entrypoint invokes these tests once:

- `scripts/tests/test_issue_status.sh`
- `scripts/tests/test_pr_contract.sh`
- `scripts/tests/test_review_convergence.sh`
- `scripts/tests/test_quality.sh`
- `scripts/tests/test_template.sh`

Tests use disposable files and mocked GitHub clients. The template checker uses
its private Node.js implementation `scripts/quality/check_template.mjs`.
It checks local Markdown links and concrete repository path references outside
code fences, active wiki owners/source paths, necessary files and the CI entrypoint. The explicit `--source-audit` option
reads literal source markers from the provenance profile and scans extraction
residue; it is not a blanket restriction on downstream project vocabulary or assets. Template placeholders and external links are not fetched. Human review
still owns semantic correctness and external-link validity.

Installation is never part of the default read-only quality command. The CI
provisioner requires a CI environment; local setup follows the tool runbook.

## 函数字典工具

Python 3.9.5 环境及准备步骤见[本地开发说明](../docs/runbooks/local-development.md)。
以下产品检查独立于 Harness，实施／评审时分别运行：

```bash
.venv/bin/python -m sql_apm.sql.function_dictionary validate rules/functions/v1.0.1.json
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/functions/coverage.py
```

来源重建、有界日志核对、规则升级及维护入口见[函数字典说明](../rules/functions/README.md)。
日志探测使用本地忽略输入；普通测试仅使用人工数据，不访问数据库或生产服务。

## 语句类别调查

`python -m sql_apm.diagnostics.statement_census` 只读盘点 HashData CSV 的词法类别与异常，
输出固定标签、计数、摘要和来源定位，不执行 SQL、不判定训练黑名单。
全量扫描及有界回放命令见[类别核查报告](../docs/reports/statement-category-census-2026-09-26.md)。
生产输入保留在本地忽略目录；合成回归随 `.venv/bin/python -m unittest discover -s tests -v` 运行。

## 数据库初始化与验证

- `scripts/db/initialize.sh`：从明确指定的已有 PG17 实例引导项目账号／数据库，
  以项目账号安装或核对 schema、物理表、约束、索引和结构版本；
  `upgrade` 显式执行 1.0.0 → 1.1.0 的 MPP 命名迁移。
- `.venv/bin/python scripts/db/verify.py`：自动创建并清理私有 disposable PG17 实例，
  回放合成存储用例；默认不会接触已有服务或生产数据。

完整参数、凭据、阶段恢复和检查方式见[数据库操作说明](../docs/runbooks/database-initialization.md)。
SQL 资源位于 `sql_apm/storage/`，业务数据库驱动不是这两个入口的依赖。

## 解析器结构保真探测

MPP 解析适配位于 `sql_apm/sql/mpp_parser.py`；共用词法检查及PG语法树处理位于同包的
`lexical.py`、`pg_ast.py`。可复用探测逻辑位于 `sql_apm/diagnostics/`，诊断统一通过包模块运行，
不再保留 `scripts/diagnostics/` 转发层。测试直接导入包内模块。

| 包模块命令 | 用途 |
| --- | --- |
| `python -m sql_apm.diagnostics.parser_fidelity` | 候选解析器比较 |
| `python -m sql_apm.diagnostics.mpp_adapter_probe` | 原140个定位的MPP适配重放 |
| `python -m sql_apm.diagnostics.mpp_expansion_probe` | 扩展形态抽样和重放 |
| `python -m sql_apm.diagnostics.mpp_expansion_matrix` | 固定JSON矩阵的字段与关系检查 |
| `python -m sql_apm.diagnostics.mpp_broad_matrix` | 整体完整结构与批次矩阵 |
| `python -m sql_apm.diagnostics.mpp_broad_replay` | 全部来源文件的有界抽样和重放 |
| `python -m sql_apm.diagnostics.mpp_full_scan` | 完整EOF读取、精确原文去重与全量解析覆盖 |
| `python -m sql_apm.diagnostics.mpp_full_audit` | 全量失败特征及历史固定结果对照 |
| `python -m sql_apm.diagnostics.approximate_sql` | 文本／文件的解析拒绝与独立观察用近似指纹 |
| `python -m sql_apm.diagnostics.mpp_approximate_replay` | 全部旧拒绝及已修复对照的近似能力重放 |
| `python -m sql_apm.diagnostics.mpp_full_repair` | 固定原文库的版本修复重放与完整结构摘要对照 |
| `python -m sql_apm.diagnostics.normalize_sql` | 可靠结构归一化、结构指纹及独立近似结果的文本／文件入口 |
| `python -m sql_apm.diagnostics.normalization_replay` | 固定样本的归组、结构守恒及稳定性验证 |
| `python -m sql_apm.diagnostics.normalization_diff` | 原文选择集的脱敏快照、分组／状态差分及 v4／v5 自动核对 |

在仓库根目录运行上述模块命令，解释器使用 `.venv/bin/python`；解析实验依赖仍需通过
`PYTHONPATH=var/parser-probe/site-packages` 指定。
可选实验依赖锁定在 `tests/parser_probe/requirements.txt`，两份合成输入位于
`tests/parser_probe/fixtures/`；pglast 7.18 已由根目录 `requirements.txt` 锁定为运行依赖，
SQLGlot 仍仅用于候选实验。生产原文和缓存留在
本地忽略区域，报告及结构摘要位于 `docs/reports/` 与 `docs/reports/data/`。

[目录调整报告](../docs/reports/parser-layout-2026-09-27.md)记录迁移、依赖方向与等价验证；
[整体扩展报告](../docs/reports/mpp-broad-validation-2026-09-27.md)记录语法支持、抽样分母和缺口。
[全量覆盖报告](../docs/reports/mpp-full-scan-2026-09-27.md)说明完整遍历、失败分类与本地索引使用。
[修复报告](../docs/reports/mpp-full-repair-2026-09-27.md)记录版本5、新节点与全量结构回归。
解析探测工具仅提供诊断；可靠归一化及指纹通过 `normalize_sql` 和 Normalizer 核心调用，
规则快照、安装方法及命令退出码见[可靠接口说明](../docs/design/sql-normalization.md)。

近似能力的纯标准库核心位于 `sql_apm/sql/approximate.py`，正式接口、退出码和观察用途限制见
[接口说明](../docs/design/sql-approximate.md)。它不提供可靠结构指纹、正常基线或统计服务。

解析能力的当前正式名称为 `mpp-adapter/9`。上述候选比较、probe、扩展、全量扫描和修复模块
是历史诊断工具，保留供既有报告追溯；不会因本次命名调整迁移或删除。规则变更验证使用
[分组差分命令](../docs/design/sql-normalization.md#规则变更分组差分)，支持全部、字节标记或 ID 选择集。

## 补充日志与样本门槛观察

`python -m sql_apm.diagnostics.log_supplement` 提供扩展清单、类别对照和实际日期汇总；
`python -m sql_apm.diagnostics.threshold_coverage` 在带 `--record-dates` 的完整原文索引上，
按集群比较实际算法基准（当前方案名 `v5`）、候选位置／函数归一及 TiDB 式词法对照的 30／200／1,000 次、7 日覆盖。
使用与上文一致的解释器及解析依赖路径，原文／缓存保留在忽略目录。
[操作说明](../docs/runbooks/log-supplement.md)定义方案、分母、参数、失败与恢复；
[补充报告](../docs/reports/cluster-log-supplement-2026-09-28.md)记录本轮证据。
这些计数不等同执行样本、训练资格或基线可用率；#13 的 v4 结果须在冻结 checkout 复现。

## 耗时离散诊断

`python -m sql_apm.diagnostics.duration_dispersion extract` 只读提取带 SQL 的 duration 记录，
`report` 对已审计的 v4／v5 快照计算真实维度的门槛覆盖和新增合并子组的中位数倍数分布。
命令、分母、脱敏及恢复规则见[耗时诊断说明](../docs/runbooks/duration-dispersion.md)。
原文、索引和耗时缓存均留在本地忽略目录。
