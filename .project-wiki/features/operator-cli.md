---
id: feature.operator-cli
type: feature
status: active
owners:
  - .project-wiki/features/operator-cli.md
updated: 2026-10-10
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/54
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/47
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/43
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/41
    status: current
  - path: docs/reports/sql-scanning-2026-10-04.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/29#issuecomment-5937111169
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/29
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/27
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/25
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/21
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/18
    status: current
  - path: .project-wiki/log.md
    status: historical
  - path: docs/reports/knowledge-reorganization-2026-09-25.md
    status: current
related:
  - feature.log-ingestion
  - feature.baseline-versions
  - contract.training-eligibility
  - feature.sql-search-and-views
confidence: high
---

# 命令行与本地配置操作

## Summary

完整流程、只导入、重新构建和版本查询已提供命令，配置由本地文件维护。
设计导入、构建、状态或诊断命令及本地配置时阅读。

## Source Of Truth

以下保留原知识索引各节的用户确认、日期、来源状态、证据限制和待定事项。
这些条款定义目标或事实，不表示相关产品功能已经实现；较早条款中的待定表述
应结合明确链接的后续确认阅读，不能覆盖后续已确认规则。
[知识变更记录](../log.md)用于追溯；涉及 Issue 时以其在线正文和评论核对任务契约。

## Contracts

### 已确认的首期操作入口

- 确认日期：2026-09-25。
- 来源：用户对日志导入、基线构建、任务状态及诊断结果查询统一提供命令行入口，
  来源映射、批次文件清单、黑名单及排除时段使用本地配置文件维护，Grafana
  继续承担既有 SQL 检索与展示范围的建议，选择“命令行与本地配置文件”。
- 来源状态：current；#18 已交付导入命令及 JSON 配置，②训练快照／判定和③统计命令已交付；#29 实现完整编排、发布与版本查询，界面仍待后续实现。
- 首期为日志导入、基线构建、任务状态和诊断结果查询提供命令行入口。
  日志来源映射、导入批次文件清单、黑名单和故障／维护排除时段通过本地配置
  文件维护；具体文件格式、字段及目录在实施时确定。
- 命令行及配置处理沿用既有来源登记、完整文件、批次完整性、同集群串行、
  安全重试、固定构建输入及发布检查规则；选择命令行不改变这些业务约束。
  构建仍记录实际使用的配置依据，配置变更按已确认规则用于后续构建。
- Grafana 继续承担 SQL 检索、基线与执行历史展示；不增加导入、构建、黑名单
  和排除时段的网页管理要求。任务管理 API 不作为本次确认的首期必要入口；
  SQL 检索服务接口与 Grafana 具体插件仍按原范围另行选型。
- 命令、参数、输出格式及配置关联见本页各实施节和对应操作说明。
  完整流程与分步使用的业务范围已按下述规则确认；本次不创建可执行命令、
  实际配置项或定时任务，后续 SCP 及其触发方式仍按既定阶段另行实施。

