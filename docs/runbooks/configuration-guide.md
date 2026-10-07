# 配置指南

本章针对 v0.2.0。先完成安装手册的九任务并保存记录，再运行本章的演练示例。
训练配置使用 JSON；修改文件不会自动启动任务。每个示例从原配置生成单独文件，
只改该主题，避免前一个示例影响后一个。真实模板写在受保护的本地文件，不能分享原文。

## 配置放在哪里

| 想改什么 | 入口 | 何时生效 |
| --- | --- | --- |
| 窗口、样本门槛、模板黑名单、排除时段 | `--training-config` 指定的 JSON | 下一次 full／rebuild 的新快照和新构建 |
| 结果保留月数 | 同一 JSON 的 `retention` | 下一次 cleanup；不改已经封存的训练配置 |
| 解析并发 | full／import 的 `--workers` | 本次尚未成功导入的文件 |
| 集群、来源声明、批次完整清单 | `--config` 指定的导入 JSON | 本次导入 |
| 数据库连接 | `SQL_APM_DSN`、`PGPASSFILE` | 下一条命令 |

下面的检查清单是配置键的机器可读目录。`{cluster}`、`{source}`、`{batch}` 是调用者的标识，
`[]` 表示列表成员，`{layer}` 为五层之一。训练配置拒绝未知键；导入配置目前允许额外键，
只消费所列键（来源对象的额外键也会参与来源声明摘要），额外键不是新增功能，不能依靠拼写错误生效。
完整键集合与这一宽松行为均由开发机检查核对；本版不修改校验规则。

<!-- configuration-keys -->
```json
{
  "training": {
    "root": ["version", "clusters", "window", "templates", "exclusions", "thresholds", "retention"],
    "window": ["cutoff_date", "days"],
    "templates[]": ["id", "sql", "cluster", "database", "execution_user", "description"],
    "exclusions[]": ["id", "cluster", "start", "end", "reason"],
    "thresholds": ["overall", "day", "week", "weekday", "hour"],
    "thresholds.{layer}": ["basic_count", "p95_count", "p99_count", "coverage_min"],
    "retention": ["months", "clusters"],
    "retention.clusters": ["{cluster}"]
  },
  "import": {
    "root": ["version", "clusters", "sources", "batches"],
    "sources.{source}": ["cluster", "build", "timezone", "declaration"],
    "batches.{batch}": ["source", "files_confirmed_complete", "dates", "files"],
    "batches.{batch}.files[]": ["path", "origin_key", "closed_and_copied"],
    "extra_keys": "accepted"
  }
}
```

## 想缩短或延长训练窗口

含义：按北京时间的推算开始时间选执行，截止日也包含在内。默认 30 天；`days` 必须是
正整数，`cutoff_date` 是规范 `YYYY-MM-DD` 日期。full 默认取当前批次声明的最大日期；
rebuild 必须传 `--cutoff-date`。CLI 的 `--days`、`--cutoff-date` 覆盖 JSON 中的相应值。
窗口为截止日减 days−1 的 00:00 到截止日次日 00:00，右边界不包含。

修改后需要 full／rebuild，成功发布才切换当前版本，既有版本不变。某完整批次只要有一个最终成功／重复跳过的文件，其 last_log_at 为空或不早于窗口起点，
就整批入选；不使用窗口右边界提前剔除批次。事件仍按推算开始时间过滤。
有已完成批次但筛选后一个也没有时，回退到全部已完成批次（window_fallback=true）。

<!-- example:window training -->
```json
{"version":1,"clusters":["119"],"window":{"cutoff_date":"2026-07-31","days":2}}
```

九任务后的可复制示例（生成这个主题的配置并调用真实 rebuild）：

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" run window
```

确认：本轮 119 为四个已完成批次，窗口缩至两天后 `excluded_batches > 0`；核对
snapshot_finished 进度行中的 `completed_batches`、`selected_batches`、`excluded_batches`、`window_fallback`。
排除的是快照输入，原文件、执行和旧版本仍在。

## 想改变“样本不足”的门槛

门槛只影响查询时的充足性判断，不改变统计值、不丢掉样本不足的组。默认值如下。
所有可改门槛为非负整数（不接受布尔值）；没有强制 basic ≤ p95 ≤ p99。

| 层 | basic_count | p95_count | p99_count | coverage_kind（固定） | coverage_min |
| --- | ---: | ---: | ---: | --- | ---: |
| overall | 30 | 200 | 1000 | active_days | 7 |
| day | 30 | 200 | 1000 | none | 0（只能为 0） |
| week | 30 | 200 | 1000 | active_days | 3 |
| weekday | 30 | 200 | 1000 | active_weeks | 4 |
| hour | 30 | 200 | 1000 | active_days | 7 |

省略层或键沿用默认值；`coverage_kind` 不能填写为配置键。门槛封存在新构建配置中，
因此需要 rebuild；旧版本仍用各自的门槛。以下只提高 overall 的 basic_count。

<!-- example:threshold training -->
```json
{"version":1,"clusters":["119"],"window":{"cutoff_date":"2026-07-31"},"thresholds":{"overall":{"basic_count":1000000000}}}
```

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" run threshold
```

