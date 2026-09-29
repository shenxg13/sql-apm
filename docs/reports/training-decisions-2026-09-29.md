# 训练样本判定验证

本报告对应 [Issue #21](https://github.com/shenxg13/sql-apm/issues/21)，
实现设计与边界见[判定设计](../design/training-decisions.md)。

## 验证范围

合成用例、私有 PostgreSQL 17 结构迁移及 55 个本地日志文件重新导入后的判定汇总。
真实输入的 SHA-256 依据沿用[55 文件清单](data/log-supplement-manifest-2026-09-28.json)，
原始日志、原文、数据库名、用户名及私有配置均不提交。统计、构建发布和生产部署不属于本次验证。

全量验收进行中，最终实测数据在完成后补入；本占位不代表 D7 已通过。
