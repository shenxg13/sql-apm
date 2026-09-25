# 离线数据契约字段字典

本页与[契约正文](../../../.project-wiki/contracts/offline-data-contract.md)共同定义逻辑格式 `1.0.0`。
字段名是模块交接语言，不要求一对象一物理表。所有枚举均为本版本封闭集合，诊断说明文本除外。
以下表中未标可选的字段均必填；`?` 表示可以显式 null，`[]` 表示列表，空列表不表示未知。
组合字段逐项应用所列类型；引用记为 `ref(Type)`。一个交接包可仅含本边界的对象，
但包外引用必须可从指定不可变数据集解析，不能只留下机器路径。

## 共用类型

| 类型 | 定义与约束 |
| --- | --- |
| id、text | 非空字符串；id 不透明，text 原文不作 trim／改写；无值用约定的 null |
| instant | 带明确 UTC 偏移的时间点；样例使用 ISO 8601，HashData 转成 `+08:00`，不损失来源精度 |
| date | 北京时间自然日期 `YYYY-MM-DD`，不是时间戳截断前的其他时区日期 |
| decimal | 有限十进制数；JSON 样例用字符串；耗时非负，0 与 null 分开 |
| count | 非负整数；定位序号和必需非空列表另设下限 |
| digest | `{algorithm: text, value: text}`；记录实际算法和对应内容，算法选择不在本设计固定 |
| evidence_ref | `ref(EvidenceRecord)`；问题无法划定 CSV 边界时改用文件级定位，不虚构 record_no |
| interval | `{start: instant, end: instant}`，start < end，含开始不含结束；执行零时长用两个相等时间点表示，不冒充配置区间 |
| reason | 可公开的解释文本；不把原始 SQL、身份或凭据嵌入公开诊断 |
| metric | `{value: decimal?, reason: null/no_samples/zero_denominator}`；有值时 reason=null，null 时必须有原因 |

交接包头 `contract_version: text`、`profile: text`、`dataset_id: id` 必填。
HashData profile 为 `hashdata-csv/1`；样例的 `batch-job-example/1` 仅示范独立来源结构。
常规对象均有各自 `*_id`，同一交接包共享头部上下文，不要求每行重复存版本。

## 输入和导入

### Source

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| source_id | id | 固定日志来源；改路径不换身份 |
| system_kind | text | HashData 为 `hashdata`；未来来源独立定义 |
| scope_id | id | HashData 的逻辑计算集群标识；与本来源产生的 cluster_id 相同 |
| mapping_ref | text | 不可变人工来源映射配置的版本／内容引用；不能只有可变文件路径 |
| declared_build | text | 已确认源端版本谱系；不伪称已经读取私有源码 |
| timezone | text | 本 profile 为 `UTC+08:00`；解释 CST 必须有来源确认 |
| declaration_evidence | text | 人工登记、版本／时区确认的可追溯依据 |

### File

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| file_id | id | 某来源下一份不可变内容的逻辑身份 |
| source_id | ref(Source) | 去重和关联所在来源 |
| content_identity | text | 经内容一致性规则确认的身份凭据；实现不得只用文件名或 SQL |
| checksum | digest | 原文件字节摘要；与文本／指纹摘要分开 |
| byte_count | count | 原文件字节数 |
| locator | text | 本地可定位路径／归档引用；可迁移，不能成为身份 |
| closed_and_copied | bool | 人工确认源端已结束写入且拷贝完成；false 时不启动导入 |
| declaration_evidence | text | 完整文件确认依据 |

### Batch

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| batch_id | id | 某集群一次人工提交清单 |
| scope_id | id | 本批次唯一集群 |
| declared_dates | date[]，非空 | 人工声明覆盖日期；不等于实测覆盖 |
| files_confirmed_complete | bool | 人工确认所需文件齐全 |
| entries | `{file_id: ref(File), final_attempt_id: ref(ImportAttempt)?}[]`，非空 | 清单唯一 file_id；未处理时引用可空；尝试的 batch_id 和 file_id 须相符 |
| state | pending/processing/complete/failed/conflict | 程序清单处理结论，不能仅由人工声明推出 complete |
| problem_ids | ref(Problem)[] | 批次问题和未解决冲突；问题数量不等于执行数量 |

