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
Linux x86_64 可运行 `scripts/quality/install_local_tools.sh [DIR]` 安装固定版本的工具：
默认目录为 `var/harness-tools`，也接受一个相对或绝对目录；目标已存在、末段为 `.` 或 `..`、未知选项或多于一个
参数时会在下载和写入前拒绝。安装在目标旁暂存，成功后才发布目标，失败时清理临时目录。
脚本不改 `PATH`、系统目录或用户的 npm 缓存与全局前缀。使用默认安装位置时，每次检查临时
设置 `PATH="$PWD/var/harness-tools/bin:$PATH"`；检查入口不会自动查找该目录。
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

项目业务检查在项目 Runbook 中定义，并加入任务的验证计划。把命令写进项目所有的
`scripts/quality/project_checks.sh`，CI 会在通用入口之后单独调用它；通用入口不调用它，
始终能独立验证 harness。

## 接入 GitHub

先确定 owner、仓库名、可见性、许可证安排与发布范围，再创建或关联远程并推送。
配置以下状态标签和默认类型标签，随后回读核对。模板仓库设置只在该仓库要供其他项目复用时启用，
普通业务仓库不需要；SQL APM 使用普通业务仓库：

| Label | Color |
| --- | --- |
| `status:triage` | `D4C5F9` |
| `status:planned` | `0E8A16` |
| `status:in-progress` | `1D76DB` |
| `status:blocked` | `B60205` |
| `status:needs-review` | `FBCA04` |
| `status:done` | `0E8A16` |
| `type:feature` | `A2EEEF` |
| `type:defect` | `D73A4A` |
| `type:release` | `5319E7` |
| `type:maintenance` | `C5DEF5` |
| `type:process` | `FEF2C0` |

每次操作显式核对目标。示例中的 OWNER/REPO 是需要替换的占位值：

```bash
gh label create 'status:triage' --color D4C5F9 --repo OWNER/REPO
```

对其他状态和类型使用表中名称和颜色；已有标签先读取再决定是否调整。
按主要交付物为每个 Issue 选择恰好一个类型，在转为 `status:planned` 前添加；
类型含义、项目扩展与更正规则见 [Issue 类型](../.harness/workflows/github-planning.md#issue-types)。
接入后的任务使用需求模板、审计状态命令、PR 模板及独立评审流程。
CI 调用同一本地质量入口；GitHub CLI 与连接器的访问需分别验证。

## 并发实施与评审

不同 Issue 可以同时实施；同一台机器上有其他会话正在使用同一检出时，实施必须使用
自己的 Git 工作树，不在别人使用的检出里切换分支。工作树放在仓库同级，目录为
`仓库名-issue-N`；创建命令见 [实施会话](../.harness/workflows/github-planning.md#implementation-session)。
没有并发使用者时不强制建立工作树。评审使用固定在被评审 head 的独立工作树，或用
不改变检出状态的只读命令，见 [评审流程](../.harness/workflows/review-sync.md)。

忽略文件不随工作树共享；检查工具可安装在仓库外共享目录，并按命令设置绝对 `PATH`，
主检出默认位置 `var/harness-tools` 的安装也可以从各工作树复用，定位方法
见 [本地资源约定](../.harness/tooling-runtime.md#worktrees-and-local-resources)。项目 runbook
须声明端口、数据库和数据目录等资源哪些可共享、哪些须独占或单独分配。

执行完成步骤的会话负责清理：实施工作树在合并及合并后验收完成后删除，评审工作树在
该轮记录发出后删除，取消 Issue 时也删除。先用 `git worktree list` 找到目录，检查并保留
被忽略的证据或数据，再用 `git worktree remove`；完整步骤见
[清理规则](../.harness/workflows/github-planning.md#worktree-cleanup)。Git 不会为被忽略文件阻止删除。

新日志按一次变更一个文件写入 [知识日志目录](../.project-wiki/log/) 和
[Harness 更新日志目录](../.harness/update-log/)，旧日志保留冻结历史。
两项工作若改同一契约或结构版本链，仍在 Issue 依赖中声明先后；导航等文件的偶发冲突
照常解决。合并后核对该次 `main` 推送的检查；失败时先回退该合并或立即修复，检查恢复
之前不合并其他 PR。此规则不要求合并前在最新目标上重跑，也不引入合并队列。

## 权限与初始化证据

项目文件只描述流程，不能自动授予工作区写入、网络、令牌或连接器权限。
首次发布的自检记录与正式独立评审分别记录。公开仓库的许可证由所有者明确选择。
