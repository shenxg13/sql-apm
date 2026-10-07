# SQL 检索与查询层开发说明

本说明服务开发与评审，不随程序包交付。已确认范围以 [Issue #47](https://github.com/shenxg13/sql-apm/issues/47) 为准。

## 实施与验证顺序

1. 冻结 1.9.0 DDL，增加 ASCII 预处理生成列和数据库查询函数，接入 1.10.0 初始化与带数据升级。
2. 首先在 #45 已停止的九任务库副本测量模糊查询。原件不启动；副本升级前后逐表核对旧列内容和行数，记录空间与耗时。
3. 接入命中列表、版本、基线五层、执行明细／时间汇总、训练判定及 JSON 命令。计算指纹复用当前 Normalizer，匹配与查询规则由数据库持有。
4. 合成验证覆盖切词／符号／空白、四种结果、多语句提示、版本守卫、五类计时和无计时、分页、版本输入范围、旧判定不可复算、只读及清理并发。
5. 全量验证固定模糊输入的独立匹配、结构命中直接计数、既有统计函数逐项对照；最坏结构和随机样本各记录首次／重复查询耗时。
6. 完成制包、隔离随包验证、Harness 和 PR 契约检查，提供用户试用步骤，保留 S23 人工确认与独立评审。

升级在事务内重写原文表，需要停止写入；失败可回滚并重试。查询不创建任务或回填指纹。
常规查询事务尽快结束；统计读取沿用结果读取锁，清理按已有每月等待预算退出或重试。
性能以开发机 measured 结果报告，生产累积规模的影响单列推断，不以目标值作为硬门禁。

## API 约定

函数位于项目 schema，以下以默认 `sql_apm` 为例。连接应设置 `search_path=sql_apm,pg_catalog`。
`p_normalization` 由当前安装 Normalizer 的 context 生成（与导入的 `N:` 标识相同）；调用不创建该规则记录。
所有时间过滤以记录结束时间为准，区间为左闭右开；按小时／天汇总采用北京时间。

参数中的集群／数据库／执行用户用于精确等值筛选。SQL 身份为这三项加结构指纹；NULL 筛选表示不限。
指纹只在当前 `normalization_id` 内查找，不把旧规则和近似指纹混入正式分组。
原文用 `sql_id` 按需读取，默认查询不会携带它。空耗时是未知，不是零。

| 函数 | 参数（按顺序；方括号内有默认值） | 返回列与含义 |
| --- | --- | --- |
| `mpp_search_fold` | `value text` | ASCII A–Z 转小写；删除空格、TAB、LF、CR、FF、VT，其他字符原样。 |
| `mpp_search_terms` | `value text` | `text[]`；双引号配对部分为连续短语，不配对的引号作普通字符；拼接相邻片段后按六类空白切词；空结果／超过20项报错。 |
| `mpp_query_occurrences` | `normalization, [fingerprint, scope, database, user, start, end]` | `analysis_id, occurrence_id` 联合标识记录；`scope_id, database, execution_user, fingerprint` 为身份；`timing_type, unit, request_shape, outcome, end_at, estimated_start_at, duration_ms` 为执行事实；`sql_id, file_id, record_no, line_start, line_end` 为原文及来源定位。 |
| `mpp_query_hits` | `normalization, fingerprint, [scope, database, user]` | 每身份一行；`record_count, first_at, last_at, timing_types, has_unknown_timing, has_baseline, current_build, current_rules_match`。基线存在不等于达到样本门槛。 |
| `mpp_query_fuzzy` | `normalization, input, [scope, database, user, start, end, order='count']` | `fingerprint, example_sql_id, matched_texts, record_count, scopes, databases, execution_users, last_at, total_structures`；按结构聚合，最多50行，最后一列为限量前结构总数。无匹配返回零行。 |
| `mpp_query_versions` | `normalization, [scope]` | 仅已发布版本；`scope_id, build_id, built_at, published_at, window_start, window_end, window_days, normalization_id, algorithm_version, parser_version, dictionary_rules_version, is_current, results_cleaned, cleaned_at, rules_match`。 |
| `mpp_query_statistics` | `normalization, scope, database, user, fingerprint, [build, layer='overall']` | 每计时／桶一行；身份、版本、桶和范围，加下面的统计列。无该计时的存储结果时返回 `no_samples`；其余层次已有桶以保存结果为准，无结果的计时给空桶标记。 |
| `mpp_query_timeline` | `normalization, fingerprint, scope, database, user, start, end, [bucket='hour']` | 每计时和结束时间桶一行；`timing_type, bucket_at, record_count, status_counts, known_duration_count, p50_ms, p95_ms, max_ms`。`bucket` 可选 `hour/day`；分位数仅使用已知耗时。 |
| `mpp_query_training` | `build, records jsonb` | 输入是最多1000个 `{analysis_id, occurrence_id}`；输出该二列及 `decision, reason_codes, detail`。`included` 参与；`excluded/outside_window/unresolved` 按原因解释；`not_in_version_input` 表示未选入；`decision_unavailable` 表示旧规则无法复算。 |
| `mpp_query_missing_rules` | `normalization` | 全库尚无该规则原文级指纹结果的份数；已有拒绝结果不计为未处理。 |
| `mpp_query_text` | `sql_id` | `sql_id, sql_text`，返回精确保存的原文。 |
| `mpp_query_observations` | `approximate, [scope, database, user]` | 当前版本的独立观察统计；身份、版本、计时与统计 JSON。不参与正式基线判断。 |

这里表中省略的参数前缀均为 `p_`。标识／文本参数为 `text`；起止时间为 `timestamptz`。
`mpp_query_fuzzy` 的 `p_order` 可选 `count/recent`，同值以指纹稳定排序；符号不解释为通配符或正则。
匹配文本先物化，再和保留记录统一聚合；只有匹配原文在筛选内的记录参与计数。
原文仅匹配但没有相应记录时不产生候选。

统计列包括 `sample_state, included_count, active_days, excluded_count, exclusions_by_reason,
sufficiency, metric_null_reasons`，以及17列：`min_ms, max_ms, mean_ms, p25_ms, p50_ms,
p75_ms, p90_ms, p95_ms, p99_ms, stddev_ms, cv, mad_ms, iqr_ms, log_median, log_mad, p95_p50, p99_p50`。
`sufficiency` 按封存配置调用既有门槛函数，含 `basic/p95/p99` 的实测数量、要求、`met` 和原因。
`sample_state=no_samples` 仍返回该计时类别。函数不重算已保存的统计值。

## JSON 适配与命令

`mpp_query_search` 包装模糊结果，零行时仍给 `total_structures=0`；输入完整 `struct:算法:64位十六进制摘要`
时转调精确命中列表。指纹直查采用精确检索的身份筛选，时间／文本排序参数不参与直查。
`mpp_query_exact` 接收 Python 已算出的指纹和可选近似值，返回 `has_baseline`、
`records_without_baseline`、`not_seen`、`unreliable_fingerprint`；可靠未命中仍给指纹。
每个命中行的 `has_baseline=false` 明确代表该身份的当前版本无正式统计。
同一输入的不同身份可以同时具有或没有基线，顶层状态反映是否至少一个身份具有基线。

`mpp_query_select_version` 在读锁下选择当前或指定已发布版本；清理过返回 `results_cleaned`，
不同规则返回 `normalization_version_mismatch`，无当前版本返回 `no_current_baseline`。
`mpp_query_baseline` 包装版本信息和统计结果。查看不会更改当前版本。

`mpp_query_history(normalization, fingerprint, scope, database, user, [start, end, limit=100,
cursor jsonb, build, bucket])` 提供明细或便利时间汇总的 JSON 包装。
无起止时间时由 `mpp_query_time_bounds` 取该身份最近一条记录减7天，结束边界多1微秒以包含最后一条。
只给结束时间时开始取其前7天；只给开始时间时结束取最后一条加1微秒。
返回六组：五类计时及 NULL 未知计时；阶段和调用标记 `stage_or_call`，不相加为完整执行。
`next_cursor` 按结束时间、analysis_id、occurrence_id 的倒序键分页；保持相同筛选与返回的起止范围。
`limit` 为1–1000；训练判定用 `build` 显式启用，使用该版本实际封存的文件／分析映射。
全库缺少当前规则原文结果时附 `history_incomplete_current_rules` 和原文份数，为零不附警告。
无可靠时间的记录在基础函数不设时间筛选时可读；基于时间的历史窗口无法归入这些记录。

```bash
python -m sql_apm search find 'orders "where x ="' --order count
python -m sql_apm search exact --file /tmp/query.sql
python -m sql_apm search versions --cluster C1
python -m sql_apm search baseline --cluster C1 --database demo --user analyst --fingerprint "$FP"
python -m sql_apm search executions --cluster C1 --database demo --user analyst --fingerprint "$FP" --bucket hour
```

连接沿用 `SQL_APM_DSN`，schema 用 `search --schema NAME` 指定。所有正常结果、输入错误和帮助输出均为 JSON；
成功查询（包括未命中／无法可靠归一化）退出0；参数／连接／读取错误退出1。
原文文件最多读取既有归一化上限加1字节，整体超限按第四种结果返回。
批次整体未命中时复用适配器的词法边界，前20条各算指纹并查库；注释／提示和字符串内分号保留。
`statement_hints` 不等于整批命中；不从单条反查批次，也不转换 `?`、`#{}`、`:name`。

数据库函数可直接再筛选／汇总，例如：

```sql
SET search_path=sql_apm,pg_catalog;
SELECT timing_type,outcome,count(*)
FROM mpp_query_occurrences(:'normalization', :'fingerprint', 'C1')
GROUP BY timing_type,outcome;
SELECT timing_type,included_count,p95_ms,sufficiency->'p95'
FROM mpp_query_statistics(:'normalization','C1','demo','analyst',:'fingerprint')
WHERE timing_type IN ('request','execute_first');
```

## 升级、只读与清理

结构1.10.0只新增生成列 `mpp_sql_text.search_text` 和查询函数。没有新表、扩展或其他表列／约束变化；
1.9.0 DDL冻结，迁移追加新版本回执，原始文本和导入写入字段不变。
更早版本继续走已验证的升级链；版本号按数字数组排序，避免把1.9排在1.10之后。
命令行每次请求使用只读、可重复读事务；原文归一化不持久化、不回填旧指纹、不写任务。

统计读取沿用 `mpp_require_results` 的父表 ACCESS SHARE 锁，事务结束前保护清理判定和读取的一致性。
长查询可能使清理在该月共享的10秒等待预算内退出为 `lock_timeout`；释放读取后可重试，已保存结果不变。
直接数据库消费者也应及时提交／回滚，避免空闲事务长持锁。合成验收覆盖实际长读锁和清理重试。

`verify_search.py` 是合成验收；`verify_search_full.py prepare/audit/exact` 是显式全量核对，使用关闭的真实库副本。
全量核对会扫描原文和全部原有表并占用副本空间；摘要逐行在服务器计算，公开输出只含数量、哈希和耗时。
不自动加入日常 Harness。测试文档见[开发机试用步骤](../runbooks/sql-search-trial.md)。

数据库函数名、参数和列是供后续 Grafana 消费的契约；未来变化须修改本说明并进入之后的发布说明。
本次不发版；随包三份用户文档仍对应 v0.2.0／1.9.0，由后续 Grafana 工作补齐查询说明。