### ImportAttempt

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| attempt_id、batch_id、file_id | id、ref(Batch)、ref(File) | 同一文件重试有新尝试 ID |
| retry_of | ref(ImportAttempt)? | 原失败或中断尝试，同一个 file_id；首次为空 |
| state | pending/running/succeeded/duplicate_skipped/failed/interrupted/conflict | succeeded 可含已可靠隔离的记录级问题 |
| started_at、finished_at | instant?、instant? | 未开始为空，终态须 finished_at；运行中 finished_at=null |
| duplicate_of | ref(ImportAttempt)? | 仅 duplicate_skipped 必填，引用同源同内容且 succeeded 的先前尝试 |
| reliable_record_count | count? | 确定边界且处理过的逻辑记录数；未知不能填 0 |
| problem_ids | ref(Problem)[] | 错误、冲突及定位；failed/interrupted/conflict 须有原因记录 |

同一 File 的 EvidenceRecord 在重试时复用身份；实现可用事务／幂等写入等手段达成，
本契约只要求成功输入对后续计算可见一次。跨批次引用同一成功文件也不得再次计入输入。

### EvidenceRecord

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| record_id | id | 来源记录引用名，对应唯一 `(file_id, record_no)` |
| file_id、record_no | ref(File)、正整数 | CSV 逻辑记录从 1 开始；与物理行不同 |
| line_start、line_end | 正整数、正整数 | 物理行闭区间，line_start <= line_end |
| decode_state | decoded/invalid | 观察字段能否无损解码；无效字节仍由 File 原字节定位保留 |
| observed | map(两位列序号 → string?)，原始列允许空字符串 | 原始列的无损值，序号见映射；允许仅投影下游所需列；无法解码列为 null 并注明问题 |
| problem_ids | ref(Problem)[] | 该记录问题；原始 CSV 空字段、未投影字段及无法解码要在问题／映射中区分 |

`observed` 不保存推算开始、训练资格或猜测的状态。投影缺少列不得被当作原 CSV 为空。
需要回放的列由完整 File 与定位恢复；不要求把每版所有原始字段复制到数据库。

## 解释、身份和训练

### Analysis

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| analysis_id | id | 一套不可变来源解释结果 |
| file_ids | ref(File)[] | 本次解释的明确文件集合，去重 |
| profile | text | 来源格式／语义 profile，与输入一致 |
| mapping_version、parser_version、association_version | text 各一 | 实际版本／不可变内容引用；版本字符串必须能追溯对应定义 |
| supersedes | ref(Analysis)? | 新解释代替哪次解释；不修改被替代对象 |
| evidence_manifest | text | 文件与记录选择的固定清单／谓词依据；样例可指向明确的 record_id 集合 |

### SqlText

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| sql_id | id | 完整原文身份，不能作为指纹 |
| text | text | 可靠源 SQL 的完整精确内容，保留格式及 `$n` |
| evidence_refs | evidence_ref[]，非空 | 原文来源；同文复用可以增加来源关系而不修改原文 |

缺失、不完整或不可解码的文本保存在 EvidenceRecord／原文件中，不创建看似完整的 SqlText。
词法闭合不证明客户端文本完整；完整性结论及支持范围由 Occurrence 的 sql_state 和 Analysis 说明。

