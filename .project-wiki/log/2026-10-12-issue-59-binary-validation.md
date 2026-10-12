# v1.0.0 初始系统二进制验证

按 [Issue #59](https://github.com/shenxg13/sql-apm/issues/59) 的第二轮要求，在用户恢复的初始 Kylin 上
复用第一轮唯一运行时成品。实测及原始证据边界见[二进制验证报告](../../docs/reports/v100-binary-validation-2026-10-12.md)。

[运行环境主题](../decisions/runtime-and-components.md#首个生产版本与二进制部署2026-10-12)补记二进制安装、
系统包及业务结果；[命令行主题](../features/operator-cli.md#生产安装账号与交互环境2026-10-12)补记账号验收。
第二轮完整流程、真实重启和备份核验均已完成，第三轮用户亲自安装和明确确认仍待完成。
记录宿主机休眠的耗时边界和私有辅助工具的修正，保留原始证据，没有修改业务或 Harness 规则。
