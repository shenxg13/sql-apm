# SQL APM 生产升级手册

适用于已有数据的正式 v1.3.0（结构 1.14.0）原地升级到 v1.3.1（结构 1.15.0）。
安装根仍为 `/data/sql-apm`。程序、验收文件及四个随包看板更新；配置、密码、运行时、
sqlfind 配置、每日运行单元和数据库目录继续使用。新实例见[生产安装手册](production-installation.md)。

## 1. 选择维护窗口

选择每日运行结束后、下一次运行开始前。通知使用者暂停检索和手工产品命令，关闭 sqlfind。
升级入口检查正在运行的产品任务和每日运行；发现忙碌会拒绝，不能以终止正常任务的方式强行升级。
Grafana、指纹服务和已有每日运行定时器会暂停，数据库服务保持运行；升级后恢复原有状态。
未安装的每日运行单元保持未安装，原本关闭的定时器保持关闭。

**本次不备份数据库。迁移提交后不能回退到 v1.3.0。**
迁移只替换 `mpp_query_refresh`、`mpp_query_merge_totals` 两个函数，不改表、索引或业务数据。
提交前失败会自动回滚，旧程序和数据库保持原状，恢复服务后可以继续使用；
提交后不提供降级脚本，问题由后续修复版本处理。必须在第 4 步明确确认后才会继续。

停写时间包含停止服务、结构检查和迁移、替换程序、自检及恢复服务，人工核对另计。
Issue #82 开发机带历史库迁移为 **0.624 秒**（继承的 measured），只是数据库迁移，不是总停写时间。
验证机本版升级入口的命令耗时、完整停写与空间实测见[升级验证报告](../reports/v131-upgrade-2026-11-11.md)。
生产尚未测量；安排维护窗口时另留文件核验、人工核对及异常处理时间，不把开发机耗时作为生产承诺。

空间预检使用 `1073741824 + 2 × P` 字节，其中 `P` 是新程序和验收目录中全部文件的字节和。
1 GiB 是函数定义、catalog／WAL、升级记录的工作余量；两倍 `P` 用于暂存和替换，旧程序留在记录目录中。
本次没有数据回填、排序、建索引或数据重写，预算不随事件数增长，不使用上一版的回填模型，
也不为预检扫描历史事件。它是保守的工作预算（modeled），不是 WAL 总量的硬上限。
安装根和数据库目录所在文件系统分别检查；外置 WAL 或 tablespace 会拒绝，须单独评估。