### Occurrence

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| occurrence_id、analysis_id | id、ref(Analysis) | 复合引用确定此解释版本中的一次实际事件 |
| scope_id、source_id | id、ref(Source) | 集群和来源必须与主证据一致 |
| anchor_ref | evidence_ref | 该次事件的主计时／终止证据；只能支持一个同单位事件 |
| evidence_refs | evidence_ref[]，非空 | 含 anchor_ref 及配对／错误证据，限定其影响范围 |
| unit | request/call | 请求与阶段／调用分开；阶段不是新增 request |
| request_shape | single/batch/unknown | SQL 单条／完整批次粒度；与调用所属文本形态分开于 unit |
| database、execution_user | text? 各一 | 日志可可靠取得的身份；不明为空并诊断，不能创建正常 Group |
| sql_id | ref(SqlText)? | sql_state=complete 必须有值；其他状态必须为空 |
| sql_state | complete/missing/incomplete/invalid_encoding/uncertain | SQL 原文可靠性；不表示 SQL 执行结果 |
| timing_type | request/execute_first/execute_fetch/parse/bind/null | null 时 timing_reason 必填，非 null 时为空 |
| timing_reason | reason? | 未知／范围外类别依据 |
| outcome | success/failed/cancelled/timed_out/unknown | 本次 request/call 的结果；不是整个 portal 状态 |
| outcome_evidence_refs | evidence_ref[] | 确定结果时非空；unknown 可空，不从缺少错误推定成功 |
| association | `{state: reliable/unpaired/ambiguous, method: text, evidence_refs: evidence_ref[], reason: reason?}` | reliable 需证据且 reason=null；其余需原因；方法属于 Analysis 固定版本 |
| end_at、duration_ms、estimated_start_at | instant?、decimal?、instant? | 可靠结束、耗时、推算开始；不可靠数值置空并通过 value_reasons 解释 |
| start_basis | end_minus_duration/null | 有推算开始才为 end_minus_duration |
| value_reasons | map(字段名 → reason) | 上述三量为 null 时各有原因；时间可算时必须满足减法 |

`unit=request` 只能对应 request 或未知；其余四类为 call。不确定 Execute 可保留
call 和主证据，但 timing_type=null；不能用 unpaired 同时给出 execute_first/fetch。
结果明确且文本完整不自动代表 association 可靠。

### Normalization 与 Fingerprint

| 对象．字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| Normalization.normalization_id | id | 固定规则组合的身份 |
| Normalization.algorithm_version、parser_version | text 各一 | 指纹算法与 SQL 解析能力版本，和来源记录解析器区分 |
| Normalization.dictionary_schema_version | 正整数 | 当前字典格式 1，非本数据契约版本 |
| Normalization.dictionary_rules_version | text | 当前字典语义版本 1.0.1；历史可以引用旧版 |
| Normalization.dictionary_digest | digest | 现有字典工具返回的规范内容摘要，不是任意文件字节摘要 |
| Normalization.rules_ref | text | 不可变规则内容引用，摘要本身不能替代解释规则 |
| Fingerprint.fingerprint_id | id | 一次规则组合下的文本指纹结果身份 |
| Fingerprint.sql_id | ref(SqlText) | 完整原文；缺失 SQL 不伪造 Fingerprint，Decision 记录 sql_missing |
| Fingerprint.normalization_id | ref(Normalization) | 与 Group 和 Build 一致 |
| Fingerprint.state | reliable/unsupported_syntax/normalization_failed | 不支持不等于 SQL 执行失败 |
| Fingerprint.value | text? | 仅 reliable 必填；非 reliable 必须 null |
| Fingerprint.reason | reason? | 非 reliable 必填；可靠时 null |

### Group

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| group_id | id | 五项逻辑键在规则上下文中的引用 |
| profile、normalization_id | text、ref(Normalization) | 相同值只有在同来源语义及同规则下可比较 |
| cluster_id、database、execution_user | id、text、text | 固定集群／日志身份；禁止空值 |
| fingerprint_id | ref(Fingerprint) | 必须 reliable；它的 value 是第四项键，不能拿 sql_id 替代 |
| timing_type | request/execute_first/execute_fetch/parse/bind | 第五项键 |

不同原文在同规则下可有相同 fingerprint.value，Group 可引用其中任一可靠结果作为身份依据，
Decision 使用自己的 fingerprint_id，校验其 value 与 Group 一致；不要求 sql_id 一致。
`request_shape` 的区分由结构身份保证，不额外增加业务分组键。

