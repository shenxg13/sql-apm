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

`scripts/diagnostics/statement_census.py` 只读盘点 HashData CSV 的词法类别与异常，
输出固定标签、计数、摘要和来源定位，不执行 SQL、不判定训练黑名单。
全量扫描及有界回放命令见[类别核查报告](../docs/reports/statement-category-census-2026-09-26.md)。
生产输入保留在本地忽略目录；合成回归随 `.venv/bin/python -m unittest discover -s tests -v` 运行。

## 数据库初始化与验证

- `scripts/db/initialize.sh`：从明确指定的已有 PG17 实例引导项目账号／数据库，
  以项目账号安装或核对 schema、物理表、约束、索引和结构版本。
- `.venv/bin/python scripts/db/verify.py`：自动创建并清理私有 disposable PG17 实例，
  回放合成存储用例；默认不会接触已有服务或生产数据。

完整参数、凭据、阶段恢复和检查方式见[数据库操作说明](../docs/runbooks/database-initialization.md)。
SQL 资源位于 `sql_apm/storage/`，业务数据库驱动不是这两个入口的依赖。
