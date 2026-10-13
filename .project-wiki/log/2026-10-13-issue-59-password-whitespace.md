# Issue #59 密码首尾空白整改

[R1 阻断 F-59-60-R1-001](https://github.com/shenxg13/sql-apm/pull/60#issuecomment-6146826478)
指出 Grafana 文件 provider 去除了合法密码首尾空白。按原契约修复启动与密码消费链，
同步生产／演练手册和[账号操作主题](../features/operator-cli.md#生产安装账号与交互环境2026-10-12)。
加入真实 Grafana／私有 PG17 的安装与轮换回归；目标机结果及候选身份由 PR 整改记录归档。
密码范围、上游最短长度、业务数据行为、唯一运行时和 Harness 流程不变。
