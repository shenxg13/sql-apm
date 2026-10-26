# SQL APM 生产升级手册

适用于已有数据的正式 v1.1.0（结构 1.12.0）原地升级到 v1.2.0（结构 1.13.0）。
安装根仍为 `/data/sql-apm`。程序、验收文件及四个随包看板更新；配置、密码、运行时、
sqlfind 配置、每日运行单元和数据库目录继续使用。新实例见[生产安装手册](production-installation.md)。

## 1. 选择维护窗口

选择每日运行结束后、下一次运行开始前。通知使用者暂停检索和手工产品命令，关闭 sqlfind。
升级入口检查正在运行的产品任务和每日运行；发现忙碌会拒绝，不能以终止正常任务的方式强行升级。
Grafana、指纹服务和已有每日运行定时器会暂停，数据库服务保持运行；升级后恢复原有状态。
未安装的每日运行单元保持未安装，原本关闭的定时器保持关闭。

**本次不备份数据库。迁移提交后不能回退到 v1.1.0。**
如果提交后发现无法继续使用新版本，唯一恢复方式是重装 v1.1.0，
从仍然保留的原始日志重新导入并构建，需要数小时；已经过期删除的原始日志无法重新导入。
必须在第 4 步明确确认后才会继续。迁移事务失败时则自动回滚，旧程序和数据保持可用。

生产规模的迁移模型为 **7–15 分钟**，额外工作空间约 **6 GB**；不含文件核对、自检和人工核对。
这是按历史读写量估计的模型，不能承诺生产停机时长。
入口按全部保留构建的 overall 行数放大空间预算，至少预留 6 GB，再加新程序与验收文件的暂存空间。
本次开发机全量样本迁移实测 9.56 秒，验证机部分样本实测 4.58 秒；
迁移后数据库分配空间分别增加约 580 MB 和 69 MB。这不是迁移过程的临时空间峰值，
两处较小规模、已有缓存的结果也不能替代生产的 7–15 分钟模型。
环境差异和完整核对见[升级验证报告](../reports/v120-upgrade-2026-10-27.md)；生产迁移后还需带出升级记录比较。

## 2. 解包并核对交付

使用交付方提供的完整离线包、独立 SHA-256 摘要和固定提交。以原执行账号登录，具备 sudo 权限。
以下准备目录在安装根之外，必须是全新目录。先创建执行账号可写的准备目录，再传入离线包：

```bash
test ! -e /data/sql-apm-upgrade-v1.2.0
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /data/sql-apm-upgrade-v1.2.0
```

将离线包放在该目录中，不覆盖旧安装包。

```bash
set -euo pipefail
export APM_ROOT=/data/sql-apm
export APM_UPGRADE=/data/sql-apm-upgrade-v1.2.0
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

预期输出旧版本 v1.1.0、新版本 v1.2.0、程序提交、服务状态、额外空间预算和可用空间，
最后为 `OK: preflight passed; no installation changes made`。
检查旧程序清单、固定运行时逐文件摘要、1.12.0 结构、任务空闲、服务状态、路径及空间。
固定运行时归档摘要为 `d271d6a1471c41723724b48afad59f9675e1b324747fbb7dfae283f97340314c`，
已安装清单与该归档的固定清单绑定，不重新编译或安装依赖。

失败时停下，按错误处理版本、损坏文件、忙碌任务、空间或权限。此步骤不停止服务、不写升级目录、
不修改程序和数据库。不要修改程序清单、版本回执或放宽检查来绕过拒绝。

## 4. 确认并升级

再次确认：**不备份；迁移提交后不可回退，只能重装旧版并从原始日志重建。**
下列 `--confirm-no-rollback` 表示操作者已阅读并接受这一点；不提供该参数会立即拒绝。

```bash
"$APM_ROOT/app/.venv/bin/python" "$APM_UPGRADE/app/scripts/deployment/upgrade.py" apply \
  --root "$APM_ROOT" --candidate "$APM_UPGRADE/app" --verification "$APM_UPGRADE/verification" \
  --confirm-no-rollback
```

入口重新检查前置条件，暂存交付文件，停止写入方，然后在一个事务内迁移。
锁等待上限 5 秒，超时即回滚。迁移提交后替换程序，沿用原虚拟环境，更新四个随包看板，
核对结构、解释器和 `daily status`，恢复服务原有状态。不会启用原本未启用的服务或定时器。
预期最后为 `OK: upgraded to v1.2.0`，并打印升级记录目录。
每条命令的退出码、耗时及输出保存在 `$APM_ROOT/upgrades/时间戳/`，`state.json` 记录阶段。
该目录也保留被替换的程序文件供诊断；它不是数据库备份，不能用来降级。

## 5. 升级后核对

先将上一步打印的完整记录目录填入 `APM_RECORD`。执行随包自检；它使用独立临时库，不重建产品库。

```bash
export APM_RECORD='填写上一步打印的升级记录目录'
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

预期程序 v1.2.0、结构 1.13.0，自检最后为 `every runtime and packaged acceptance entry passed`。
`daily status` 能读到旧运行记录；本命令不启动每日运行。

照[检索与看板使用指南](search-guide.md)，在网页逐项核对：

- 关键词、原文包含、结构匹配三种检索均能找到既有数据。
- SQL 检索、SQL 列表、SQL 详情、运行状态四个看板均能加载；抽查升级前保存的同一 SQL 和基线版本。
- SQL 列表的当前基线排行、详情明细和两个下拉框可正常使用。
- 按[sqlfind 使用说明](sqlfind-guide.md)执行一次检索并进入交互详情，核对已有只读配置继续可用。
- 核对每日运行单元和定时器状态与升级记录中的升级前状态一致。

核对通过后再安排下一次每日运行。生产性能复测使用独立工具包 **1.1.0**，
只支持 v1.2.0／结构 1.13.0；使用全部用例、每项三次、含计划和重点分析，避开每日运行。
结果包格式和脱敏规则不变，操作见[性能检查手册](performance-check.md)。

## 6. 失败与中断

- 前置检查失败：安装保持原样，处理原因后重新执行 `check`。
- 迁移失败且输出 `migration rolled back`：旧版结构检查通过，原服务已恢复；
  保存该次记录，排除锁等待等原因后重新执行 `apply`，会生成新的记录目录。
- 中断、掉电、提交后文件替换或启动失败：不要手工移回旧程序，不要启动旧写入方。
  保留交付目录及记录，使用下列入口核对数据库回执并继续。
  若迁移进程仍在运行，入口拒绝恢复；等待进程结束后再执行。

```bash
"$APM_ROOT/python-3.13.16/bin/python3.13" "$APM_UPGRADE/app/scripts/deployment/upgrade.py" resume \
  --root "$APM_ROOT" --record "$APM_RECORD" --confirm-no-rollback
```

恢复入口从记录中的虚拟环境位置加载原有依赖。结构仍为 1.12.0 时只恢复旧服务；
已为 1.13.0 时完成新程序替换和核对。
连接失败或回执不明确时保持服务停止，保留日志交给实施方处理，不猜测事务是否已经提交。
