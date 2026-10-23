# Issue #70 R2：公开运行文本的候选冲突

依据 [R2 台账 F-70-003](https://github.com/shenxg13/sql-apm/pull/71#issuecomment-6355458999)，
自动候选预检补充时间戳、主机信息、I/O 分类、产品源码位置和看板标识；候选回退与打包扫描规则保持原有语义。
条款归入[运行环境与组件](../decisions/runtime-and-components.md#生产使用性能检查2026-10-22)，
具体来源见[设计](../../docs/design/performance-toolkit.md)，取用与证据见[报告](../../docs/reports/performance-toolkit-2026-10-22.md)。
本轮验证机不可用，目标机补测缺口交 R3 判断；未纳入的观察项不随整改扩展。
