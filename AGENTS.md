# Agent Entry

SQL APM 的产品范围与交付阶段见
[项目范围](.project-wiki/decisions/project-scope.md)。用户明确授权的任务界定本轮工作；
已确认需求、待定建议和已实现行为须分开解释。

## 按任务读取

- 简单文字修正：读取目标文件及直接相关约束。
- 非简单开发或流程调整：读取 `.harness/index.md` 和 `.harness/rules.md`，
  用 `.harness/catalog.md` 选择适用工作流，检查相关源文件。
- 涉及业务要求：从 `.project-wiki/index.md` 选择相关主题；仅沿实际涉及的关联条款补读。
- 运行仓库工具或诊断环境问题前：查阅 `.harness/tooling-runtime.md`。
- 更新知识：按 `.harness/workflows/wiki-update.md` 和
  `.project-wiki/methods/knowledge-maintenance.md` 维护负责该规则的主题页。

已读取且仍适用的说明可以复用。根入口和知识索引保留路由，详细需求不在这里重复。

## GitHub 与交付

仓库已接入 `shenxg13/sql-apm`；需求、Issue 实施、评审及交接遵循
`.harness/workflows/github-planning.md`。开始 Issue 工作时读取其在线正文和评论。
`status:*` 标签是执行状态的唯一来源，用 `scripts/github/issue_status.sh` 检查和转换。
未认领工作只从 `status:planned` 开始；恢复 `status:in-progress` 须有明确交接且无并发所有者。
已确认范围变更先更新对应在线 Issue 契约，再实施。远程不可达不重置生命周期。
Issue、流程评论和 PR 正文默认使用中文。

首次发布与必要修复的授权、证据保留在 `.harness/plans/local-bootstrap.md` 和
`docs/reports/initial-publication.md`，仅追溯初始化或发布范围时阅读。

## 验证与边界

- 按改动选择检查；完整离线 Harness 检查入口为 `scripts/quality/check.sh`。
  `--pr` 还会访问 GitHub，须符合本轮网络权限。业务检查由具体任务契约定义。
- 使用会话的文件系统、网络和审批政策。网络故障不直接等同于凭据无效，
  按工具指南诊断；已有授权在其范围内继续有效。
- 原始需求快照保持字节不变，生产日志保持本地忽略；报告区分观察、推断与验证。
- 需求或流程发生持久变化时更新对应知识正文，日志留摘要和链接；
  仅导航变化更新索引，仅协作规则变化更新本入口。
