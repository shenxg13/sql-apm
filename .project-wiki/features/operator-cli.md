---
id: feature.operator-cli
type: feature
status: active
owners:
  - .project-wiki/features/operator-cli.md
updated: 2026-10-15
sources:
  - path: https://github.com/shenxg13/sql-apm/issues/67
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6184745033
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6184734459
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6183967369
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6183765468
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6183022357
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6162457097
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6159484696
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/62
    status: current
  - path: scripts/grafana/install.py
    status: current
  - path: https://github.com/shenxg13/sql-apm/pull/60#issuecomment-6146826478
    status: current
  - path: docs/reports/v100-user-acceptance-2026-10-13.md
    status: current
  - path: https://github.com/shenxg13/sql-apm/issues/59
    status: current
  - path: docs/reports/v100-binary-validation-2026-10-12.md
    status: current
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

### sqlfind 的离线交付（2026-10-16）

[Issue #67](https://github.com/shenxg13/sql-apm/issues/67) 确认将 `bin/sqlfind`（保留执行权限）和
`scripts/sqlfind/configure.py` 加入 v1.1.0 程序包，数据库与终端检查加入独立验收包。
[生产手册](../../docs/runbooks/production-installation.md#11-sqlfind-一次性配置)只写一次性配置与撤销：
以 sfmon 使用生产 socket／端口、sql_apm_ro 和已有受保护密码文件，重新登录后使用短命令。
[sqlfind 使用说明](../../docs/runbooks/sqlfind-guide.md)单独生成 SQLFIND.html，网页指南只增加指向它的链接。
开发机制包见[准备报告](../../docs/reports/v110-preparation-2026-10-16.md)；
[Kylin 部分样本验证](../../docs/reports/v110-target-validation-2026-10-16.md)已完成配置、使用说明示例、交互和看板数值比对。
最多 10,416 份原文的结构，三次伪终端测量的详情／v 中位数为 1.093348／1.498511 秒；
仅代表本轮部分样本，不含 PuTTY 网络／渲染或全量数据。PuTTY 用户验收和正式发布仍待完成。

### sqlfind 运维入口（2026-10-13）

来源：[Issue #62](https://github.com/shenxg13/sql-apm/issues/62) 于 2026-10-12 确认的契约。
实现新增 `sqlfind` 和同名 Python 子命令；开发机自动验证和部分样本环境已具备。
C28 已由用户于 2026-10-15 [明确确认](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6183967369)：
用 PuTTY 以 `sfmon` 新登录运行短命令，80×24 与最大化窗口均正常；该人工证据与自动检查分开记录。

- 运维人员在 SQL APM 主机登录后使用短命令，仓库入口为 `bin/sqlfind`（2026-10-15 用户要求迁移），根目录不留旧入口。
  入口解析符号链接，从 `bin` 上一级定位程序目录和项目 Python；已有 `search` JSON 命令保持兼容。
- 给出输入时一次输出，无输入且标准输入输出都是终端时进入交互。参数、文件、标准输入只能选一个，
  文件与标准输入原样保留 UTF-8 字节中的首尾空白和换行。时间无时区时按北京时间。
- 交互按词读取一行；整段和完整 SQL 粘贴后回车、Ctrl-D，换行统一 LF，结束输入的那次回车不算 SQL。
  多余粘贴不作为菜单指令；Ctrl-C 取消步骤或查询，输入／查询失败可以重试。无 readline 依赖、无历史文件。
- 交互帮助（2026-10-14 用户确认）：最外层方式菜单逐行列出三种方式、`?` 帮助和 `q` 退出。
  候选列表和 SQL 详情的常驻操作提示也每个命令一行；`v` 的各用法分别列出，Ctrl-C 返回提示独立一行。
  方式、候选和详情菜单均支持 `?`，按流程和操作位置分组说明，每个命令独占一行；解释 `o` 排序、`l` 重看列表、
  `i` 身份组合、`t` 计时类别、原文与历史操作，以及回车、Ctrl-D、Ctrl-C 和退出的作用域。
  帮助不访问数据库，不改变当前菜单、筛选、排序、原文或身份／计时选择及历史设置，不分屏、不落盘。
  检索内容输入阶段的 `?` 按字面保留，不触发帮助；启动参数仍通过 `sqlfind --help` 查看。
- 启动帮助分组（2026-10-15 用户确认）：`--help` 分通用启动参数、非交互查询参数、进入交互模式；
  保留两种模式的启动示例，详细菜单操作由 `?` 提供。参数明确适用条件，区分候选时间与历史时间、
  整页详情中的全文与单独全文输出。两种模式的原文与历史查询能力已按[检索主题](sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)对齐。
- 交互输出边界（2026-10-14 用户确认）：有实际输出的命令和检索提交有会话内递增操作编号、命令名及用途的开始标记，
  以相同编号的结束标记和空行收尾，再显示下一轮菜单。设置向导属于发起命令的同一段；失败和取消也说明原因并收尾。
  有效的方式选择（含默认回车和别名）、`n` 新检索、`q` 及列表／详情回车退出直接跳转或退出，不产生空段、不占编号。
  `b` 会重显列表，因此保留输出边界；错误提示和菜单等待时的 Ctrl-C 反馈仍有边界。
  操作编号用于回滚定位，与候选标记、原文编号分开；不复制 SQL 到标题，不落盘，不清屏、不分页。
  一次输出不添加这些标记；`f` 只输出当前原文全文，具体原文范围由[检索主题](sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护。
- 终端退格实现修正（2026-10-14 用户试用反馈）：BS／DEL 同时删除输入与屏幕字符，处理中文、制表符和折行。
  方向键的 CSI／SS3 序列整体忽略，不进入 SQL 或操作命令；当前不提供方向键编辑／历史浏览。
  这是交互输入的缺陷修复，未改变检索规则；自动验证与 PuTTY 用户确认仍分开记录。
- 交互详情支持 `h 历史设置`（2026-10-14 用户确认扩展），条数、时间与最新／最慢排序在会话内保留；
  候选 `o` 调整检索排序，详情 `v`／`v 编号`／`v matched`／`v all` 查看及切换原文范围。逐项提示、取消、恢复默认及
  查询口径由[检索主题](sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护。设置不写入账号配置或历史文件。
- 查询只读、不执行输入 SQL，不写表、不创建任务、不落盘 SQL／结果；短命令在启动解释器前禁用 Python 缓存。
  成功查询含无命中、无法生成可靠指纹退出 0，参数／输入／连接／查询错误退出 1；交互正常退出 0。
  错误使用中文且不带密码或堆栈，查询超时明确说明；下游提前关闭管道时退出 1，标准错误保持为空。
- 账号配置与 #59 一样由 home 登录 profile 引入专属片段，但只增加 PATH，不改已有 `psql` 环境。
  默认连接使用配置的只读账号；环境 `SQL_APM_DSN`、`PGPASSFILE` 和显式 `--schema` 优先。
  既无 DSN 又无配置时提示先配置。配置不读写密码、数据库账号或系统文件。
- `scripts/sqlfind/configure.py install` 只维护 home 的专属目录和一个登录片段，打印改动路径；相同输入幂等，
  `uninstall` 恢复原文件字节并保留后来新增的其他设置。密码仍仅在已有的受保护 passfile 中。
  账号的 `~/.sqlfind/bin/sqlfind` 指向仓库 `bin/sqlfind`；相同配置重跑时可迁移本工具管理且指向同一程序目录旧入口的符号链接。
  迁移只原子替换该链接，保留连接、密码、profile 及撤销记录；非符号链接和其他目标不覆盖，迁移后仍可幂等执行和撤销。
- 不分屏、不进入全屏，支持终端回滚。80 列起表格可用续行保持完整，200 列候选每个一行；
  候选示例、过长身份及原文目录摘要可以标注截断。对齐只用 ASCII／中文全角字符，其他字符转义。
  “这次检索”说明和拒绝提示不参与表格对齐，保留函数返回的文字和操作者词中的符号，只按窗口宽度换行及转义控制字符。
  当前编码不能表示的字符以替代符号显示并提示。重定向默认 100 列，`--width` 可调整。
- 原文显示（2026-10-15 用户确认）：详情原文、`f`、`--view text` 和 `--full-text` 保留制表符、LF 和 CR LF，
  其他 C0（含单独 CR）、DEL、C1（U+0080–U+009F）使用 ASCII 可见转义，并在原文区外说明本次显示中转义的字符数。
  不为原文插入宽度折行，截断仍按原文字符数，库内原文、原文总字符数和编号不变；
  需要逐字节原文时使用既有 `python -m sql_apm search text`。该规则只改变显示，不改变检索、指纹及历史口径。

检索及详情规则由[检索主题](sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)维护，
参数、数据来源与配置文件见[开发说明](../../docs/design/sql-search.md#面向运维的-sqlfind)，
PuTTY 设置、试用环境和撤销见[试用步骤](../../docs/runbooks/sqlfind-trial.md)。
本轮只在开发机实现和验证，离线包清单、随包手册和目标机验证由后续发版 Issue 负责。

### 生产安装账号与交互环境（2026-10-12）

来源：[Issue #59](https://github.com/shenxg13/sql-apm/issues/59) 已确认要求；实现脚本与本机合成验证
不能替代目标机账号、服务和真实重启验收。

- 项目角色 `sql_apm` 与只读角色 `sql_apm_ro` 密码由操作者在终端不回显输入两次，不再随机生成。
  脚本通过本地管理员写入 SCRAM 校验值，受保护文件保存在程序目录外，pgpass 中正确转义冒号和反斜杠。
  输入不一致、空密码或换行会被拒绝；项目不叠加长度和字符限制。两个数据库账号接受任意非空单行密码。
  用户于 2026-10-12 [明确确认](https://github.com/shenxg13/sql-apm/issues/59#issuecomment-6117961397)
  Grafana 的 admin、viewer 保留上游至少 4 字符的限制，不修改固定组件绕过限制。
- postgres 系统用户使用正常 shell，不设置系统登录密码；本地 peer 超级用户不设数据库密码，不开放远程超级用户登录。
- sfmon 登录后裸 `psql` 使用项目属主账号，`psql -U sql_apm_ro` 使用只读账号，均自动引用 0600 的密码文件。
  切换到 postgres 后裸 `psql` 使用本地超级用户。账号 home 的登录 profile 设置 PATH、连接默认值和密码文件，
  不写入全局环境；产品命令、定时器和常驻服务继续显式指定连接。
- 轮换密码按[生产手册](../../docs/runbooks/production-installation.md)重新交互输入，同步交互及服务文件。
  数据库写入失败不替换已有文件；若数据库已更新而文件保存失败，先修正文件权限再以同一密码重试。
- 首尾空格／制表符属于密码原值，不静默去除。R1 发现 Grafana 文件 provider 会 trim 空白，
  因此生成的服务与开发启动入口统一经 `scripts/grafana/install.py start` 每次重读受保护文件；
  admin 保留首尾空白且原文不再作变量展开，数据源在最后一次环境替换中取得原值。
  密码不进入参数、生成配置或登录 profile；数据源密码仅放在 Grafana 子进程环境。
  账号数据库中已初始化的 admin 密码仍通过 Grafana 修改，替换原始文件不会自动轮换账号。

部署、自启、生产资源与验收边界见[运行决定](../decisions/runtime-and-components.md#首个生产版本与二进制部署2026-10-12)。
目标机实测（measured，2026-10-12）：初始 Kylin 二进制安装后的四个账号均通过短密码、冒号和反斜杠登录，
空密码、不一致及 Grafana 上游 1–3 字符拒绝单独保留；裸 psql、远程超级用户拒绝、只读拒写均通过。
登录 profile 存在／暂时移除两种状态各执行一次实际 systemd 每日服务，命令状态、数据源和指纹连接一致，
常驻服务进程号不变，profile 最后按原字节恢复。详见
[二进制验证报告](../../docs/reports/v100-binary-validation-2026-10-12.md)。上述账号结果不代表第三轮用户验收。
第三轮由用户亲自按生产手册操作，并于 2026-10-13 明确确认安装上线及界面验收通过；
其授权公开的原话和范围见[用户确认记录](../../docs/reports/v100-user-acceptance-2026-10-13.md)。

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
  成功指运行跑完并且没有任何失败或被跳过的集群，从未成功过时从第一次运行算起）。运行每得出一个结论就立即写下，
  被停止或被强制终止的运行已经发现的问题照样列出；某一天的问题取最近一次看过那一天的运行的结论，构建和清理的问题取最近一次做到那一步的运行所记录的，
  被跳过、被中止或被强制终止的运行没有看到的日期、没有做到的步骤，不会让问题消失。
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