### Decision

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| decision_id、build_id | id、ref(Build) | 本次构建对事件的筛选解释 |
| occurrence_ref | `{analysis_id: ref(Analysis), occurrence_id: id}` | 版本固定的事件解释 |
| fingerprint_id、group_id | ref(Fingerprint)?、ref(Group)? | 可失败／为空；只有可靠归组时 group_id 有值 |
| state | included/excluded/unresolved/outside_window | 分别是实际训练、明确排除、暂不训练、窗口外 |
| in_window | bool? | 开始未知为 null；false 对应 outside_window，不能计窗口排除 |
| count_scope | group/batch/none | group 需可靠 Group、事件及时间；batch 为无法可靠分桶问题；窗口外为 none |
| reasons | `{code: text, rule_ref: text, evidence_refs: evidence_ref[]}[]` | 多重已知原因；同 code 不重复；rule_ref 定位固定配置／契约条款 |
| rule_evaluations | map(规则名 → matched/not_matched/not_evaluated) | 规则名为 outcome、duration、association、sql、fingerprint、blacklist、exclusion_interval、window；全部列出，表示各不合格条件是否命中 |

原因码：`execution_failed`、`execution_cancelled`、`execution_timed_out`、`outcome_unknown`、
`duration_unknown`、`association_unreliable`、`timing_unknown`、`sql_missing`、`sql_incomplete`、
`sql_encoding_invalid`、`sql_uncertain`、`fingerprint_failed`、`identity_missing`、`start_unknown`、
`blacklist_category`、`blacklist_template`、`excluded_interval`、`outside_window`。
新增会影响决策的原因遵守契约枚举演进规则。

included 需 in_window=true、count_scope=group、全部必要资格可靠、无排除原因；
excluded／unresolved 至少一个原因，不删除已知事实；outside_window 必有对应原因。
未取得 SQL／时间时相关规则 not_evaluated，不能称未命中黑名单／维护时段。
黑名单有明确命中同时又存在可靠性问题时 state=excluded 仍可保留其他原因；
仅有可靠性不足而没有明确业务／失败排除时用 unresolved；两者都不参与训练。

### Problem

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| problem_id | id | 问题记录身份，不是执行身份 |
| level | record/file/batch/build | 影响范围，可靠界定才可用 record |
| batch_id、file_id、build_id | ref(Batch)?、ref(File)?、ref(Build)? | 对应层级必须有定位；record 层需 batch_id 与 file_id |
| evidence_refs | evidence_ref[] | 已知可靠记录边界；文件损坏可为空 |
| occurrence_ref | Occurrence 复合引用? | 仅可可靠归属时填写；否则 null |
| code、reason | text 各一 | 诊断原因；code 是说明标签，不隐式改变训练或发布行为 |
| effect | isolate_record/fail_file/hold_batch/block_publication/informational | 影响明确，不用严重级别推断效果 |
| count_unit、count | log_record/file/problem、count | 明确诊断计数单位；不冒充执行数 |
| resolution | open/isolated/resolved | isolated 仅适用于能可靠隔离的记录范围；resolved 保留解决依据 |
| resolution_evidence | text? | resolved 必填，其余可空；不预设人工核对后的自动合并方案 |

## 构建、统计和发布

### InputSnapshot 与 ConfigSnapshot

| 对象．字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| InputSnapshot.input_id、scope_id | id 各一 | 固定输入范围及集群 |
| InputSnapshot.batch_ids、file_ids、analysis_ids | 对应 ref[] 各一 | 已完成批次、成功内容、选定解释；列表去重 |
| InputSnapshot.selection | `{kind: explicit/immutable_manifest, refs: Occurrence复合引用[], manifest_ref: text?}` | explicit 列出精确事件；manifest 必须可重现等价集合，非可变查询或只有时间水位 |
| InputSnapshot.frozen_at | instant | 固定输入时刻，与历史窗口截止日不同 |
| ConfigSnapshot.config_id、scope_id | id 各一 | 不可变配置快照及集群 |
| ConfigSnapshot.source_mapping_refs | text[] | 本次所有来源映射版本；内容须可追溯 |
| ConfigSnapshot.normalization_id | ref(Normalization) | 整窗统一规则 |
| ConfigSnapshot.window | `{cutoff_date: date, days: 正整数, start: instant, end: instant}` | 默认 days=30，含 cutoff 当天，起止计算须一致 |
| ConfigSnapshot.blacklist | `{category_ref: text, template_ref: text, category_rules: text[], template_rules: text[]}` | 实际不可变规则依据及内容；首期模板可为空，类别规则引用 #2 的准确边界，不靠首词粗匹配 |
| ConfigSnapshot.exclusions | `{rule_id: id, scope_id: id, start: instant, end: instant, reason: text}[]` | 人工配置半开排除时段，无配置为空列表 |
| ConfigSnapshot.thresholds | map(层次 → `{basic_count: count, p95_count: count, p99_count: count, coverage_kind: none/active_days/active_weeks, coverage_min: count}`) | 五层独立，应用到五类；记录实际参数，不引用可变默认值 |
| ConfigSnapshot.statistics_version | text | 公式及数值精度／容差策略的不可变定义；本版语义为下表公式，后续实现应追加其数值策略依据 |

