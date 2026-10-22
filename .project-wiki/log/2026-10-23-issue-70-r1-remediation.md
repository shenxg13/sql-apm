# Issue #70 R1 整改

依据 [R1 台账](https://github.com/shenxg13/sql-apm/pull/71#issuecomment-6345466034)
及[维护者采纳记录](https://github.com/shenxg13/sql-apm/issues/70#issuecomment-6345776755)，
在[运行时主题](../decisions/runtime-and-components.md#生产使用性能检查2026-10-22)记录名称冲突预检、
选择阶段独立超时、终端与包首概要、普通计划预算及父目录准备要求。
公共元数据白名单不变；O-70-003 未采纳，表达式仍整体脱敏。
实现见[设计](../../docs/design/performance-toolkit.md)，执行细节见[手册](../../docs/runbooks/performance-check.md)。
