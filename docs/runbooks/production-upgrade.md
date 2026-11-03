# SQL APM 生产升级手册

适用于已有数据的正式 v1.2.0（结构 1.13.0）原地升级到 v1.3.0（结构 1.14.0）。
安装根仍为 `/data/sql-apm`。程序、验收文件及四个随包看板更新；配置、密码、运行时、
sqlfind 配置、每日运行单元和数据库目录继续使用。新实例见[生产安装手册](production-installation.md)。

## 1. 选择维护窗口

选择每日运行结束后、下一次运行开始前。通知使用者暂停检索和手工产品命令，关闭 sqlfind。
升级入口检查正在运行的产品任务和每日运行；发现忙碌会拒绝，不能以终止正常任务的方式强行升级。
Grafana、指纹服务和已有每日运行定时器会暂停，数据库服务保持运行；升级后恢复原有状态。
未安装的每日运行单元保持未安装，原本关闭的定时器保持关闭。

**本次不备份数据库。迁移提交后不能回退到 v1.2.0。**
如果提交后发现无法继续使用新版本，唯一恢复方式是重装 v1.2.0，
从仍然保留的原始日志重新导入并构建，需要数小时；已经过期删除的原始日志无法重新导入。
必须在第 4 步明确确认后才会继续。迁移事务失败时则自动回滚，旧程序和数据保持可用。

生产规模停写模型为 **22–61 分钟**（modeled），不含文件校验、自检和人工核对；
不能承诺实际停机时长。#73 开发机全量副本迁移约 164–168 秒（measured）。
本版升级入口中的迁移命令：开发机全量副本 174.156 秒，验证机部分样本 66.937 秒（measured）；
这些不是生产耗时，也不含停服务、程序替换及后续人工核对。
完整边界见[升级验证报告](../reports/v130-upgrade-2026-11-03.md)。

空间预检按当前事件数 `N`（`mpp_occurrence` 的精确行数）换算：

- 参照规模 30,180,000 条事件；工作盘预算 100 GB，新增结构持续占用 20.064 GB。
- 保守地把两项相加后同比缩放：`ceil(N × 120064000000 / 30180000)` 字节。
  #73 的 100 GB 情景本已含部分持续占用，本入口额外计入 20.064 GB，作为保守余量，避免低估。
- 上述结果至少取 6,000,000,000 字节，再加新程序和验收文件总大小的两倍作为暂存空间。
- 对安装根和数据库目录所在文件系统均核对可用空间；外置 WAL 或 tablespace 会拒绝，须单独评估。
- 新增结构每天约增长 **0.803–0.812 GB**（modeled）；30 天约 24.1–24.4 GB，
  另留 15% 为约 27.7–28.0 GB。GB 为十进制；这些日增不等同于迁移临时峰值。

模型来源为 [#73 报告](../reports/query-records-2026-11-03.md)。
迁移工作空间受 WAL 回收、排序和临时表影响；模型不是严格空间上界。
升级后第一次每日运行的导入可能变慢，开发机同批导入（含指纹）增加 **18.3%**；仍应在每日运行之外升级。
本版不带出生产升级记录；生产是否升级完成由性能结果包的版本和数据证据判定，
结构检查、自检以用户确认为准，不用性能结果代替生产迁移耗时。

## 2. 解包并核对交付

使用交付方提供的完整离线包、独立 SHA-256 摘要和固定提交。以原执行账号登录，具备 sudo 权限。
以下准备目录在安装根之外，必须是全新目录。先创建执行账号可写的准备目录，再传入离线包：

```bash
set -euo pipefail
test ! -e /data/sql-apm-upgrade-v1.3.0
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /data/sql-apm-upgrade-v1.3.0
```

将离线包放在该目录中，不覆盖旧安装包。

```bash
set -euo pipefail
export APM_ROOT=/data/sql-apm
export APM_UPGRADE=/data/sql-apm-upgrade-v1.3.0
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

预期输出旧版本 v1.2.0、新版本 v1.3.0、程序提交、服务状态、额外空间预算和可用空间，
最后为 `OK: preflight passed; no installation changes made`。
检查旧程序清单、固定运行时逐文件摘要、1.13.0 结构、任务空闲、服务状态、路径及空间。
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

再次确认：**不备份；迁移提交后不可回退，只能重装旧版并从原始日志重建。**
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
export APM_UPGRADE=/data/sql-apm-upgrade-v1.3.0
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

退出码为 `0` 且日志末尾出现 `OK: upgraded to v1.3.0`，才进入第 5 步。
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

预期程序 v1.3.0、结构 1.14.0，自检最后为 `every runtime and packaged acceptance entry passed`。
`daily status` 能读到旧运行记录；本命令不启动每日运行。

照[检索与看板使用指南](search-guide.md)，在网页逐项核对：

- 关键词、原文包含、结构匹配三种检索均能找到既有数据。
- SQL 检索、SQL 列表、SQL 详情、运行状态四个看板均能加载；抽查升级前保存的同一 SQL 和基线版本。
- SQL 列表的当前基线排行、详情明细和两个下拉框可正常使用。
- 按[sqlfind 使用说明](sqlfind-guide.md)执行一次检索并进入交互详情，核对已有只读配置继续可用。
- 核对每日运行单元和定时器状态与升级记录中的升级前状态一致。

核对通过后先完成一次每日运行，使最近七天有日志，再执行全量复测。生产性能复测使用独立工具包 **1.2.0**，
只支持 v1.3.0／结构 1.14.0；使用全部用例、每项三次、含首次执行读取指标、计划和重点分析，避开每日运行。
结果包格式和脱敏规则不变，操作见[性能检查手册](performance-check.md)。

## 6. 失败与中断

`resume` 的目录恢复和后台包装沿用 v1.2.0 的夹具覆盖；本版额外验证看板复制在私有 umask 下保留随包权限。
本版升级版本回执、复制权限及连接错误提示的实测范围在[升级验证报告](../reports/v130-upgrade-2026-11-03.md)中单列。
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
恢复入口从记录中的虚拟环境位置加载原有依赖。结构仍为 1.13.0 时只恢复旧服务；
对应日志为 `OK: migration rolled back`，此时仍是旧版，不执行新版核对。
已为 1.14.0 时完成新程序替换和核对，日志为 `OK: upgraded to v1.3.0`，随后执行第 5 步。
连接失败或回执不明确时保持服务停止，保留日志交给实施方处理，不猜测事务是否已经提交。
