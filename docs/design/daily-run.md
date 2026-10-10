# 每日运行开发说明

面向维护者：每日运行由哪些部分组成、数据怎样记录、怎样验证。操作步骤见[操作说明](../runbooks/daily-run.md)，
契约见 [Issue #54](https://github.com/shenxg13/sql-apm/issues/54)，实测见[报告](../reports/daily-run-2026-10-10.md)。

## 组成

| 部分 | 位置 | 说明 |
| --- | --- | --- |
| 命令 | `sql_apm/cli/daily.py` | `daily run`、`daily status`；信号、退出码和输出沿用其他命令的约定 |
| 配置 | `sql_apm/daily/config.py` | 独立的 JSON，引用导入配置和训练配置；连接数据库之前整体校验 |
| 接收目录 | `sql_apm/daily/inbox.py` | 只读文件名和大小；按日期分拣，决定哪些天要导入、哪些已导入、哪些是问题。已导入的日期的文件是否还是导入时的内容，由编排核对 |
| 编排 | `sql_apm/daily/run.py` | 逐集群执行导入、构建、清理、删除原始文件，并记录 |
| 运行记录 | `sql_apm/storage/daily.py`、结构 1.12.0 | 五张表和查询函数 |
| 停止信号 | `sql_apm/daily/interrupt.py` | 运行正在等一条语句时，停止信号也能及时生效 |
| 定时启动 | `daily/systemd/*.template`、`scripts/daily/install.py` | 只生成单元文件，不安装 |
| 传输脚本 | `daily/fetch-logs.sh`、`daily/fetch-logs.conf.example` | Bash，独立于产品命令 |
| 看板 | `scripts/grafana/build_dashboards.py` 的 `status_dashboard` → `grafana/dashboards/mpp-status.json` | 第四个随包看板 |

## 编排的做法

- **复用而不重写。** 每一步调用的就是对应人工命令的函数：导入用 `Importer.run`，构建发布用 `baseline.workflow.run`（即 rebuild），
  清理用 `CleanupStore.execute`。因此导入、判定、统计、发布检查和清理的规则只有一份。
- **每一步是一个既有的任务。** 一个集群的各步依次各自取得、释放集群占用（`task` 表里是 `import_only`、`rebuild`、`cleanup` 三种模式的记录），
  没有新增任务模式。
- **集群正忙。** 轮到一个集群时先看它是否正被别的任务占用（只查 `pg_locks`，自己不取锁，也不写任务记录）。被占用就整个跳过：
  不导入、不构建、不清理，也不删除原始文件，即使这次本来没有任何一步要开任务。之后任何一步遇到 `cluster_busy`，余下的步骤同样不做。
  删除原始文件这一步自己不开任务，执行期间由运行的连接持有同一个集群占用；取不到也按被跳过处理。
- **日批。** 批次标识为 `daily:来源:日期`，同一来源同一天总是同一个批次；清单是首次处理时目录里那一天的全部文件，
  `origin_key` 取文件名。重试、冻结和冲突都落在既有的批次规则上。
- **已导入的日期怎样核对。** 依据是内容，不是文件名和大小。导入时每个文件的 SHA-256 已由导入保存（`source_file`）；
  `mpp_daily_file` 为每个文件名记下它对应的导入内容，以及最近一次读它并确认内容相同的那一刻的五个值：设备号、inode、大小、
  修改时间和状态改变时间（纳秒）。之后每次运行：
  - 五个值都没变的文件视为没动过，不读；
  - 任何一个值变了就重读并算 SHA-256：与导入保存的相同则记下新的五个值，不同则是冲突（`files_changed_after_import`）；
  - 多出导入时没有的文件名、或在没有任何删除决定的情况下少了一部分文件，同样是冲突；
  - 删除原始文件之前，那一天的每个文件一律重读核对，不看五个值。
  文件名和内容取自导入自己已提交的行（批次清单、`source_file`），不依赖运行的记录：运行在导入提交之后、写下自己的记录之前被杀，
  下一次运行照样知道导入了哪些文件、内容是什么，只是没有五个值，于是把它们读一遍。
  开销（本地 55 个真实日志文件，14.73 GiB，measured）：取全部文件的五个值 0.1 毫秒；全部重读并算 SHA-256 21.2 秒，约 1.4 秒／GiB（页缓存已热）。
  平常每个文件只被多读两遍：导入成功后一遍，到期删除前一遍。保留期内约 80 GB 全部重读一遍按这个速度约两分钟，只在文件的时间被整体改动后才会发生。
  已知的边界：内容被改而五个值一个都没变的文件，日常核对看不出来，但删除前的重读会发现，文件不会被删。
- **构建条件。** 构建未关闭，并且该集群没有当前版本，或已导入成功的最新日期（`batch_date` 里已完成批次的最大日期，人工批次也算）
  减当前版本的截止日不小于间隔。截止日取该最新日期。
- **清理。** 先用只读的预览判断有没有过期或未清完的月份，有才执行，所以平时不产生清理任务。拿不到锁（`lock_timeout`）和
  分组行未删完（`cleanup_groups_pending`）记为等待，不算失败；其余算失败。
- **删除原始文件。** 只处理“有标记、批次已完成、日期早于界限或者删除已经开始”的日期，并且先把那一天现有的每个文件重读核对。
  核对通过后，先在 `mpp_daily_file` 写下删除决定（`removing_*`），再逐个删除；每删一个文件，就在同一个事务里把它记为已删除（`removed_*`）
  并把本次运行的文件数、字节数加上；最后删标记并把天数加一。逐个文件解析真实路径，确认是位于接收目录之内的普通文件才删除；符号链接从不跟随。
  因此删除在任何一处被打断（删除失败、被停止、被杀）都能由后面的运行接着做完：有删除决定而已不在目录里的文件算作已删除，
  不会被当成“文件少了”的冲突。删除开始之后新出现的文件、内容变了的剩余文件仍然是冲突，那一天不再继续删。
  各次运行记录里的天数、文件数、字节数加起来，恰好等于实际删除的数量；被杀的那次运行在死之前已写下的部分也在它自己的记录里。
- **单实例。** 运行用自己的一个连接持有会话级咨询锁（`mpp_daily_lock_key()`，含 schema 名），取不到就退出。
  运行记录也写在这个连接上，与各步骤的连接分开。
- **中止。** 停止信号（SIGTERM、SIGINT）在 Python 取得控制时变成 `KeyboardInterrupt`：当前集群记为 aborted，运行记为 aborted，退出码 130。
  运行正在等一条语句（长时间的计算、等锁）时 Python 拿不到控制，所以信号的到达同时由一个线程读取（`signal.set_wakeup_fd`），
  它反复取消各步骤连接上正在执行的语句，直到运行退出；写运行记录的那个连接不取消，中止才能记下来。
  systemd 到达最长运行时间和 `systemctl stop` 发的都是 SIGTERM，走的是同一条路。实测从信号到进程退出不到 0.1 秒。
- **被杀。** 什么也来不及写：运行记录留在 running。查询函数发现没有会话持有运行锁，就把它显示为 unfinished；
  下一次运行开始时把它改写为 unfinished。各步骤自己的残留由既有的任务恢复机制处理，集群占用由连接存活检查释放。
- **待处理问题怎样算。** 一次运行没有做到的步骤，对那一步的问题什么也说明不了。所以每一类问题取“最近一次真正做到那一步的运行”所记录的：
  日期类问题看导入一步是否做完（`import_state='done'`），构建类看构建一步是否做到，清理类看清理一步是否做到。
  被跳过、被中止或被杀的运行不会让它没有碰过的问题消失；问题在后面的运行真正处理掉之后才消失。
  “集群被跳过”取最近一次轮到该集群的运行；“太久没有成功的运行”里的成功指运行跑完并且没有任何失败或被跳过的集群，
  从来没有成功过时从第一次运行算起，运行正在进行时也照样列出。

## 连接存活检查

`sql_apm/storage/ingestion.py` 的 `connect` 是产品唯一的建连处。它在连接建立后执行
`SET client_connection_check_interval`，值来自环境变量 `SQL_APM_CONNECTION_CHECK_SECONDS`（默认 10，0 关闭，上限 3600；
非法值以 `invalid_connection_check_seconds` 拒绝）。用环境变量是因为现有各命令没有共同的配置文件，只有环境是共同的。
该参数是会话级的，普通账号可设，不需要改服务端配置。

## 运行记录

| 表 | 内容 |
| --- | --- |
| `mpp_daily_run` | 一次执行：启动方式、状态、是否有失败、起止时间、当时的本地日期、`stale_after_hours` |
| `mpp_daily_cluster` | 该次执行里的一个集群：状态，以及导入、构建、清理、删除原始文件四步各自的状态、原因和数量，`stage_seconds`。删除的天数、文件数、字节数随删除逐个累加 |
| `mpp_daily_day` | 该次执行里某来源某一天的导入结果、批次标识、文件数、字节数、新增记录数、耗时；成功时附文件名和大小（只作记录） |
| `mpp_daily_problem` | 处理完一个集群后写下的待处理问题（六类），以及接收目录里不合命名的文件（`nonconforming_file`，不是问题） |
| `mpp_daily_file` | 已导入日期的每个文件名一行，不随运行重复：对应的导入内容、最近一次核对时的五个值、删除决定和已删除的时间及运行 |

状态取值：运行为 running、finished、aborted、unfinished；集群为 pending、running、done、skipped、aborted；
导入为 not_reached、incomplete、done；构建为 not_reached、disabled、no_data、not_due、published、no_samples、failed；清理为 not_reached、disabled、nothing、cleaned、pending、failed；
删除原始文件为 not_reached、disabled、nothing、deleted、failed。

查询函数（只读，命令行和看板共用）：

| 函数 | 返回 |
| --- | --- |
| `mpp_daily_runs()` | 全部运行，已把无主的 running 显示为 unfinished |
| `mpp_daily_problems()` | 当前的待处理问题。入表的六类各取最近一次真正做到那一步的运行所记录的；另外现算三类：集群被跳过、最近一次运行没有正常结束、太久没有成功的运行 |
| `mpp_daily_clusters()` | 最近一次运行里的每个集群：最新已导入日期、当前版本的截止日和发布时间（取库里现在的情况）、它在该次运行里的状态、待处理问题数 |
| `mpp_daily_recent(n)` | 最近 n 次运行，每个集群一行 |
| `mpp_view_daily_last()`、`mpp_view_daily_clusters()`、`mpp_view_daily_problems()`、`mpp_view_daily_recent(n)` | 看板用：把代码换成中文、日期写成文本、时间取到秒 |
| `mpp_view_daily_label(kind, code)` | 代码到中文的对照；不认识的代码原样返回 |

`daily status` 输出的是前四个函数的结果，所以它与看板读的是同一份数据。

## 看板

“运行状态”（`mpp-status`）由同一个生成程序产生，只用 PostgreSQL 数据源和表格、数值两种面板，每分钟自动刷新，时间选择器隐藏。
最近一次运行的时间和结果对各集群都一样，所以作为四个数值放在最上面，不做成集群表里每行相同的一列。
三张表的列宽按 1920 像素宽的窗口排定：短列给定宽度，带文字的列平分其余宽度，任何一列的最小宽度都不超过平分所得，
否则表格会横向滚动。其余三个看板的顶部各加了一个到它的链接。

## 定时器与服务单元

服务是 `Type=simple`，用 `RuntimeMaxSec` 限制定时启动的运行；拉取作为 `ExecStartPre=-…`，失败被忽略，
它的时间由 `TimeoutStartSec` 另行限制（同一个数值）。定时器 `Persistent=true`。实测到的 systemd 行为：
运行期间到达的时刻不会并发启动第二个，但定时器在该次运行结束后按上次触发时间重新计算，于是立即补跑一次；
多个错过的时刻也只补一次。这与停机后的补跑是同一个机制。

## 验证入口

| 入口 | 内容 | 需要 |
| --- | --- | --- |
| `python -m unittest discover -s tests` | `tests/test_daily.py`：配置、目录分拣、连接检查间隔、停止信号、单元文件里的连接串；`tests/test_dashboards.py`：四个看板的静态检查 | 无 |
| `scripts/db/verify_daily.py` | 合成日志上的规则：标记、逐天导入、构建间隔、失败隔离、集群正忙、单实例、清理、删除原始文件、连接存活、中止与恢复、运行记录 | PostgreSQL 17 |
| `scripts/db/verify_daily_recovery.py` | 不能丢、不能藏的情形：导入之后文件被改、删除被打断、集群正忙而无事可做、问题跨过被跳过或被中止的运行、“成功”的口径、等语句时收到停止信号 | PostgreSQL 17 |
| `scripts/db/verify.py` | 含 1.11.0 带数据升级到 1.12.0（`tests/database/daily_migration.py`） | PostgreSQL 17 |
| `scripts/daily/verify_fetch.py --sshd-root DIR` | 传输脚本对回环地址上的真实 sshd | 解包的 `openssh-server` |
| `scripts/daily/verify_timer.py [--sshd-root DIR]` | 用户级 systemd 上的定时器行为，约 13 分钟 | systemd 用户管理器 |
| `scripts/grafana/verify_status_e2e.py --directory DIR` | 无界面浏览器核对运行状态看板，含九类待处理问题 | `setup_dev.py synthetic` 的环境和 #51 准备的浏览器 |
| `scripts/daily/verify_replay.py run`／`compare` | 本地真实样本的三种方式回放和最终版本比对 | 本地样本，数小时 |

私有 sshd 的准备（不需要 root，放在忽略目录里）：

```bash
dnf download --repo baseos --destdir var/issue54/downloads openssh-server
mkdir -p var/issue54/sshd && rpm2archive - < var/issue54/downloads/openssh-server-*.rpm | tar -xzf - -C var/issue54/sshd
```

看板检查与 #51 的浏览器共用，但需要项目的解释器（它要直接调用产品代码）：

```bash
source var/issue51/browser/env
PYTHONPATH=var/issue51/browser/venv/lib/python3.13/site-packages \
  .venv/bin/python scripts/grafana/verify_status_e2e.py --directory var/issue54/e2e
```

它会在环境里增加三个集群，因此要在一个新搭的合成环境上运行，或排在 `verify_e2e.py` 之后。
