# SQL 检索与查询层开发说明

本说明服务开发与评审，不随程序包交付。已确认范围以 [Issue #47](https://github.com/shenxg13/sql-apm/issues/47) 为准；
切词规则、整段方式和候选的补充列按 [Issue #51](https://github.com/shenxg13/sql-apm/issues/51) 修订（结构 1.11.0）。
看板用的 `mpp_view_*` 函数、指纹服务和只读账号见 [Grafana 检索与看板开发说明](grafana-dashboards.md)。

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
| `mpp_search_terms` | `value text` | `text[]`；按词：只按六类空白切开，引号是普通字符；空结果／超过20项报错。 |
| `mpp_search_passage` | `value text` | `text[]`，只有一项；整段：整个输入去掉六类空白后作为一段，不受20项限制；空结果报错。 |
| `mpp_query_occurrences` | `normalization, [fingerprint, scope, database, user, start, end]` | `analysis_id, occurrence_id` 联合标识记录；`scope_id, database, execution_user, fingerprint` 为身份；`timing_type, unit, request_shape, outcome, end_at, estimated_start_at, duration_ms` 为执行事实；`sql_id, file_id, record_no, line_start, line_end` 为原文及来源定位。 |
| `mpp_query_hits` | `normalization, fingerprint, [scope, database, user]` | 每身份一行；`record_count, first_at, last_at, timing_types, has_unknown_timing, has_baseline, current_build, current_rules_match`。基线存在不等于达到样本门槛。 |
| `mpp_query_fuzzy` | `normalization, input, [scope, database, user, start, end, order='count', mode='words']` | `fingerprint, example_sql_id, matched_texts, record_count, scopes, databases, execution_users, last_at, total_structures, structure_texts, identities, top_scope, top_database, top_user, top_records, top_last_at`；按结构聚合，最多50行，`total_structures` 为限量前结构总数。`mode` 可选 `words/passage`。无匹配返回零行。 |
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

两种文本方式用同一套比较：ASCII 字母转小写、去掉六类空白后按字面比较，符号不作通配符或正则。
按词要求每个词都出现，位置和顺序不限；整段要求整个输入连续出现。引号在两种方式下都是普通字符，
例如 `"a"."b"` 就是一个含四个引号的词。两种文本方式的输入上限是 256 KB（UTF-8 字节），由命令行和检索页各自
在调用数据库之前检查，命令行超出时返回 `{"state":"failed","reason":"search_input_too_large"}`；数据库里的函数
本身不设这个上限。`search exact` 的输入恰好是一个结构指纹值（前后可以有六类空白）时不解析，直接按该指纹查，
与文本方式的直查一致。#47 的“英文双引号表示整段”已按
[#51 的确认](https://github.com/shenxg13/sql-apm/issues/51)作废；检索尚未随任何版本发布，没有已发布的行为被改变。
`structure_texts` 是该结构的原文总数；`identities`、`top_*` 是命中原文在筛选范围内的记录所涉及的身份数，
以及其中记录最多的身份、它的记录数和最近时间。
没有可靠指纹、未进入原文表的输入不在模糊检索范围内，这是既有存储边界。

统计列包括 `sample_state, included_count, active_days, excluded_count, exclusions_by_reason,
sufficiency, metric_null_reasons`，以及17列：`min_ms, max_ms, mean_ms, p25_ms, p50_ms,
p75_ms, p90_ms, p95_ms, p99_ms, stddev_ms, cv, mad_ms, iqr_ms, log_median, log_mad, p95_p50, p99_p50`。
`sufficiency` 按封存配置调用既有门槛函数，含 `basic/p95/p99` 的实测数量、要求、`met` 和原因。
`sample_state=no_samples` 仍返回该计时类别。函数不重算已保存的统计值。

## JSON 适配与命令

`mpp_query_search` 包装文本检索结果（末尾参数 `mode`，输出含所用方式），零行时仍给 `total_structures=0`；输入完整 `struct:算法:64位十六进制摘要`
时转调精确命中列表；数据库入口先去掉首尾六类 ASCII 空白再判断，内部空白不删除。
指纹直查采用精确检索的身份筛选，时间／文本排序参数不参与直查。
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
只给开始时间且晚于该身份最后一条记录，或该身份没有可靠结束时间时，明细与小时／天汇总
均返回成功的六个空组及空游标，仍给出推导的边界。显式同时给出起止且起点不早于终点仍拒绝。
返回六组：五类计时及 NULL 未知计时；阶段和调用标记 `stage_or_call`，不相加为完整执行。
`next_cursor` 按结束时间、analysis_id、occurrence_id 的倒序键分页；保持相同筛选与返回的起止范围。
`limit` 为1–1000；训练判定用 `build` 显式启用，使用该版本实际封存的文件／分析映射。
全库缺少当前规则原文结果时附 `history_incomplete_current_rules` 和原文份数，为零不附警告。
无可靠时间的记录在基础函数不设时间筛选时可读；基于时间的历史窗口无法归入这些记录。

```bash
python -m sql_apm search find 'orders status' --order count
python -m sql_apm search find 'where x = 1 and' --mode passage
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

结构 1.11.0 只增改函数并给只读账号授权（见 [Grafana 开发说明](grafana-dashboards.md#只读账号)）；1.10.0 带数据原地升级，
不重写任何表。升级前须由管理员再执行一次 `bootstrap` 创建只读账号。

`verify_search.py` 是合成验收；`verify_search_full.py prepare/audit/exact` 是显式全量核对，使用关闭的真实库副本。
全量核对会扫描原文和全部原有表并占用副本空间；摘要逐行在服务器计算，公开输出只含数量、哈希和耗时。
不自动加入日常 Harness。测试文档见[开发机试用步骤](../runbooks/sql-search-trial.md)。

## 查询计划与累积规模

精确命中直接按当前归一化、profile、可靠状态及结构指纹等值连接指纹和执行记录，计数不读取
来源证据表。基线存在性及统计分组查找都给齐 `profile='mpp-csv/1'`，允许使用现有分组索引。
`mpp_query_hits`、模糊聚合、历史、时间边界、时间汇总及统计函数局部设置
`plan_cache_mode=force_custom_plan`，让 PostgreSQL 按本次参数化简可选条件并估算选择率。
按这种方式规划时，模糊检索扫描原文的一步不会选用并行；1.11.0 起 `mpp_query_fuzzy` 另在函数上把
`parallel_setup_cost` 和 `parallel_tuple_cost` 设为零，使它在长连接下也使用并行，其他设置不变。
数字见 [Grafana 实测报告](../reports/grafana-dashboards-2026-10-09.md)。
设置只在函数内生效，不改变调用者会话；没有禁用顺序扫描或强制某个索引。
依据见 [PG17 计划缓存说明](https://www.postgresql.org/docs/17/runtime-config-query.html#GUC-PLAN-CACHE-MODE)。

精确命中的主要成本随匹配原文数、对应执行记录数和身份数增长；密集结构允许规划器选择顺序
扫描。多语句逐条提示最多另做20次精确查找，成本累加，不能用单条耗时代表整条命令。
模糊匹配仍扫描原文，再聚合匹配记录；全库缺少当前规则结果的精确计数也仍读取全部原文及指纹。
该计数提供当前事务的一致性警告，本轮不新增缓存或计数表，避免过期结果和写入维护成本。
时间边界在已给结束时间时直接由参数计算，只有缺少结束时间才读取该身份记录求最新时间。

基础 `mpp_query_occurrences` 保持可内联，供外层筛选、聚合和 LIMIT 下推；历史与时间汇总的
调用处采用上述定制计划。外部消费者若使用带可空筛选参数的长期 prepared statement，
仍可能选到通用计划；对这类请求可在事务中设置 `SET LOCAL plan_cache_mode=force_custom_plan`。
给基础函数添加函数级配置会阻止内联，因此本轮不这样处理。版本列表只查少量版本元信息；
观察分组仍可能顺序扫描，其可选身份加近似值查询缺少适用的现有前导索引，本轮只测量并记录。

`verify_search_plans.py --directory PRIVATE_COPY` 在已升级且停止的专用副本核对函数内部实际计划，
记录四类结构、1／5／20条完整命令和长输入切分耗时。它仅在诊断管理员会话加载 `auto_explain`，
不安装扩展；SQL 和原始计划只保留在指定私有目录，公开摘要不含业务原文或身份。

数据库函数名、参数和列是供后续 Grafana 消费的契约；未来变化须修改本说明并进入之后的发布说明。
本次不发版；随包三份用户文档仍对应 v0.2.0／1.9.0，由 [Issue #52](https://github.com/shenxg13/sql-apm/issues/52) 补齐查询说明。
