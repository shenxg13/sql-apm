# PostgreSQL 物理结构与 MPP 命名

本设计由 [Issue #7](https://github.com/shenxg13/sql-apm/issues/7) 承接
[逻辑契约 1.0.0](../../.project-wiki/contracts/offline-data-contract.md)。
用户在实施前核对了单账号、统计明细、空桶及首版普通表方案，并于 2026-09-26 授权实施。
当前物理结构版本为 `1.1.0`，完整列、类型、空值、约束与索引定义以
[DDL](../../sql_apm/storage/schema.sql) 为准；本页解释映射及责任边界。

这是存储结构与初始化交付，尚未交付业务写入接口、解析器、指纹或统计引擎。
[验证入口](../../scripts/db/verify.py) 通过 psql 写入合成记录，不证明业务算法正确。

## 命名、类型与版本

- 一个项目库、一个项目 schema、一个登录角色，默认均为 `sql_apm`，分别可配置。
  数据库 UTF8，使用 template0、libc 的 C 排序规则；身份文本按精确内容比较。
- ID 使用非空 `text`，由生产者确定，不以 SQL、时间或命令号猜测执行身份。
  同类型主键在项目库内唯一；调用方跨数据集提供稳定且不冲突的 ID。
  逻辑 `dataset_id` 是交接包标识，由后续交接层管理；不充当数据库执行主键。
  `scope` 固定集群、system_kind、profile、contract_version；单个集群不能混用不同来源语义。
- 时间使用 `timestamptz`（PG17 的微秒精度），原始表示保留在证据中；
  HashData 窗口使用显式 UTC+8 转换，不依赖操作系统、数据库或会话的默认时区。
  调用方传入带偏移时间；若未来来源超出微秒精度，先扩展精度策略，不能静默舍入后声称无损。
- 耗时和 17 个统计指标使用不指定 scale 的 `numeric`，存储层不再次取整或量化；
  拒绝负值、NaN、正负 Infinity。标准差／对数的计算精度由后续公式实现版本声明。
  数量使用非负 `bigint`；未知为 NULL，有明确原因，真实零仍为 0。
- 状态采用 `text + CHECK`，逻辑契约枚举变化仍按契约演进；不用数据库 enum 固定未来升级路径。
- `schema_version` 保存结构版本、schema.sql 的 SHA-256 和首次应用时间。
  升级保留 1.0.0 的原始记录并新增 1.1.0 记录，新库只记录 1.1.0；相同版本重跑保留时间；结构版本与契约、Normalization、配置及 Build 版本各自独立。

## 逻辑对象到物理映射

未列出的同名标量字段按[字段字典](offline-data-contract/fields.md)直接落列；
关系数组拆表，派生的反向 ID 列表通过外键查询重建，不双写。

| 逻辑对象／字段 | 物理结构与差异 |
| --- | --- |
| Source | `scope` 保存 system_kind/profile/contract_version；`source` 保存映射、源端版本、时区、声明证据。 |
| File | `source_file`；checksum 拆为 algorithm/value，content_identity 与字节摘要分开；来源下内容身份唯一，locator 可迁移。 |
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
| Build.coverage_index | `mpp_build_coverage`，每组每层一行，computed_keys/empty_keys 为 JSONB 数组；statistic_ids 从结果表得到。 |
| Decision | `mpp_decision`，同构建同解释事件唯一；关联 Build、Occurrence、Fingerprint、Group；rule_evaluations 用 JSONB。 |
| Decision.reasons | `mpp_decision_reason` 按 decision/code 去重，rule_ref 落列，证据用 `mpp_decision_reason_evidence`。 |
| Problem | `problem` + `problem_evidence`；批次、文件、构建、事件定位；尝试的问题关系为 `attempt_problem`，其余 problem_ids 通过定位查询。 |
| Statistic | `mpp_statistic`，见下一节；Build/Group 上下文通过复合外键保持一致。 |
| Publication | `publication`；结果、前一构建、时间、原因分别保存，失败尝试不丢弃。 |
| CurrentVersion | `current_version` 每 scope 一行，引用成功 Publication 的构建及时间；空版本三字段同时 NULL。 |
| Task | `task`；关联结果用 `task_batch`、`task_build`、`task_publication`，同集群引用。 |

`scope`、输入快照、构建和发布核心不要求非 SQL 来源伪造 SQL。
非 HashData 的配置／构建可以没有 normalization_id；HashData 必须具备。
指纹、五项分组、Occurrence、五类覆盖和 17 项指标是本期 HashData 物理结构。
未来其他来源增加自己的执行／分组／结果结构和 profile 规则，不能把合成扩展示例当作已交付适配器。

## MPP 专属结构与版本升级

用户于 2026-09-26 确认按系统独立保存统计结果，并授权本次 MPP 专属表调整。
MPP 是生产 HashData 系统的内部专名，详见[系统称谓](../../.project-wiki/decisions/project-scope.md#已确认的生产系统称谓)。
各系统共享适用的统计计算代码和构建／发布框架，来源专属结果独立落表；
当前只交付 MPP，不预建 Luban、Baichuan 或 TiDB 表，也不抽象公共统计／分组表。

当前共 41 张表，其中以下 14 张由 1.0.0 原名增加 `mpp_` 前缀：

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
`system_kind=hashdata`、`profile=hashdata-csv/1`、逻辑契约 1.0.0 和历史 ID 均不改写。

[冻结的 1.0.0 DDL](../../sql_apm/storage/versions/1.0.0.sql)保持原始字节，
[迁移入口](../../sql_apm/storage/migrate.sql)只支持明确的 1.0.0 → 1.1.0 路径。
入口先完整核对旧 catalog 和版本摘要，再执行表／索引／约束改名，
验证最终 catalog 与新库目标一致后登记版本；同一事务提交，失败整体回滚。
约束名按目标定义对应，处理 PostgreSQL 自动命名的长度截断，不猜测截断后的列名。
已在 1.1.0 的库先完整校验，成功后返回，不重复登记版本。

这是有维护窗口的串行升级，执行前暂停业务写入及相关查询。
DDL 锁等待上限为 5 秒，等待超时回滚；改名不主动扫描、复制或重写业务数据。
小规模验证核对逐表内容、关系 OID／relfilenode 和约束 OID 保留，
不以此声称生产升级耗时或吞吐已经验证。独立统计表也不代表共享实例的资源隔离。
原名 SQL 调用需随版本切换，没有保留旧名兼容视图；精确操作和恢复见
[升级说明](../runbooks/database-initialization.md#从-100-升级到-110)。

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
basic/P95/P99 三项门槛结果在 sufficiency 中，保存实际／要求数量、覆盖类型与数量、
met 和 reasons。CHECK 校验必需字段、数量、覆盖、met 与当前统计一致；
完整理由集合、配置对应及指标公式由业务计算器复核。

Bucket 使用 layer、bucket_date、bucket_number；overall 两键都 NULL，
day/week 只用日期（week 要求周一），weekday/hour 只用整数（1–7／0–23）。
`UNIQUE NULLS NOT DISTINCT` 保证整体及其他桶不会因 NULL 键而重复。
range 为半开区间，week 额外保留 partial_week。

有效样本数为零时所有指标 NULL/no_samples，覆盖及首末时间为空。
真实零耗时有可计算的零指标，CV 和 P95/P50、P99/P50 为 NULL/zero_denominator。
样本不足不清空可计算指标。示例可以回放已有契约的 ST1–ST5，基础样例只有
一次真实执行，却分别产生整体、天、周、星期、小时五行结果。

零有效且零排除的桶可只出现在 empty_keys；有排除数的桶必须显式保存 Statistic。
后续构建器检查每个已知组五层键完整、两集合无交集，查询层展开空桶。
没有实际观察到的分组不预造空组。默认 30 天窗口通常为 67～68 个逻辑桶／分组／构建，
分组已含计时类别；各层样本数不能跨层相加当执行总数。

首版普通表；按构建／分组／桶的唯一索引支持版本查询，group/build/layer 索引支持组历史。
真实容量、分组数和留存期限尚未测量／确认。每日版本持续增长，首次实际入库后评估
每版行数、表／索引占用及查询计划；若需要分区，优先评估按构建月份，而非混合语义的桶日期。
后续分区需要显式迁移及主外键、查询调整，不声称可零成本切换。

## 数据库与应用责任

| 数据库当前保证 | 后续应用必须保证 |
| --- | --- |
| 主外键、关键同集群／同规则引用、唯一事件／分组／结果键 | 来源映射真实、同一事件跨解释只选一套；同指纹不同 SQL 正确归组 |
| 文件逻辑记录定位、尝试与批次文件匹配 | 文件完整、同源同内容成功才能跳过、冲突阻止批次完成，失败尝试原因齐全 |
| 完整 SQL 与 sql_id 同时存在、计时单位与类别、Execute 未配对不能标首取／续取 | SQL 完整性、结果成功证据、可靠配对，证据必需列表非空且归属正确 |
| 数值有限且非负，未知量有原因，时间／状态的行内一致性 | 推算开始精确减法及精度接收、窗口归属、排除时段相交、黑名单与资格规则 |
| 构建与配置／输入引用、决策原因同 code 去重、评估值枚举 | 快照和旧解释不可变；规则内容可解析；included 全部资格可靠且无排除原因 |
| 统计桶合法、唯一，17 指标及空值原因、分位数顺序、门槛结果基本一致 | 17 公式、全窗口计算、活跃日期／周去重和正确归属、排除数量及完整理由集合 |
| 当前指针只引用匹配集群、版本、时间的成功发布记录 | 发布前检查全部结果完整保存、五类覆盖齐全、非空样本、失败保持旧指针；事务原子切换 |
| 历史引用默认 NO ACTION，不级联删除业务记录 | 保留历史、同集群任务串行、忙时拒绝及崩溃恢复；暂不自动清理 |

单账号拥有项目数据和 DDL 权限是已确认行为；数据库约束不意味着 owner 无法主动修改规则。
复杂列表/JSONB 内容、跨行聚合及状态转换不以本次 CHECK 代替业务实现。

## 索引与成本

主键／唯一约束自动建立的索引用于身份和引用；额外索引只覆盖：
来源文件摘要候选、文件尝试、集群／SQL 的执行开始时间、规则下指纹值、
集群构建时间、构建分组决策、组历史统计、集群发布时间。
不为所有 JSONB 建 GIN，不对完整 SQL 文本建 B-tree，不进行真实日志全量入库。

资源证据为 qualitative：普通表及明确索引减少首版维护分支；text ID、numeric、
重复的复合引用列及版本化 Decision 有存储成本。执行原文／明细不按构建复制，
但本版本选择显式保存每个 Build 的 Decision，实际规模后再评估可重放压缩表示。
初始化用无业务数据的临时预期 schema 比较 catalog，代价是重复创建小型空结构；
不扫描业务数据来验证结构，也不在每行写入时运行 catalog 检查。
完整初始化串行；并发初始化、在线升级和生产吞吐不在本次承诺中。

初始化及恢复操作见[操作说明](../runbooks/database-initialization.md)，实测边界见
[验证记录](../reports/postgresql-storage-2026-09-26.md)。
