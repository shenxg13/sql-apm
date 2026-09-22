# 初始化说明

## 准备项目

1. 将已选定版本的文件复制到新项目，按项目选择独立 Git 历史或现有仓库集成。
2. 从 [Agent 入口](../AGENTS.md) 阅读流程，更新 README、任务路由及项目知识索引。
3. 按 [运行模型模板](../.project-wiki/templates/operating-model.md) 创建并确认项目决策。
4. 在项目 Runbook 中记录业务运行时、非秘密参数和测试命令。
5. 保存采用的模板版本或提交 SHA，并记录本地定制，供未来人工更新使用。

有 GitHub remote 的项目按 Issue 工作流推进；尚无首次 remote 的新项目按
[本地初始化流程](../.harness/workflows/local-bootstrap.md) 推进。

## 本地验证

先按 [工具手册](runbooks/issue-pr-quality-tooling.md) 安装声明工具。
质量入口枚举 Git 跟踪文件，新项目首次检查前应将拟交付文件加入暂存区。

```bash
git init -b main
git add .
scripts/quality/check.sh --check-tools-only
scripts/quality/check.sh
git diff --cached --check
```

已有 Git 仓库从 `git add` 开始。本地入口不需要 GitHub 登录；流程回归在临时目录
中模拟 `gh`，不会读写真实 Issue 或 PR。临时目录由测试创建并精确清理。

项目业务检查在项目 Runbook 中定义，并加入任务的验证计划。可在项目自己的 CI
步骤调用，通用入口始终能独立验证 harness。

## 接入 GitHub

先确定 owner、仓库名、可见性、许可证安排与发布范围，再创建或关联远程并推送。
启用模板仓库设置与以下状态标签，随后回读核对：

| Label | Color |
| --- | --- |
| `status:triage` | `D4C5F9` |
| `status:planned` | `0E8A16` |
| `status:in-progress` | `1D76DB` |
| `status:blocked` | `B60205` |
| `status:needs-review` | `FBCA04` |
| `status:done` | `0E8A16` |

每次操作显式核对目标。示例中的 OWNER/REPO 是需要替换的占位值：

```bash
gh label create 'status:triage' --color D4C5F9 --repo OWNER/REPO
```

对其他状态使用表中名称和颜色；已有标签先读取再决定是否调整。
接入后的任务使用需求模板、审计状态命令、PR 模板及独立评审流程。
CI 调用同一本地质量入口；GitHub CLI 与连接器的访问需分别验证。

## 权限与初始化证据

项目文件只描述流程，不能自动授予工作区写入、网络、令牌或连接器权限。
首次发布的自检记录与正式独立评审分别记录。公开仓库的许可证由所有者明确选择。
