# 检索与看板使用指南

本指南随 v0.3.0 交付，对应结构 1.12.0。示例 SQL、集群 C1、数据库 demo 和用户 analyst 均为人工示例，
执行时换成自己的筛选值。先按[安装手册](kylin-offline-deployment.md)安装，再从浏览器登录 Grafana。
日常查看使用只读数据库账号；它能读取全部 SQL 原文，原文中可能包含业务数据。

## 一、怎样检索

Grafana 的“MPP / SQL 检索”有三种方式，输入后点击检索。筛选集群、数据库和执行用户可以缩小范围。

| 方式 | 适合什么情况 | 例子与规则 |
| --- | --- | --- |
| 按词 | 只记得表名、列名或几个片段 | `orders status`；按空白分词，每个词都出现即可，顺序不限，最多 20 项 |
| 整段 | 记得一段连续 SQL | `where status = 1`；整个输入作为一段比较，不受 20 项限制 |
| 完整 SQL | 手上有完整 SQL，想找同一结构的基线 | `SELECT * FROM orders WHERE id = 42`；按当前规则生成结构指纹，查同结构的记录与基线 |

按词和整段均只把 ASCII A–Z 转小写，去掉空格、TAB、LF、CR、FF、VT 后按字面匹配。
`%`、`_`、引号等都是普通字符，不是通配符、正则或分词分隔符。两种方式输入上限均为 256 KB UTF-8 字节。
完整 SQL 上限为 512 KB；输入恰好是 `struct:算法:64位十六进制摘要` 时，三种方式均可直接查指纹。
可靠结构归一化不等于删除所有常量：某些位置的常量、函数参数和带引号的名字会保留。
多语句按整批匹配；整批未命中时显示前 20 条的逐条提示，逐条命中不表示整批命中。
不自动把 `?`、`#{}`、`:name` 转成真实值。

命令行在程序目录执行，沿用 `SQL_APM_DSN` 和 `PGPASSFILE`；请为查看操作指定只读账号。

```bash
.venv/bin/python -m sql_apm search find 'orders status' --order count
.venv/bin/python -m sql_apm search find 'where status = 1' --mode passage
.venv/bin/python -m sql_apm search exact --file /path/to/query.sql
.venv/bin/python -m sql_apm search versions --cluster C1
.venv/bin/python -m sql_apm search baseline --cluster C1 --database demo --user analyst --fingerprint "$FP"
.venv/bin/python -m sql_apm search executions --cluster C1 --database demo --user analyst --fingerprint "$FP" --bucket hour
```

`$FP` 填检索结果的完整结构指纹。命令输出 JSON；成功查询（含未命中、无法生成可靠指纹）退出 0，
参数、连接或读取失败退出 1。`search --schema NAME` 可指定项目 schema。
候选列表最多显示 50 个结构，总数是限量前的数目。按词／整段的次数、身份和最近时间只统计命中原文在筛选内的记录。
完整 SQL 按整个结构统计。点击候选进入“SQL 详情”。

## 二、怎样读结果

### 四种检索结果

| 状态 | 含义与下一步 |
| --- | --- |
| `has_baseline` | 至少一个身份有正式统计；继续看计时类别、版本和样本条件，存在基线不等于样本充足 |
| `records_without_baseline` | 有执行记录，但相应身份当前没有正式基线；可以看原文和历史 |
| `not_seen` | 当前规则及筛选范围内没找到；可靠指纹仍返回，可检查筛选或换按词／整段 |
| `unreliable_fingerprint` | 无法生成可靠结构指纹；看原因，近似结果仅供独立观察，不能当作正常基线 |

SQL 身份由“集群、数据库、执行用户、结构指纹”共同确定；计时类别再将其分成不同统计组。
同一结构可在不同身份中分别有／没有基线。没有处理过当前规则的原文会带缺失规则提示，查看不会补算或回填。

### 五类计时

| 代码 | 量的是什么 | 单位 |
| --- | --- | --- |
| `request` | 简单协议的一次完整请求，可能是一条 SQL 或整批多语句 | 请求 |
| `execute_first` | 扩展协议首次 Execute 调用 | 调用 |
| `execute_fetch` | 同一 portal 后续取结果的 Execute 调用 | 调用 |
| `parse` | 扩展协议 Parse 阶段 | 阶段调用 |
| `bind` | 扩展协议 Bind 阶段 | 阶段调用 |

扩展协议没有可直接使用的“请求整体”计时；Parse、Bind、首次 Execute 和续取不相加成整条 SQL 耗时，
也不将它们的次数相加为业务执行次数。未知计时不是第六类基线。失败、取消、超时的记录可能没有计时和耗时；
详情页每个计时类别都会列出这些记录，列表里的未成功次数按 SQL 身份统计，不能跨类别再求和。

### 五个时间层次

| layer | 样本范围 |
| --- | --- |
| `overall` | 训练窗口内全部有效样本 |
| `day` | 每个具体日期 |
| `week` | 自然周：北京时间周一 00:00 至下周一 00:00 |
| `weekday` | 合并各周相同星期的样本 |
| `hour` | 合并各天相同小时的样本，即跨天小时画像 |

基线按北京时间的推算开始时间分桶：结束时间减本次计时耗时。阶段／调用的开始时间不是整条 SQL 开始时间。
周桶只使用训练窗口内数据，边缘不足整周不补取窗口外样本。历史执行的时间筛选和图表按结束时间，范围左闭右开。
历史按小时／天现算的汇总，与基线的跨天小时画像是不同用途。

