---
id: method.knowledge-maintenance
type: method
status: active
owners:
  - .harness/workflows/wiki-update.md
  - .harness/workflows/knowledge-sync.md
  - .project-wiki/index.md
updated: 2026-09-25
sources:
  - path: .harness/rules.md
    status: current
  - path: docs/reports/knowledge-reorganization-2026-09-25.md
    status: current
related:
  - architecture.agent-development-harness
  - decision.project-scope
confidence: high
---

# 项目知识维护方法

## Summary

入口负责选择阅读范围，主题页完整保存需求，证据文档保留来源与验证结果。
仅在维护知识、重组文档或发现口径冲突时阅读本页，不作为所有任务的前置阅读。

## Source Of Truth

- 2026-09-25 用户在讨论入口膨胀问题后明确要求：“开始执行优化，执行的过程中注意不要丢失已经确认的需求”。
- 用户确认的需求定义目标，代码及实际验证定义已实现行为；两者分别标记。
- [官方文章](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
  建议按任务读取相关资料、保持入口精简。以下目录分工是本项目的应用方案，非官方字数限制。
- [迁移记录](../../docs/reports/knowledge-reorganization-2026-09-25.md)记录本次完整性核对。

## Contracts

| 位置 | 维护职责 |
| --- | --- |
| 根 Agent 入口 | 适用于全仓的协作规则、任务入口与必要限制 |
| Wiki 索引 | 主题、用途和链接；不展开需求正文 |
| Wiki 主题页 | 当前有效的完整需求、确认依据、边界和待定事项 |
| Harness | 开发、知识维护、Issue 交付和检查流程 |
| Runbook | 实际操作步骤、命令及环境检查记录；业务规则链接到主题页 |
| 原文与报告 | 原始资料及调查／验证证据；观察和推断不得自动提升为要求 |
| 知识日志 | 变更摘要与条款链接；历史记录不覆盖当前主题页 |
| GitHub Issue | 该任务的实时契约、评论和执行状态；本地不维护正文或状态镜像 |

已确认细节即使很长也须保存到适当主题页。缩短入口时不删除业务边界、例外、来源、
试行数值或未确认标记。简介不取代正文；跨主题关系通过明确链接表达。
不把所有主题文件列成每次必读清单，也不为每项需求新增 Skill。

## Workflows

1. 从简短索引选择任务相关主题，读取目标条款及实际涉及的关联规则；已有上下文仍有效时复用。
2. 确认变化后更新负责该规则的主题正文。需要改变其他主题的契约时同步那些条款，并注明来源。
3. 新增页面、调整路径或阅读条件时更新索引；仅规则数值变化不向索引复制正文。
4. 只有 Agent 工作方式变化才修改根入口或 Harness；只有实际操作变化才修改 runbook。
5. 在知识日志添加简短摘要及正文链接；保留此前确认和修订的历史，不把旧建议恢复为现行要求。
6. 涉及正在交付的 Issue 时，遵循既有范围变更流程更新在线契约，不用 wiki 更新代替。
7. 重组已有知识时，先记录原章节到新位置的映射，检查正文、来源、相对链接与引用。
   业务语义变化须另有用户依据，不能夹带在文档搬迁中。
8. 运行与改动相称的排版、链接和完整性检查；出现不一致先核对来源，不自行删除一方来消除冲突。

## Failure Modes

- 每次确认都向根入口、索引和 runbook 复制同一份详细要求，形成多个维护副本。
- 拆分文件后仍要求每项任务阅读全部文件，入口缩短而实际上下文不减。
- 只保留摘要导致细节丢失，或把待定、候选、已确认和已实现混为一谈。
- 原始资料、历史日志或报告中的旧口径覆盖了后续明确确认。

## Update Rules

知识职责或读取方式变化时同步本页、对应 Harness 工作流及简短索引。
保留原始 SQL Baseline 快照的字节；新的确认写入主题页，不改原文。

## Open Questions

本次优化没有改变产品需求及未决事项；按具体任务读取相应主题的 Open Questions。
