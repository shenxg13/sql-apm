# MPP 来源标识统一验收（2026-10-05）

本报告对应 [Issue #33](https://github.com/shenxg13/sql-apm/issues/33)。
改名前基线为 `7ebd1256335b6659477ca4c04f37139b7ec4c7b8`，包含 #31、#34；
本次改名不调整归一化算法、计时、训练、统计或发布规则。
当前报告只记录已经取得的证据；55 文件完整重放及 Kylin 验收尚在进行或等待环境信息。

## 实现与迁移

系统组成、新旧标识及确认来源集中见[系统称谓](../../.project-wiki/decisions/project-scope.md#已确认的生产系统称谓)。
源码适配包为 `ingestion/mpp/`，默认字典为 1.0.2，当前结构 1.7.0，逻辑契约 1.0.0。
旧库除版本登记外有业务行即以 `mpp_naming_requires_empty_schema` 拒绝迁移，不做 ID 换算。
这避免把旧 profile 产生的指纹当成新 profile 的结果。

1.6.0 DDL SHA-256 为 `9a9f0b57e40a13cd524cd8107a58da326615be9bbe849f690b3bf989ae632a1f`。
历史 DDL／迁移、两个旧字典、既有报告及原始资料经逐文件 SHA-256 核对保持原字节；
知识日志只追加新条目。历史迁移回归由冻结的 1.6.0 入口继续执行；当前入口另测
新建、空 1.6.0 升级、空 1.0.0 连续升级、带数据拒绝与全部同版本重跑模式。

## 已完成的有界验证

| 检查 | 实测结果 |
| --- | --- |
| 普通测试 | 69 项通过，包含旧 profile 拒绝、字典内容相同及两个旧文件摘要 |
| 解析专项 | 202 项通过 |
| 私有 PG17 结构／历史迁移／新迁移 | 通过，实例自动停止并清理 |
| 导入专项 | 125 项通过 |
| 训练专项 | 300 项通过 |
| 发布专项 | 33 项通过 |
| 新标识／比较器合成验证 | 通过，正式和观察统计的值变化均被检测 |
| 制包及 HTML 回归 | 17 项通过，包含新旧文件路径差异 |

## 全量等价比较方法

以下为实际执行方案；完成结果在本节补记，不将运行中视为通过。
每个代码版本使用独立全新 PG17 私有实例；按固定 manifest 验证 55 个文件摘要，
119 和 120 各一个完整批次，30 天窗口，截止日分别为 2026-07-31 和 2026-09-19。
来源声明、文件清单、训练配置相同，均使用四解析进程。

稳定执行键为文件 SHA-256、记录号、起止行号与执行单位。每组以最小稳定执行键对应，
核对完整的有序成员序列；再按对应组、层和桶读取全部统计列，仅排除 partition_id、
build_id、group_id，保留所有计数、指标、空值原因、时间范围和活跃日期。
完整有序流采用 SHA-256 记录；比较不使用原 SQL、数据库名、执行用户名、随机 ID 或生成时间。
另逐项比较记录／原文／指纹状态／近似结果／问题计数、六项检查及发布结论。

```bash
.venv/bin/python scripts/db/verify_mpp_naming_full.py \
  --app-root var/issue33/reference --logs raw/inbox/hashdata \
  --output var/issue33/baseline-run
.venv/bin/python scripts/db/verify_mpp_naming_full.py \
  --app-root var/issue33/candidate --logs raw/inbox/hashdata \
  --output var/issue33/candidate-run
.venv/bin/python scripts/db/verify_mpp_naming_full.py --compare \
  var/issue33/baseline-run/report.json var/issue33/candidate-run/report.json
```

这里显式使用尚未改名的本地证据目录；程序默认目录已经改为 `raw/inbox/mpp/`。
两次运行的数据库、配置及明细日志只保留本地忽略目录；报告只提交计数、标识、摘要、耗时与原因码。
本次不以历史报告代替新版本实测。

## Kylin 与交付边界

待取得验证机 SSH 目标后，按[手册第 10.2 节](../runbooks/kylin-offline-deployment.md#102-mpp-标识版验收)
复用已安装依赖、重建验证库并运行至少一个集群首批；记录新标识与实测耗时。
本 Issue 不包含打标签、发布 Release、重新安装系统依赖或重跑原九任务。
