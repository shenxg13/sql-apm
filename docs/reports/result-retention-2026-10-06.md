# 版本结果按月留存验证

契约：[Issue #43](https://github.com/shenxg13/sql-apm/issues/43)，基线 main
`f4ef32af9d70ab479d66293dd1e0fd69cf9dc714`（含 #41）。
结构为 1.9.0，逻辑契约保持 1.0.0；[实现设计](../design/postgresql-storage.md#190-版本结果留存)
与[操作入口](../runbooks/build-publication.md#版本结果清理)分别维护事务和命令约定。

## 已完成的合成验证

Python 3.9.5／PostgreSQL 17.10 私有实例；TCP 关闭，结束后自动停止并删除。
清理专用 14 项验证覆盖只读预览、当前版本保护、已发布与未发布结果、原表内容摘要不变、
查询返回 results_cleaned、六种入口互斥、两种锁占用和六阶段 SIGKILL 恢复。
两种锁占用分别在 9.922／9.923 秒返回（含任务登记与报告），数据保持原样；
分组收尾遇锁冲突报告 cleanup_groups_pending，调大保留月数后重跑仍可收尾。
发布回归 33 项、统计回归 32 项通过；项目与宿主 Python 单元测试各 72 项通过。
初次 CI 暴露日历单元测试误依赖 psycopg2，已把纯日历规则放回 baseline 层，保持离线测试无驱动依赖。
验收工具链先以两集群九个微型合成版本完成导出、恢复、升级、月份移动、清理及摘要核对自检。

1.8.0 含数据升级后各旧表内容与 receipt 不变；1.7.0 含数据和更早空库可连续升级。
发现冻结 1.5→1.6、1.6→1.7 迁移的相对 schema.sql 引用后，以临时目录绑定对应冻结
目标，旧文件保持原字节。连续空库升级的临时 catalog 锁在 savepoint 回滚后释放，
使用 PostgreSQL 默认锁表配置即可完成，不把扩大 max_locks_per_transaction 当成验收前提。

## 真实数据验收准备

本机此前验证用的临时数据库已清理。使用固定 55 文件在冻结 main 上重新构建 1.8.0
九版本数据库，再以 pg_dump／pg_restore 制作独立验收副本。日志仍位于本地忽略目录
raw/inbox/hashdata；目录沿用历史名字，不改变当前 MPP 产品称谓。
文件 SHA-256 与[固定清单](data/log-supplement-manifest-2026-09-28.json)逐个核对。

所有版本同月构建。为同时验证整月清理与当前保护，仅在副本内部将 120 当前版本的
构建时间及分区移到新的保留月份，保留九个版本身份；移动前后排除 partition_id 的
四种结果表内容摘要必须相同。119 当前月份保持原样并受保护。
测试使用内部参考月份，产品 cleanup CLI 不支持修改当前日期。

真实副本实测尚在执行；本段将在实测完成后写入结果及机器记录。

## 命令

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/db/verify.py
.venv/bin/python scripts/db/verify_publication.py
.venv/bin/python scripts/db/verify_statistics.py
.venv/bin/python scripts/db/verify_cleanup.py
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

真实数据入口（输出目录必须不存在；显式高成本验收）：

```bash
.venv/bin/python scripts/db/verify_cleanup_full.py prepare \
  --reference var/issue43/base --logs raw/inbox/hashdata \
  --output var/issue43/real-source-rerun
.venv/bin/python scripts/db/verify_cleanup_full.py verify \
  --dump var/issue43/real-source/baseline-180.dump \
  --output var/issue43/real-verification
```

本次准备阶段用临时脚本调用冻结 checkout 的 verify_publication_full.cluster：
同一清单、119 五次／120 四次任务、默认四进程、每次要求成功发布，之后导出副本。
提交的 prepare 子命令复用相同真实 CLI 顺序，供后续独立复现，不把该脚本等同生产入口。
原文、连接、数据库副本及过程日志均留在忽略目录；提交报告只保存计数、标识、时间、大小和摘要。

## 证据边界

合成故障用例证明事务、互斥与恢复语义，不代表长期生产吞吐。
统计分区字节包括索引和 TOAST；分组关联按行删除的空间供普通 VACUUM 后复用，
不把它计入立即释放字节。exclusive_seconds 是成功锁请求发起至提交返回的测量区间，
含客户端往返，是排他锁持有时间的上界。120 三十天体量仍未实测。
本任务不重跑 Kylin、不重新制包、不发布版本，也未增加自动清理或改变连接存活参数。
