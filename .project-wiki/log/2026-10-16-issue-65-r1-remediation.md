# Issue #65 R1 整改

按 [R1 台账](https://github.com/shenxg13/sql-apm/pull/66#issuecomment-6207513007)消除旧 Bash 的 here-string 临时文件，
并按维护者的[范围确认](https://github.com/shenxg13/sql-apm/issues/65#issuecomment-6207653317)和
[观察项采用](https://github.com/shenxg13/sql-apm/issues/65#issuecomment-6207662845)落实无效源目录连接前失败、日期汇总排序、收取错误原因。
[日志获取主题](../features/log-ingestion.md#mpp-平台推送与收取2026-10-16)保存现行要求与来源；
[报告](../../docs/reports/log-push-2026-10-16.md#r1-整改与复验)记录 Bash 5.0、无空闲 inode 的隔离 `/tmp` 复验及原模板行为差异。
