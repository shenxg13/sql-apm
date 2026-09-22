# SQL APM 本地开发说明

## 当前阶段

当前仓库提供开发流程和原始项目知识。具体业务需求、项目运行模型、业务运行时
和服务依赖在后续沟通中确认。原始资料及其状态见[知识索引](../../.project-wiki/index.md)。

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
首次远程建立前按[本地流程](../../.harness/workflows/local-bootstrap.md)推进；
后续确认的需求与决策另行记录，保留原文供追溯。
GitHub 目标、发布范围和许可证需在接入远程前明确。
