# 完整流程、重新构建与版本查询

业务规则见[构建与版本](../../.project-wiki/features/baseline-versions.md)，
连接、事务及成本见[设计](../design/build-publication.md)。使用 Python 3.9.5、PG17 和结构 1.7.0；
连接通过既有 `SQL_APM_DSN` 提供，不把凭据写进命令参数或报告。

## 显式运行

来源、文件齐全声明和批次 JSON 沿用[导入说明](log-ingestion.md)。
训练 JSON 沿用[训练配置](training-decisions.md)，完整流程可省略 window.cutoff_date，
窗口天数默认 30。命令参数覆盖该次本地配置，不改写文件。

```bash
.venv/bin/python -m sql_apm full \
  --config var/local/import.json --source source119 --batch daily119 \
  --training-config var/local/training.json --workers 4

.venv/bin/python -m sql_apm rebuild --cluster 119 \
  --training-config var/local/training.json --cutoff-date 2026-07-31 --days 30

.venv/bin/python -m sql_apm status --cluster 119 --limit 20
.venv/bin/python -m sql_apm history --cluster 119 --limit 20
```

full 默认截止日为本批声明日期的最大值；可用 `--cutoff-date YYYY-MM-DD` 覆盖。
对较早批次执行 full 会把当前版本窗口移回该批日期；补导历史日批且希望保持当前窗口时，须显式传入截止日。
rebuild 必须给出该参数，可用 `--retry-of BUILD_ID` 引用同集群失败／中断构建，
但仍重新封存输入配置、从头计算。两种流程选取本集群全部完整批次，在固定窗口内按原判定筛选；
日批次只新增当日文件，旧明细不重复导入或复制到版本中。
失败或冲突只阻止该批次自身发布；后续 full／rebuild 仍只选已完成批次，窗口内缺少未完成批次不阻止发布，
当前版本也不另加缺天提示。两项均已由用户确认，见[版本规则](../../.project-wiki/features/baseline-versions.md#发布输入与补导确认2026-10-02)。

阶段顺序为 import → snapshot → build → check → publish，rebuild 从 snapshot 开始。
任一异常停止后续步骤。通过检查且有正式有效样本时切换；合法样本不足仍可发布。
五类全零时 result=no_samples，退出码 0 表示流程完成，本次未更新；查询保留原发布时间，
无旧版本为 baseline_state=no_baseline。仅阶段／调用样本可发布，查询带 request_baseline_missing=true。
配置校验、检查、发布或其他执行失败返回 1；命令行语法错误（如缺失必填参数）返回 2；捕获人工中断返回 130。

## 占用与恢复

full、import、rebuild、training snapshot、statistics 都按集群互斥。
忙时返回 cluster_busy 并记录 busy_rejected。不同集群可同时执行，共享规则缓存按短事务更新。
只有 import 和 full 可首次登记集群；rebuild、training snapshot 和 statistics 对未登记集群返回
unknown_cluster，不写集群或任务记录。每次新占用获得后自动将残留任务、尝试和未发布构建记为 interrupted／owner_exited；
无手工解锁或续算命令。数据库连接失效后原进程不能重连提交，人工重新运行任务。
已经完整成功导入的文件按原去重规则复用；原失败记录保留。

status 返回当前版本、最近任务（包括阶段、时间、产物、原因）、最近一次未发布原因。
未发布原因也包含完整流程／重建在导入或计算等前置阶段的失败，按实际时间与发布决定比较。
history 只列成功发布的历史版本，按发布时间倒序；两者限量 1–1000，默认 20。
输出不包含 SQL 原文、源数据库名或执行用户名。查询不获取任务锁。

### 隔离记录计数

`status.current` 中始终返回两个整数（没有该类问题时为 0）：

| 字段 | 单位与含义 |
| --- | --- |
| `fingerprint_normalization_timeout` | 当前生效版本固定输入中的归一化超时隔离日志记录数。 |
| `fingerprint_normalization_worker_failed` | 同一输入范围内解析进程异常隔离日志记录数。 |

计数来自版本输入文件关联的 `problem` 记录，不是不同 SQL 数、执行次数或全库累计问题数；
同一 SQL 在多条日志中失败会分别计数。后来导入的文件在成为新版本输入前不影响当前计数。
仅展示，不增加发布检查或阻止发布；没有生效版本时 `current=null`。
`history` 的历史版本输出保持原格式。这两个计数在 #34 中未改变当时的 1.6.0 结构；
当前 MPP 标识版采用 1.7.0，已有数据的旧库按[初始化说明](database-initialization.md#升级到-170)重建。

## 覆盖与分区

```sql
SELECT * FROM mpp_coverage('BUILD_ID', false, 'GROUP_ID');
SELECT * FROM mpp_coverage('BUILD_ID', true, 'OBSERVATION_GROUP_ID');
SELECT kind, layer, row_count, group_count
FROM mpp_build_layer_count WHERE build_id='BUILD_ID';
```

第三参数为空时推导整个构建，可能输出大量分组；日常诊断优先限定分组。
computed_keys 对应统计中存在的桶，其余窗口键为 empty_keys；不用覆盖表持久保存。
正式层计数在发布前核对，观察层计数只作诊断。

构建前确保本集群当月正式／观察分区存在，并尝试预建缺失的次月分区；也可在明确连接中提前执行
`SELECT mpp_ensure_result_partition('119', DATE '2026-11-01');`。
独立空表 ATTACH 避免父表 ACCESS EXCLUSIVE 锁阻塞普通读取。建外键仍需要锁共享的
mpp_build_group／mpp_build_observation_group；次月可选预建通过 NOWAIT 试锁，遇到其他集群
的结果事务时跳过，后续构建再尝试，不让本次构建等待该事务。
集群首次建立当月分区、或次月始终未能预建后跨月首次使用时，必须等对方构建事务结束；
其时长由对方事务决定，没有固定秒数保证。可在无构建时手动预建以避开这次等待。
其他 DDL 仍可能互相等待；普通读取可继续。版本保留和清理另行设计。

## 专项与真实验收

```bash
.venv/bin/python scripts/db/verify_publication.py
.venv/bin/python scripts/db/verify.py
.venv/bin/python scripts/db/verify_publication_full.py \
  --root raw/inbox/mpp --output var/publication-fresh
```

前两个命令创建并自动清理私有 PG17，合成用例涵盖故障注入、真实进程强杀、连接失效、
忙时拒绝、跨集群与读取并发。全量命令显式重导固定清单的 55 文件，输出目录必须不存在，
两个集群各执行首批 full 加三个日批 full，119 再执行 rebuild；不会访问已有数据库。
每任务报告阶段时间、命令进程树 PSS 峰值和结果分区净增长；并发期间 PG 进程树与数据库
总量由两个集群共享，不能把各任务峰值相加解释为机器总峰值。
最终用同快照的独立指标 oracle、全组观察 oracle 和冻结计算器逐行对照核验。
120 本地只有七天，生产首批三十天成本未实测。

`verify_coverage_migration_full.py` 仅用于已显式准备的私有 1.5.0 验收实例：先逐行比较
全部正式覆盖，再迁移，核对正式／观察统计摘要及重跑。`--legacy` 指定本地验收描述，
含私有连接、固定基线提交和两个构建标识；该文件及原始输入必须留在忽略目录。
它不接管生产服务，不替代常规初始化命令。

描述文件必需字段为 `dsn`、`baseline_sha` 和 `builds`，后者按集群保存
`result.build_id`。先用冻结旧版在私有实例完成导入和统计，保持实例存活，再执行：

```bash
.venv/bin/python scripts/db/verify_coverage_migration_full.py \
  --legacy var/legacy-validation/ready.json --output var/coverage-migration.json
```

该命令检查连接确实指向 `/tmp/sql-apm-pg-*/socket` 且版本为 1.5.0，
先比较覆盖，再升级并核对统计；输出文件须不存在。旧实例拥有者负责最后停止和清理。