样本门槛默认值从[统计要求](../../../.project-wiki/contracts/baseline-statistics.md)引用：
整体／跨天小时 30、200、1000 且 7 活跃日；逐天只检查数量；逐周加 3 活跃日；
跨周星期加 4 个有该星期样本的自然周。它们可配置，不能按窗口不够自动降低。

### Build

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| build_id、scope_id | id 各一 | 一次构建尝试即一个输出版本，集群独立 |
| input_id、config_id | ref(InputSnapshot)、ref(ConfigSnapshot) | 固定且同集群 |
| retry_of | ref(Build)? | 人工重算所参考的失败尝试；不是恢复其半成品 |
| state | running/calculated/failed/interrupted | calculated 表示全部必需结果计算成功，不表示发布 |
| started_at、finished_at | instant、instant? | 运行中 finished_at=null，终态必填 |
| results_saved | bool | 必需结果及构建依据是否全部可靠保存 |
| checks | map(检查名 → `{state: passed/failed/not_run, reason: text?}`) | batch_complete、rules_consistent、results_complete、results_saved、counts_consistent、values_consistent 全部具备；passed 时 reason=null |
| timing_coverage | map(五类 → `{included_count: count, excluded_count: count, group_ids: ref(Group)[]}`) | 五类齐全；各值取本类别各组整体统计的和，不能叠加五层；不因缺请求整体就判整窗零样本，不跨类相加成执行总数 |
| statistic_ids | ref(Statistic)[] | 所有已计算逻辑结果；物理稀疏保存需 coverage_index 可展开 |
| coverage_index | `{group_id: ref(Group), layer: layer, computed_keys: Bucket键[], empty_keys: Bucket键[]}[]` | 每个已知 Group 五层齐全；两列表无交集，合并等于该层窗口所需键 |
| problem_ids | ref(Problem)[] | 失败／中断有原因；孤立记录问题不强制构建失败 |

整体所需键为 null；逐天为窗口每个日期；逐周为与窗口相交各周一；星期 1–7；小时 0–23。
empty_keys 仅表示已完成计算、零有效且零排除的桶；有排除数即必须有显式 Statistic。
无已知 Group 时 coverage_index 可以为空，timing_coverage 仍保留五类零值与诊断原因。
calculated 但 results_saved=false 允许表达保存失败，此时对应检查失败并禁止发布。

### Statistic、Bucket 与门槛结果

| 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| statistic_id、build_id、group_id | id、ref(Build)、ref(Group) | 同版本同组同桶唯一 |
| bucket | 见下表 | 单一时间层次，无日×小时等额外组合 |
| included_count、excluded_count | count 各一 | 本范围有效／排除事件数，单位由 Group.timing_type 决定 |
| exclusions_by_reason | map(Decision原因码 → count) | 同事件同原因一次，合计可大于 excluded_count |
| active_dates、active_week_starts | date[] 各一 | 有效样本推算开始日期及自然周去重集；不把只有日志的日／周算入 |
| first_sample_at、last_sample_at | instant? 各一 | 有效样本推算开始的最小／最大值；0 样本均 null |
| metrics | map(下述全部指标名 → metric) | 不允许漏掉不可计算指标；单位由固定字段定义 |
| sufficiency | map(basic/p95/p99 → ThresholdResult) | 三项均计算；不足仍保留 metrics |

