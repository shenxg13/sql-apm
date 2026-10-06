# PostgreSQL 物理结构与 MPP 命名

本设计由 [Issue #7](https://github.com/shenxg13/sql-apm/issues/7) 承接
[逻辑契约 1.0.0](../../.project-wiki/contracts/offline-data-contract.md)。
用户在实施前核对了单账号、统计明细、空桶及首版普通表方案，并于 2026-09-26 授权实施。
当前物理结构版本为 `1.9.0`，完整列、类型、空值、约束与索引定义以
[DDL](../../sql_apm/storage/schema.sql) 为准；本页解释映射及责任边界。

本页描述存储结构与初始化；#18 的[导入写入器](log-ingestion.md)已适配 1.9.0；#21 的[判定接口](training-decisions.md)复用导入事实，③[统计引擎](baseline-statistics.md)由 #25 交付。
[验证入口](../../scripts/db/verify.py) 通过 psql 写入合成记录，不证明业务算法正确。

## 命名、类型与版本

- 一个项目库、一个项目 schema、一个登录角色，默认均为 `sql_apm`，分别可配置。
  数据库 UTF8，使用 template0、libc 的 C 排序规则；身份文本按精确内容比较。
- ID 使用非空 `text`，由生产者确定，不以 SQL、时间或命令号猜测执行身份。
  同类型主键在项目库内唯一；调用方跨数据集提供稳定且不冲突的 ID。
  逻辑 `dataset_id` 是交接包标识，由后续交接层管理；不充当数据库执行主键。
  `scope` 固定集群、system_kind、profile、contract_version；单个集群不能混用不同来源语义。
- 时间使用 `timestamptz`（PG17 的微秒精度），原始表示保留在证据中；
  MPP 窗口使用显式 UTC+8 转换，不依赖操作系统、数据库或会话的默认时区。
  调用方传入带偏移时间；若未来来源超出微秒精度，先扩展精度策略，不能静默舍入后声称无损。
- 耗时和 17 个统计指标使用不指定 scale 的 `numeric`，存储层不再次取整或量化；
  拒绝负值、NaN、正负 Infinity。标准差／对数的计算精度见③的公式实现设计。
  数量使用非负 `bigint`；未知为 NULL，有明确原因，真实零仍为 0。
- 状态采用 `text + CHECK`，逻辑契约枚举变化仍按契约演进；不用数据库 enum 固定未来升级路径。
- `schema_version` 保存结构版本、schema.sql 的 SHA-256 和首次应用时间。
  升级保留原始历史记录并登记经过的版本，新库只记录 1.9.0；相同版本重跑保留时间；结构版本与契约、Normalization、配置及 Build 版本各自独立。

## 逻辑对象到物理映射

未列出的同名标量字段按[字段字典](offline-data-contract/fields.md)直接落列；
关系数组拆表，派生的反向 ID 列表通过外键查询重建，不双写。

| 逻辑对象／字段 | 物理结构与差异 |
| --- | --- |
| Source | `scope` 保存 system_kind/profile/contract_version；`source` 保存映射、源端版本、时区、声明证据。 |
| File | `source_file`；checksum 拆为 algorithm/value，content_identity 与字节摘要分开；来源下内容身份唯一，locator 可迁移；1.8.0 增加可空 first_log_at／last_log_at。 |
| Batch | `import_batch`；declared_dates → `batch_date`，entries → `batch_entry`。条目最终尝试须匹配 batch/file。 |
| ImportAttempt | `import_attempt`；retry_of/duplicate_of 自引用同一 File。重试不覆盖尝试历史。 |
| EvidenceRecord | `evidence_record`；唯一 (file_id,record_no)，定位行区间正数；observed 为 JSONB 投影。 |
| Analysis | `analysis` + `analysis_file`；保存解释版本、明确文件集、替代关系及不可变证据清单依据。 |
| SqlText | `mpp_sql_text` + `mpp_sql_text_evidence`；完整原文 text，共享文本可追加来源关联。 |
| Occurrence | `mpp_occurrence`；主键 (analysis_id,occurrence_id)，association 拆列，value_reasons 用 JSONB。 |
| Occurrence 的三组证据 | `mpp_occurrence_evidence`，purpose 为 support/outcome/association，主证据另有 anchor_ref 外键。 |
| Normalization | `mpp_normalization`；固定算法、解析器、字典语义／格式版本、规范内容摘要及 rules_ref。 |
| Fingerprint | `mpp_fingerprint`；唯一 (sql_id,normalization_id,profile)，可靠结果与失败原因分开。 |
| Group | `mpp_baseline_group`；cluster_id 映射 scope_id，冗余 fingerprint_value 通过复合外键核对。五项键加规则上下文唯一。 |
| InputSnapshot | `input_snapshot`；列表分别为 `input_batch`、`input_file`、`input_analysis`、`mpp_input_occurrence`；也支持不可变清单引用。 |
| ConfigSnapshot | `config_snapshot`；window 拆列；source_mapping_refs、blacklist、exclusions、thresholds 为包含实际内容的 JSONB。 |
| Build | `build`；同集群输入及配置、重试、状态、结果保存标志；配置／归一化上下文有复合外键。 |
| Build.checks | `build_check`，按构建和六类检查名唯一，保存 passed/failed/not_run 及原因。 |
| Build.timing_coverage | `mpp_build_timing_coverage`，五类计数分别保存；group_ids 从同构建覆盖记录关联 Group 得到。 |
| Build.coverage_index | `mpp_coverage` 从封存窗口与统计行推导；`mpp_build_layer_count` 保存每层行数和分组数供核对；statistic_ids 从结果表得到。 |
| Decision | 首期由 `mpp_training_decisions` 按快照推导，不逐条永久保存；`mpp_decision` 保留历史结构，不由②写入。 |
| Decision.reasons | 函数返回原因数组、规则引用与锚点证据，按事件／原因去重；既有 reason／evidence 表保留。 |
| Problem | `problem` + `problem_evidence`；批次、文件、构建、事件定位；尝试的问题关系为 `attempt_problem`，其余 problem_ids 通过定位查询。 |
| Statistic | `mpp_statistic`，见下一节；Build/Group 上下文通过 mpp_build_group 集中校验并用复合外键引用。 |
| Publication | `publication`；结果、前一构建、时间、原因分别保存，失败尝试不丢弃。 |
| CurrentVersion | `current_version` 每 scope 一行，引用成功 Publication 的构建及时间；空版本三字段同时 NULL。 |
| Task | `task`；关联结果用 `task_batch`、`task_build`、`task_publication`，同集群引用。 |

`scope`、输入快照、构建和发布核心不要求非 SQL 来源伪造 SQL。
非 MPP 的配置／构建可以没有 normalization_id；MPP 必须具备。
指纹、五项分组、Occurrence、五类覆盖和 17 项指标是本期 MPP 物理结构。
未来其他来源增加自己的执行／分组／结果结构和 profile 规则，不能把合成扩展示例当作已交付适配器。

## MPP 专属结构与版本升级

用户于 2026-09-26 确认按系统独立保存统计结果，并授权本次 MPP 专属表调整。
MPP 是这套生产系统的内部统称，详见[系统称谓](../../.project-wiki/decisions/project-scope.md#已确认的生产系统称谓)。
各系统共享适用的统计计算代码和构建／发布框架，来源专属结果独立落表；
当前只交付 MPP，不预建 Luban、Baichuan 或 TiDB 表，也不抽象公共统计／分组表。

1.1.0 共 41 张表，1.2.0 新增 5 张近似观察表，共 46 张。其中以下 14 张由 1.0.0 原名增加 `mpp_` 前缀：

| 原名 | 1.1.0 名称 |
| --- | --- |
| `sql_text` | `mpp_sql_text` |
| `sql_text_evidence` | `mpp_sql_text_evidence` |
| `occurrence` | `mpp_occurrence` |
| `occurrence_evidence` | `mpp_occurrence_evidence` |
| `normalization` | `mpp_normalization` |
| `fingerprint` | `mpp_fingerprint` |
| `baseline_group` | `mpp_baseline_group` |
| `input_occurrence` | `mpp_input_occurrence` |
| `decision` | `mpp_decision` |
| `decision_reason` | `mpp_decision_reason` |
| `decision_reason_evidence` | `mpp_decision_reason_evidence` |
| `build_timing_coverage` | `mpp_build_timing_coverage` |
| `build_coverage` | `mpp_build_coverage` |
| `statistic` | `mpp_statistic` |

27 张其他表保留名称。`config_snapshot.normalization_id` 的外键指向
`mpp_normalization`，`problem` 的执行定位外键指向 `mpp_occurrence`。
这两张公共表仍有 MPP 专属关联，未来其他系统的规则及执行定位另行设计。
各专属表的索引和约束名称同步前缀；指标、列类型、键及约束语义保持不变。
上述 1.1.0 改表名前缀时不改当时的来源标识和历史 ID；1.7.0 的标识变更见下节。

[冻结的 1.0.0 DDL](../../sql_apm/storage/versions/1.0.0.sql)保持原始字节，
[冻结的 1.1.0 DDL](../../sql_apm/storage/versions/1.1.0.sql)同样保持已发布字节；
[迁移入口](../../sql_apm/storage/migrate.sql)支持 1.0.0 → 1.1.0 → 1.2.0 → 1.3.0 → 1.4.0 → 1.5.0 → 1.6.0 → 1.7.0 → 1.8.0 → 1.9.0；1.7.0／1.8.0 可带数据升级，更早版本只允许空库，亦允许从任一已发布中间版本开始。
入口先完整核对旧 catalog 和版本摘要，再执行表／索引／约束改名，
验证最终 catalog 与新库目标一致后登记版本；同一事务提交，失败整体回滚。
约束名按目标定义对应，处理 PostgreSQL 自动命名的长度截断，不猜测截断后的列名。
每一步先验证完整源结构再迁移，连续升级同一事务提交。已在 1.9.0 的库只核验，不重复登记。

这是有维护窗口的串行升级，执行前暂停业务写入及相关查询。
DDL 锁等待上限为 5 秒，等待超时回滚；1.1.0 改名不重写数据，1.2.0 新增
可靠指纹前缀 CHECK 会扫描该列，既有非法 approx: 值使升级整体失败，不删除或改写该行。
小规模验证核对逐表内容、关系 OID／relfilenode 和约束 OID 保留，
不以此声称生产升级耗时或吞吐已经验证。独立统计表也不代表共享实例的资源隔离。
原名 SQL 调用需随版本切换，没有保留旧名兼容视图；精确操作和恢复见
[升级说明](../runbooks/database-initialization.md#升级到-170)。

## 统计结构 1.4.0

[#25](https://github.com/shenxg13/sql-apm/issues/25)确认统计完整保存与分区瘦身。
1.4.0 共 53 张普通／父表，其中 23 张 MPP 专属表（动态叶分区另计）；
两张新增表为 mpp_result_partition 和 mpp_build_group。Build 增加 partition_id 与 diagnostics，
后者保存按状态、计数范围和原因的脱敏计数，包括无法归组的批次层面事实。
[冻结的 1.3.0 DDL](../../sql_apm/storage/versions/1.3.0.sql)保留已发布字节。
2026-09-30 范围变更取消物理 sufficiency；schema 及有效的 1.3.0→1.4.0 迁移同步使用新目标定义。
该调整发生于 1.4.0 交付前，当时没有生产部署，因此未另增版本号；早期验收实例不作为兼容升级起点。
Issue #27 已将最终 [1.4.0 DDL](../../sql_apm/storage/versions/1.4.0.sql)冻结为迁移与结构检查基线；
当前结构为下述 1.9.0，保留 1.6.0 的独立观察统计与覆盖推导，使用统一 MPP 标识。

## 训练判定结构 1.3.0

[Issue #21](https://github.com/shenxg13/sql-apm/issues/21)确认快照及原文结果持久化、
逐条结论按需推导，详见[设计](training-decisions.md)。新增五张表，总计 51 张，其中 21 张 MPP 专属表。

| 新表 | 职责 |
| --- | --- |
| mpp_training_rule | 归一化完整快照、类别边界、模板示例及指纹，内容寻址规则身份。 |
| mpp_training_sql | `(sql_id, rule_id)` 唯一，类别结论和模板候选、可靠／失败 Fingerprint 引用。 |
| input_file_analysis | 每快照每文件唯一 Analysis；精确固定解释集合，复用已有 input 关联表。 |
| input_manifest | 不可变清单内容与封存标记，清单摘要位于 input_snapshot。 |
| training_config | 判定规则版本及完整来源映射，补充既有 config_snapshot。 |

新建和迁移均安装快照／缓存不可变触发器及单一 SQL 判定函数。函数定义、所有权、ACL、
触发器定义和启用状态进入 catalog 检查；不再一概拒绝所有函数，而是拒绝未声明或漂移的函数。
旧 Decision 表、Build、Statistic 和已保存数据均保留；本次不创建 Build 或永久 Decision 行。
Group 身份在②按需推导，③保存统计时可用同一身份落入既有分组表。

[冻结的 1.2.0 DDL](../../sql_apm/storage/versions/1.2.0.sql)保留已发布字节和摘要。
1.2.0→1.3.0 为可靠指纹增加包含 sql_id 的复合唯一键，供缓存外键阻止跨原文引用；
其索引构建会扫描已有指纹，新增表不复制事件。迁移遵守维护窗口、5 秒锁等待及单事务回滚。
此成本用于可靠缓存引用，避免应用写错原文与 Fingerprint 配对；不增加每次查询的逐行审计。

## 近似观察结构 1.2.0

用户于 2026-09-29 确认 [#17](https://github.com/shenxg13/sql-apm/issues/17) 在导入前补齐结构。
逻辑定义和接口逐项映射见[字段字典](offline-data-contract/fields.md#approximateruleapproximateinput-与-approximateresult)。

| 表 | 键、字段及责任 |
| --- | --- |
| mpp_approximate_rule | rule_id 主键；algorithm_version/profile/rules_digest 唯一；rules_ref 与完整 JSONB 规则快照。写入方按接口规范验证摘要。 |
| mpp_approximate_input | input_id 主键；原字节 bytea、长度和 SHA-256 的 CHECK；摘要索引只缩小候选，写入方比较完整字节复用。 |
| mpp_approximate_result | result_id 主键；input_id/rule_id/structural_reason 唯一；状态、值、原因、diagnostics、replacements、observation_only、completeness 和 source_bytes_included。 |
| mpp_approximate_evidence | result_id/record_id 主键及外键；同结果可关联多份证据，无可靠事件也能保存。 |
| mpp_occurrence_approximate | analysis_id/occurrence_id/rule_id 主键；引用同规则结果及已关联证据，复合外键约束事件／证据同 scope；每次事件独立。 |

normalized 为 JSON 文本并检查对象语法，写入方使用 `ensure_ascii=True` 保留非法编码和 NUL
的转义；不直接使用 JSONB。读取时反序列化还原表示，词法问题、开放括号和 unverified 标记
不丢失。数据库验证状态条件与用途，表示内部字段、规则规范摘要、value 重算、原证据实际归属
及同一输入的串行复用由 #18 写入方校验。不能把摘要唯一键或大型 bytea 全文 B-tree 当作复用实现。

available 要求值、表示非空，reason 为空；unavailable／failed 值和表示为空，reason 非空，
replacements 为 0。所有结果都要求算法、规则和结构失败原因，kind=approximate、
observation_only=true、completeness=unverified。rule_id/value 索引只供观察候选查询。

可靠 mpp_fingerprint 新增具名 CHECK 拒绝 approx: 前缀；Group／Decision 的既有外键仍只
指向可靠表，不新增近似引用。其余既有表和约束语义保持不变。残片不写入 mpp_sql_text；
同一结果可供多个事件引用，实际事件计数和训练资格不受复用影响。

导入时先在同一事务写入原字节、规则／结果及证据，再写已可靠识别的事件与引用；事件来源和
证据支持关系由导入器验证，无法识别事件则只保存记录级事实。此流程由 #18 实施，观察统计由 #27 在下述独立结构保存；本次仅提供[临时实例验证](../runbooks/database-initialization.md#近似结果全量往返验证)。

## 原文身份及证据

SQL 完整内容保存为 text，content_sha256 为 32 字节 SHA-256；CHECK 核对摘要与 UTF8 原文一致。
编码转换不能用于 PG17 的 immutable 生成列表达式，因此显式写入并校验摘要。

摘要索引不是唯一索引。后续写入接口须先按摘要找候选，再比较完整原文，
相同内容复用 SQL ID；碰撞时分别保存不同原文。不能将摘要唯一约束或唯一全文 B-tree
当作完整原文身份。文件内容身份亦由导入器确认，不能仅靠摘要或文件名宣布重复成功。

完整原文唯一复用属于后续写入事务责任，当前结构允许明确引用同一文本。
每次真实执行仍独立保存；无法可靠识别事件边界时只保存 EvidenceRecord/Problem。
可靠性不足、不完整 SQL、失败执行和未知耗时都有可表达的状态，不默认可训练。

## 统计明细、空桶及增长

一行对应 Build × Group × Bucket，17 项指标各自为普通 numeric 列；
空值原因在 metric_null_reasons 中，排除原因计数在 exclusions_by_reason 中。
basic/P95/P99 三项门槛结果为逻辑 sufficiency，不再逐行保存物理 JSONB，相关三个 CHECK 随列删除。
`mpp_statistic_sufficiency` 按 Build 引用的封存 ConfigSnapshot 的 thresholds／statistics_version，
以及当前行的层次、数量与覆盖数组，派生全部实际／要求数量、覆盖类型、met 和完整 reasons。
函数显式识别公式版本，不以当前默认门槛解释旧构建；字段语义及查询示例见
[统计设计](baseline-statistics.md#门槛结果的数据库派生)。指标公式仍由③计算器实现并独立复算。

Bucket 使用 layer、bucket_date、bucket_number；overall 两键都 NULL，
day/week 只用日期（week 要求周一），weekday/hour 只用整数（1–7／0–23）。
`UNIQUE NULLS NOT DISTINCT` 保证整体及其他桶不会因 NULL 键而重复。
range 为半开区间，week 额外保留 partial_week。

有效样本数为零时所有指标 NULL/no_samples，覆盖及首末时间为空。
真实零耗时有可计算的零指标，CV 和 P95/P50、P99/P50 为 NULL/zero_denominator。
样本不足不清空可计算指标。示例可以回放已有契约的 ST1–ST5，基础样例只有
一次真实执行，却分别产生整体、天、周、星期、小时五行结果。

零有效且零排除的桶可只出现在 empty_keys；有排除数的桶必须显式保存 Statistic。
③构建器保存每个已知组五层完整覆盖，验收核对键完整、两集合无交集，查询层展开空桶。
没有实际观察到的分组不预造空组。默认 30 天窗口通常为 67～68 个逻辑桶／分组／构建，
分组已含计时类别；各层样本数不能跨层相加当执行总数。

1.4.0 的 mpp_statistic、mpp_build_coverage 均为 LIST 分区父表，按“集群＋构建月份”
创建叶表；每个集群每月每父表一个，保持层次为键，不按构建、层次或计时类别拆表。
两集群全年有构建时约 24 个叶分区／父表。构建月份按 started_at 的北京时间月份确定。
新增 mpp_result_partition 登记紧凑 bigint 分区编号，Build 引用它并校验集群／月份；
新增 mpp_build_group 每构建每组一行，集中保证集群、规则与 profile 一致，结果表复合外键引用。
两张父表因此移除 scope_id、normalization_id、profile 三个重复文本列。

Statistic 去掉 statistic_id 文本代理主键，自然键为
`(partition_id,build_id,group_id,layer,bucket_date,bucket_number)`，使用
`UNIQUE NULLS NOT DISTINCT`。同一 Build 只能属于一个分区；业务身份仍是构建、分组、层次与桶。
Coverage 主键为 `(partition_id,build_id,group_id,layer)`。自然键支持构建内读取；两表均保留
`(group_id,build_id,layer)` 索引支持按组查历史。查询版本时可从 Build 取得分区编号并用于裁剪。

分区函数在每次构建前按需建立当月分区，短锁仅保护本月 DDL；相同月份多次构建不增加分区。
结构检查在预期空 schema 中重建已登记分区，再完整比较边界、父子关系、叶表和索引。
1.3.0→1.4.0 先锁定并确认旧两表都为空，否则整体回滚并报
statistics_migration_requires_empty_results；没有任何自动清理或丢弃旧结果路径。
新增列与两张空表重建之外，原文、执行、快照、Build 和发布数据保留。

## 数据库与应用责任

| 数据库当前保证 | 后续应用必须保证 |
| --- | --- |
| 主外键、关键同集群／同规则引用、唯一事件／分组／结果键 | 来源映射真实、同一事件跨解释只选一套；同指纹不同 SQL 正确归组 |
| 文件逻辑记录定位、尝试与批次文件匹配 | 文件完整、同源同内容成功才能跳过、冲突阻止批次完成，失败尝试原因齐全 |
| 完整 SQL 与 sql_id 同时存在、计时单位与类别、Execute 未配对不能标首取／续取 | SQL 完整性、结果成功证据、可靠配对，证据必需列表非空且归属正确 |
| 数值有限且非负，未知量有原因，时间／状态的行内一致性 | 推算开始精确减法及精度接收、窗口归属、排除时段相交、黑名单与资格规则 |
| 构建与配置／输入引用、决策原因同 code 去重、评估值枚举 | 快照和旧解释不可变；规则内容可解析；included 全部资格可靠且无排除原因 |
| 统计桶合法、唯一，17 指标及空值原因、分位数顺序；函数按指定版本派生完整门槛结果 | 17 公式、全窗口计算、活跃日期／周去重和正确归属、排除数量；查询绑定原构建配置 |
| 当前指针只引用匹配集群、版本、时间的成功发布记录 | 发布前检查全部结果完整保存、五类覆盖齐全、非空样本、失败保持旧指针；事务原子切换 |
| 历史引用默认 NO ACTION，不级联删除业务记录 | 保留历史、同集群任务串行、忙时拒绝及崩溃恢复；暂不自动清理 |

单账号拥有项目数据和 DDL 权限是已确认行为；数据库约束不意味着 owner 无法主动修改规则。
复杂列表/JSONB 内容、跨行聚合及状态转换不以本次 CHECK 代替业务实现。

## 索引与成本

主键／唯一约束自动建立的索引用于身份和引用；额外索引只覆盖：
来源文件摘要候选、文件尝试、集群／SQL 的执行开始时间、规则下指纹值、
集群构建时间、构建分组决策、组历史统计、集群发布时间。
不为所有 JSONB 建 GIN，不对完整 SQL 文本建 B-tree。③全量验收的资源实测见统计验证报告。

初版普通表资源选择为 qualitative；1.4.0 统计分区与完整保存由 #25 确认，
计算资源和容量在该任务真实数据验收中实测。执行原文／明细不按构建复制，
Decision 由②按需推导；③仅在计算期间 TEMP 物化，随事务结束删除。
初始化用无业务数据的临时预期 schema 比较 catalog，代价是重复创建小型空结构；
不扫描业务数据来验证结构，也不在每行写入时运行 catalog 检查。
外键内部触发器在引用表、被引用表两侧均检查非默认启用模式；D／R／A 明确拒绝。
比较异常模式及其约束名，不比较 PostgreSQL 自动生成的触发器名或 OID，
也不要求兼容部分表具有尚未建表的入向外键触发器。此检查不证明历史业务行有效。
完整初始化串行；并发初始化、在线升级和生产吞吐不在本次承诺中。

初始化及恢复操作见[操作说明](../runbooks/database-initialization.md)，实测边界见
[验证记录](../reports/postgresql-storage-2026-09-26.md)。

## 观察统计结构 1.5.0

[#27](https://github.com/shenxg13/sql-apm/issues/27) 新增三张表，共 56 张普通／父表，26 张 MPP 专属表。
`mpp_observation_group` 保存五维及近似规则身份，代表 result_id 与 rule_id/value 的非空复合外键
保证只能引用 available 近似结果；不同规则不会合并。未知计时使用 unknown，仅存排除。
`mpp_build_observation_group` 关联 Build、分区和观察组，批量触发器拒绝跨集群／profile；
被引用观察组不得修改身份，Build 也不能改变其集群／profile 上下文。

`mpp_observation_statistic` 的自然键、17 numeric 指标、计数／覆盖数组与空值约束与正式统计一致，
其外键只指向观察关系表。可靠指纹前缀检查及正式分组／结果外键保持不变。
不保存 sufficiency 或观察覆盖表；正式门槛函数和批量 SQL 不读取观察表。
所有统计仍由应用保证执行去重、真实维度和计时依据，数据库不重新运行资格判断。

`mpp_ensure_result_partition` 同时维护三张父表的集群／构建月份叶分区。
迁移先验证冻结 1.4.0 的完整 catalog（包括已登记动态分区），再增加近似结果复合唯一索引、
观察表及已有月份的空观察叶分区。新增索引读取近似结果；已有正式分区和数据不重写。
仍需维护窗口，失败全事务回滚；新库、升级和重跑共用完整结构检查。

## 编排与覆盖结构 1.6.0

[#29](https://github.com/shenxg13/sql-apm/issues/29)以方案 C 替代物理覆盖表，
保留冻结的 1.5.0 DDL；迁移删除 mpp_build_coverage 并回填 `mpp_build_layer_count`。
逻辑覆盖通过 `mpp_coverage` 按封存窗口推导，正式、观察结果均适用。
父表／普通表总数仍为 56，MPP 专属表仍为 26；覆盖表移除、构建层计数表新增。
Task 新增模式、阶段、数据库时间及阶段耗时，既有任务新增时间不代表历史执行实测。

主要业务写入共用持有集群锁的连接，发布记录与当前指针同事务提交；六项检查核对正式结果，
观察结果不影响发布门槛。新月份独立建表再 ATTACH，构建前确保当月，次月预建遇共享分组表锁忙时跳过并留待后续构建；
旧月份结果和索引继续保留。连接失效、任务恢复及控制成本见[编排设计](build-publication.md)。

## 1.7.0 来源标识统一

[Issue #33](https://github.com/shenxg13/sql-apm/issues/33) 将来源标识统一为 MPP，
不更改表名、列类型或业务语义。新旧标识见[系统称谓](../../.project-wiki/decisions/project-scope.md#已确认的生产系统称谓)。
1.6.0 DDL 按字节冻结；1.7.0 只替换 profile 约束并登记版本，逻辑契约保持 1.0.0。
当前迁移入口在任何历史迁移之前检查旧库是否为空（除结构版本记录外无业务行），
有数据则以 `mpp_naming_requires_empty_schema` 拒绝且整笔事务回滚；不回写标识或换算 ID。
这取代过去“可保留已有导入数据连续升级至当前版”的操作前提；既有数据环境须重建。

历史迁移本身仍按已发布脚本保留。其带数据回归在验收资源中以冻结的 1.6.0 入口执行，
不作为旧库可以带数据升级至 1.7.0 的证据。新建、空库连续升级、带数据拒绝和同版本重跑
均另由当前入口验证。

## 1.8.0 文件实际日志时间

[Issue #35](https://github.com/shenxg13/sql-apm/issues/35) 新增 `source_file.first_log_at` 和
`last_log_at`（timestamptz），命名约束 `source_file_log_time_bounds` 要求同时为空或
同时有值且最早不晚于最晚。导入读取全部日志记录第 0 列，与事件结束时间共用解析函数；
无法解析的时间忽略。与文件成功状态同事务写入，失败回滚；重复文件沿用首次值。

1.7.0 DDL 冻结于 `sql_apm/storage/versions/1.7.0.sql`；新迁移
`sql_apm/storage/migrations/1.7.0-to-1.8.0.sql` 只增加列及约束，允许已有数据，
不回填，两列均为 NULL。旧 DDL 与迁移摘要不变，新库直接建立 1.8.0；更早版本的
非空库仍须按 #33 重建，空库可连续升级。结构检查包含新列和约束。

这两列是导入过程派生的物理元数据，与文件校验和、字节数同类，逻辑契约保持 1.0.0。
可供后续留存清理和导入侧大表分区复用，本次不实现清理或分区。
选批只查询文件元数据，既有事件、SQL 原文、判定算法及统计数据不重写。

## 1.9.0 版本结果留存

[Issue #43](https://github.com/shenxg13/sql-apm/issues/43)确认
[按构建月份留存](../../.project-wiki/contracts/sql-storage.md#版本结果按月留存2026-10-06)。
物理契约增加一张表（共 57 张父表／普通表，其中 27 张 MPP 表），不改变逻辑契约 1.0.0。

| 对象 | 新增行为 |
| --- | --- |
| mpp_result_partition | cleaned_at 表示统计分区已原子移除；groups_cleaned_at 表示分组关联收尾完成。 |
| build | 不增加列、不改历史值；results_saved 与月份标记共同区分 available、results_cleaned 和从未保存。 |
| mpp_cleanup_month | 主键 (task_id, partition_id)，保留逐月结果、构建数、前后字节、释放字节、删除行数及阶段时间。 |
| task | 增加 cleanup 模式及阶段，共用既有集群会话锁、忙时拒绝和残留任务恢复。 |
| 查询函数 | mpp_require_results 取得读锁并检查留存；mpp_read_statistics、mpp_coverage 和批量门槛 SQL 拒绝已清理结果。 |

清理在占用同集群 Task 后先读取月份、当前指针、构建数及叶表大小。逐月在单调时钟的
10 秒锁等待预算内，以 NOWAIT 尝试两张统计父表和两张目标叶表的 ACCESS EXCLUSIVE 锁。
每次冲突回滚整个尝试并释放已取得的锁，短暂停顿后再试，不使用 CONCURRENTLY DETACH。
取得锁后只执行两张叶表 DROP、单行月份标记和单行审计更新，提交后释放排他锁；
不在此事务扫描／删除分组关联，也不逐构建更新。两类结果与标记同一提交点生效。
查询的读锁与该事务冲突，读者只能观察完整旧结果或明确的 results_cleaned。
移除统计叶表不需要对分组关联表申请排他锁；同集群写入已有 Task 互斥，分组删除在后续
批次进行。不取这两把多余的锁，避免被分组表上的 VACUUM／autovacuum 阻挡后续月份。

随后使用现有以 partition_id 开头的主键，按 (build_id, group_id) 游标向前推进，
以有界 ctid 批次最多每批删除 10,000 个关联行；避免 VACUUM 尚未运行时反复扫描已删前缀。
删除计数与该批行删除一起提交，提交成功后才推进游标。锁冲突时回滚该批并重试，不跳过
回滚行、不重复累计计数。分区移除和所有分组批次共用同一月份的剩余等待预算；失败尝试
及退避按单调时钟扣减，成功 DDL／删除批次的工作时间不占此预算，不在切换阶段时重置。
预算用尽后，尚未移除统计时报 cleanup_lock_timeout、该月保持原样；统计已移除时报
cleanup_groups_pending，保留已提交批次，随后重跑收尾。中断时仍保持标记与统计原子可见。
不改组身份，不清理快照、原文、明细或规则缓存。分组按行删除不立即缩小共享表文件，
普通 VACUUM 后可复用；释放字节只统计已 DROP 的两张统计分区及其索引／TOAST。
应用在同集群任务锁下保护当前月份；分区创建函数与构建／分组写入触发器拒绝已清理月份。

构建层数、计时摘要、六项检查和历史发布不重算、不覆盖。批量门槛 SQL 以留存检查为外侧行，内侧参数化 LATERAL 查询沿用原五次门槛校验；
两侧 OFFSET 0 保留执行边界，防止优化器因空分区跳过检查，仍流式输出批量结果。
指标公式和门槛语义不变。
mpp_read_statistics 用于传入 group_id 的单组读取；不传分组时会先物化整个版本的结果，
外层过滤不能避免这项成本。批量读取使用上述守卫和参数化 LATERAL 连接物理表的方式，
具体 SQL 见[操作说明](../runbooks/build-publication.md#版本结果清理)。错误只含固定原因码。

1.8.0→1.9.0 只增加元数据和查询／写入保护函数，保留旧行和 receipt；冻结 DDL 和旧迁移
不改字节。旧迁移的 schema.sql 相对引用在临时目录绑定到对应版本，临时源 catalog 使用
savepoint 回滚释放锁；实际连续升级保持单事务。清理中断、互斥、升级、真实副本及成本
证据由[验证报告](../reports/result-retention-2026-10-06.md)维护。