确认：与九任务的 119 最后一版相比，全部正式／观察统计值逐行摘要相同，只有样本不足
标记的数量变化。辅助工具通过 `mpp_statistic_sufficiency` 查询已封存门槛并保存计数。

## 想排除某类 SQL

系统自带七个语句类别的筛选规则，默认额外模板 `templates=[]`。操作者不能增减内置
类别枚举；可以加 SQL 模板。模板按可靠归一化指纹匹配，不是关键词、正则或整类开关。
一条原文的参数变体可能同指纹；多语句原文按整体指纹匹配。

每条必须有非空 `id`、`sql`、`description`，可选非空 `cluster`、`database`、`execution_user`
缩小适用范围；省略即不限制该维度。cluster 必须已登记，id 在模板和排除时段中不能重复。
SQL 必须能可靠归一化；JSON 校验通过不等于 SQL 解析通过。修改后 rebuild，旧版本不变。

下面是人工 SQL 的合法配置示例。目标机演练工具会只读选取 119 原基线中一个实际纳入的
分组代表原文，写入私有配置并重新构建；公开记录只保留模板指纹和排除计数。

<!-- example:template training -->
```json
{"version":1,"clusters":["119"],"window":{"cutoff_date":"2026-07-31"},"templates":[{"id":"guide-template","sql":"SELECT 1","cluster":"119","description":"演练模板"}]}
```

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" run template
```

确认：构建诊断出现模板黑名单排除计数，所选模板对应的执行不再纳入，其他规则不变。
模板原文及数据库／执行用户条件只能在受保护环境中查看。

## 想排除某个时段

默认 `exclusions=[]`。每条必须且只能有 `id`、`cluster`、`start`、`end`、`reason`；
字符串非空，集群已登记，id 与模板共用唯一命名空间。时间必须显式带 `+08:00`，
start < end；正耗时执行按 [推算开始时间, 结束时间) 与排除区间的交集判断；零耗时按开始时间是否落入
左闭右开区间判断。多个命中原因保留，不重复计算执行。

修改后 rebuild，旧版本不变。下面排除 119 的最后一天，不更改训练窗口。

<!-- example:exclusion training -->
```json
{"version":1,"clusters":["119"],"window":{"cutoff_date":"2026-07-31"},"exclusions":[{"id":"guide-period","cluster":"119","start":"2026-07-31T00:00:00+08:00","end":"2026-08-01T00:00:00+08:00","reason":"演练排除时段"}]}
```

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" run exclusion
```

确认：该时段内的执行转为排除并有原因计数，窗口和其他规则保持原值。

## 想改变结果保留月数

默认保留 **当月和之前 2 个整月**，按数据库时钟的北京时间及构建月份判断。
`retention.months` 为全局默认，可用 `retention.clusters` 对已登记集群覆盖；均为 ≥1 的整数，
不接受布尔值。更晚月份保留，当前生效版本的整个月份受保护。

此配置不进入封存的训练配置，不需要 rebuild；修改后只影响下一次 cleanup 的计划。
full／rebuild 不自动清理；full 的 `expired_result_months` 是提示，包含过期但受保护的月份。
执行只删除正式／观察统计月分区和构建分组关联，不删原文、执行明细、分组定义、快照、
构建和发布记录。已删除结果不可恢复，history 保留版本和清理时间。

<!-- example:retention training -->
```json
{"version":1,"clusters":["119","120"],"window":{"cutoff_date":"2026-07-31"},"retention":{"months":12,"clusters":{"120":12}}}
```

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" run retention
```

这个示例只预览，确认输出 `retention_months=12`。真正删除必须在核对预览后显式执行
`python -m sql_apm cleanup --cluster 119 --training-config FILE --execute`。
整轮最后的模拟日期演练还要分别验证受保护、保留、过期和清理完成，按安装手册执行。

## 想改变解析并发

full／import 的 `--workers` 默认 4，合法范围为整数 1–8；rebuild 不读取日志、不接收此参数。
本版九任务使用默认 4。本次证据不保证其他并发数或其他主机的性能。
参数影响本次新文件解析，不改变 5 秒归一化期限，也不会重新解析已成功文件或修补其旧超时。

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" run workers
```

