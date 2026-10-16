# v1.1.0 Kylin 与跨机器验证

依据 [Issue #67](https://github.com/shenxg13/sql-apm/issues/67)，更新
[运行环境](../decisions/runtime-and-components.md)、[日志接入](../features/log-ingestion.md)和
[命令行交付](../features/operator-cli.md)的已验证范围：二进制安装、系统包保持相同、
两集群各三天推送／收取／每日运行、sqlfind 与看板核对、部分样本 O-62-005 测量。
生产手册修正原安装终端的 psql 路径，并要求等待实际服务运行结束和检查成功后再启用定时器。

实测与验收工具的诊断过程见[目标机报告](../../docs/reports/v110-target-validation-2026-10-16.md)。
用户 PuTTY、手册和平台界面确认仍待完成；没有修改产品行为、数据库结构或 Harness 流程，没有发布或合并。
