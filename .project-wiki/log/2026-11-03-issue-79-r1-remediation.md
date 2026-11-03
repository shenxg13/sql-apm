# v1.3.0 R1 整改

依据 [R1 台账](https://github.com/shenxg13/sql-apm/pull/80#issuecomment-6604700984)及
[O-79-001 纳入记录](https://github.com/shenxg13/sql-apm/issues/79#issuecomment-6604701575)，更新
[运行环境主题](../decisions/runtime-and-components.md#v130-带数据升级2026-11-03)：
工具包按数据库调用位置排除内部 SQL，保留同名公开字段检查，并按两处 cases 身份比较决定是否继承完整结果。
安装手册明确正式 v1.2.0 升级入口；其余随包手册同步当前版本及历史说明，操作步骤不变。
具体复测与制包证据由[升级验证报告](../../docs/reports/v130-upgrade-2026-11-03.md)及 PR 整改记录维护。