### 17 个指标及样本条件

| 指标 | 解释 |
| --- | --- |
| `min_ms`、`max_ms`、`mean_ms` | 最小、最大、算术平均耗时 |
| `p25_ms`、`p50_ms`、`p75_ms`、`p90_ms`、`p95_ms`、`p99_ms` | 连续线性插值分位数；P50 即中位数 |
| `stddev_ms` | 总体标准差，除以样本数 n |
| `cv` | 总体标准差 / 均值 |
| `mad_ms` | 与中位数距离的中位数，不乘缩放系数 |
| `iqr_ms` | P75 − P25 |
| `log_median`、`log_mad` | 先对毫秒耗时取 ln(1+x)，再算中位数和未缩放 MAD |
| `p95_p50`、`p99_p50` | 对应分位数与 P50 的比值 |

分位数的位置为 `1 + (n - 1) × p`，非整数位置线性插值。耗时单位为毫秒并保留小数；
CV、比值和对数指标不标成毫秒。未知耗时不补零；无样本、均值为零、P50 为零等无法计算的指标以 NULL 加原因表示。
样本不足仍保留可计算数值。

三项条件为 `basic`（基础观察）、`p95`、`p99`。每项都同时检查有效样本数量和活跃天数覆盖，
实际值、要求、是否满足和原因在 `sufficiency` 中。默认数量门槛分别为 30、200、1000；覆盖要求随时间层次变化，
按版本封存配置计算，完整默认值及调整方法见[配置指南](configuration-guide.md#想改变样本不足的门槛)。
不同计时和不同桶不能互借样本。满足条件也不自动构成异常判定或告警。

排除信息包括排除总数及按原因计数。常见原因是未成功、计时或 SQL 身份不可靠、类别黑名单、模板黑名单和排除时段；
窗口之外和未选入版本输入单独解释。某条记录可有多个原因，原因计数不能直接相加当作排除总数。
旧规则无法复算时显示“判定不可用”，不使用当前规则伪造历史判定。

### 四个看板

“SQL 详情”分原文、基线、执行历史三个区。顶部选择身份、计时和参照版本；默认时间范围基于该身份最近记录向前 7 天。
原文按需显示，超长原文分段；基线展示五层统计、样本条件与排除原因。已清理或规则不匹配的版本不能作为有效参照。
执行历史展示真实执行、状态计数、明细和对比；图上每格最多保留最慢和最快的真实执行，不能把点数当总执行数。

对比只用所选计时的整体层基线，统计已知耗时中超过 P50／P95／P99 的次数和占比，并展示对应样本条件。
“超过 P95”只表示超出该分位值，不等于异常。选原文只缩小历史范围，不把整个结构的基线改成该原文的专属基线。
“按原文拆开”适合找同一结构下不同取值的耗时差异；“取值不同的那一段”是去掉共同头尾后的近似显示，不是语法分析结论。

“SQL 列表”提供按时间范围现算的排行、按当前基线版本的排行和版本列表，点行进入详情。
“运行状态”提供各集群现状、待处理问题和最近每日运行记录，只展示，不能从页面触发导入、构建或清理。
状态、原因和处理步骤见[每日运行操作说明](daily-run.md)。

## 三、查询函数与自建看板

数据库函数均在项目 schema 下，以下使用 `sql_apm`。只读账号单条查询超时默认 2 分钟；查询结束及时提交或回滚，
避免持有统计读取锁妨碍清理。函数不修改当前版本；独立观察统计不参与正式基线。

```sql
SET search_path=sql_apm,pg_catalog;
SELECT mpp_view_rules() AS normalization;
SELECT * FROM mpp_query_versions(mpp_view_rules(), 'C1');
SELECT * FROM mpp_query_fuzzy(mpp_view_rules(), 'orders status', p_mode => 'words');
SELECT * FROM mpp_query_fuzzy(mpp_view_rules(), 'where status = 1', p_mode => 'passage');
SELECT * FROM mpp_view_daily_clusters();
SELECT * FROM mpp_view_daily_recent(20);
```

下面的 psql 示例先从实际检索结果设置 `normalization`、`fingerprint`、`sql_id`、`build` 四个变量；
`:'变量'` 是 psql 的安全字符串引用，不可原样粘贴进 Grafana。

```sql
SELECT timing_type, included_count, p95_ms, sufficiency->'p95'
FROM mpp_query_statistics(:'normalization', 'C1', 'demo', 'analyst', :'fingerprint');
SELECT * FROM mpp_query_text(:'sql_id');
SELECT * FROM mpp_view_executions(:'normalization', :'fingerprint',
  mpp_view_identity_token('C1', 'demo', 'analyst'), 'request', :'build',
  '2026-07-01 00:00:00+08', '2026-08-01 00:00:00+08');
```

### 自建看板的约定

以 admin 登录，把自己的看板保存到“MPP / 用户自定义”。随包四个看板由文件配置，界面里不能保存修改；
需要修改时另存到用户自定义。查看账号不能保存看板。SQL 数据源始终使用只读账号，查看账号也不能获得数据库写权限。

Grafana 模板变量插值会处理 `$name`、`${name}` 和 `[[name]]`；Business Forms 的表单更新可能再次展开变量。
数据库字符串拼接、URL 参数、换行转换也会悄悄改变粘贴内容。随包检索表单通过编码传递输入；自建看板不要将原始 SQL
直接拼到 URL、查询字符串或 JavaScript 模板中。使用 PostgreSQL 数据源正确引用值，或仿照随包的 URL 安全 base64 标记。
不要为恢复单个符号手工取消 SQL 转义。完整 SQL 的指纹计算由本机指纹服务完成，不能靠数据库函数重新实现解析器。

浏览器输入的 CR LF、CR、LF 均以 LF 到达；“一字不差的原文”将这三种换行视为相同，其余字符逐一比较。
若保留常量内的换行导致结构变化，服务仅在库里存在一份除换行形式外完全相同的原文时采用另一换行的结果，并在页面注明。
复杂混合换行找不到时用整段方式或命令行。指纹服务停止时完整 SQL 检索不可用，其他方式仍可使用；更新产品后重启服务。

### 参数和返回字段的共同含义

下面各函数签名列出完整参数、默认值和返回类型，按实际顺序调用；签名用于查阅，不是创建函数的安装命令。
省略有 DEFAULT 的参数使用其默认值。`p_` 是参数前缀；NULL 身份筛选表示不限，身份标记用 encode/decode 函数转换。

| 名称 | 含义 |
| --- | --- |
| `normalization`、`rules`、`normalization_id` | 归一化规则上下文；新界面通常通过 mpp_view_rules() 取得；无当前版本时需用安装程序的规则上下文 |
| `fingerprint`、`approximate` | 可靠结构指纹／独立观察近似值，两者不能互换 |
| `scope`、`scope_id`、`cluster`、`database`、`user`、`execution_user` | 集群、数据库、执行用户；数组 scopes/databases/execution_users 是候选涉及的集合 |
| `identity`、`top_identity`、`token` | 编码后的三项身份／文本标记，非 SQL 原文 |
| `input`、`text`、`value`、`a`、`b` | 函数待处理文本；完整 SQL 模式的查询 input 已是指纹 |
| `mode` | words、passage 或看板函数的 exact |
| `start`、`end`、`start_at`、`end_at` | 结束时间筛选边界，左闭右开；执行行的 end_at 是记录结束时间 |
| `build`、`build_id`、`current_build` | 所选或当前已发布基线版本；不是应用版本号 |
| `layer`、`bucket`、`bucket_date`、`bucket_number`、`bucket_at`、`range_start`、`range_end` | 统计层次／桶、实际日期或星期／小时序号，以及覆盖范围 |
| `timing`、`timing_type`、`timings` | 五类计时或筛选集合；unknown 表示没有可靠计时类别 |
| `order`、`limit`、`cursor` | 排序、返回上限和分页标记；保持筛选不变，使用返回的游标继续读 |
| `sql_id`、`example_sql_id`、`exact_sql_id`、`only_sql_id`、`hit_sql_id` | 原文标识；示例、确切命中、唯一命中或当前选择 |
| `sql_text`、`selected` | 保存的原文，以及是否为明确选择的那一份 |
| `analysis_id`、`occurrence_id`、`anchor_ref` | 分析与执行联合身份、锚点证据定位 |
| `file_id`、`record_no`、`line_start`、`line_end`、`source_file`、`source_lines` | 来源文件与逻辑记录、物理行定位 |
| `unit`、`request_shape`、`outcome`、`outcomes` | 请求／阶段／调用单位，单条／整批形态，以及成功／失败／取消／超时等状态 |
| `duration_ms`、`estimated_start_at`、`min_ms`、`max_ms` | 耗时、推算开始时间；明细筛选中的 min/max 是耗时上下界 |
| `record_count`、`executions`、`known_executions`、`known_durations`、`known_duration_count` | 记录总数／其中有已知耗时的数量；按所在函数的身份和时间范围统计 |
| `first_at`、`last_at`、`top_last_at` | 首次／最近记录时间；top 前缀指命中最多的身份 |
| `matched_texts`、`structure_texts`、`identities`、`total_structures` | 命中原文数、结构的全部原文数、涉及身份数、限量前结构数 |
| `top_scope`、`top_database`、`top_user`、`top_label`、`top_records` | 记录最多的命中身份及显示名、数量 |
| `has_unknown_timing`、`has_baseline`、`current_rules_match` | 是否有未知计时记录、当前正式统计、当前版本规则是否匹配 |
| `built_at`、`published_at`、`window_start`、`window_end`、`window_days` | 构建／发布时间、训练窗口左右边界与天数 |
| `algorithm_version`、`parser_version`、`dictionary_rules_version` | 指纹算法、解析器和函数字典版本 |
| `is_current`、`results_cleaned`、`cleaned_at`、`rules_match`、`selectable`、`status` | 当前标记、清理状态与时间、规则匹配、可否选作参照及原因状态 |
| `sample_state`、`included_count`、`active_days`、`excluded_count` | 样本状态、有效数量、活跃天数和排除总数 |
| `exclusions_by_reason`、`sufficiency`、`metric_null_reasons` | 排除原因计数、三项样本条件和指标为空的原因 JSON |
| `basic_met`、`p95_met`、`p99_met`、`min_samples` | 三项条件是否满足；基线排行的最少样本筛选 |
| `decision`、`reason_codes`、`detail`、`training`、`records` | 训练判定、原因、详情；records 为最多 1000 个执行身份的 JSON 数组 |
| `statistic`、`status_counts` | 观察统计 JSON、各执行状态计数 JSON |
| `step_ms`、`slot_at`、`slot_count`、`kind` | 图表网格宽度、网格起点、格内数量；点的 kind 表示最快或最慢 |
| `reference`、`baseline_ms`、`condition_met`、`above`、`above_share`、`expected_share`、`note` | 对比分位、基线值、条件、超出次数／占比、理论尾部占比和说明；占比不是百分数整数 |
| `rank`、`differing`、`median_ms`、`slowest_ms`、`above_p95`、`above_p95_share` | 原文排名、近似差异片段、中位／最慢耗时、超过基线 P95 的数量和占比 |
| `search_hit`、`range_texts`、`matching`、`ranked_rows` | 是否命中本次搜索、范围内原文总数、限量前明细／排行总行数 |
| `hit_mode`、`hit_input`、`comparison` | 本次文本命中条件、明细与基线的比较标签 |
| `seq`、`item`、`content`、`label`、`state_label`、`code` | 显示顺序、说明项及内容、中文标签、待翻译代码 |
| `statement`、`hints`、`state`、`reason` | 逐条提示的语句序号、提示 JSON、查询或运行结果、原因 |
| `total_ms`、`not_success` | 已知耗时合计、该身份未成功记录数量 |
| `run_id`、`started_by`、`started_at`、`finished_at`、`local_date` | 每日运行身份、手工／定时启动、起止时间与服务器日期 |
| `failed`、`run_state`、`run_failed`、`run_reason`、`run_result`、`result` | 运行失败标记、状态、原因及展示结果 |
| `run_started_at`、`run_finished_at`、`last_success_at`、`stale_after_hours` | 运行起止、上次成功时间及多久未成功的提醒界限 |
| `ordinal`、`cluster_state`、`cluster_failed`、`cluster_reason`、`cluster_result`、`last_result` | 集群处理顺序、该集群状态／失败／原因及结果标签 |
| `source_id`、`source`、`log_date`、`result_month`、`file_count`、`seen_at` | 来源、日志日期、结果月份、文件数及最近发现问题的时间 |
| `newest_imported`、`version_cutoff`、`version_published_at`、`open_problems` | 最新已导入日期、当前版本截止日和发布时间、待处理问题数 |
| `import_state`、`imported_days`、`failed_days`、`imported` | 导入状态、成功／失败日期和页面上的导入摘要 |
| `build_state`、`build_reason`、`cutoff_date`、`publication_id` | 构建结果及原因、采用的截止日和发布记录 |
| `cleanup_state`、`cleanup_reason`、`months_cleaned`、`months_pending`、`released_bytes` | 清理状态／原因、完成／待续月份数及释放字节数 |
| `raw_state`、`raw_reason`、`raw_days`、`raw_files`、`raw_bytes` | 原始文件删除状态／原因、日期数、文件数和字节数；看板函数的 raw_files 为摘要文本 |
| `nonconforming_files`、`other_files`、`stage_seconds`、`cluster_started_at`、`cluster_finished_at`、`cluster_seconds`、`seconds` | 不合命名文件数、分阶段秒数、集群起止与耗时、整次耗时 |
| `problem`、`subject`、`hint`、`cleanup` | 问题类别、涉及日期／月份、处理建议，以及清理摘要 |
| `days`、`bytes` | 每日运行格式化函数的日期数组／字节数输入 |

各函数的具体默认值和返回列见以下签名及说明。排序枚举：原文检索 count／recent；
原文拆分 count／median／slowest；执行明细 latest／slowest；SQL 列表 count／total／mean／slowest／not_success；
基线排行 samples／p50／p95／p99／max／mean。

## 四、完整函数签名

本节签名由开发机自动检查与结构定义逐项核对，函数名、参数、默认值和返回列变化都会被发现。

### mpp_search_fold

文本比较预处理：仅 ASCII 转小写并去六类空白，返回处理后的文本。

<!-- query-function:mpp_search_fold -->
```sql
CREATE OR REPLACE FUNCTION mpp_search_fold(value text) RETURNS text
```

### mpp_search_terms

按词切分并预处理，返回词数组；空输入或超过 20 词报错。

<!-- query-function:mpp_search_terms -->
```sql
CREATE OR REPLACE FUNCTION mpp_search_terms(value text) RETURNS text[]
```

### mpp_search_passage

整段预处理，返回只有一个元素的数组；空输入报错。

<!-- query-function:mpp_search_passage -->
```sql
CREATE OR REPLACE FUNCTION mpp_search_passage(value text) RETURNS text[]
```

### mpp_query_occurrences

基础执行记录；允许按身份与结束时间筛选。返回列为执行事实与来源定位。

<!-- query-function:mpp_query_occurrences -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_occurrences(
    p_normalization text, p_fingerprint text DEFAULT NULL, p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL, p_user text DEFAULT NULL,
    p_start timestamptz DEFAULT NULL, p_end timestamptz DEFAULT NULL)
RETURNS TABLE (analysis_id text, occurrence_id text, scope_id text, database text,
    execution_user text, fingerprint text, timing_type text, unit text, request_shape text,
    outcome text, end_at timestamptz, estimated_start_at timestamptz, duration_ms numeric,
    sql_id text, file_id text, record_no bigint, line_start bigint, line_end bigint)
```

### mpp_query_versions

已发布版本清单及窗口、规则和清理状态。

<!-- query-function:mpp_query_versions -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_versions(p_normalization text,p_scope text DEFAULT NULL)
RETURNS TABLE (scope_id text, build_id text, built_at timestamptz, published_at timestamptz,
    window_start timestamptz, window_end timestamptz, window_days integer,
    normalization_id text, algorithm_version text, parser_version text, dictionary_rules_version text,
    is_current boolean, results_cleaned boolean, cleaned_at timestamptz, rules_match boolean)
```

### mpp_query_hits

按结构查每个 SQL 身份的记录数量、时间和当前基线状态。

<!-- query-function:mpp_query_hits -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_hits(
    p_normalization text,p_fingerprint text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE (scope_id text,database text,execution_user text,fingerprint text,
    record_count bigint,first_at timestamptz,last_at timestamptz,timing_types text[],
    has_unknown_timing boolean,has_baseline boolean,current_build text,current_rules_match boolean)
```

### mpp_query_fuzzy

按词或整段查结构候选；order=count/recent，mode=words/passage，最多 50 行。

<!-- query-function:mpp_query_fuzzy -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_fuzzy(
    p_normalization text,p_input text,p_scope text DEFAULT NULL,p_database text DEFAULT NULL,
    p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
    p_order text DEFAULT 'count',p_mode text DEFAULT 'words')
RETURNS TABLE (fingerprint text,example_sql_id text,matched_texts bigint,record_count bigint,
    scopes text[],databases text[],execution_users text[],last_at timestamptz,total_structures bigint,
    structure_texts bigint,identities bigint,top_scope text,top_database text,top_user text,
    top_records bigint,top_last_at timestamptz)
```

### mpp_query_missing_rules

尚无当前规则原文级结果的原文份数；已有解析拒绝不算缺失。

<!-- query-function:mpp_query_missing_rules -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_missing_rules(p_normalization text) RETURNS bigint
```

### mpp_query_text

按原文标识读取精确原文。

<!-- query-function:mpp_query_text -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_text(p_sql_id text) RETURNS TABLE(sql_id text,sql_text text)
```

### mpp_query_select_version

选择当前或指定已发布版本并保护读取；已清理、规则不匹配、无当前版本分别报错。返回 build_id。

<!-- query-function:mpp_query_select_version -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_select_version(p_normalization text,p_scope text,p_build text DEFAULT NULL)
RETURNS text
```

### mpp_query_training

对最多 1000 个执行身份返回该版本的训练判定；records 为 analysis_id/occurrence_id 对象数组。

<!-- query-function:mpp_query_training -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_training(p_build text,p_records jsonb)
RETURNS TABLE(analysis_id text,occurrence_id text,decision text,reason_codes text[],detail jsonb)
```

### mpp_query_time_bounds

补齐历史时间边界；默认取最后记录向前 7 天，右边界加 1 微秒以包含最后一条。

<!-- query-function:mpp_query_time_bounds -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_time_bounds(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL)
RETURNS TABLE(start_at timestamptz,end_at timestamptz)
```

### mpp_query_timeline

按结束时间按小时或天聚合，bucket=hour/day；分位数仅用已知耗时。

<!-- query-function:mpp_query_timeline -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_timeline(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz,p_end timestamptz,p_bucket text DEFAULT 'hour')
RETURNS TABLE(timing_type text,bucket_at timestamptz,record_count bigint,status_counts jsonb,
    known_duration_count bigint,p50_ms double precision,p95_ms double precision,max_ms numeric)
```

### mpp_query_history

历史 JSON 包装：五类计时及未知组、groups、next_cursor 与范围；limit 为 1–1000，bucket 为空取明细，否则 hour/day。指定 build 才给历史训练判定。

<!-- query-function:mpp_query_history -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_history(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
    p_limit integer DEFAULT 100,p_cursor jsonb DEFAULT NULL,p_build text DEFAULT NULL,p_bucket text DEFAULT NULL)
RETURNS jsonb
```

### mpp_query_observations

按近似值查询当前版本的独立观察统计；不参与正式基线判定。

<!-- query-function:mpp_query_observations -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_observations(p_approximate text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE(scope_id text,database text,execution_user text,build_id text,timing_type text,statistic jsonb)
```

### mpp_query_statistics

读取指定版本五层统计；layer=overall/day/week/weekday/hour，无样本给 no_samples 和空指标，不重算保存值。

<!-- query-function:mpp_query_statistics -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_statistics(p_normalization text,p_scope text,p_database text,
    p_user text,p_fingerprint text,p_build text DEFAULT NULL,p_layer text DEFAULT 'overall')
RETURNS TABLE(build_id text,scope_id text,database text,execution_user text,fingerprint text,
    timing_type text,layer text,bucket_date date,bucket_number smallint,range_start timestamptz,range_end timestamptz,
    sample_state text,included_count bigint,active_days integer,excluded_count bigint,exclusions_by_reason jsonb,
    sufficiency jsonb,metric_null_reasons jsonb,
    min_ms numeric,
    max_ms numeric,
    mean_ms numeric,
    p25_ms numeric,
    p50_ms numeric,
    p75_ms numeric,
    p90_ms numeric,
    p95_ms numeric,
    p99_ms numeric,
    stddev_ms numeric,
    cv numeric,
    mad_ms numeric,
    iqr_ms numeric,
    log_median numeric,
    log_mad numeric,
    p95_p50 numeric,
    p99_p50 numeric)
```

### mpp_query_exact

接收已计算的可靠指纹或独立近似值，返回 state、fingerprint、hits 及提示 JSON；四种状态见前文。

<!-- query-function:mpp_query_exact -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_exact(p_normalization text,p_fingerprint text,p_approximate text DEFAULT NULL,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,p_reason text DEFAULT NULL)
RETURNS jsonb
```

### mpp_query_search

文本检索 JSON 包装；零命中仍返回 total_structures=0，完整指纹输入直接查身份。

<!-- query-function:mpp_query_search -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_search(p_normalization text,p_input text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,
    p_end timestamptz DEFAULT NULL,p_order text DEFAULT 'count',p_mode text DEFAULT 'words')
RETURNS jsonb
```

### mpp_query_baseline

基线 JSON 包装，返回版本、身份和统计；已清理或规则不匹配时返回明确状态。

<!-- query-function:mpp_query_baseline -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_baseline(p_normalization text,p_scope text,p_database text,p_user text,
    p_fingerprint text,p_build text DEFAULT NULL,p_layer text DEFAULT 'overall') RETURNS jsonb
```

### mpp_view_encode

UTF-8 文本编码成无填充的 URL 安全 base64。

<!-- query-function:mpp_view_encode -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_encode(p_value text) RETURNS text
```

### mpp_view_decode

把 URL 安全 base64 标记解码为文本。

<!-- query-function:mpp_view_decode -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_decode(p_token text) RETURNS text
```

### mpp_view_identity_token

将集群、数据库、执行用户编码为身份标记。

<!-- query-function:mpp_view_identity_token -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_identity_token(p_scope text,p_database text,p_user text) RETURNS text
```

### mpp_view_identity

解开身份标记，返回三项身份；标记不是权限凭据。

<!-- query-function:mpp_view_identity -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_identity(p_token text)
RETURNS TABLE(scope_id text,database text,execution_user text)
```

### mpp_view_label

将 kind/code 转成中文标签，未识别时保留代码。

<!-- query-function:mpp_view_label -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_label(p_kind text,p_code text) RETURNS text
```

### mpp_view_rules

返回最近发布的当前版本使用的归一化标识；没有当前版本返回 NULL。

<!-- query-function:mpp_view_rules -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_rules() RETURNS text
```

### mpp_query_text_id

按字节完全相同的文本找原文标识；换行变体由指纹服务处理，此函数不改文本。

<!-- query-function:mpp_query_text_id -->
```sql
CREATE OR REPLACE FUNCTION mpp_query_text_id(p_text text) RETURNS text
```

### mpp_view_common_prefix

辅助函数，返回两文本的共同前缀字符数，用于近似显示差异。

<!-- query-function:mpp_view_common_prefix -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_common_prefix(p_a text,p_b text) RETURNS integer
```

### mpp_view_identities

列出结构实际出现的身份、记录数和基线标记。

<!-- query-function:mpp_view_identities -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_identities(p_normalization text,p_fingerprint text)
RETURNS TABLE(identity text,label text,scope_id text,database text,execution_user text,
    record_count bigint,last_at timestamptz,has_baseline boolean)
```

### mpp_view_records

一个身份、一类计时的执行事实；每类都附上无计时的失败、取消和超时记录。

<!-- query-function:mpp_view_records -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_records(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_start timestamptz,p_end timestamptz,p_sql_id text DEFAULT NULL)
RETURNS TABLE(analysis_id text,occurrence_id text,sql_id text,outcome text,end_at timestamptz,
    duration_ms numeric,request_shape text,anchor_ref text)
```

### mpp_view_timings

列出身份中出现的计时供下拉框选择，并含中文名和数量。

<!-- query-function:mpp_view_timings -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_timings(p_normalization text,p_fingerprint text,p_identity text)
RETURNS TABLE(timing text,label text,record_count bigint)
```

### mpp_view_versions

列出版本及可否作为参照；规则不同或已清理时 selectable=false。

<!-- query-function:mpp_view_versions -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_versions(p_normalization text,p_identity text)
RETURNS TABLE(build_id text,label text,selectable boolean,status text,is_current boolean,
    published_at timestamptz,built_at timestamptz,window_start timestamptz,window_end timestamptz,
    window_days integer,rules text,results_cleaned boolean,rules_match boolean)
```

### mpp_view_statistics

看板版五层统计入口；没有可用版本返回零行。

<!-- query-function:mpp_view_statistics -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_statistics(p_normalization text,p_fingerprint text,p_identity text,
    p_build text,p_layer text DEFAULT 'overall')
RETURNS TABLE(build_id text,scope_id text,database text,execution_user text,fingerprint text,
    timing_type text,layer text,bucket_date date,bucket_number smallint,range_start timestamptz,range_end timestamptz,
    sample_state text,included_count bigint,active_days integer,excluded_count bigint,exclusions_by_reason jsonb,
    sufficiency jsonb,metric_null_reasons jsonb,
    min_ms numeric,max_ms numeric,mean_ms numeric,p25_ms numeric,p50_ms numeric,p75_ms numeric,p90_ms numeric,
    p95_ms numeric,p99_ms numeric,stddev_ms numeric,cv numeric,mad_ms numeric,iqr_ms numeric,
    log_median numeric,log_mad numeric,p95_p50 numeric,p99_p50 numeric)
```

### mpp_view_points

每个 step_ms 网格选最慢和最快各一个真实执行；kind 表示两种点，slot_count 为格内数量。

<!-- query-function:mpp_view_points -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_points(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_start timestamptz,p_end timestamptz,p_step_ms bigint,p_sql_id text DEFAULT NULL,
    p_min_ms numeric DEFAULT NULL,p_max_ms numeric DEFAULT NULL)
RETURNS TABLE(end_at timestamptz,duration_ms numeric,kind text,sql_id text,slot_count bigint)
```

### mpp_view_counts

按 step_ms 时间格与执行状态统计数量；slot_at 为格的起点。

<!-- query-function:mpp_view_counts -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_counts(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_start timestamptz,p_end timestamptz,p_step_ms bigint,p_sql_id text DEFAULT NULL)
RETURNS TABLE(slot_at timestamptz,outcome text,record_count bigint)
```

### mpp_view_compare

返回 P50/P95/P99 参照的数量条件、基线值、超出次数和比例，不作异常判定。

<!-- query-function:mpp_view_compare -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_compare(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_build text,p_start timestamptz,p_end timestamptz,p_sql_id text DEFAULT NULL)
RETURNS TABLE(reference text,baseline_ms numeric,condition_met boolean,known_executions bigint,
    above bigint,above_share numeric,expected_share numeric,note text)
```

### mpp_view_texts

按原文拆分所选范围，返回近似差异片段、耗时与 P95 对比、搜索命中；排序为 count／median／slowest。

<!-- query-function:mpp_view_texts -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_texts(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_build text,p_start timestamptz,p_end timestamptz,p_order text DEFAULT 'count',
    p_hit_mode text DEFAULT NULL,p_hit_input text DEFAULT NULL,p_hit_sql_id text DEFAULT NULL,
    p_limit integer DEFAULT 10)
RETURNS TABLE(rank bigint,sql_id text,differing text,executions bigint,known_executions bigint,
    median_ms numeric,slowest_ms numeric,above_p95 bigint,above_p95_share numeric,
    search_hit boolean,range_texts bigint)
```

### mpp_view_executions

执行明细，含原文标识、来源、与基线的比较和训练判定；limit 默认 200。

<!-- query-function:mpp_view_executions -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_executions(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_build text,p_start timestamptz,p_end timestamptz,p_sql_id text DEFAULT NULL,
    p_outcomes text DEFAULT NULL,p_min_ms numeric DEFAULT NULL,p_max_ms numeric DEFAULT NULL,
    p_order text DEFAULT 'latest',p_limit integer DEFAULT 200)
RETURNS TABLE(end_at timestamptz,duration_ms numeric,outcome text,comparison text,request_shape text,
    sql_id text,source_file text,source_lines text,training text,analysis_id text,occurrence_id text,
    matching bigint)
```

### mpp_view_sql_text

取该结构中的所选原文或一份示例，返回 selected 标记及原文总数。

<!-- query-function:mpp_view_sql_text -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_sql_text(p_normalization text,p_fingerprint text,p_sql_id text DEFAULT NULL)
RETURNS TABLE(sql_id text,sql_text text,selected boolean,structure_texts bigint)
```

### mpp_view_search

三种检索统一的表格结果；exact 输入为已计算的结构指纹，其他输入为原始文本。

<!-- query-function:mpp_view_search -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_search(p_normalization text,p_mode text,p_input text,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,
    p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,p_order text DEFAULT 'count',
    p_exact_sql_id text DEFAULT NULL)
RETURNS TABLE(fingerprint text,example_sql_id text,matched_texts bigint,structure_texts bigint,
    record_count bigint,identities bigint,scopes text[],databases text[],execution_users text[],
    last_at timestamptz,total_structures bigint,top_identity text,top_label text,top_records bigint,
    top_last_at timestamptz,only_sql_id text)
```

### mpp_view_search_note

按 seq 排列的检索说明，包括输入长度和摘要、方式及结果提示。

<!-- query-function:mpp_view_search_note -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_search_note(p_normalization text,p_mode text,p_input text,
    p_fingerprint text DEFAULT NULL,p_state text DEFAULT NULL,p_reason text DEFAULT NULL,
    p_exact_sql_id text DEFAULT NULL,p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE(seq integer,item text,content text)
```

### mpp_view_hints

显示整批未命中后的逐条提示；statement 为语句序号，状态和次数按身份筛选计算。

<!-- query-function:mpp_view_hints -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_hints(p_normalization text,p_hints text,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE(statement integer,fingerprint text,state text,state_label text,record_count bigint)
```

### mpp_view_ranking

时间范围内按身份和计时现算排行；timings 是逗号分隔代码，未成功数量按身份统计。

<!-- query-function:mpp_view_ranking -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_ranking(p_normalization text,p_start timestamptz,p_end timestamptz,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,
    p_timings text DEFAULT 'request,execute_first',p_order text DEFAULT 'count',p_limit integer DEFAULT 100)
RETURNS TABLE(scope_id text,database text,execution_user text,fingerprint text,timing_type text,
    record_count bigint,known_durations bigint,total_ms numeric,mean_ms numeric,slowest_ms numeric,
    not_success bigint,last_at timestamptz,identity text,ranked_rows bigint)
```

### mpp_view_baseline_ranking

各身份当前基线的排行，min_samples 筛选有效样本数，三项 met 表示样本条件。

<!-- query-function:mpp_view_baseline_ranking -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_baseline_ranking(p_normalization text,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,
    p_timings text DEFAULT 'request,execute_first',p_order text DEFAULT 'p95',
    p_min_samples bigint DEFAULT 0,p_limit integer DEFAULT 100)
RETURNS TABLE(scope_id text,database text,execution_user text,fingerprint text,timing_type text,build_id text,
    included_count bigint,active_days integer,p50_ms numeric,p95_ms numeric,p99_ms numeric,max_ms numeric,
    mean_ms numeric,basic_met boolean,p95_met boolean,p99_met boolean,identity text,ranked_rows bigint)
```

### mpp_daily_lock_key

内部辅助函数，返回每日运行咨询锁的键；查看状态应使用下面的查询函数，不自行持有该锁。

<!-- query-function:mpp_daily_lock_key -->
```sql
CREATE OR REPLACE FUNCTION mpp_daily_lock_key() RETURNS bigint
```

### mpp_daily_runs

每日运行记录；结合当前锁持有情况解释没有正常结束的运行。

<!-- query-function:mpp_daily_runs -->
```sql
CREATE OR REPLACE FUNCTION mpp_daily_runs()
RETURNS TABLE(run_id text,started_by text,state text,failed boolean,reason text,
    started_at timestamptz,finished_at timestamptz,local_date date,stale_after_hours integer)
```

### mpp_daily_problems

当前仍需处理的问题；按最近真正检查过相关日期的结论选择，保留中断前已发现的问题。

<!-- query-function:mpp_daily_problems -->
```sql
CREATE OR REPLACE FUNCTION mpp_daily_problems()
RETURNS TABLE(kind text,scope_id text,source_id text,log_date date,result_month date,
    reason text,file_count integer,run_id text,seen_at timestamptz)
```

### mpp_daily_clusters

每集群现状、当前版本日期及最近处理结果和待处理问题数。

<!-- query-function:mpp_daily_clusters -->
```sql
CREATE OR REPLACE FUNCTION mpp_daily_clusters()
RETURNS TABLE(scope_id text,ordinal integer,newest_imported date,version_cutoff date,
    version_published_at timestamptz,run_id text,run_started_at timestamptz,run_state text,
    cluster_state text,cluster_failed boolean,cluster_reason text,open_problems bigint)
```

### mpp_daily_recent

最近 p_limit 次运行的每集群详细阶段结果；各阶段状态与秒数、导入日期、清理及删除计数。

<!-- query-function:mpp_daily_recent -->
```sql
CREATE OR REPLACE FUNCTION mpp_daily_recent(p_limit integer DEFAULT 20)
RETURNS TABLE(run_id text,started_by text,run_state text,run_failed boolean,run_reason text,
    run_started_at timestamptz,run_finished_at timestamptz,
    scope_id text,ordinal integer,cluster_state text,cluster_failed boolean,cluster_reason text,
    import_state text,imported_days date[],failed_days jsonb,newest_imported date,
    build_state text,build_reason text,cutoff_date date,build_id text,publication_id text,
    cleanup_state text,cleanup_reason text,months_cleaned integer,months_pending integer,released_bytes bigint,
    raw_state text,raw_reason text,raw_days integer,raw_files bigint,raw_bytes bigint,
    nonconforming_files bigint,stage_seconds jsonb,cluster_started_at timestamptz,cluster_finished_at timestamptz)
```

### mpp_view_daily_label

每日运行代码的中文标签。

<!-- query-function:mpp_view_daily_label -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_daily_label(p_kind text,p_code text) RETURNS text
```

### mpp_view_daily_days

日期数组转成供页面展示的中文文本。

<!-- query-function:mpp_view_daily_days -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_daily_days(p_days date[]) RETURNS text
```

### mpp_view_daily_bytes

字节数转成可读大小文本。

<!-- query-function:mpp_view_daily_bytes -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_daily_bytes(p_bytes bigint) RETURNS text
```

### mpp_view_daily_last

运行状态看板顶部的一行摘要：上次运行、上次成功和待处理问题总数。

<!-- query-function:mpp_view_daily_last -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_daily_last()
RETURNS TABLE(started_at timestamptz,finished_at timestamptz,seconds double precision,started_by text,
    result text,last_success_at timestamptz,open_problems bigint)
```

### mpp_view_daily_clusters

运行状态看板的每集群表；日期与结果转为显示文本。

<!-- query-function:mpp_view_daily_clusters -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_daily_clusters()
RETURNS TABLE(cluster text,newest_imported text,version_cutoff text,version_published_at timestamptz,
    last_result text,open_problems bigint)
```

### mpp_view_daily_problems

当前问题列表及处理提示，subject 为日期或月份，detail 是问题说明。

<!-- query-function:mpp_view_daily_problems -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_daily_problems()
RETURNS TABLE(problem text,cluster text,source text,subject text,detail text,hint text,seen_at timestamptz)
```

### mpp_view_daily_recent

最近运行展示表；每次、每集群一行，导入／构建／清理／原始文件删除为摘要。

<!-- query-function:mpp_view_daily_recent -->
```sql
CREATE OR REPLACE FUNCTION mpp_view_daily_recent(p_limit integer DEFAULT 20)
RETURNS TABLE(started_at timestamptz,finished_at timestamptz,seconds double precision,started_by text,
    run_result text,cluster text,cluster_result text,imported text,failed text,build text,cleanup text,
    raw_files text,other_files bigint,cluster_seconds double precision)
```
