# 升级与恢复改为后台执行

用户在手工验收准备时要求用 `nohup … &` 执行升级，已同步 [Issue #74](https://github.com/shenxg13/sql-apm/issues/74)
的手册要求与[确认记录](https://github.com/shenxg13/sql-apm/issues/74#issuecomment-6443649903)。
[运行环境约定](../decisions/runtime-and-components.md#v120-带数据升级2026-10-27)保存执行方式；
[升级手册](../../docs/runbooks/production-upgrade.md)补充日志、PID、退出码、重连检查及后台恢复。
原升级入口、迁移和服务控制不变；后台启动不代表升级成功，先核对退出码与结果标志。
