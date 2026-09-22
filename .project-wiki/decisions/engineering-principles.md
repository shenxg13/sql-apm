---
id: decision.engineering-principles
type: decision
status: active
owners:
  - .harness/index.md
  - .harness/workflows/large-change.md
  - .harness/workflows/review-sync.md
updated: 2026-09-22
sources:
  - path: .harness/plans/local-bootstrap.md
    status: current
  - path: .harness/plans/initial-extraction.md
    status: historical
related:
  - architecture.agent-development-harness
confidence: high
---

# Engineering Principles

## Summary

功能与数据正确性是最低交付底线。控制措施应针对项目明确的风险，并考虑性能、
吞吐、资源、并发和恢复成本。具体维护者、消费者、信任边界与部署环境由项目声明。

## Source Of Truth

本页定义通用工程原则；项目接受的运行模型定义适用威胁；当前确认的需求定义
工作负载预算、验收标准和不可豁免门禁。

## Contracts

- 防止静默数据损失、重复和错误归属；需要原子可见性的发布不得暴露不完整状态。
- 有崩溃恢复要求的操作应满足已确认的幂等性和确定性恢复契约。
- 破坏性操作应有明确目标、核验范围及当前授权。
- 保护凭据、秘密和敏感信息。
- 全量扫描、重放、长事务、全局锁及逐行重复校验等高成本措施必须说明具体风险、
  预期资源与恢复成本，以及成本更低的替代方案。
- 证据分为 `qualitative`、`modeled`、`measured`；推断或建模不得标记为实测。
- 验证成本与不确定性的影响相称；普通小改动无需机械增加全量性能实验。
- 项目应从运行模型模板建立并确认自己的决策；示例不自动成为项目约定。

## Workflows

需求阶段确定影响设计的风险与证据等级；实施阶段满足契约并保留来源；评审阶段
核验措施是否针对实际风险、成本是否合理及证据边界是否准确。

## Failure Modes

把性能优先解释为允许错误结果或秘密泄露；把额外控制直接等同于更安全；把某个
项目的信任假设强加给所有项目；把模拟、估算或历史结果当作当前实测。

## Update Rules

维护权、部署、外部消费者、自动化权限或事故证据改变时重新审视运行模型。
原则改变须同步对应 Harness 门禁与需求、实施、评审流程。

## Open Questions

None currently known.
