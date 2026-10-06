# 完整流程、重新构建与版本查询

业务规则见[构建与版本](../../.project-wiki/features/baseline-versions.md)，
连接、事务及成本见[设计](../design/build-publication.md)。使用 Python 3.9.5、PG17 和结构 1.9.0；
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
但仍重新封存输入配置、从头计算。两种流程按[窗口条件](../../.project-wiki/features/baseline-versions.md#按训练窗口选批2026-10-04-确认issue-35)选入完整批次，再按既有逐条规则筛选；
日批次只新增当日文件，旧明细不重复导入或复制到版本中。
失败或冲突只阻止该批次自身发布；后续 full／rebuild 只选满足窗口条件的已完成批次，窗口内缺少未完成批次不阻止发布，
当前版本也不另加缺天提示。两项均已由用户确认，见[版本规则](../../.project-wiki/features/baseline-versions.md#发布输入与补导确认2026-10-02)。

阶段顺序为 import → snapshot → build → check → publish，rebuild 从 snapshot 开始。
任一异常停止后续步骤。通过检查且有正式有效样本时切换；合法样本不足仍可发布。
五类全零时 result=no_samples，退出码 0 表示流程完成，本次未更新；查询保留原发布时间，
无旧版本为 baseline_state=no_baseline。仅阶段／调用样本可发布，查询带 request_baseline_missing=true。
配置校验、检查、发布或其他执行失败返回 1；命令行语法错误（如缺失必填参数）返回 2；捕获人工中断返回 130。

## 占用与恢复

full、import、rebuild、training snapshot、statistics 和 cleanup --execute 都按集群互斥。
忙时返回 cluster_busy 并记录 busy_rejected。不同集群可同时执行，共享规则缓存按短事务更新。
只有 import 和 full 可首次登记集群；rebuild、training snapshot、statistics 和 cleanup 对未登记集群返回
unknown_cluster，不写集群或任务记录。每次新占用获得后自动将残留任务、尝试和未发布构建记为 interrupted／owner_exited；
无手工解锁或续算命令。数据库连接失效后原进程不能重连提交，人工重新运行任务。
已经完整成功导入的文件按原去重规则复用；原失败记录保留。

进程被强制终止后，其数据库会话退出之前集群仍被占用，新任务仍返回 `cluster_busy`。
当前连接未设置存活检查参数；若终止时正在执行语句，占用会持续到该语句结束。
确认旧会话已退出后重新运行即可；没有手工解锁命令，也不自动等待、排队或重试。

在 Baseline PostgreSQL 17 上用项目数据库账号连接实际项目库，通过以下只读查询查看
指定集群的占用会话；`cluster` 替换为实际集群标识。锁键与当前任务申请使用相同算法，
这里不尝试取得锁，也不创建任务记录。

```sql
\set cluster '119'
WITH lock_key AS (
    SELECT hashtextextended(:'cluster', 1835101) AS value
)
SELECT a.pid, a.backend_start, a.state, a.query_start, a.wait_event_type, a.wait_event
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
CROSS JOIN lock_key k
WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 1
  AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND l.classid::bigint = ((k.value >> 32) & 4294967295::bigint)
  AND l.objid::bigint = (k.value & 4294967295::bigint);
```

记录该旧会话的 `pid` 和 `backend_start`，将下面两个示例值替换为实际结果后查询。
无结果表示该旧会话已退出；同时核对启动时间，避免把复用的 PID 误认为旧会话。
任务表中的 `running`／`interrupted` 状态不能替代会话退出检查。

```sql
\set old_pid 12345
\set old_backend_start '2026-10-06 10:00:00+08'
SELECT pid, backend_start, state, query_start, wait_event_type, wait_event
FROM pg_stat_activity
WHERE pid = :'old_pid'::integer
  AND backend_start = :'old_backend_start'::timestamptz;
```

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
`history` 在 #43 增加结果清理标记和时间（见下节）。这两个计数在 #34 中未改变当时的 1.6.0 结构；
当前采用 1.9.0；1.7.0／1.8.0 可带数据升级，更早非空库仍须按[初始化说明](database-initialization.md#升级到-190)重建。

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

## 窗口选批与进度

Issue #35 取代此前“选取本集群全部完整批次”的操作口径。文件的最晚实际时间不早于窗口起点，
或文件时间未知时保留整批；不设终点上界。若无候选，退回全部完整批次，照常生成零样本结果。
显式 `training snapshot --batch` 不改变。首批可按每日一批登记，避免一个多日首批长期留在窗口输入内。

`snapshot_finished` 增加 `completed_batches`、`selected_batches`、`excluded_batches` 和
`window_fallback`。前三项为完整批次总数、实际选入数、未选入数；回退时选入全部，
`excluded_batches=0`、`window_fallback=true`。窗口未选入不代表清理，旧明细仍在库中；
无该版本判定时按[展示约定](../../.project-wiki/features/sql-search-and-views.md#未选入批次的历史判定2026-10-04)解释。
同一文件若在另一选入批次中重复引用，仍属于快照输入；检查历史事件时以快照文件清单为准。

专项命令 `.venv/bin/python scripts/db/verify_window.py` 使用私有临时实例。
真实对照由 `scripts/db/verify_window_full.py` 显式选择基线／候选 checkout 与固定日志，
不纳入日常 Harness。

## 版本结果清理

[留存契约](../../.project-wiki/contracts/sql-storage.md#版本结果按月留存2026-10-06)由 #43 确认。
先预览该集群全部结果月份，再显式执行：

```bash
.venv/bin/python -m sql_apm cleanup --cluster 119 --training-config var/local/training.json
.venv/bin/python -m sql_apm cleanup --cluster 119 --training-config var/local/training.json --execute
```

同样支持 `--schema`。省略 `--execute` 只读，不占用任务、不写记录。
保留月数来自训练配置的 `retention`，默认 2；月份由数据库北京时间决定，无手工日期参数。
例如当前 2026-10、N=2 时，保留 8、9、10 月及未来月份；7 月及更早过期。
当前生效版本所在月份受保护，即使已过期也不删除。配置改小后按新值执行，不再二次确认。
建议在两个集群都没有构建时运行。

预览示例字段（标识及数值均为合成示例）：

```json
{
  "state": "preview",
  "retention_months": 2,
  "current_month": "2026-10-01",
  "current_version_month": "2026-09-01",
  "expired_months": 1,
  "months": [{
    "partition_id": 123,
    "build_month": "2026-07-01",
    "state": "expired",
    "reason": "retention_expired",
    "published_builds": 20,
    "unpublished_builds": 2,
    "statistic_bytes": 1048576,
    "observation_bytes": 65536,
    "total_bytes": 1114112,
    "cleaned_at": null,
    "groups_pending": false
  }]
}
```

月份状态为 retained／expired／protected／cleaned；构建数统计曾保存结果的构建，
发布数按是否有成功发布记录区分，同一构建不重复计数。已清理月份仍显示历史构建数。
占用字节包括两张统计叶表、索引和 TOAST，不含共享分组关联表或 WAL。
`full` 最后一行增加 `expired_result_months`，0 也显式输出；计数包含过期但受保护的月份。
它只提示，不自动删除；`rebuild` 也不清理。

执行留下一条 mode=cleanup 的任务；忙时返回 `cluster_busy`，仍记录 busy_rejected。
每个月份先尝试取得所需锁，按单调时钟给定总共 10 秒预算，超时输出 `cleanup_lock_timeout`、
该月保持原样，其他月份继续；可在占用解除后重跑。成功取得锁后的短事务同时移除两张统计
分区并标记 cleaned_at，提交后按每批最多 10,000 行删除分组关联。分组删除中断时预览显示
`groups_pending=true`，重跑会继续收尾，即使后来调大保留月数也会清完已移除统计的月份。
收尾阶段若另一个清理事务短暂持有分组表排他锁，立即放弃当前批次并返回
`cleanup_groups_pending`；此时统计已清理，不报告为“该月份保持原样”的前置锁超时。

执行输出的 months 来自 `mpp_cleanup_month`，包含每月状态、原因、构建数、before_bytes、
after_bytes、released_bytes、两种分组删除行数、起止时间、exclusive_seconds 和 group_seconds。
排他时长从成功的锁请求发起到事务提交返回测量，含往返开销，是持锁时长的上界；分组耗时
为本次运行各删除批次的语句及计数更新前耗时合计。分组行删除的空间由普通 VACUUM 复用，
不计作立即释放的磁盘字节，也不自动执行 VACUUM FULL。

退出码：成功或无可清理为 0；部分月份未完成、占用拒绝或执行失败为 1；配置／参数错误为 2；
捕获人工中断为 130。强制终止按前文的数据库会话退出规则恢复，重跑保留旧任务及其逐月记录。

`history` 继续显示已清理的版本，并增加 `results_cleaned` 和 `cleaned_at`。
`build.results_saved` 表示曾成功保存，保持原值；与月份的 cleaned_at 联合判断现有结果。
results_saved=false 表示从未保存，与 results_saved=true 且 cleaned_at 非空明确区分。
`mpp_coverage`（正式／观察）、`mpp_read_statistics` 和批量门槛接口对已清理构建报固定
`results_cleaned`，不能把它解释为没有分组或空桶。当前版本及其查询不受清理影响。

```sql
SELECT b.build_id, b.results_saved,
       b.results_saved AND p.cleaned_at IS NOT NULL AS results_cleaned,
       CASE WHEN b.results_saved THEN p.cleaned_at END AS cleaned_at
FROM build b LEFT JOIN mpp_result_partition p USING (partition_id)
WHERE b.build_id = 'BUILD_ID';

SELECT * FROM mpp_read_statistics('BUILD_ID');
SELECT * FROM mpp_read_statistics('BUILD_ID', true);
SELECT * FROM mpp_cleanup_month WHERE task_id = 'TASK_ID';
```

查询入口先持有统计父表的读锁再检查标记，防止通过检查后遇上分区移除。
直接按物理表执行的维护 SQL 须自行检查上述状态；产品按构建读取结果统一使用受保护入口。
没有恢复或删除单个构建的命令。展示界面的选择限制另行交付。
