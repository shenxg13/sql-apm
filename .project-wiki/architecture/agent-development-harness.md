---
id: architecture.agent-development-harness
type: architecture
status: active
owners:
  - AGENTS.md
  - .harness/index.md
  - scripts/quality/check.sh
updated: 2026-09-25
sources:
  - path: .harness/plans/local-bootstrap.md
    status: current
  - path: .harness/plans/initial-extraction.md
    status: historical
  - path: scripts/README.md
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
- 接入 GitHub 后，Issue 正文定义任务契约，标签定义执行状态。
- 本地初始化的已确认范围保存在初始化计划，结果保存在验证报告。

## Contracts

- 按任务读取相关知识，索引仅保留主题路由；业务需求完整保存在各自主题页。
- 确认需求后更新所属正文并记录简短日志；仅导航或 Agent 工作方式变化才更新入口。
- 知识搬迁保留正文、来源、边界和待定事项，具体流程见[知识维护方法](../methods/knowledge-maintenance.md)。
- 本地质量入口默认离线；Issue、PR 测试用可控客户端模拟。
- 评审 JSON 校验器只验证结构与协议一致性，不证明评审实际执行。
- 项目配置使用 Wiki 的运行模型决策和任务路由；首版不提供配置生成器。
- 模板检查只验证其声明的文件、引用与残留规则，语义完整性仍需人工审查。

## Workflows

1. 初始化并明确项目模型、命令和配置。
2. 根据任务类型选择小改动、大改动、知识维护或 GitHub 流程。
3. 执行适当验证；重大改动执行独立评审与交付流程。
4. 按来源版本人工审阅通用更新。

## Failure Modes

- 把模拟 GitHub 或本地自检描述为真实远程交付或独立评审。
- 将业务运行时加入通用质量入口，破坏独立运行能力。
- 更新模板时覆盖下游项目自己的运行模型与知识。

## Update Rules

通用流程、脚本接口、质量边界或知识职责改变时更新本页及对应源文件。

## Open Questions

跨平台扩展与自动更新工具留待有明确需求时讨论。