导入预期应区分证据：#82 开发机带历史配对相对 v1.2.0 多 **19.59%**（measured），
生产规模估算为 **+19.6%～+21.6%**（modeled）。单次测量的缓存条件不统一；
v1.3.0 文档的“+18.3%”只是空库四文件测试，不能代表生产带历史导入。
生产效果须以升级后第一次每日运行前后的采集实测判定；目标为每条记录耗时不超过旧版参照的 1.2 倍，
窄记录 `seq_scan` 增量为 0。实测不达目标时记录数值，由用户决定后续处理。
积压多天时总耗时可能仍较长，应比较每条记录耗时和实际覆盖日数。
来源与更正见[本版发布说明](../releases/v1.3.1.md)及 [#82 验证报告](../reports/query-refresh-2026-11-11.md)。

本版不带出生产升级记录；结构检查、自检以用户确认为准，采集 JSON 的程序／结构身份作为佐证。
升级后必须先完成第 5 步末尾的基准采集，再进行第一次每日运行。
确认下一次定时触发留下足够时间完成核对和采集；时间不足则选择另一个维护窗口。

```bash
systemctl list-timers sql-apm-daily.timer --all --no-pager
```

预期看到下一次触发时间；未安装定时器时没有该行。升级入口会恢复原来的定时器状态。

## 2. 解包并核对交付

使用交付方提供的完整离线包、独立 SHA-256 摘要和固定提交。以原执行账号登录，具备 sudo 权限。
以下准备目录在安装根之外，必须是全新目录。先创建执行账号可写的准备目录，再传入离线包：

```bash
set -euo pipefail
test ! -e /data/sql-apm-upgrade-v1.3.1
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /data/sql-apm-upgrade-v1.3.1
```

将离线包放在该目录中，不覆盖旧安装包。

```bash
set -euo pipefail
export APM_ROOT=/data/sql-apm
export APM_UPGRADE=/data/sql-apm-upgrade-v1.3.1
export APM_EXPECTED_COMMIT='填写交付记录中的40位提交'
export APM_EXPECTED_BUNDLE_SHA256='填写交付记录中的64位完整包摘要'
export PYTHONDONTWRITEBYTECODE=1
export PGPASSFILE="$APM_ROOT/private/pgpass"
export SQL_APM_DSN="host=$APM_ROOT/socket port=5432 dbname=sql_apm user=sql_apm"
unset PGPASSWORD PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR
cd "$APM_UPGRADE"
[[ "$APM_EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$APM_EXPECTED_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$APM_EXPECTED_BUNDLE_SHA256" offline-bundle.tar.gz | sha256sum -c -
test ! -e offline-bundle
test ! -e app
test ! -e verification
tar -xzf offline-bundle.tar.gz
(cd offline-bundle && sha256sum -c SHA256SUMS)
test "$(cat offline-bundle/PROGRAM_COMMIT)" = "$APM_EXPECTED_COMMIT"
tar -xzf offline-bundle/program.tar.gz
tar -xzf offline-bundle/verification.tar.gz
(cd app && sha256sum -c SHA256SUMS)
(cd verification && sha256sum -c SHA256SUMS)
test "$(cat app/PROGRAM_COMMIT)" = "$APM_EXPECTED_COMMIT"
test "$(cat verification/PROGRAM_COMMIT)" = "$APM_EXPECTED_COMMIT"
```

预期全部摘要通过，程序和验收目录的提交一致。包内 `UPGRADE.html` 是本手册，
`RELEASE.html` 是发布说明及离线文档入口。不要执行新安装手册里的初始化和密码设置。

## 3. 只读前置检查

```bash
"$APM_ROOT/app/.venv/bin/python" "$APM_UPGRADE/app/scripts/deployment/upgrade.py" check \
  --root "$APM_ROOT" --candidate "$APM_UPGRADE/app" --verification "$APM_UPGRADE/verification"
```

预期输出旧版本 v1.3.0、新版本 v1.3.1、程序提交、服务状态、额外空间预算和可用空间，
最后为 `OK: preflight passed; no installation changes made`。
检查旧程序清单、固定运行时逐文件摘要、1.14.0 结构、任务空闲、服务状态、路径及空间。
固定运行时归档摘要为 `d271d6a1471c41723724b48afad59f9675e1b324747fbb7dfae283f97340314c`，
已安装清单与该归档的固定清单绑定，不重新编译或安装依赖。

失败时停下，按错误处理版本、损坏文件、忙碌任务、空间或权限。此步骤不停止服务、不写升级目录、
不修改程序和数据库。不要修改程序清单、版本回执或放宽检查来绕过拒绝。
连接或认证失败时，每个子命令以非零码退出并输出一行 `STOP: database connection or authentication failed` 开头的说明，
不打印原始 traceback 或连接凭据。检查本地数据库服务、socket 和受保护密码文件后重试。

若提示 `service is not settled`，先查看产品单元，定位原因；以下以指纹服务为例：

```bash
systemctl show sql-apm-fingerprint.service --property=LoadState,ActiveState,SubState,UnitFileState
sudo journalctl -u sql-apm-fingerprint.service -n 50 --no-pager
```

不要直接清除一个仍在故障中的状态。修正日志指出的原因后，清除该单元的失败标志；
原来应运行的服务重新启动并核对健康，原来停用的保持停用，然后重跑前置检查：

```bash
sudo systemctl reset-failed sql-apm-fingerprint.service
sudo systemctl start sql-apm-fingerprint.service
systemctl is-active sql-apm-fingerprint.service
```

预期为 `active`；Grafana 同理处理对应服务。每日任务失败先查明原因并确认没有运行中的任务，
不要为清除状态直接启动每日运行；定时器也须恢复原来的启停状态。

## 4. 确认并在后台升级

再次确认：**不备份；迁移只替换两个函数，提交后不可回退，问题由后续修复版本处理。**
下列 `--confirm-no-rollback` 表示操作者已阅读并接受这一点；不提供该参数会立即拒绝。
以第 2 步的执行账号运行，继续使用已导出的环境变量。先确认 sudo 可免交互执行：

```bash
sudo -n true
```

没有报错后，用 `nohup … &` 启动。每次新建一个控制日志目录，保留历次运行的输出和退出码：

```bash
umask 077
export APM_UPGRADE_RUN
APM_UPGRADE_RUN=$(mktemp -d "$APM_UPGRADE/run-apply-XXXXXXXX")
nohup env -u BASH_ENV bash --noprofile --norc -c '
  set +e
  "$APM_ROOT/app/.venv/bin/python" -u "$APM_UPGRADE/app/scripts/deployment/upgrade.py" apply \
    --root "$APM_ROOT" --candidate "$APM_UPGRADE/app" --verification "$APM_UPGRADE/verification" \
    --confirm-no-rollback
  result=$?
  printf "%s\n" "$result" > "$APM_UPGRADE_RUN/exit-code.tmp"
  mv "$APM_UPGRADE_RUN/exit-code.tmp" "$APM_UPGRADE_RUN/exit-code"
  exit "$result"
' </dev/null >"$APM_UPGRADE_RUN/console.log" 2>&1 &
printf '%s\n' "$!" > "$APM_UPGRADE_RUN/pid"
printf '后台已启动；请保存控制日志目录：%s\n' "$APM_UPGRADE_RUN"
```

**这里返回终端只表示后台已启动，不表示升级成功。** `pid` 是后台包装进程的 PID；
`console.log` 收集前置检查、进度和错误，`exit-code` 在升级入口退出后写入。
`-u` 让 Python 输出及时写入日志；标准输入关闭，后台不会等待密码或终端输入。
后台 shell 不读取启动文件，避免账号中残留的旧版升级变量覆盖本次指定的目录。
`nohup` 用于在普通 SSH 断开后继续运行；它不能防止重启、掉电或管理员终止进程。

查看进度：

```bash
tail -n 50 -f "$APM_UPGRADE_RUN/console.log"
```

按 `Ctrl-C` 只退出日志查看，后台升级继续执行。重新登录后只恢复变量，
将刚才保存的完整目录填入最后一行；不要重复解包或再次启动升级：

```bash
export APM_ROOT=/data/sql-apm
export APM_UPGRADE=/data/sql-apm-upgrade-v1.3.1
export APM_UPGRADE_RUN='填写后台启动时打印的控制日志目录'
```

检查是否结束：

```bash
tail -n 50 "$APM_UPGRADE_RUN/console.log"
if test -f "$APM_UPGRADE_RUN/exit-code"; then
  printf '升级入口退出码：'
  cat "$APM_UPGRADE_RUN/exit-code"
else
  printf '尚无退出码；检查下列进程和控制日志。\n'
  ps -p "$(cat "$APM_UPGRADE_RUN/pid")" -o pid,etime,args || true
fi
```

退出码为 `0` 且日志末尾出现 `OK: upgraded to v1.3.1`，才进入第 5 步。
非零退出码按第 6 步处理；没有退出码且进程已经消失时，按异常中断检查，不能当作成功。

入口重新检查前置条件，暂存交付文件，停止写入方，然后在一个事务内迁移。
锁等待上限 5 秒，超时即回滚。迁移提交后替换程序，沿用原虚拟环境，更新四个随包看板，
核对结构、解释器和 `daily status`，恢复服务原有状态。不会启用原本未启用的服务或定时器。
成功时在 `console.log` 打印升级记录目录（`record=...`）。
每条命令的退出码、耗时及输出保存在 `$APM_ROOT/upgrades/时间戳/`，`state.json` 记录阶段。
该目录也保留被替换的程序文件供诊断；它不是数据库备份，不能用来降级。

## 5. 升级后核对

确认上一步退出码为 `0` 且日志报告升级成功，再将 `record=...` 后的完整目录填入 `APM_RECORD`。
这是 `$APM_ROOT/upgrades/` 下的升级记录目录，与 `APM_UPGRADE_RUN` 控制日志目录不同。
执行随包自检；它使用独立临时库，不重建产品库。重新登录后补回数据库连接环境：

```bash
export APM_RECORD='填写上一步打印的升级记录目录'
export PYTHONDONTWRITEBYTECODE=1
export PGPASSFILE="$APM_ROOT/private/pgpass"
export SQL_APM_DSN="host=$APM_ROOT/socket port=5432 dbname=sql_apm user=sql_apm"
unset PGPASSWORD PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR
cd "$APM_ROOT/app"
.venv/bin/python scripts/deployment/verify_package.py --installed
scripts/db/initialize.sh check --host "$APM_ROOT/socket" --port 5432 \
  --pg-bin "$APM_ROOT/postgresql-17.10/bin"
.venv/bin/python scripts/deployment/runtime_check.py --binary --root "$APM_ROOT" \
  --output "$APM_RECORD/selfcheck"
.venv/bin/python -m sql_apm daily status
command -v sqlfind
sqlfind --help
```

预期程序 v1.3.1、结构 1.15.0，自检最后为 `every runtime and packaged acceptance entry passed`。
`daily status` 能读到旧运行记录；本命令不启动每日运行。

照[检索与看板使用指南](search-guide.md)，在网页逐项核对：

- 关键词、原文包含、结构匹配三种检索均能找到既有数据。
- SQL 检索、SQL 列表、SQL 详情、运行状态四个看板均能加载；抽查升级前保存的同一 SQL 和基线版本。
- SQL 列表的当前基线排行、详情明细和两个下拉框可正常使用。
- 按[sqlfind 使用说明](sqlfind-guide.md)执行一次检索并进入交互详情，核对已有只读配置继续可用。
- 核对每日运行单元和定时器状态与升级记录中的升级前状态一致。

最后，以 sfmon 按[性能检查手册的导入采集步骤](performance-check.md#导入前后采集)准备独立工具包 **1.3.0**，
在首次每日运行之前保存 `before.json`。这是升级后基准，必须在恢复的每日定时器下次触发之前完成；
不要先手工启动每日运行。采集只读，输出不含 SQL 文本、文件名或凭据。
运行结束再采 `after.json`，两次之间不重置统计、不重启数据库、不并发导入；记录有无并发查询。
审阅两份 JSON 后交给实施方核对。待日志导入到前一天，再按同手册“定向补测”执行；
使用各集群及全部集群的 1／7／30 天排行、最高频身份的七天详情和 200 行明细，以及首次读取指标。
本次无需全部用例三轮计时；旧产品 v1.3.0 继续使用旧工具包 1.2.0。

## 6. 失败与中断

`resume` 沿用夹具覆盖，本版不在真实安装上贯通执行。连接错误 `STOP:` 和 failed 单元处理的命令与路径未变，
继承 v1.3.0 在同一验证机的实测；其他手册命令与路径在本版验证机执行。
覆盖与继承依据在[升级验证报告](../reports/v131-upgrade-2026-11-11.md)中单列。
`apply` 自动回滚与操作者执行 `resume` 分别记录，不互相代替。

- SSH 断开或退出 `tail`：先按第 4 步查看原控制日志和退出码，不能据此判断升级中断。
- 前置检查失败：安装保持原样，处理原因后重新执行 `check`。
- 迁移失败且输出 `migration rolled back`：旧版结构检查通过，原服务已恢复；
  保存该次记录，排除锁等待等原因后重新执行 `apply`，会生成新的记录目录。
- 中断、掉电、提交后文件替换或启动失败：不要手工移回旧程序，不要启动旧写入方。
  保留交付目录及记录，使用下列入口核对数据库回执并继续。
  若迁移进程仍在运行，入口拒绝恢复；等待进程结束后再执行。
- 升级后恢复服务时出现 `failed`：按第 3 步查看原因，修正后对失败的具体单元 `reset-failed`。
  数据库迁移若已提交，不重新执行 `apply` 或退回旧程序；对原升级记录执行本节 `resume`，
  由入口完成核对和原服务状态恢复。恢复结束仍需再次核对健康。
- 停止定时器后发现每日任务刚启动：不终止该任务，不开始迁移。
  等待任务结束，再对该次记录执行恢复入口，恢复原服务和定时器状态。

从失败日志的 `Upgrade record: ...` 或此次升级的 `state.json` 确认完整记录目录，
设置 `APM_RECORD`，并确认原升级包装进程及其迁移子进程都已结束，再后台恢复。
若前置检查失败且没有产生升级记录，应修正原因后重新检查，不使用 `resume`。

```bash
export APM_RECORD='填写本次失败的升级记录目录'
sudo -n true
umask 077
export APM_UPGRADE_RUN
APM_UPGRADE_RUN=$(mktemp -d "$APM_UPGRADE/run-resume-XXXXXXXX")
nohup env -u BASH_ENV bash --noprofile --norc -c '
  set +e
  "$APM_ROOT/python-3.13.16/bin/python3.13" -u "$APM_UPGRADE/app/scripts/deployment/upgrade.py" resume \
    --root "$APM_ROOT" --record "$APM_RECORD" --confirm-no-rollback
  result=$?
  printf "%s\n" "$result" > "$APM_UPGRADE_RUN/exit-code.tmp"
  mv "$APM_UPGRADE_RUN/exit-code.tmp" "$APM_UPGRADE_RUN/exit-code"
  exit "$result"
' </dev/null >"$APM_UPGRADE_RUN/console.log" 2>&1 &
printf '%s\n' "$!" > "$APM_UPGRADE_RUN/pid"
printf '后台恢复已启动；请保存控制日志目录：%s\n' "$APM_UPGRADE_RUN"
```

用第 4 步相同命令查看本次恢复的日志和退出码；恢复仍需退出码为 `0` 才算成功。
恢复入口从记录中的虚拟环境位置加载原有依赖。结构仍为 1.14.0 时只恢复旧服务；
对应日志为 `OK: migration rolled back`，此时仍是旧版，不执行新版核对。
已为 1.15.0 时完成新程序替换和核对，日志为 `OK: upgraded to v1.3.1`，随后执行第 5 步。
连接失败或回执不明确时保持服务停止，保留日志交给实施方处理，不猜测事务是否已经提交。