开发机示例对已完成的 119 最后一批调用 import，显式传 `--workers 4`，预期重复跳过，
不产生新版本。它验证参数和重复导入行为；四进程真实解析已由全新九任务验证。
目标机不单独执行此例，避免把重复导入审计混入九任务。

## 想改变导入来源与完整性声明

没有自动发现集群、来源和文件的默认值。`version=1`，`clusters` 为唯一的非空字符串列表；
每个来源以键名标识并指定已登记集群、固定 build `HashData Warehouse 3.13.13`、
固定 timezone `UTC+08:00` 及非空人工声明。这是来源事实，不能仅因想“通过检查”而填写。

批次以键名标识，指定 source、唯一且非空的规范日期列表、非空文件列表；
`files_confirmed_complete` 和每个文件的 `closed_and_copied` 必须是 JSON 的 true。
path 非空，相对路径按配置文件目录解释；解析到同一路径的重复文件被拒绝。
可选 origin_key 为非空原来源标识，默认省略。文件必须已关闭、复制完成且清单完整。

<!-- example:import import -->
```json
{"version":1,"clusters":["119"],"sources":{"guide-source":{"cluster":"119","build":"HashData Warehouse 3.13.13","timezone":"UTC+08:00","declaration":"人工演练输入"}},"batches":{"guide-batch":{"source":"guide-source","dates":["2026-07-31"],"files_confirmed_complete":true,"files":[{"path":"sample.csv","origin_key":"guide-file","closed_and_copied":true}]}}}
```

修改仅影响新导入；同来源同文件内容按摘要去重，不能通过改路径强制重新解析。
对已登记声明或已有批次做冲突更改会失败，先保存输出并核对真实来源。
单独 import 不构建／发布；之后 full／rebuild 才生成使用新数据的版本。

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" run import
```

开发机示例复制现有 120 来源与完整清单，调用最后一批 import 并检查重复跳过及文件计数，
不编造新的生产来源声明。合成 JSON 的真实 CSV 导入另由配置指南检查入口在私有实例执行。
确认输出批次完成、文件清单和重复跳过数一致；目标机不单独执行此例。

## 连接、路径与其他操作参数

连接凭据放程序目录外 0600 的 PGPASSFILE，`SQL_APM_DSN` 指定 host、port、dbname、user。
DSN 留空时由 libpq 使用默认连接参数，本手册始终显式填写；不把密码写进 JSON、命令行或报告。目标实例、监听地址、
允许客户端网段、端口、Python／PG 安装路径和日志目录，统一按安装手册第 1、7、8 节设置。
`--schema` 默认 `sql_apm`，初始化必须使用同一个 schema。
初始化的数据库、schema 和项目角色名须匹配 `[a-z][a-z0-9_]{0,62}`，不能以 `pg_` 开头，
也不能取 `postgres`、`template0`、`template1`、`public`、`information_schema`；端口为 1–65535 的整数。
手册固定 shared_buffers、work_mem、maintenance_work_mem、WAL 设置是本次演练配置，
不是新增产品开关；变更这些 PG 参数按 PostgreSQL 生效方式处理，本轮不做调优实验。

status／history 的 `--limit` 默认 20，范围 1–1000，仅改变返回历史长度，不改版本。
rebuild 的 `--retry-of` 指向同集群失败／中断构建，建立重试关系；没有自动重试或默认指向。
`statistics` 可通过封存 input／config 标识计算独立构建，不自动发布；普通操作者用 full／rebuild。
`training snapshot` 要求显式 `--batch`，不执行 full／rebuild 的自动选批，诊断时保留完整命令。
不要把这些诊断参数写入训练 JSON。

## 不可配置的内容

- 五项正式分组：集群、数据库、执行用户、可靠结构指纹、计时类型；五类计时也是固定的。
- 七个内置语句类别：SET、BEGIN、COMMIT、VACUUM、ANALYZE、CREATE INDEX、ALTER TABLE；
  以及它们的组合判定，不能把任意关键词加入类别枚举。
- 5 秒归一化期限、函数规则版本和算法标识不能通过训练 JSON 改写。
- 五层统计及各层 coverage_kind、北京时间、自然周边界、指标公式、覆盖口径固定。
- 观察统计与正式基线隔离；观察组不进入正式门槛和发布检查。
- cleanup 按构建月判断、当前版本整月保护、每月共享 10 秒锁等待预算、分组分批删除方式固定。
- cleanup 没有日期参数；模拟日期只用于本次已授权专用演练机的最后一步。
- 没有自动清理、每日定时任务或连接存活参数的新配置入口。
