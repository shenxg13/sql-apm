---
id: architecture.agent-development-harness
type: architecture
status: active
owners:
  - AGENTS.md
  - .harness/index.md
  - scripts/quality/check.sh
updated: 2026-10-12
sources:
  - path: .harness/plans/local-bootstrap.md
    status: current
  - path: .harness/plans/initial-extraction.md
    status: historical
  - path: scripts/README.md
    status: current
  - path: .harness/workflows/github-planning.md
    status: current
  - path: .harness/workflows/review-sync.md
    status: current
  - path: .harness/tooling-runtime.md
    status: current
  - path: docs/provenance.md
    status: current
related:
  - decision.engineering-principles
  - method.knowledge-maintenance
confidence: high
---

# Agent Development Harness

## Summary

入口负责路由，Harness 负责流程，Wiki 负责持久知识，脚本负责可机械验证的契约。
项目业务代码、运行时和服务由采用模板的项目自行定义。

## Source Of Truth

- 确认需求定义目标，当前源代码与验证结果定义已实现行为。
- 接入 GitHub 后，Issue 正文定义任务契约，`status:*` 标签定义执行状态，`type:*` 标签定义类型。
- 本地初始化的已确认范围保存在初始化计划，结果保存在验证报告。
- 用户于 2026-10-11 确认采用上游流程；[本次采用](../../docs/provenance.md#本次采用)记录
  固定来源、确认与差异。下面记录工作流和检查契约，实际验收证据由对应 PR 保存。

## Contracts

- 按任务读取相关知识，索引仅保留主题路由；业务需求完整保存在各自主题页。
- `CLAUDE.md` 只有一行 `@AGENTS.md`，Claude Code 启动时以导入方式把 `AGENTS.md` 与它一起
  加载，不依赖 Agent 自行打开；入口规则只在 `AGENTS.md` 维护。`CODEX.md` 是保留的短路由，
  Codex 直接读取 `AGENTS.md`。模板检查不解析导入，实际加载由对应 PR 的人工检查证明；
  上下文压缩后是否重新展开未经验证。本项目以合并后的新 Claude Code 会话实测启动加载，
  验收证据由在线 Issue 与 PR 保存；来源与确认见[本次采用](../../docs/provenance.md#本次采用)。
- 确认需求后更新所属正文并记录简短日志；仅导航或 Agent 工作方式变化才更新入口。
  新知识日志写入 `.project-wiki/log/`，Harness 更新写入 `.harness/update-log/`，一次变更
  一个文件；格式与职责见[知识维护方法](../methods/knowledge-maintenance.md)。原有两份日志
  冻结保留，模板检查验证新条目文件名，历史文件仍是必需文件。
- 并发使用同一检出时，实施必须在仓库同级的 `仓库名-issue-N` 工作树中进行；无并发时
  可沿用当前检出。评审使用固定 head 的独立工作树或不改变检出的只读命令。
  执行完成步骤的会话负责实施、评审及取消时的清理，并先检查被忽略文件，见
  [交付流程](../../.harness/workflows/github-planning.md#worktree-cleanup)。
- 工作树不共享被忽略的本地资源；仓库外的兼容检查工具可通过绝对 `PATH` 共用，
  主检出默认位置 `var/harness-tools` 的安装同样可以按绝对路径从它的各个链接工作树复用，
  独立克隆不适用。端口、数据库、数据目录的共享或独占方式由项目 runbook 声明。定位主检出的
  方法见[运行时指南](../../.harness/tooling-runtime.md#worktrees-and-local-resources)，
  来源与确认见[本次采用](../../docs/provenance.md#本次采用)。
- 并发不消除契约或结构版本链的真实依赖，仍由 Issue 声明先后。合并后核对该次 `main`
  推送的检查，失败时先回退或立即修复，检查恢复前不合并其他 PR；不要求合并前重跑最新
  组合，不引入合并队列，也不改变固定评审面、轮次和阻断规则。
- 知识搬迁保留正文、来源、边界和待定事项，具体流程见[知识维护方法](../methods/knowledge-maintenance.md)。
- 本地质量入口默认离线；Issue、PR 测试用可控客户端模拟。
- Issue 按主要交付物选恰好一个类型，默认五类、产品边界及需求会话职责见
  [Issue 类型](../../.harness/workflows/github-planning.md#issue-types)。来源与确认见
  [本次采用](../../docs/provenance.md#本次采用)；状态脚本
  只在 planned 准入处检查任意 `type:` 前缀的标签数量，写入前拒绝缺少或重复类型，
  不自动归类、不追溯其他状态，所有转换保留类型。验证证据由对应 PR 保存。
- 本地安装由 `scripts/quality/install_local_tools.sh` 显式联网执行，在 Linux x86_64
  将固定工具安装到新目录，默认 `var/harness-tools`；检查入口不自动查找该目录，
  调用时临时设置 `PATH`。安装与工具边界见[工具手册](../../docs/runbooks/issue-pr-quality-tooling.md)。
- 项目业务检查由项目所有的 `scripts/quality/project_checks.sh` 声明，CI 在通用入口之后
  单独调用；通用入口不调用它。本项目入口保留原 CI 的函数字典校验、单元测试和
  函数覆盖三条 Python 命令，详见[脚本导航](../../scripts/README.md)。
- 整改仅覆盖未关闭台账项及其必要回归和文档同步，按条目列出文件并在接受交接前核对；
  观察项默认单独记录，维护者在在线 Issue 纳入后按台账核验，未纳入项在批准后逐条处置。
  维护者与风险责任人按已确认运行模型认定；未声明时个人仓库由账号所有者承担，组织仓库
  须先声明。完整规则见[评审协议](../../.harness/workflows/review-sync.md#review-convergence-protocol)，
  来源与确认见[本次采用](../../docs/provenance.md#本次采用)。
  观察项和整改记录仅用 Markdown，JSON 校验器不检查这些内容，实施与验证证据由关联 PR 保存。
- 评审 JSON 校验器只验证结构与协议一致性，不证明评审实际执行。
- 项目配置使用 Wiki 的运行模型决策和任务路由；首版不提供配置生成器。
- 模板检查只验证其声明的文件、引用与残留规则，语义完整性仍需人工审查。

## Workflows

1. 初始化并明确项目模型、命令和配置。
2. 根据任务类型选择小改动、大改动、知识维护或 GitHub 流程。
3. 执行适当验证；重大改动执行独立评审与交付流程。
4. 按来源版本人工审阅通用更新；下游的通用改进按更新说明回流。

## Failure Modes

- 把模拟 GitHub 或本地自检描述为真实远程交付或独立评审。
- 将业务运行时加入通用质量入口，破坏独立运行能力。
- 更新模板时覆盖下游项目自己的运行模型、知识与业务检查入口。

## Update Rules

通用流程、脚本接口、质量边界或知识职责改变时更新本页及对应源文件。

## Open Questions

跨平台扩展与自动更新工具留待有明确需求时讨论。