| Bucket 字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| layer | overall/day/week/weekday/hour | 独立的五层之一 |
| key | null/date/整数 | overall=null；day=具体日期；week=周一日期；weekday=1–7；hour=0–23 |
| range_start、range_end | instant 各一 | overall／weekday／hour 为整个窗口边界；day 为该日；week 为与窗口相交边界 |
| partial_week | bool? | 仅 week 必填；自然周未被窗口完整覆盖为 true，其他层 null |

weekday／hour 的 range 只是外部窗口边界，实际集合还必须按对应 key 筛选。
ThresholdResult 为 `{required_count: count, actual_count: count, coverage_kind,
required_coverage: count, actual_coverage: count, met: bool, reasons: text[]}`。
实际量取本 Statistic；none 的 required／actual_coverage 均为 0；理由仅用
`sample_count_below_min`、`active_days_below_min`、`active_weeks_below_min`。
met 当且仅当全部条件满足，met=true 时 reasons 为空；它不声明异常判断可用。

| 指标名 | 单位 | 公式引用的精确定义 |
| --- | --- | --- |
| min_ms、max_ms、mean_ms | ms | 最小、最大、sum/n |
| p25_ms、p50_ms、p75_ms、p90_ms、p95_ms、p99_ms | ms | Type 7：排序后从 1 开始位置 `1+(n-1)*p`，相邻线性插值 |
| stddev_ms | ms | 总体标准差，除以 n |
| cv | 无 | stddev/mean，mean=0 为 null/zero_denominator |
| mad_ms | ms | median(abs(x-median(x)))，不缩放 |
| iqr_ms | ms | p75-p25 |
| log_median、log_mad | 无 | 先对每个毫秒值算 ln(1+x)，再分别取中位数及未缩放 MAD |
| p95_p50、p99_p50 | 无 | 相应分位数/p50，p50=0 为 null/zero_denominator |

n=0 时全部 17 项指标 null/no_samples，覆盖集为空；n=1 时分位数等于样本、
stddev/MAD/IQR/log_MAD 为 0，其他指标按各自分母处理。数值不因门槛不足置空。
输入中未知 duration 不进入有效样本，不把它当成 0 参与均值。

### Publication、CurrentVersion 与 Task

| 对象．字段 | 类型／条件 | 含义 |
| --- | --- | --- |
| Publication.publication_id、scope_id、build_id | id、id、ref(Build) | 此集群一次发布决定／尝试 |
| Publication.previous_build_id | ref(Build)? | 尝试前生效版本，无旧版为 null |
| Publication.result | published/no_samples/check_failed/publish_failed | published 需 calculated、保存成功、检查全过、至少一类有样本 |
| Publication.reason | text? | published 为 null，其他必填；no_samples 只用于计算和检查成功的五类零有效样本 |
| Publication.at | instant | 发布成功／失败／不切换决定的时间 |
| CurrentVersion.scope_id | id | 每个集群一个当前指针，读取者以此为准 |
| CurrentVersion.build_id | ref(Build)? | 最后成功整体发布的版本，无版本为 null |
| CurrentVersion.last_success_at | instant? | 与该次 published 时间一致；失败／空结果不能刷新 |
| CurrentVersion.publication_id | ref(Publication)? | 当前指针对应成功发布；无版本时为空，失败尝试从历史独立查询 |
| Task.task_id、scope_id | id 各一 | 一次显式操作和集群 |
| Task.mode | full/import_only/rebuild | 完整流程、只导入、复用已导入输入重建 |
| Task.state | running/succeeded/failed/interrupted/busy_rejected | 忙时拒绝不修改既有任务；不自动排队 |
| Task.stage | import/build/publish/none | 当前或最后阶段；忙时为 none |
| Task.batch_ids、build_ids、publication_ids | 对应 ref[] 各一 | 实际产生的结果，未进入阶段为空 |
| Task.busy_task_id | ref(Task)? | 仅 busy_rejected 必填，同集群运行中任务 |
| Task.reason | text? | failed/interrupted/busy_rejected 必填 |

Task.succeeded 仅表示本次操作流程完成，仍须查看 Publication：只导入可无发布，
零样本流程可正常完成但不切换。中断占用恢复机制留待实现，不能凭时间到期假定可并发接管。
CurrentVersion 更新必须原子可见整个版本；任何发布失败都保持原三个引用／时间值。