关联条款：[已确认的集群标识与日志来源映射](log-ingestion.md#已确认的集群标识与日志来源映射)；[首期导入批次的完整性判定](log-ingestion.md#首期导入批次的完整性判定)；[已确认的同集群任务串行与忙时处理](baseline-versions.md#已确认的同集群任务串行与忙时处理)；[已确认的构建输入与配置固定](baseline-versions.md#已确认的构建输入与配置固定)；[已确认的首期统一入口展示范围](sql-search-and-views.md#已确认的首期统一入口展示范围)。

### 已确认的完整流程与分步使用

- 确认日期：2026-09-25。
- 来源：用户对提供一次执行导入、构建、检查及满足条件后生效的完整流程命令，
  同时保留“只导入”和“复用已完成导入数据重新构建”两种用法的建议回复“确认”。
- 来源状态：current；#18 已交付只导入，#29 实现完整阶段编排；流程职责沿用本节确认。
- 完整流程：用户确认批次文件齐全后，显式执行一次命令，依次完成导入、基线
  构建和检查；满足既有发布条件时自动生效。进入构建前，清单中所有文件仍须
  导入成功或符合既有同来源、相同内容且此前已成功导入的跳过规则。
- 只导入：执行日志导入，完成后不构建或切换基线；批次及文件状态仍按既有
  规则记录，失败或未完成的导入不因此成为可用构建输入。
- 重新构建：复用已完整成功导入的数据，为选定训练窗口重新计算基线，通过
  检查且满足发布条件后生效。每次固定实际输入及配置；计算失败后重试仍从头
  计算选定窗口，无需仅因计算失败而重复导入 CSV。
- 同集群串行规则覆盖完整流程及各分步用法，不能利用阶段切换绕过任务互斥。
  某阶段失败时停止后续步骤，保留当前生效版本及诊断记录；可可靠隔离的记录级
  问题仍按既有规则处理，不自动升级为整个导入阶段失败。
- 样本不足不单独阻止生效；整窗五类计时均无有效样本时仍保留当前版本，不能
  因命令流程完成而覆盖原有发布限制。
- 这里的自动生效属于用户显式发起的本次命令流程，不新增定时调度、自动重试
  或排队要求。（2026-10-10：定时启动和下一次运行自动重试由[每日运行命令](#每日运行命令2026-10-10)提供；`full`／`rebuild` 本身仍不调度、不重试、不排队。）具体命令、参数及阶段状态见[操作说明](../../docs/runbooks/build-publication.md)。

关联条款：[首期导入批次的完整性判定](log-ingestion.md#首期导入批次的完整性判定)；[已确认的同集群任务串行与忙时处理](baseline-versions.md#已确认的同集群任务串行与忙时处理)；[已确认的基线计算失败重试范围](baseline-versions.md#已确认的基线计算失败重试范围)；[已确认的五类计时统一版本与发布边界](baseline-versions.md#已确认的五类计时统一版本与发布边界)。

### 已实现的“只导入”命令

[Issue #18](https://github.com/shenxg13/sql-apm/issues/18) 提供 `python -m sql_apm import`，
JSON 本地配置、显式 `--source`／`--batch` 和计数／原因码输出。
参数、身份冻结、文件冲突和重试操作见[导入操作说明](../../docs/runbooks/log-ingestion.md)。
此命令只执行导入；完整流程、重新构建及发布由 #29 的独立命令提供。

### 已实现的训练快照与诊断入口（2026-09-29）

[Issue #21](https://github.com/shenxg13/sql-apm/issues/21)落实②的本地 JSON 配置与
`python -m sql_apm training snapshot`／`training summary`。模板示例、可选身份限定、
北京时间半开时段及窗口参数的格式、固定快照 ID 和脱敏输出见
[操作说明](../../docs/runbooks/training-decisions.md)。
此入口固定输入／配置、复用原文缓存及汇总数据库按需判定；统计由③的 statistics 命令交付；完整构建、重新构建和发布
由 #29 的④命令实现。用户维护具体模板条目，初始为空；本次不提供类别增删入口。

## 已实现的统计命令

Issue #25 提供 `python -m sql_apm statistics --cluster ... --input ... --config-id ...`，
对②已封存快照创建构建并保存五层结果；输出仅含计数、固定原因和不透明标识。
可引用同快照失败尝试的 `--retry-of`，从头计算；这不交付④的完整重建流程。
参数、恢复和退出码见[操作说明](../../docs/runbooks/baseline-statistics.md)。

Issue #27 将独立观察统计纳入同一命令和事务；输出增加 `observations` 计数及原因汇总，
参数保持不变。观察结果不带充足性结论，失败与正式结果一起回滚；
[观察操作说明](../../docs/runbooks/baseline-statistics.md#观察统计与全量验收)解释归属及规则间计数边界。

### 完整流程与版本查询（2026-10-01）

来源：[Issue #29](https://github.com/shenxg13/sql-apm/issues/29) 的已确认操作边界。
`full` 依次导入、封存快照、计算、检查和有条件发布；默认截止日取本批声明覆盖日期的最后一天，
`--cutoff-date` 可覆盖。`rebuild` 复用成功导入的数据并重新封存，必须显式给出 `--cutoff-date`。
对较早批次执行 full 会使窗口回到该批日期；补导时由操作者显式指定截止日，用户已于 2026-10-02 确认，
详见[发布输入与补导规则](baseline-versions.md#发布输入与补导确认2026-10-02)。
`status` 显示当前版本、最近任务及最近未发布原因，`history` 显示历史成功版本。
按 [Issue #34](https://github.com/shenxg13/sql-apm/issues/34)，`status.current` 还显示
`fingerprint_normalization_timeout` 和 `fingerprint_normalization_worker_failed` 两个整数，
0 也显式返回。单位是当前版本固定输入中的隔离日志记录，不是执行或不同 SQL 数；
后来导入但尚未纳入当前版本的文件不参与计数。没有当前版本时仍为 `current=null`。
查询只读且仅展示，不新增发布检查；字段示例与边界见
[隔离记录计数](../../docs/runbooks/build-publication.md#隔离记录计数)。

五种原有写入入口及 cleanup --execute 共用集群任务占用，包括独立的 import、training snapshot 和 statistics。
忙时返回 `cluster_busy` 并保存 busy_rejected 任务；不会排队或中断持有者。
按 [Issue #41](https://github.com/shenxg13/sql-apm/issues/41) 的确认补充现有占用边界：
进程被强制终止后，在其数据库会话退出之前集群仍被占用，新任务仍返回 `cluster_busy`。
当前连接未设置存活检查参数；若当时正在执行语句，占用持续到该语句结束。
确认旧会话已退出后人工重新运行；无手工解锁命令，不自动等待、排队或重试。
查看占用会话与确认退出的方法见[占用与恢复](../../docs/runbooks/build-publication.md#占用与恢复)。
是否调整连接存活检查等参数留到配置每日自动任务时另行确认，本次不改变产品连接或任务行为。
2026-10-10 修订（#54）：产品连接已打开存活检查，默认 10 秒；上面“占用持续到该语句结束”只在把间隔设为 0 时成立，见[每日运行命令](#每日运行命令2026-10-10)。
只有 import／full 首次登记集群；其余写入入口及 cleanup 预览对未登记集群返回 unknown_cluster，不新增集群或任务。
任务保留模式、阶段、产物、时间和原因，查询仅输出标识、时间、计数及原因码。
参数及 JSON 示例由[操作说明](../../docs/runbooks/build-publication.md)维护；
失败／零样本发布行为继续由[版本要求](baseline-versions.md)维护。

### 版本结果清理入口（2026-10-06）

[Issue #43](https://github.com/shenxg13/sql-apm/issues/43) 提供 `cleanup --cluster CLUSTER
--training-config FILE [--execute]`，支持 --schema。默认预览全部月份的保留／过期／保护／
已清理状态、历史发布／未发布构建数和两种统计分区字节，不占用任务、不写记录。
执行逐月报告结果、删除行数与释放空间，退出码为成功 0、配置文件内容错误／执行失败／
部分未完成 1、命令行参数解析错误 2、人工中断 130；固定原因码不变。
此区分由[维护者整改决定](https://github.com/shenxg13/sql-apm/issues/43#issuecomment-6019720422)
明确，沿用其余五个入口的约定；脱敏范围仍为标识、月份、时间、计数、大小和固定原因码。

训练 JSON 的可选顶层 retention 包含默认 months 及 clusters 覆盖，值为最小 1 的整数，
省略默认 2，覆盖键须列在顶层 clusters，未知键拒绝。此配置不进入封存快照，不影响
训练规则标识。full 增加 expired_result_months，0 也显示，包含过期但受当前版本保护的
月份；full／rebuild 均不执行清理。具体示例、等待和恢复见
[操作说明](../../docs/runbooks/build-publication.md#版本结果清理)，配置校验见
[训练配置](../../docs/runbooks/training-decisions.md#结果保留配置)。

### 检索与详情命令（2026-10-07）

[Issue #47](https://github.com/shenxg13/sql-apm/issues/47) 实现 `python -m sql_apm search`：
`find` 为文本／完整指纹入口，`exact --sql/--file` 为完整SQL或批次，`baseline` 为摘要或指定层次／版本，
`executions` 为明细／翻页或小时／天汇总，`versions` 列可见版本，`text --sql-id` 按需取原文。
全部输出JSON，不写任务或回填指纹；环境连接沿用SQL_APM_DSN。
字段、参数及退出码由[开发说明](../../docs/design/sql-search.md)维护；匹配和展示规则见
[检索主题](sql-search-and-views.md#检索与查询层2026-10-07)。用户试用另有步骤，Grafana待后续交付。

2026-10-08 修订（[Issue #51](https://github.com/shenxg13/sql-apm/issues/51)，已实现）：`search find` 增加
`--mode words|passage`，默认按词；按词只按空白切、引号是普通字符，原“引号表示整段”的写法作废，
连续片段用 `--mode passage`；按词和整段的输入超过 256 KB 时以 `search_input_too_large` 拒绝；`search exact`
的输入恰好是一个结构指纹值时直接按该指纹查（2026-10-09，R1 评审后）。新增常驻的 `python -m sql_apm fingerprint-service`（只监听本机，供 Grafana 的
完整 SQL 检索使用），规则见[检索主题](sql-search-and-views.md#grafana-检索与看板2026-10-08)，接口见
[开发说明](../../docs/design/grafana-dashboards.md#指纹服务)。导入、构建、清理和状态查询仍只用命令行。

### 每日运行命令（2026-10-10）

来源：[Issue #54](https://github.com/shenxg13/sql-apm/issues/54) 的已确认契约、[需求确认记录](https://github.com/shenxg13/sql-apm/issues/54#issuecomment-6092182097)和[2026-10-10 的范围变更确认](https://github.com/shenxg13/sql-apm/issues/54#issuecomment-6092385134)；来源状态 current。
已实现并在开发机验证。

- `python -m sql_apm daily run --config FILE [--trigger manual|timer]` 运行一次即退出，不常驻。它按导入配置 `clusters` 的顺序逐个处理集群，
  一个集群做完导入、构建、清理和文件删除后再轮到下一个，不并行：逐天导入有齐全标记的日期、按构建间隔至多构建发布一次、
  自动清理过期的版本结果、删除到期的原始文件，并把运行记录写入数据库。规则分别见
  [日志获取](log-ingestion.md#每日运行的日志获取与齐全标记2026-10-10)、[构建](baseline-versions.md#每日运行中的构建2026-10-10)和
  [留存](../contracts/sql-storage.md#每日运行中的自动清理与原始文件删除2026-10-10)。
- 同一时间只允许一个每日运行，第二个以 `daily_run_active` 立即退出且不改动数据。集群正被其他任务占用时，本次运行跳过该集群并记录，不等待；
  被跳过的集群什么都不动，包括不删除它的原始文件，即使这次它没有要导入、构建或清理的内容。
  一个集群的失败不影响其他集群。
- 退出码沿用既有约定：0 为全部完成且没有失败（包括没有可做的事、整窗没有有效样本、清理因拿不到锁留到下次）；
  1 为存在失败的日期、构建或发布失败、有集群被跳过；2 为参数错误；130 为被中止。输出为逐行 JSON，只含原因码、计数、日期、
  文件名和标识，不含 SQL 原文、库名和用户名。
- 收到停止信号时命令停下当前步骤，把这次运行记为被中止；命令正在等一条语句或等锁时也一样，信号到达后各步骤连接上的语句被取消
  （开发机实测从信号到退出不到 0.1 秒，measured）。定时启动的运行超过最长时间、`systemctl stop` 走的是同一条路。
  被强制终止或断电时来不及记录，那次运行显示为未正常结束。
  两种情形下一次运行都照常接上：已成功导入的文件不重复导入，构建从头计算。
- `python -m sql_apm daily status [--limit N]` 只读，不占用集群，显示最近若干次运行、各集群现状和当前的待处理问题。
  待处理问题有九类：导入失败或有冲突的日期、有文件没标记的日期、有标记没文件的日期、标记日期不早于当天的日期、等待清理的月份、
  上次运行被跳过的集群、尚未成功的构建或发布、上次运行被中止或未正常结束、太久没有成功的运行（默认 48 小时，可配置；
  成功指运行跑完并且没有任何失败或被跳过的集群，从未成功过时从第一次运行算起）。每一类问题取最近一次真正做到那一步的运行所记录的：
  被跳过、被中止或被强制终止的运行没有做到的步骤，不会让问题消失。
  同一份数据也由 Grafana 的[运行状态看板](sql-search-and-views.md#运行状态看板2026-10-10)展示；第一版不做主动通知。
- 每日运行的配置是独立的 JSON，引用导入配置和训练配置；未知键和非法取值在连接数据库之前拒绝。构建间隔默认 1 天，自动清理默认开，
  原始文件保留天数默认 45。现有命令的参数、输出和退出码不变。
- **连接存活检查。** 自本次起所有产品连接都打开存活检查，间隔由环境变量 `SQL_APM_CONNECTION_CHECK_SECONDS` 给出，默认 10 秒，0 关闭。
  进程被强制终止后，集群占用在约一个间隔内释放，不再取决于当时那条语句还要执行多久。这回答了下文 #41 留下的待定。
- 定时启动用 systemd 的定时器和服务单元模板，安装是可选的一步，见
  [运行决定](../decisions/runtime-and-components.md#每日运行的运行方式2026-10-10)。命令和步骤见
  [操作说明](../../docs/runbooks/daily-run.md)，开发细节见[开发说明](../../docs/design/daily-run.md)。

## Workflows

按任务涉及的边界补读：

- [日志导入、来源与异常处理](log-ingestion.md)。
- [基线构建、版本与发布](baseline-versions.md)。
- [训练资格、黑名单与排除时段](../contracts/training-eligibility.md)。
- [SQL 检索、Grafana 与历史展示](sql-search-and-views.md)。

## Failure Modes

不能把候选建议、历史观察或待验证实现当成已确认且已交付的行为；
本页各节保留的限制及关联条款共同约束相应任务。

## Update Rules

本主题的确认内容只在本页维护；其他入口保留链接。跨主题变更同步实际受影响的
条款，按[知识维护方法](../methods/knowledge-maintenance.md)记录来源与变更。

## Open Questions

完整流程、重新构建和查询参数见上述操作说明；自动调度由每日运行提供（#54），未增加网页管理。
Grafana 的离线安装、使用者文档和目标机实测由 #52 交付。
