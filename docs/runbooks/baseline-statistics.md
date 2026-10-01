# 统计计算操作

本入口交付③：引用②封存快照创建一次构建，完整计算五层统计并保存到 PostgreSQL。
[统计规则](../../.project-wiki/contracts/baseline-statistics.md)定义公式和门槛，
[设计](../design/baseline-statistics.md)说明数值、分区、事务和恢复边界。

## 前提与调用

先按[初始化说明](database-initialization.md#升级到-160)升级到 1.6.0，完成导入，
使用[训练快照命令](training-decisions.md)得到 input_id 与 config_id。
继续使用相同 libpq 环境或 SQL_APM_DSN，不把密码写入命令或提交配置。

```bash
.venv/bin/python -m sql_apm statistics \
  --cluster example-cluster --input INPUT_ID --config-id CONFIG_ID
```

可指定 `--schema`；必须使用同集群的封存快照。未封存、跨集群、未知公式版本、
缺少相应原文缓存的输入不能完成计算。命令不执行发布检查或切换当前版本。

每次开始从数据库时钟取得 started_at，据此生成带北京时间日期前缀和随机部分的不透明 Build ID；
自动创建该集群对应月份的
正式和观察统计的当月／次月分区，无需提前按月运维。月份由构建开始时间决定，不由日志日期或训练窗口决定。
同一月份所有构建共享分区，层次与计时类别不拆表。空月无构建时不预建。

输出为 JSON 行：build_created、decisions_derived、statistics_progress、observations_written、results_written 和最终
statistics 汇总。中间 results_written 表示已完成事务内写入；只有最终 state=calculated 且
results_saved=true 表示提交成功。各层行数、计时类别计数、判定状态、计数范围和原因可核对；
五类与五层的数量不相加作为真实执行总数。输出不包含 SQL、数据库名、执行用户或驱动错误文本。

退出码：成功 0，配置／计算／保存失败 1，Ctrl-C 或 SIGTERM 130。
单组不足仍保存全部可计算指标；有排除的空样本桶保存 NULL/no_samples；完全空桶进入 empty_keys。

## 查询门槛结果

门槛不作为物理 JSONB 列重复保存。使用 Build 的封存配置，示例中的参数由调用方绑定：

```sql
SELECT s.build_id, s.group_id, s.layer, s.bucket_date, s.bucket_number,
       mpp_statistic_sufficiency(
         c.statistics_version, c.thresholds, s.layer, s.included_count,
         s.active_dates, s.active_week_starts
       ) AS sufficiency
FROM mpp_statistic s
JOIN build b USING (build_id)
JOIN config_snapshot c USING (config_id)
WHERE s.build_id = $1 AND s.group_id = $2;
```

在选定项目 schema 的 search_path 下执行；不要用当前默认配置替换 c.thresholds 或版本。
返回三项完整 ThresholdResult；未知公式版本明确报 unsupported_statistics_version。
结果是样本条件标记，是否能用于异常判断仍受统计契约限制。

全构建聚合使用[批量规范 SQL](../../sql_apm/storage/statistics_sufficiency.sql)。每次只绑定一个
`build_id`；查询从该 Build 一次取封存配置，返回 partition_id、build_id、group_id、layer、
bucket_date、bucket_number 和 basic_met／p95_met／p99_met。在项目 schema 的 search_path 下，
例如已有 psycopg2 游标 `cur` 和所选 `build_id` 时：

```python
from pathlib import Path

batch_sql = Path("sql_apm/storage/statistics_sufficiency.sql").read_text()
cur.execute("""
    SELECT layer, count(*), count(*) FILTER (WHERE basic_met),
           count(*) FILTER (WHERE p95_met), count(*) FILTER (WHERE p99_met)
    FROM (""" + batch_sql + """) result GROUP BY layer ORDER BY layer
""", {"build_id": build_id})
distribution = cur.fetchall()
```

在仓库根目录运行该示例；SQL 文本来自仓库固定资源，Build ID 始终通过驱动绑定。
批量路径只返回三个达标标记；明细及不足原因仍用上面的完整函数。
普通批量汇总无需先取回所有统计行到 Python。不存在的 Build 返回空集；空集本身不代表构建成功。

成本测量命令只创建合成数据与私有 PG17，不连接现有服务；输出文件必须不存在：

```bash
.venv/bin/python scripts/db/benchmark_statistics_sufficiency.py \
  --output var/statistics-query-cost.json
```

默认 60,000 组、五次交替重复测量，测前预热；逐行核对批量标记与完整函数，
并测量 2,000 组明细查询。准备数据、并发、分区遍历不计入查询计时。
原 `verify_statistics_full.py` 的聚合继续使用完整函数作为验收参考，成本高于这里的批量路径。
本机热缓存 measured：834,999 行聚合，完整函数五次中位数 9,116.744 ms，
批量路径 240.039 ms（约 38 倍）；单组完整结果查询的每轮均值中位数 0.349 ms。
按相同行成本线性 modeled 至 120 的 5,850,925 行，分别约 63.9 秒与 1.68 秒；
这些外推不包含生产 I/O、并发或数据分布变化。
实测和外推边界见[R2 整改报告](../reports/baseline-statistics-r2-remediation-2026-10-01.md)。

## 失败与重新计算

失败／中断的计算事务回滚；已登记 Build 与原因保留，results_saved=false。
主进程正常捕获错误记录固定码；异常退出时监护进程使用独立连接记录 worker_disconnected。
同机 SIGTERM／SIGKILL 路径有合成验收。整机或数据库不可用时，不能即时更新状态，
遗留 running／false 也不能解释为完整结果；整体任务恢复和忙时管理见[完整流程操作](build-publication.md)。

```bash
.venv/bin/python -m sql_apm statistics \
  --cluster example-cluster --input INPUT_ID --config-id CONFIG_ID \
  --retry-of FAILED_BUILD_ID
```

retry_of 仅接受同集群、同一对快照的 failed／interrupted 构建；新尝试重新计算全部窗口，
不续写失败半成品。修改输入或配置时先另建快照，以新快照发起计算。
这只是一次统计尝试的引用参数；完整流程、重新构建、同集群串行和发布见[操作说明](build-publication.md)。

## 验证与成本

合成检查自动创建私有 PG17，结束后停止并清理实例：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/db/verify.py
.venv/bin/python scripts/db/verify_statistics.py
```

真实验收是显式高成本操作：重新导入固定清单全部 55 文件，每集群创建快照并计算两次，
核对所有组的五层守恒、覆盖索引、与判定的计数／原因一致，并以固定种子抽样至少 1,000 组
独立复算全部指标。包含最大组、单样本组和含排除组；不设耗时／内存通过门槛。

```bash
.venv/bin/python scripts/db/verify_statistics_full.py \
  --root raw/inbox/hashdata --output var/statistics/new-acceptance
```

输出目录必须新建；原始数据、配置、数据库和中间结果不提交。
`--dsn` 仅供验收维护者连接已重导的私有临时实例进行后半段验证，不是产品导入或计算入口。
每表和每分区精确行数／总大小记录在报告；分区父表大小为所有叶分区总和，不能再与叶表相加。
内存按一秒采样当前 Python 进程树与私有 PostgreSQL 进程树的 PSS，记录各自及合计峰值；
它是有采样间隔的 measured 值，不含导入及快照准备阶段峰值，不等同于进程 RSS 之和。构建耗时及内存也不包含随后验收查询和复算的成本。

范围变更前的完整实测与验证边界见[历史统计验证报告](../reports/baseline-statistics-2026-09-30.md)。

范围变更后的完整重导、SQL 门槛复算及新容量见[整改验收报告](../reports/baseline-statistics-2026-10-01.md)。

## 观察统计与全量验收

每次 `statistics` 同时保存独立观察结果，最终 JSON 的 `observations` 包含 observation_only=true、
观察组数、五层行数、候选 SQL 状态数量（不可靠、缺失或指纹失败记录）、去重执行的归属数量、无法归组原因及各近似规则的
样本／排除／原因计数。SQL 可靠性原因表示进入观察的依据，不是观察排除原因。
不同规则可能引用同一执行，各规则计数不能相加解释为独立执行总量。
无 SQL、近似不可用、身份／推算开始时间未知仅出现在构建诊断，窗口外不进入统计。

结果在 `mpp_observation_statistic`，同一 Build 下查询；没有充足性结论。
不对该表调用门槛函数，正式批量门槛查询不读取观察表。没有事件的桶由窗口和已有行推导。
观察失败会使本次全部统计回滚，CLI 返回固定失败码；retry-of 同样从头计算两类结果。

```bash
.venv/bin/python scripts/db/verify_observations.py
.venv/bin/python scripts/db/verify_observations_full.py \
  --root raw/inbox/hashdata --output var/observation-acceptance/new-run
```

第二条显式重新导入 55 文件，在私有 PG17 以 119 截止 2026-07-31、120 截止 2026-09-19
各构建并重复。沿用 #25 的固定验收排除时段；复算全部观察组的每个桶和 17 指标。
用冻结 main 基线 `f038932fe0d5453450b91a59d5239c95bacb0078` 的统计实现生成正式对照，
通过数据库逐行比较确认不变；该 Git 对象须在本地存在。只复用正式计算类，使用当前连接和 1.6.0 结构；只移除冻结计算器的旧覆盖物理写入，保留原计算。
`--dsn` 仅供已重导私有实例的分阶段验收；输出目录、原文、实例和中间结果保持本地忽略。

计算用时、观察阶段用时及进程树 PSS 峰值分别记录；基线与新构建按顺序运行，受缓存和系统
负载影响，用时／峰值差值不能归因于观察代码本身。观察阶段峰值包含现存 PostgreSQL 缓存，
不代表纯新增分配。表和分区实测单独列出，父表大小已含叶表，避免重复相加。

本次完整数据、近似可用性差异与测量边界见[观察统计验证报告](../reports/observation-statistics-2026-10-01.md)。

## 覆盖推导

1.6.0 不再保存 mpp_build_coverage；以 `mpp_coverage(build_id, false, group_id)` 查询正式覆盖，
第二参数 true 查询观察覆盖，第三参数 NULL 返回全部组。构建层计数在
`mpp_build_layer_count`，用于核对结果是否丢行。日常查询优先限定单组，避免展开整个构建。
