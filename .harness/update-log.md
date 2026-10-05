# Harness Update Log

| Date | Commit | Summary |
| --- | --- | --- |
| 2026-09-21 | Upstream history | 通用模板历史，见[上游来源](../docs/provenance.md) |
| 2026-09-22 | `859c39d1697241579b9bde443125477f88b1827c` | 采用固定模板初始化 SQL APM 本地仓库；定制范围见[来源记录](../docs/provenance.md)，本地检查见[验证记录](../docs/reports/local-bootstrap-validation.md) |
| 2026-09-25 | 本地知识维护流程调整 | 按用户授权精简入口、按任务阅读和主题单处维护；未采纳新上游版本，见[迁移记录](../docs/reports/knowledge-reorganization-2026-09-25.md) |
| 2026-10-05 | 本地评审收敛流程调整 | 按 [Issue #38](https://github.com/shenxg13/sql-apm/issues/38) 允许 R1～R3 任一轮直接 `approve`：R2 无当前阻断且已核对全部验收标准与不可豁免门禁时批准，不再强制进入 R3，停用 `pass_to_R3`；同步评论模板、校验脚本和流程回归，未采纳新上游版本，规则见[评审收敛协议](workflows/review-sync.md#review-convergence-protocol) |
