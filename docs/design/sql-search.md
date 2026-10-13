# SQL 检索与查询层开发说明

本说明服务开发与评审，不随程序包交付。已确认范围以 [Issue #47](https://github.com/shenxg13/sql-apm/issues/47) 为准；
切词规则、整段方式和候选的补充列按 [Issue #51](https://github.com/shenxg13/sql-apm/issues/51) 修订（结构 1.11.0）。
看板用的 `mpp_view_*` 函数、指纹服务和只读账号见 [Grafana 检索与看板开发说明](grafana-dashboards.md)。

## 面向运维的 sqlfind

[Issue #62](https://github.com/shenxg13/sql-apm/issues/62) 新增根目录可执行脚本 `sqlfind`，
对应 `python -B -m sql_apm sqlfind`。脚本解析符号链接，使用程序目录的 `.venv/bin/python`，
保留调用者当前目录、参数、标准输入、终端和退出码。帮助中的名称始终是 `sqlfind`。
开发机试用与撤销步骤见 [sqlfind 试用](../runbooks/sqlfind-trial.md)。
本轮不调整离线包清单、随包手册、数据库结构或 Grafana；以后发版时另行纳入短入口和配置工具。

### 输入与参数

给出输入后只打印一次；没有输入且标准输入、标准输出都是终端时进入交互。
命令行位置参数、`--file FILE`、非终端标准输入三选一，即使另一个来源为空也拒绝混用。
文件与标准输入按 UTF-8 字节读取，保留 CR LF、单独 CR、LF 和首尾空白；不执行输入的 SQL。

| 参数 | 行为 |
| --- | --- |
| `--mode words\|passage\|exact` | 默认按词，所有词都出现；整段要求连续出现；完整 SQL 按可靠结构匹配，不猜测方式。前两种上限 256 KB，完整 SQL 上限 512 KB，按词最多 20 词。完整指纹值在三种方式下均可直查。 |
| `--cluster`、`--database`、`--user` | 精确筛选；省略表示不限。 |
| `--start`、`--end` | 按结束时间筛选，左闭右开；无时区按北京时间，有时区按输入。 |
| `--order count\|recent` | 记录数多的在前（默认）或最近执行在前，复用看板排序。 |
| `--show 1` 或 `--show s1` | 重新检索后只打印指定候选或逐条提示的详情；无效标记列出可用标记并退出 1。不指定时即使只有一行也只打印候选。 |
| `--identity N` | 选择详情身份表中的序号，默认与看板链接的记录最多身份相同。 |
| `--timing CODE` | 请求整体、Execute 首次／续取、Parse、Bind 或无计时；默认用该身份有记录的第一类。代码见帮助。 |
| `--limit N` | 最近执行显示行数，默认 20，范围 1–1000。 |
| `--full-text` | 显示所选原文全文；默认上限 4000 字符，截断时说明总字符数及查看办法。 |
| `--schema NAME` | 显式 schema 优先，其次 `SQL_APM_SCHEMA`、配置值，最后 `sql_apm`。 |
| `--width N` | 重定向输出的固定宽度，默认 100，最小 20；终端始终跟随窗口。 |

`--identity`、`--timing`、`--limit`、`--full-text` 仅与 `--show` 一起使用。
检索完成（含未命中、无法可靠归一化）退出 0；参数、输入、连接或查询失败退出 1。
错误使用固定中文说明，不输出堆栈、输入 SQL 或驱动连接细节；查询超时明确提示。
只读账号默认单条语句 120 秒，既有管理员配置仍生效。

### 输出的数据来源

| 内容 | 现有查询来源 |
| --- | --- |
| 完整 SQL 结果、相同原文、换行形式回退 | 直接调用 `service.fingerprint.Service.exact`，不使用 HTTP，不复制指纹或换行算法。 |
| 这次检索 | `mpp_view_search_note` 原有项目及文案；另说明生效筛选、排序、北京时间和换行回退。 |
| 候选、总数及默认身份 | `mpp_view_search`；原文开头取 `mpp_query_text`。最多 50 个结构，总数和显示行数各一次。 |
| 逐条提示 | `mpp_view_hints`；语句总数与前 20 条截断标记来自完整 SQL 结果。逐条命中不等于整批命中。 |
| 原文及原文份数 | `mpp_view_sql_text`，传入候选 `only_sql_id`，与看板进入详情时相同。 |
| 全部身份、首次／末次时间、当前基线存在性 | `mpp_view_identities` 加 `mpp_query_hits` 的首次时间；按词／整段仍以命中原文记录最多的身份为默认。 |
| 当前版本、发布时间、训练窗口 | `mpp_view_versions` 的 `is_current` 行，不用最新发布替代当前生效指针。 |
| 五类计时整体指标及样本条件 | `mpp_view_statistics` 的整体层，省略无样本类别，基础／P95／P99 门槛直接取函数结果。 |
| 默认计时与中文代码名 | `mpp_view_timings`、`mpp_view_label`。 |
| 最近执行、比较与状态计数 | `mpp_view_executions` 最新在前；计数对 `mpp_view_records` 按状态聚合，使用相同身份、计时、原文和时间范围。 |

默认执行窗口是候选链接所用身份最近一次执行往前 7 天，右端加 1 毫秒以包含最后一次，
与看板相同。切换身份后使用该身份最近一次执行。仅一份命中原文或存在相同原文时，
最近执行也限定为该份；基线仍是结构和身份的整体层。失败、取消、超时记录由现有函数随各类别返回。
样本不足的指标仍显示数值，并标明“样本不足，仅供参考”；没有当前基线时明确说明。

存储适配在 `sql_apm/storage/sqlfind.py`，参数与交互在 `cli/sqlfind.py`，显示在 `cli/sqlfind_output.py`。
完整 SQL 复用服务自身的只读事务，随后注释和候选在同一个短只读可重复读事务中取得；
一次详情的各查询也在同一个短事务中完成。等待用户输入时不保留连接或事务。
两次调用之间新导入可能改变标记顺序，详情先显示完整指纹，操作者可改用该指纹固定对象。
这项复用不引入新的重查询，成本证据为 qualitative，不宣称新的性能指标。

### 交互和终端

流程为选择方式、输入、候选、详情；详情可换身份、换计时、看全文、回列表或重新检索。
首次启动明确传入 `--mode` 时跳过方式选择。提示包含可用操作及退出方法。
按词只接受一行；整段和完整 SQL 接受空行、引号、反斜杠、制表符和中文，
粘贴完成后再按一次回车、Ctrl-D 结束；仅把换行统一为 LF，并去掉这次结束操作的一个末尾换行。
终端读取使用标准库非规范模式，避免内核单行缓冲区截断长粘贴；剩余粘贴不进入菜单操作。
输入被拒绝或查询失败后可继续输入；Ctrl-C 取消当前步骤或数据库等待。无需 readline，无历史文件。

输出不分屏、不切换全屏，不使用颜色；终端可用滚轮回看。
80 列起表格行不超过窗口宽度，长表转为带字段名的续行，只有候选的原文示例及身份可以带 `...` 截断。
200 列下候选通常每个一行；详情保留完整身份。对齐文本使用 ASCII 和中文全角字符，
其他字符以 ASCII 转义显示；原文区独立输出，不插入折行或对齐字符。
使用当前输出编码，不能编码的字符以替代符号显示并说明。短命令以 `-B` 启动解释器，
不产生 SQL／结果文件或 Python 缓存；直接调用 Python 子命令时也加 `-B` 禁止解释器缓存。

### 一次配置与撤销

`scripts/sqlfind/configure.py install` 接受主机或 socket、端口、数据库、已有只读角色、schema、
已有密码文件路径。密码文件必须由当前账号拥有且无组／其他人权限，工具不读密码内容、不改密码或数据库账号。
配置保存为 `~/.sqlfind/connection.json`，登录片段仅把 `~/.sqlfind/bin` 加入 PATH；
其中短入口是程序脚本的符号链接。沿用 #59 的账号登录 profile 方式，选择 Bash 当前会读取的
`.bash_profile`、`.bash_login`、`.profile` 中第一个已有文件，无文件则创建 `.bash_profile`。
只追加有标记的 source 片段，不改已有 `psql` 和数据库环境变量。

环境存在 `SQL_APM_DSN` 时优先用它，否则用上述连接配置；`PGPASSFILE` 同样优先于配置文件路径。
连接与配置都没有时用一句中文提示先配置。重复相同配置不改文件；不同配置需先撤销，避免覆盖已有设置。
`uninstall` 删除专属片段与专属文件，恢复原登录文件字节；配置后新增的其他登录设置保留。
工具打印改动的文件，全部位于执行账号 home；不改系统 profile，不把密码放入参数、脚本或登录文件。

### 验证入口

`tests/test_sqlfind.py` 验证参数、原始字节和排版；`scripts/tests/test_sqlfind_configure.py` 验证配置与撤销。
`scripts/db/verify_sqlfind.py` 在私有 PG17 复用合成数据，逐项对照看板函数、只读表摘要和原文，
并调用 `scripts/tests/sqlfind_terminal.py` 检查伪终端流程、两种宽度、粘贴、取消与临时 home 的真实 Bash 登录。
原有 `verify_search.py`、`verify_views.py` 保持原样。
自动检查不替代 PuTTY 人工试用；C28 用户确认是正式请求评审的前置条件。

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
本身不设这个上限。`search exact` 的输入恰好是一个结构指纹值（前后可以有六类空白）、并且不超过 512 KB 时不解析，直接按该指纹查，
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
