# SQL APM

SQL APM 项目当前处于需求沟通阶段，已基于 agent-harness 建立本地开发仓库。
[SQL Baseline 原始资料](.project-wiki/raw/sql-baseline.md)是后续讨论的输入；
已确认使用 Python 3.9.5，后续在项目 `.venv/` 中建立环境，
Baseline 存储采用 PostgreSQL 17，Grafana 直接查询该库展示基线和 SQL 执行历史。
首期不引入 Prometheus，复用 HashData 已有监控，平台状态先通过任务记录和日志保留。
日志首期手动拷贝至基线服务器，不引入流式读取组件；后续通过定时 SCP 每天传输一次。
SQL 默认纳入基线计算，明确排除的语句进入黑名单，当前名单见[训练资格与黑名单](.project-wiki/contracts/training-eligibility.md#已确认的-sql-纳入与排除策略)。
基线按计算集群、数据库、执行用户、SQL 结构指纹及计时类别分别统计，详见[分组规则](.project-wiki/contracts/timing-and-grouping.md)。
后续按需准备 PostgreSQL 9.4.26 做实时采集兼容测试。
当前开发以可联网的 AlmaLinux 环境为准；生产 Kylin V10 SP2 的离线安装方案
由后续专项 Issue 承接。环境准备说明见[开发说明](docs/runbooks/local-development.md)。
首期先交付离线基线流程，再接入 SQL 检索和 Grafana 展示；两部分均在首期范围内。
交付顺序及验证安排见[项目知识](.project-wiki/decisions/project-scope.md#已确认的首期交付顺序)。
已确认需求按主题保存在知识库；各主题标明尚待确认或实施验证的事项。

## 从这里开始

- 从[项目知识索引](.project-wiki/index.md)按任务选择主题，查看完整需求、来源及确认边界。
- Agent 从 [AGENTS.md](AGENTS.md) 进入 [Harness](.harness/index.md)。
- 本地开发与检查见[开发说明](docs/runbooks/local-development.md)。
- 本次初始化范围见[初始化计划](.harness/plans/local-bootstrap.md)。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `.harness/` | 开发流程、任务路由和本地初始化计划 |
| `.project-wiki/` | 原始项目知识、通用工程原则及后续确认的决策 |
| `.github/` | Issue/PR 模板和质量 CI 配置 |
| `sql_apm/` | Python 产品源码；职责划分见[源码布局](.project-wiki/architecture/source-layout.md) |
| `rules/` | 版本化规则及来源数据 |
| `tests/` | 产品自动化测试 |
| `scripts/` | 开发、规则维护、质量和评审证据工具 |
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

GitHub 仓库：[shenxg13/sql-apm](https://github.com/shenxg13/sql-apm)，公开，默认分支 main。
首次同步范围与验证见[首次发布记录](docs/reports/initial-publication.md)。
接入远程后的需求和交付遵循 [GitHub 工作流](.harness/workflows/github-planning.md)。
许可证尚未选择，当前文件集不包含 LICENSE。
