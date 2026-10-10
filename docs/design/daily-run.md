# 每日运行开发说明

面向维护者：每日运行由哪些部分组成、数据怎样记录、怎样验证。操作步骤见[操作说明](../runbooks/daily-run.md)，
契约见 [Issue #54](https://github.com/shenxg13/sql-apm/issues/54)，实测见[报告](../reports/daily-run-2026-10-10.md)。

## 组成

| 部分 | 位置 | 说明 |
| --- | --- | --- |
| 命令 | `sql_apm/cli/daily.py` | `daily run`、`daily status`；信号、退出码和输出沿用其他命令的约定 |
| 配置 | `sql_apm/daily/config.py` | 独立的 JSON，引用导入配置和训练配置；连接数据库之前整体校验 |
| 接收目录 | `sql_apm/daily/inbox.py` | 只读文件名和大小；按日期分拣，决定哪些天要导入、哪些已安定、哪些是问题 |
| 编排 | `sql_apm/daily/run.py` | 逐集群执行导入、构建、清理、删除原始文件，并记录 |
| 运行记录 | `sql_apm/storage/daily.py`、结构 1.12.0 | 四张表和查询函数 |
| 定时启动 | `daily/systemd/*.template`、`scripts/daily/install.py` | 只生成单元文件，不安装 |
| 传输脚本 | `daily/fetch-logs.sh`、`daily/fetch-logs.conf.example` | Bash，独立于产品命令 |
| 看板 | `scripts/grafana/build_dashboards.py` 的 `status_dashboard` → `grafana/dashboards/mpp-status.json` | 第四个随包看板 |

## 编排的做法

- **复用而不重写。** 每一步调用的就是对应人工命令的函数：导入用 `Importer.run`，构建发布用 `baseline.workflow.run`（即 rebuild），
  清理用 `CleanupStore.execute`。因此导入、判定、统计、发布检查和清理的规则只有一份。
- **每一步是一个既有的任务。** 一个集群的各步依次各自取得、释放集群占用（`task` 表里是 `import_only`、`rebuild`、`cleanup` 三种模式的记录），
  没有新增任务模式。任何一步遇到 `cluster_busy`，该集群余下的步骤本次都不做，记为被跳过。
- **日批。** 批次标识为 `daily:来源:日期`，同一来源同一天总是同一个批次；清单是首次处理时目录里那一天的全部文件，
  `origin_key` 取文件名。重试、冻结和冲突都落在既有的批次规则上。
- **已导入的日期怎样核对。** 导入成功后记下那一天各文件的名字和大小（`mpp_daily_day.files`）。之后每次运行只比对名字和大小，
  不重读内容：保留期内两个集群约 80 GB，每天全部重读一遍的代价与它防的风险不相称。运行在导入之后、写下自己的记录之前被杀时，
  退回到批次冻结清单里的文件名。
- **构建条件。** 构建未关闭，并且该集群没有当前版本，或已导入成功的最新日期（`batch_date` 里已完成批次的最大日期，人工批次也算）
  减当前版本的截止日不小于间隔。截止日取该最新日期。
- **清理。** 先用只读的预览判断有没有过期或未清完的月份，有才执行，所以平时不产生清理任务。拿不到锁（`lock_timeout`）和
  分组行未删完（`cleanup_groups_pending`）记为等待，不算失败；其余算失败。
- **删除原始文件。** 删除前重新扫描目录；只删“有标记、批次已完成、文件自导入后未变、日期早于界限”的日期。逐个文件解析真实路径，
  确认是位于接收目录之内的普通文件才删除；符号链接从不跟随。
- **单实例。** 运行用自己的一个连接持有会话级咨询锁（`mpp_daily_lock_key()`，含 schema 名），取不到就退出。
  运行记录也写在这个连接上，与各步骤的连接分开。
- **中止与被杀。** 停止信号在 Python 取得控制时变成 `KeyboardInterrupt`：当前集群记为 aborted，运行记为 aborted，退出码 130。
  被杀时什么也来不及写：运行记录留在 running。查询函数发现没有会话持有运行锁，就把它显示为 unfinished；
  下一次运行开始时把它改写为 unfinished。各步骤自己的残留由既有的任务恢复机制处理。

## 连接存活检查

`sql_apm/storage/ingestion.py` 的 `connect` 是产品唯一的建连处。它在连接建立后执行
`SET client_connection_check_interval`，值来自环境变量 `SQL_APM_CONNECTION_CHECK_SECONDS`（默认 10，0 关闭，上限 3600；
非法值以 `invalid_connection_check_seconds` 拒绝）。用环境变量是因为现有各命令没有共同的配置文件，只有环境是共同的。
该参数是会话级的，普通账号可设，不需要改服务端配置。

## 运行记录

| 表 | 内容 |
| --- | --- |
| `mpp_daily_run` | 一次执行：启动方式、状态、是否有失败、起止时间、当时的本地日期、`stale_after_hours` |
| `mpp_daily_cluster` | 该次执行里的一个集群：状态，以及构建、清理、删除原始文件三步各自的状态、原因和数量，`stage_seconds` |
| `mpp_daily_day` | 该次执行里某来源某一天的导入结果、批次标识、文件数、字节数、新增记录数、耗时；成功时附文件名和大小 |
| `mpp_daily_problem` | 处理完一个集群后写下的待处理问题（七类），以及接收目录里不合命名的文件（`nonconforming_file`，不是问题） |

状态取值：运行为 running、finished、aborted、unfinished；集群为 pending、running、done、skipped、aborted；
构建为 not_reached、disabled、no_data、not_due、published、no_samples、failed；清理为 not_reached、disabled、nothing、cleaned、pending、failed；
删除原始文件为 not_reached、disabled、nothing、deleted、failed。

查询函数（只读，命令行和看板共用）：

| 函数 | 返回 |
| --- | --- |
| `mpp_daily_runs()` | 全部运行，已把无主的 running 显示为 unfinished |
| `mpp_daily_problems()` | 当前的待处理问题。每个集群取最近一次“处理过它”的运行所记录的；另外现算两类：最近一次运行没有正常结束、太久没有正常结束的运行 |
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
| `python -m unittest discover -s tests` | `tests/test_daily.py`：配置、目录分拣、连接检查间隔；`tests/test_dashboards.py`：四个看板的静态检查 | 无 |
| `scripts/db/verify_daily.py` | 合成日志上的规则：标记、逐天导入、构建间隔、失败隔离、集群正忙、单实例、清理、删除原始文件、连接存活、中止与恢复、运行记录 | PostgreSQL 17 |
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
