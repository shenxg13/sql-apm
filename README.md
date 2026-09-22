# SQL APM

SQL APM 项目当前处于需求沟通阶段，已基于 agent-harness 建立本地开发仓库。
[SQL Baseline 原始资料](.project-wiki/raw/sql-baseline.md)是后续讨论的输入；
业务架构、技术栈、实施范围、阈值和验收标准留待逐项确认。

## 从这里开始

- 阅读[项目知识索引](.project-wiki/index.md)，了解资料来源及当前确认边界。
- Agent 从 [AGENTS.md](AGENTS.md) 进入 [Harness](.harness/index.md)。
- 本地开发与检查见[开发说明](docs/runbooks/local-development.md)。
- 本次初始化范围见[初始化计划](.harness/plans/local-bootstrap.md)。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `.harness/` | 开发流程、任务路由和本地初始化计划 |
| `.project-wiki/` | 原始项目知识、通用工程原则及后续确认的决策 |
| `.github/` | Issue/PR 模板和质量 CI 配置 |
| `scripts/` | 通用质量、流程和评审证据检查 |
| `docs/` | 来源、本地开发、模板更新及验证记录 |

项目运行模型尚待确认；知识库中的模型模板和单维护者示例仅供讨论。
默认协作语言为中文，默认分支为 main。

## 本地检查

按[质量工具合同](docs/runbooks/issue-pr-quality-tooling.md)准备工具后运行：

```bash
scripts/quality/check.sh --check-tools-only
scripts/quality/check.sh
```

如使用本次初始化安装的仓库本地工具，命令见[开发说明](docs/runbooks/local-development.md)。
检查覆盖 Harness、文档和离线流程回归，业务检查将在具体需求确认后定义。

## 来源与维护

模板固定版本、本地定制及原始资料来源见[来源记录](docs/provenance.md)。
后续按[人工更新流程](docs/updating.md)审阅模板更新。
本地验证结果见[初始化验证记录](docs/reports/local-bootstrap-validation.md)。

当前交付范围为本地仓库；GitHub 发布和许可证安排留待确定。
