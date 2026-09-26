# SQL APM 本地开发说明

本页维护实际环境说明及检查命令。产品需求、阶段与待定事项分别见
[交付范围](../../.project-wiki/decisions/project-scope.md)、
[运行环境与组件](../../.project-wiki/decisions/runtime-and-components.md)。
当前以可联网 Alma 环境开展开发；Kylin 离线安装沿用后续专项工作的安排。

## 相关操作契约

| 操作 | 对应要求 |
| --- | --- |
| 本地日志及来源、批次清单 | [日志导入](../../.project-wiki/features/log-ingestion.md) |
| 导入、构建、查询任务与诊断 | [命令行与本地配置](../../.project-wiki/features/operator-cli.md) |
| 重试、串行执行和切换版本 | [构建与发布](../../.project-wiki/features/baseline-versions.md) |
| SQL 原文、明细和历史清理 | [存储与留存](../../.project-wiki/contracts/sql-storage.md) |
| SQL 输入与 Grafana 展示 | [检索及历史查看](../../.project-wiki/features/sql-search-and-views.md) |

业务 CLI 和展示功能尚未交付；数据库初始化／物理结构见下文，下列质量命令用于仓库 Harness。
开发日志样本位于本地忽略目录 `raw/inbox/hashdata/`，不是既定生产接收目录。

## Python 项目环境

项目运行及兼容验证使用精确版本 **Python 3.9.5**，与用户确认的现网版本一致。
2026-09-25 已从官方源码构建独立解释器至 `var/python-3.9.5/`，并创建 `.venv/`。
虚拟环境实际版本已核对为 3.9.5，不包含系统 site-packages；系统 Python 保持 3.9.25。
两个本地目录均由 Git 忽略。

当前仅安装 pip 26.0.1、setuptools 82.0.1、wheel 0.48.0、packaging 26.0。
业务依赖仍须兼容 Python 3.9.5，并在实施时验证和锁定。

在仓库根目录使用：

```bash
source .venv/bin/activate
python --version
python -m pip --version
python -m pip check
```

也可直接调用 `.venv/bin/python`，避免依赖终端激活状态；使用 `deactivate` 退出。
`venv` 沿用创建它的解释器版本，不能通过创建虚拟环境切换 Python 补丁版本。
仓库迁移到其他路径或其他机器时应重新创建环境，不要移动现有虚拟环境后继续使用。

bz2、lzma、sqlite3、SSL、时区和多进程等本机功能验证已通过。
构建依赖、引导工具版本、重建方法及未构建的可选模块见
[Python 环境验证记录](../reports/python-environment-2026-09-25.md)。
本机验证不代表 Kylin 离线部署或全部业务兼容验证。

## PostgreSQL 项目环境

Baseline 存储采用 **PostgreSQL 17**，保存执行记录、SQL 指纹和基线结果。
开发与正式部署的 Baseline 数据库保持相同主版本；部署时选定并记录具体 17.x
补丁版本。当前先准备一个存储实例，使用已有真实日志开发离线 Baseline。

后续测试实时采集时，按需准备独立的 **PostgreSQL 9.4.26** 实例，验证旧版
查询状态、开始时间和锁等待等基础接口。GP/HashData 特有字段需结合现场字段定义
和脱敏样本验证，再补充真实环境联调；普通 PostgreSQL 测试不代表完整兼容验证。

数据库驱动须兼容 Python 3.9.5 及对应数据库版本，实际依赖需验证并锁定。
本机 PG17.10 工具位于 /usr/pgsql-17/bin；已有[项目初始化、版本升级与临时实例验证入口](database-initialization.md)，
初版业务物理结构随之交付。验证使用自动停止／清理的私有临时实例，未部署生产项目实例；
生产部署方式、补丁版本和业务驱动选择仍由后续工作落实。

## 质量工具

所需版本以 `scripts/quality/tool-versions.env` 为准；
安装来源和通用步骤见[工具手册](issue-pr-quality-tooling.md)。

本次初始化将缺少的 Node.js、npm、ShellCheck、shfmt 和 markdownlint-cli2
安装到仓库本地忽略目录 `var/harness-tools`，不修改系统工具。
该目录不进入 Git；其他克隆需自行准备合同要求的工具。

在仓库根目录使用这些工具：

```bash
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --check-tools-only
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

如果工具已在 PATH 中，可直接调用上述脚本。
本地检查无需 GitHub 登录或业务服务，流程用例使用模拟客户端。

## 原始资料完整性

Markdown 排版检查仅跳过已有的 SQL Baseline 原文快照。
维护文档照常检查；原文内容与固定来源通过以下命令核对：

```bash
git hash-object .project-wiki/raw/sql-baseline.md
```

预期结果：`d5b8c6e4ef8d317110aec7737d49088d4a9308e4`。
原文参与模板引用检查，其内容和来源元数据维护规则见[知识规范](../../.project-wiki/schema.md)。

## 当前协作方式

从 [AGENTS.md](../../AGENTS.md) 进入开发流程。
GitHub 目标为公开仓库 shenxg13/sql-apm。
首次发布及其验证按[本地初始化流程](../../.harness/workflows/local-bootstrap.md)完成交接；
接入后的新需求按 [GitHub 工作流](../../.harness/workflows/github-planning.md)推进。
后续确认的需求与决策另行记录，保留原文供追溯；许可证选择仍待讨论。
