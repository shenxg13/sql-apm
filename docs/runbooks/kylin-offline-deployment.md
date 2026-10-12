# Kylin V10 SP2 演练验证手册

本手册用于 `v1.0.0` 的实施方和用户验收，结构保持 1.12.0；生产安装入口是
[生产手册](production-installation.md)。本手册保留九任务、样本日志、快照和证据步骤，
不把这些步骤混入生产手册。验收完成前候选包不得视为正式发布。

按 [Issue #59](https://github.com/shenxg13/sql-apm/issues/59)：用户三次恢复初始快照，分别用于
一次编译、实施方二进制完整验证、用户亲自按生产手册安装与上线。编译路径只跑安装和十个
随包验收入口，不重跑九任务；二进制成品只编译一次，后续各轮按同一摘要核对。
用户明确验收通过前不合并、不发布。真实 APM 生产机自检不是发版门槛。

实施方二进制轮先按生产手册完成安装，第 8–9 节首次拉取和首批导入用开发机样本替代；
以下第 9–13 节和附录保留既有数据核对方法。用户轮亲自按生产手册操作，样本来源仍由开发机提供。
默认自动清理旧结果和删除过期原始文件，演练接收目录必须使用副本，原件另存。

## 1. 参数与前置检查

以下在目标机以具备免密 sudo 的执行账号运行；每个新终端先重新填写本节变量。
解释器路径和允许客户端网段只在这里定义；不从 `.env` 执行配置。

```bash
set -euo pipefail
export APM_ROOT=/data/sql-apm
export APM_RUN_USER=sfmon
export APM_RUN_GROUP="$(id -gn "$APM_RUN_USER")"
test "$(id -un)" = "$APM_RUN_USER"
export APM_PYTHON="$APM_ROOT/python-3.13.16/bin/python3.13"
export APM_PG_BIN="$APM_ROOT/postgresql-17.10/bin"
export APM_APP="$APM_ROOT/app"
export APM_VERIFY="$APM_ROOT/verification"
export SQL_APM_APP_ROOT="$APM_APP"
export APM_SOCKET="$APM_ROOT/socket"
export APM_PORT=5432
export APM_SERVER='填写目标机的LAN地址'
export APM_CLIENT_CIDR='填写允许的客户端CIDR网段'
export APM_BUNDLE="$APM_ROOT/offline-bundle"
export APM_EXPECTED_COMMIT='填写交付记录中的40位程序提交'
export APM_EXPECTED_BUNDLE_SHA256='填写交付记录中的64位离线包摘要'
export APM_WORKERS=4
export APM_GRAFANA="$APM_ROOT/grafana"
export APM_GRAFANA_PORT=3000
export APM_FINGERPRINT_PORT=3001
export PGPASSFILE="$APM_ROOT/private/pgpass"
export SQL_APM_DSN="host=$APM_SOCKET port=$APM_PORT dbname=sql_apm user=sql_apm"
unset PGPASSWORD PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR
sudo -n id
cat /etc/kylin-release
uname -m
getconf GNU_LIBC_VERSION
findmnt -T /data
df -h /data
free -h
sudo -n ss -ltnp
```

预期：目标机为已约定的 Kylin SP2 x86_64、glibc 2.28；sudo 返回 root；`/data`
独立挂载且可写、空间能容纳约 15 GiB 日志、约 55–60 GiB 数据库及 WAL／构建目录。
这是依据 Alma 的估计，不是性能门槛。5432 未占用，且没有其他任务使用部署目录。
失败：连接／sudo／磁盘／端口不符先处理环境，不安装到其他主机或改变系统 Python。
快照存在性由虚拟机操作者确认，SSH 探测不能证明快照可恢复。
运行前必须填写目标地址、允许网段，以及交付记录中的完整提交和离线包摘要；
提交与摘要从实施方的交付记录取得，不用待安装包自行声明的值代替。
实施时使用交接提供的目标地址和允许网段，连接信息不写入 Issue。

开发机传输命令另定义 SSH 目标（用交接提供的执行账号与地址填写），本次端口为 22：

```bash
set -euo pipefail
export APM_SSH_TARGET='执行账号@目标机地址'
export APM_SSH_PORT=22
export APM_EXPECTED_COMMIT='填写交付记录中的40位程序提交'
export APM_EXPECTED_BUNDLE_SHA256='填写交付记录中的64位离线包摘要'
```

## 2. 准备基础工具与离线依赖

复用已核验的 Python 3.13.16／PostgreSQL 17.10 源码、cp313 wheel 和离线 RPM 集合。
Grafana 与三个插件采用随包固定清单，全部摘要和来源记在离线包 manifest.json 中。完整离线包含 RPM 仓库索引。
以下在已获授权的目标机准备目录与 tar；tar 是解包工具，应在开始前由操作者准备；缺失则停止，不在本流程启用网络源。二进制轮不安装 RPM；编译轮按第 4 节显式执行。

```bash
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" "$APM_ROOT" "$APM_ROOT/setup"
command -v tar
rpm -q tar
tar --version
```

编译根包由完整包的 support/compile-packages.txt 指定，包含 gcc、gcc-c++、make、perl、
bison、flex、pkgconf，以及 zlib、libffi、readline、OpenSSL、bzip2、xz、sqlite、
util-linux、ncurses、ICU 的开发包。Kylin UUID 头文件在 util-linux-devel。
离线 RPM 集合仅证明本次初始状态可安装，不外推到其他系统。失败时保存输出，不跳过签名。

## 3. 获取、传输与核对本次离线包

<!-- developer-only:start -->
以下制品准备只在开发仓库执行，相关制包脚本不装入程序包。构建工具使用独立且锁定的
Python-Markdown 3.8.2 环境；开发机准备及重复构建、隔离安装的完整命令见
[程序发布说明](program-release.md)。每次使用全新交付目录，不覆盖 v0.1.0 或历史候选。

```bash
APM_BUILD_COMMIT="$(git rev-parse HEAD)"
APM_DELIVERY_DIR="$PWD/var/issue59/deliveries/$APM_BUILD_COMMIT"
var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
  --commit "$APM_BUILD_COMMIT" --version v1.0.0 --kind candidate \
  --output "$APM_DELIVERY_DIR/release"
.venv/bin/python scripts/deployment/build_bundle.py \
  --output "$APM_DELIVERY_DIR/offline-bundle" \
  --release-dir "$APM_DELIVERY_DIR/release" \
  --rpm-collection var/issue31/rpm-collection \
  --python-source var/issue52/downloads/Python-3.13.16.tgz \
  --postgres-source var/issue33/delivery-final/offline-bundle/sources/postgresql-17.10.tar.gz \
  --wheel-dir var/issue52/downloads/wheels --grafana-files var/issue51/downloads \
  --compile-only
```

首次组装从官方元数据核对两个 cp313 wheel 的来源和摘要。后续可增加
`--source-manifest 本次完整包目录/manifest.json` 离线复用已核对来源，仍逐项检查摘要和
RPM 签名证据；旧 cp39 包的清单不能提供新 wheel 的来源。
<!-- developer-only:end -->

按本次交付记录填写外部摘要和精确提交，不用包内自行声明的值替代。在开发机传输：

```bash
set -euo pipefail
APM_DELIVERY_DIR="$PWD/var/issue59/deliveries/$APM_EXPECTED_COMMIT"
[[ "$APM_EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$APM_EXPECTED_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$APM_EXPECTED_BUNDLE_SHA256" "$APM_DELIVERY_DIR/offline-bundle.tar.gz" | sha256sum -c -
test "$(tar -xOf "$APM_DELIVERY_DIR/offline-bundle.tar.gz" offline-bundle/PROGRAM_COMMIT)" = "$APM_EXPECTED_COMMIT"
scp -P "$APM_SSH_PORT" "$APM_DELIVERY_DIR/offline-bundle.tar.gz" \
  "$APM_DELIVERY_DIR/offline-bundle.tar.gz.sha256" "$APM_SSH_TARGET:/data/sql-apm/"
```

目标机先验证压缩包，再解包并核对每个文件：

```bash
cd "$APM_ROOT"
set -euo pipefail
[[ "$APM_EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$APM_EXPECTED_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$APM_EXPECTED_BUNDLE_SHA256" offline-bundle.tar.gz | sha256sum -c -
sha256sum -c offline-bundle.tar.gz.sha256
test "$(tar -xOf offline-bundle.tar.gz offline-bundle/PROGRAM_COMMIT)" = "$APM_EXPECTED_COMMIT"
test ! -e "$APM_BUNDLE"
tar -xzf offline-bundle.tar.gz
cd "$APM_BUNDLE"
sha256sum -c SHA256SUMS
test "$(cat PROGRAM_COMMIT)" = "$APM_EXPECTED_COMMIT"
```

预期全部 OK，`PROGRAM_COMMIT` 与交付记录一致；根目录六份 HTML 与程序包内对应文档相同。
失败停止安装，核对来源或重传，不修改摘要绕过。程序、业务日志及凭据不在目标机上联网获取。

## 4. 编译路径与一次性成品

只在第一轮初始快照上执行。先解压程序与验收包，核对提交和 SHA256SUMS；
`compile_runtime.sh` 会保存系统版本、编译前后 RPM 清单、全部命令和输出，编译后串行运行十个入口。
它明确调用离线 yum，会升级系统包；影响见[生产手册编译附录](production-installation.md#附录-a-手工编译路径)。

```bash
cd "$APM_ROOT"
test ! -e "$APM_APP"
test ! -e "$APM_VERIFY"
tar -xzf "$APM_BUNDLE/program.tar.gz"
tar -xzf "$APM_BUNDLE/verification.tar.gz"
(cd "$APM_APP" && sha256sum -c SHA256SUMS)
(cd "$APM_VERIFY" && sha256sum -c SHA256SUMS)
bash "$APM_APP/scripts/deployment/compile_runtime.sh" "$APM_ROOT"
```

## 5. 导出与保留成品

第一轮脚本退出成功后再导出，避免读取尚未关闭的编译日志：

```bash
PYTHONDONTWRITEBYTECODE=1 "$APM_APP/.venv/bin/python" "$APM_APP/scripts/deployment/runtime_archive.py" export \
  --root "$APM_ROOT" --bundle "$APM_BUNDLE" --evidence "$APM_ROOT/runtime-build" \
  --artifact "$APM_ROOT/runtime-artifact"
(cd "$APM_ROOT/runtime-artifact" && sha256sum -c SHA256SUMS)
sha256sum "$APM_ROOT/runtime-artifact/SHA256SUMS"
```

整个 runtime-artifact 与 runtime-build 的日志复制回开发机，核对 SHA256SUMS；源码构建中间目录无需交付。
成品含固定前缀的 Python、PostgreSQL、虚拟环境，不含数据、日志、密码或源码程序。
`runtime-manifest.json` 列出每个文件、符号链接、写死 `/data/sql-apm` 的路径，以及来源与编译证据摘要。
把成品加入完整包后再开始第二轮，编译本身不代表二进制可在初始系统库上运行。

## 6. 二进制轮与自检反例

用户恢复初始快照后，重新核对系统、端口和空目录，使用已经加入成品的完整包，按生产手册安装。
安装前后分别保存排序后的完整软件包清单，并用 `cmp` 逐字比较：

```bash
rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n' | LC_ALL=C sort > "$APM_ROOT/setup/packages-before.tsv"
# 此处执行生产手册第 2–6 节；不能执行本手册第 4 节。
rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n' | LC_ALL=C sort > "$APM_ROOT/setup/packages-after.tsv"
cmp "$APM_ROOT/setup/packages-before.tsv" "$APM_ROOT/setup/packages-after.tsv"
```

自检要有两个失败记录：错误前缀，以及一个依赖系统库不可用。错误前缀可在安装前执行：

```bash
if python3 "$APM_APP/scripts/deployment/runtime_archive.py" install \
  --root /data/sql-apm-wrong --artifact "$APM_BUNDLE/runtime"; then
  echo '错误：其他目录居然被允许' >&2
  exit 1
fi
```

缺库反例由实施方使用进程级隔离隐藏一份被 ldd 确认需要的共享库，不能删除或改名主机系统库；
完整命令、被隐藏库和停止项目保存在私有机器记录。结束后退出隔离并重新完整自检。
开发机模拟失败只验证工具停止逻辑，不能充当此项目标机实测。

## 7. 建立实例与项目数据库

按[生产手册第 4 节](production-installation.md#4-建立实例数据库和账号)执行，
包括 PostgreSQL 服务、可登录的 postgres、两次输入的数据库密码和两个登录 profile。
结构应为 1.12.0，账号允许／拒绝的实测要分别记录。

- `sfmon` 登录后裸 psql 为 sql_apm，`psql -U sql_apm_ro` 为只读账号，两者不提示密码。
- `sudo su - postgres` 后裸 psql 为本机超级用户，不设登录密码或数据库密码；远程超级用户被拒绝。
- 两个数据库账号测试单字符、冒号、反斜杠密码；空密码、换行和两次不一致由脚本拒绝。
- Grafana 测试短密码、冒号和反斜杠；上游限制核对记录与当前 Issue 契约一致。
- 登录环境设置前后对同一命令、timer 及两个服务核对显式连接和结果；不得因 profile 改写任务账号。

### 7.1 以执行账号核对读取权限

以下直接以 sfmon 执行，不使用 sudo；仅打开普通文件，不读取或输出其正文。
在无导入／构建任务时检查，以避免文件创建、删除产生瞬时差异。

```bash
"$APM_PYTHON" - <<'PY'
import os
import stat
from pathlib import Path

root = Path(os.environ['APM_ROOT'])
assert os.getuid() != 0
assert __import__('pwd').getpwuid(os.getuid()).pw_name == os.environ['APM_RUN_USER']
counts = {'directories': 0, 'regular_files': 0}

def fail(error):
    raise error

for current, directories, files in os.walk(root, followlinks=False, onerror=fail):
    assert os.access(current, os.R_OK | os.X_OK), current
    counts['directories'] += 1
    for name in directories + files:
        path = Path(current) / name
        if path.is_symlink():
            assert path.resolve().is_relative_to(root), str(path)
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            assert os.access(path, os.R_OK | os.X_OK), str(path)
        elif stat.S_ISREG(mode):
            with path.open('rb'):
                pass
            counts['regular_files'] += 1
        if path.is_relative_to(root / 'pgdata'):
            assert not os.access(path, os.W_OK), str(path)
assert not os.access(root / 'pgdata', os.W_OK)
assert not os.access(root / 'socket', os.W_OK)
print('READ_ACCESS_OK', counts)
PY
stat -c '%a %U:%G %n' "$APM_ROOT/private" "$PGPASSFILE" "$APM_ROOT/pgdata" "$APM_SOCKET"
"$APM_PG_BIN/psql" -X -w -h "$APM_SOCKET" -p "$APM_PORT" -U sql_apm -d sql_apm \
  -c 'BEGIN READ ONLY; SELECT current_user,current_database(); COMMIT;'
```

预期：`READ_ACCESS_OK`、无不可读路径、socket 登录成功；第 8 节继续验证 TCP。
目录与文件数量随构建／运行变化，不使用固定数量作为门槛。失败时保存路径和错误，
不要把密码或 SQL 原文复制到验收记录；完成日志传输或后续维护后可再次运行此检查。

## 8. 远程连接与启停

按[生产手册](production-installation.md)完成受限 HBA／防火墙、Grafana 与指纹服务以及 PostgreSQL 自启。
允许网段内正确密码成功，错误密码和不允许来源失败；只读账号写入失败，匿名 Grafana 不能查看看板。
所有账号均由操作者掌握密码，不在公开记录中保留凭据。启停统一使用 systemctl。

```bash
systemctl is-enabled sql-apm-postgresql.service sql-apm-grafana.service sql-apm-fingerprint.service
systemctl is-active sql-apm-postgresql.service sql-apm-grafana.service sql-apm-fingerprint.service
sudo systemctl status sql-apm-postgresql.service --no-pager
```

真实重启验证放在安装定时器之后，保留错过的一次运行及重启后补跑记录。

## 9. 配置由验证机拉取开发机日志

本节起使用已安装的默认 `sql_apm` schema，后续操作只走 daily；首轮 full／rebuild 九任务的
实施方证据保留，复现方法移至附录 A。日常构建的版本数不沿用九任务的 5／4。

完成第 1–8 节后可直接从本节继续。若使用**第 8 步完成的快照**供实施方补验，须先确认补验证据
已备份，再由用户恢复同一快照，重设第 1 节环境变量后继续；不重复初始化实例或创建密码。
机器重启后三个服务均应自动启动：

```bash
cd "$APM_APP"
systemctl is-active sql-apm-postgresql.service
systemctl is-active sql-apm-grafana.service sql-apm-fingerprint.service
"$APM_PG_BIN/psql" "$SQL_APM_DSN" -X -v ON_ERROR_STOP=1 -c \
  'SELECT (SELECT count(*) FROM sql_apm.source_file) AS files, (SELECT count(*) FROM sql_apm.current_version) AS versions;'
```

开始本节前的预期为 `files=0, versions=0`。若已有数据，先核对起点或恢复点；不要删除业务表后继续。
当前安装程序的提交以第 1–8 节交付记录为准；使用修订手册时，另行记录它与已安装包的产品文件一致性。

### 9.1 核对反向免密与源端

本轮用开发机已有的 55 份日志作为源端，目标机通过随包的 `daily/fetch-logs.sh` 主动拉取。
`daily run` 负责处理接收目录；定时器可将“拉取、daily”接成一次任务，见第 13.1 节。
日志原件保留在开发机，目标机接收目录为 `daily-replay/inbox`。不用旧版“先 scp 全部日志、再复制并手放标记”的步骤。

以下在**验证机**执行。按交付记录填写开发机账号、地址、端口及源端根目录，路径不含空格或引号：

```bash
export APM_FETCH_LOGIN='读取账号@开发机地址'
export APM_FETCH_PORT=22
export APM_FETCH_SOURCE_ROOT='填写开发机日志根目录，其下包含119和120'
ssh -n -o BatchMode=yes -o StrictHostKeyChecking=yes -p "$APM_FETCH_PORT" \
  "$APM_FETCH_LOGIN" "test -r '$APM_FETCH_SOURCE_ROOT/119' && test -r '$APM_FETCH_SOURCE_ROOT/120'"
```

预期退出 0；未知主机密钥或免密失败先处理，不关闭主机密钥校验。仅有“开发机到验证机”免密不足以拉取。
源端账号需要列目录、读文件权限；本节摘要复核另使用源端 `sha256sum`，全部只读。

### 9.2 创建空接收目录及固定配置

这段从验收包的固定清单生成摘要文件，沿用已确认的来源声明、30 天窗口和默认门槛。
只创建配置及空目录，不复制日志、不生成标记、不连接数据库；已有目录时拒绝覆盖。
使用本次 v1.0.0 候选的验收包，不混用旧程序或验收目录。

```bash
cd "$APM_APP"
.venv/bin/python - <<'PY'
import hashlib
import json
import os
from pathlib import Path
root = Path(os.environ['APM_ROOT'])
verification = Path(os.environ['APM_VERIFY'])
manifest_path = verification / 'docs/reports/data/log-supplement-manifest-2026-09-28.json'
baseline_path = verification / 'docs/reports/data/kylin-alma-baseline-2026-10-02.json'
expected = json.loads(baseline_path.read_text())['provenance']['input_manifest_sha256']
assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == expected
manifest = json.loads(manifest_path.read_text())
config, replay = root / 'config', root / 'daily-replay'
if config.exists() or replay.exists():
    raise SystemExit('config 或 daily-replay 已存在，请先核对已执行到哪一步，不覆盖。')
config.mkdir(mode=0o700)
replay.mkdir(mode=0o700)
clusters = ['119', '120']
sources = {}
checks = {'logs': [], 'first': [], 'incremental': []}
for cluster, cutoff in [('119', '2026-07-28'), ('120', '2026-09-16')]:
    (replay / 'inbox' / cluster).mkdir(parents=True, mode=0o700)
    sources['daily-' + cluster] = dict(cluster=cluster, build='HashData Warehouse 3.13.13',
        timezone='UTC+08:00', declaration='Issue29-confirmed-local-master-files')
    for member in manifest['clusters'][cluster]['files']:
        line = member['sha256'] + '  ' + cluster + '/' + member['file']
        checks['logs'].append(line)
        phase = 'first' if member['file'][5:15] <= cutoff else 'incremental'
        checks[phase].append(line)
assert [len(checks[k]) for k in ['logs', 'first', 'incremental']] == [55, 39, 16]
def save(name, value):
    (replay / name).write_text(json.dumps(value, indent=2) + '\n')
save('import.json', dict(version=1, clusters=clusters, sources=sources, batches={}))
save('training.json', dict(version=1, clusters=clusters, window=dict(days=30)))
save('daily.json', dict(version=1, import_config='import.json', training_config='training.json',
    sources={name: dict(directory='inbox/' + item['cluster']) for name, item in sources.items()},
    workers=4, cleanup=dict(enabled=True), raw_files=dict(retention_days='off')))
for name, lines in checks.items():
    (config / (name + '.SHA256SUMS')).write_text('\n'.join(lines) + '\n')
print('配置完成：空接收目录；首批39文件、追加16文件；自动删除暂时关闭。')
PY
install -d -m 0700 "$APM_ROOT/records/daily"
for cluster in 119 120; do
  printf 'daily-%s %s %s/%s %s/daily-replay/inbox/%s %s\n' \
    "$cluster" "$APM_FETCH_LOGIN" "$APM_FETCH_SOURCE_ROOT" "$cluster" \
    "$APM_ROOT" "$cluster" "$APM_FETCH_PORT" > "$APM_ROOT/config/fetch-$cluster.conf"
done
cat "$APM_ROOT/config/fetch-119.conf" "$APM_ROOT/config/fetch-120.conf" > "$APM_ROOT/config/fetch-all.conf"
```

拉取前先从验证机只读核对源端 55 个文件的 SHA-256；清单通过标准输入传给源端，不在源端写文件：

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -p "$APM_FETCH_PORT" "$APM_FETCH_LOGIN" \
  "cd '$APM_FETCH_SOURCE_ROOT' && sha256sum -c -" < "$APM_ROOT/config/logs.SHA256SUMS" \
  > "$APM_ROOT/records/daily/source-before.log"
```

预期 55 项全部 OK。失败保留输出并停止；不改清单或略过缺失文件。

## 10. 拉取首批、追加日期并运行 daily

以下全部在验证机执行。保持默认四解析进程、5 秒归一化期限和 30 天窗口；自动清理开启，
原始文件删除暂为 off，至第 13.2 节单独验证。日志拉取脚本逐日确认文件到齐后自行放标记，**不再手工 touch**。
这批是历史样本，必须指定日期；默认拉取最近七天不会取到这些样本。

### 10.1 首批日期

先拉 119 的 7 月 1–28 日与 120 的 9 月 13–16 日，后续日期暂不拉取。
119 的空缺日期没有文件时不生成标记；预期 26、4 个日期标记，分别覆盖 26、13 个文件。

```bash
cd "$APM_APP"
daily/fetch-logs.sh --config "$APM_ROOT/config/fetch-119.conf" --from 2026-07-01 --to 2026-07-28 \
  > "$APM_ROOT/records/daily/first-fetch-119.log" 2>&1
daily/fetch-logs.sh --config "$APM_ROOT/config/fetch-120.conf" --from 2026-09-13 --to 2026-09-16 \
  > "$APM_ROOT/records/daily/first-fetch-120.log" 2>&1
(cd "$APM_ROOT/daily-replay/inbox" && sha256sum -c "$APM_ROOT/config/first.SHA256SUMS")
find "$APM_ROOT/daily-replay/inbox" -type f -name '*.complete' -size 0c | wc -l
```

预期 39 项摘要全部 OK、30 个空标记。通过后执行首批 daily：

```bash
.venv/bin/python -m sql_apm daily run --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm \
  > "$APM_ROOT/records/daily/first.log" 2>&1
.venv/bin/python -m sql_apm daily status --schema sql_apm \
  > "$APM_ROOT/records/daily/first-status.json"
for cluster in 119 120; do
  .venv/bin/python -m sql_apm status --cluster "$cluster" \
    > "$APM_ROOT/records/daily/first-$cluster-status.json"
  .venv/bin/python -m sql_apm history --cluster "$cluster" \
    > "$APM_ROOT/records/daily/first-$cluster-history.json"
done
```

预期 daily 成功；两集群各发布一个版本，截止日分别为 2026-07-28、2026-09-16。
`daily status` 预期没有待处理问题：后续 6 天的文件尚未拉取，所以没有 `files_without_marker` 提示。
旧复制路径中预先放入全部文件的六条提示不适用于这条拉取路径。
每个 status 的 `current.fingerprint_normalization_timeout` 必须为 0；不能只看发布成功。

### 10.2 追加剩余日期

拉取余下 6 个日期，脚本自动放标记，再用同一配置执行 daily。

```bash
daily/fetch-logs.sh --config "$APM_ROOT/config/fetch-119.conf" --from 2026-07-29 --to 2026-07-31 \
  > "$APM_ROOT/records/daily/incremental-fetch-119.log" 2>&1
daily/fetch-logs.sh --config "$APM_ROOT/config/fetch-120.conf" --from 2026-09-17 --to 2026-09-19 \
  > "$APM_ROOT/records/daily/incremental-fetch-120.log" 2>&1
(cd "$APM_ROOT/daily-replay/inbox" && sha256sum -c "$APM_ROOT/config/incremental.SHA256SUMS")
(cd "$APM_ROOT/daily-replay/inbox" && sha256sum -c "$APM_ROOT/config/logs.SHA256SUMS")
find "$APM_ROOT/daily-replay/inbox" -type f -name '*.complete' -size 0c | wc -l
```

预期追加 16 项摘要通过，全部 55 项摘要通过，累计 36 个空标记。通过后执行：

```bash
.venv/bin/python -m sql_apm daily run --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm \
  > "$APM_ROOT/records/daily/incremental.log" 2>&1
.venv/bin/python -m sql_apm daily status --schema sql_apm \
  > "$APM_ROOT/records/daily/incremental-status.json"
for cluster in 119 120; do
  .venv/bin/python -m sql_apm status --cluster "$cluster" \
    > "$APM_ROOT/records/daily/incremental-$cluster-status.json"
  .venv/bin/python -m sql_apm history --cluster "$cluster" \
    > "$APM_ROOT/records/daily/incremental-$cluster-history.json"
done
```

预期只新增 16 个文件的导入，累计 55 个。两个集群各有两个成功版本，当前截止日分别为
2026-07-31、2026-09-19，当前版本的发布前驱对应本节首批版本，归一化超时均为 0，待处理问题清零。
一个 daily 按日期导入本次所有齐全的新日期，再按构建间隔决定是否构建，
因此这条验收路径不要求每个日期发布一个版本。

选批数量也与附录九任务不同：daily 按日建批。119 最终窗口从 7 月 2 日开始，
7 月 1 日批次的文件末条日志早于窗口，故最终为 29 个完成批次、28 个入选、1 个排除；
120 为 7 个完成批次且全部入选，两者均无回退。首批分别为 26、4 个完成批次且全部入选。
人工九任务把多日放在同一批次，窗口外记录在批次内部过滤；批次数不同不代表统计不同，第 11 节会逐项验证。

### 10.3 无新日志重复运行

不添加文件或标记，再执行一次并保存输出：

```bash
.venv/bin/python -m sql_apm daily run --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm \
  > "$APM_ROOT/records/daily/repeat.log" 2>&1
.venv/bin/python -m sql_apm daily status --schema sql_apm \
  > "$APM_ROOT/records/daily/repeat-status.json"
for cluster in 119 120; do
  .venv/bin/python -m sql_apm status --cluster "$cluster" \
    > "$APM_ROOT/records/daily/repeat-$cluster-status.json"
  .venv/bin/python -m sql_apm history --cluster "$cluster" \
    > "$APM_ROOT/records/daily/repeat-$cluster-history.json"
done
```

预期新增一次 daily 运行记录，文件不重复导入，当前版本和两个发布历史不增加，待处理问题为零。
若执行失败，保存日志，按[每日运行](daily-run.md)处理，再以同一配置接续；
已成功的日期不重复导入。实际任务已成功、仅核对步骤失败时，先修复核对，不人为新增构建。

## 11. 与首轮人工基准逐项比较

实施方交付的 `manual-baseline/` 是首轮**同一验证机**已完成九任务的最终统计快照，含
`results.json`、四个 `.jsonl.gz` 文件及 `SHA256SUMS`。这份基准与本轮程序产品文件逐项相同，
通过交付记录核对清单摘要；它单独内网传输，不包含在公开程序包中。

在开发机执行，`APM_MANUAL_BASELINE` 填交付记录给出的目录：

```bash
APM_MANUAL_BASELINE='填写交付记录中的 manual-baseline 目录'
(cd "$APM_MANUAL_BASELINE" && sha256sum -c SHA256SUMS)
scp -r -P "$APM_SSH_PORT" "$APM_MANUAL_BASELINE" "$APM_SSH_TARGET:/data/sql-apm/"
```

目标机执行：

```bash
cd "$APM_APP"
(cd "$APM_ROOT/manual-baseline" && sha256sum -c SHA256SUMS)
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" snapshot --app-root "$APM_APP" \
  --schema sql_apm --output "$APM_ROOT/records/daily-results"
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" compare \
  --manual "$APM_ROOT/manual-baseline" --daily "$APM_ROOT/records/daily-results" \
  --output "$APM_ROOT/records/manual-daily-comparison.json"
```

预期 `passed=true`，配置、截止日、窗口一致；两张统计表逐行比较，所有统计值及 NULL 状态精确相同，
绝对误差上限为 0。本节不使用 `--cross-machine`，也不把本轮版本数与九任务的 5／4 比较。

| 集群 | 截止日 | 正式统计行 | 观察统计行 |
| --- | --- | ---: | ---: |
| 119 | 2026-07-31 | 537198 | 180 |
| 120 | 2026-09-19 | 5850925 | 6748 |

合计 6,395,051 行。任何无法解释的差异均未通过；保留两份快照、比较输出和产品摘要，先定位原因。
配置学习示例移至附录 B，在本轮验收完成并保留最终快照后按需执行。

## 12. 空闲时合成清理验证

确认第 10、11 节结束，当前没有其他 CPU 密集任务。此命令创建自己的私有临时 PG，
不连接演练库，包括预览、保护、锁等待、分批恢复和进程终止回放。

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" cleanup > "$APM_ROOT/records/verify-cleanup.log" 2>&1
```

预期退出 0，保存实际检查项数。锁等待的时间断言接近 10 秒；失败保留原始输出并分析。
若只有该时间上界超出，也不能自行放宽测试，按 Issue 契约提请用户决定。

## 13. 定时器、清理副本、传输与看板验收

继续使用第 10 节的 `daily-replay/daily.json` 和默认 `sql_apm` schema。
不再创建第二套 schema，也不重复导入真实日志。日常配置及问题处理见[每日运行](daily-run.md)。

### 13.1 安装可选定时器

先用下面命令生成文件，把 `--at` 改成服务器本地时间中接下来几分钟的时刻，观察实际启动。
普通账号生成文件；复制、重新加载、启用和停用需要 root。

```bash
.venv/bin/python scripts/daily/install.py --app-root "$APM_APP" --python "$APM_APP/.venv/bin/python" \
  --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm \
  --fetch-config "$APM_ROOT/config/fetch-all.conf" \
  --dsn "$SQL_APM_DSN" --passfile "$PGPASSFILE" --user "$APM_RUN_USER" \
  --at 17:00 --max-hours 12 --check-seconds 10 --output "$APM_ROOT/daily-units"
sudo cp "$APM_ROOT/daily-units/sql-apm-daily.service" "$APM_ROOT/daily-units/sql-apm-daily.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sql-apm-daily.timer
systemctl list-timers sql-apm-daily.timer
```

定时任务先用 `fetch-all.conf` 拉取最近七天，再执行 daily。源端只有历史样本时输出 `no_files` 正常，
不会重新拉取 7 月／9 月样本；手动 `daily run` 本身不会执行拉取。拉取失败时仍会处理已放标记的日期，
所以同时检查拉取输出和 daily 状态。

到点后查 `sudo journalctl -u sql-apm-daily.service` 和 `daily status --schema sql_apm`，
预期启动方式为定时，已经导入的日期和当前版本保持不变。改时刻重新生成两文件并执行
`sudo systemctl daemon-reload`、`sudo systemctl restart sql-apm-daily.timer`。
停用用 `sudo systemctl disable --now sql-apm-daily.timer`；停止当前运行另用 `sudo systemctl stop sql-apm-daily.service`。
保持 timer 启用，安排关机期间错过一次运行后真实重启；不人工启动数据库，核对三个服务自启和一次补跑成功。
不通过修改服务器日期触发演练。最长运行、重叠和强制结束的自动故障记录由实施方另存。

### 13.2 自动删除只针对演练副本

保留首轮统计和状态记录后，将演练配置的原始文件保留天数改成 1，执行一次。
历史样本已经早于此界限，预期已成功导入的 55 个副本及齐全标记被删除；开发机的原始日志目录保持不变。

```bash
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ['APM_ROOT']) / 'daily-replay/daily.json'
x = json.loads(p.read_text())
x['raw_files']['retention_days'] = 1
p.write_text(json.dumps(x, indent=2) + '\n')
PY
.venv/bin/python -m sql_apm daily run --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm \
  > "$APM_ROOT/records/daily/delete.log" 2>&1
.venv/bin/python -m sql_apm daily status --schema sql_apm \
  > "$APM_ROOT/records/daily/delete-status.json"
```

删除后从验证机再次只读核对开发机原始日志摘要，并导出当前版本与删除前比较：

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -p "$APM_FETCH_PORT" "$APM_FETCH_LOGIN" \
  "cd '$APM_FETCH_SOURCE_ROOT' && sha256sum -c -" < "$APM_ROOT/config/logs.SHA256SUMS" \
  > "$APM_ROOT/records/daily/source-after-delete.log"
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" snapshot --app-root "$APM_APP" \
  --schema sql_apm --output "$APM_ROOT/records/daily-after-delete-results"
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" compare \
  --manual "$APM_ROOT/records/daily-results" --daily "$APM_ROOT/records/daily-after-delete-results" \
  --output "$APM_ROOT/records/daily-delete-comparison.json"
```

预期比较 `passed=true`，当前版本及两表统计零差异。检查清理和删除两个步骤均有记录；当前月份受保护时结果清理为零是正常情况。
不要求实际删除旧月统计结果，不调整服务器日期。重新复制已删除日期用于演练前先理解冻结清单规则，不能随意改名或增加文件。

### 13.3 模拟源端的传输

使用演练机本机 SSH 的独立目录作源端；不连接生产主机。先为执行账号配置到本机的密钥登录，
并核对主机密钥加入 known_hosts。源端账号只需登录及目录、文件读取权限，见每日运行章节“传输脚本”。
下例的目标目录不在每日运行配置中，合成 CSV 不会混入真实统计。

初始快照上可以用以下步骤配置本机免密。已有默认密钥时沿用其公钥，不覆盖密钥；
主机公钥直接读取本机 SSH 的公钥文件。若 known_hosts 已有不同的记录，连接会拒绝，先核对原因，不关闭校验。

```bash
install -d -m 0700 "$HOME/.ssh"
if [ ! -e "$HOME/.ssh/id_ed25519" ]; then
  ssh-keygen -q -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519"
fi
test -f "$HOME/.ssh/id_ed25519.pub"
touch "$HOME/.ssh/authorized_keys" "$HOME/.ssh/known_hosts"
chmod 600 "$HOME/.ssh/authorized_keys" "$HOME/.ssh/known_hosts"
if ! grep -qxF "$(cat "$HOME/.ssh/id_ed25519.pub")" "$HOME/.ssh/authorized_keys"; then
  cat "$HOME/.ssh/id_ed25519.pub" >> "$HOME/.ssh/authorized_keys"
fi
if ! ssh-keygen -F 127.0.0.1 -f "$HOME/.ssh/known_hosts" >/dev/null; then
  sudo awk '{print "127.0.0.1 " $1 " " $2}' /etc/ssh/ssh_host_ed25519_key.pub >> "$HOME/.ssh/known_hosts"
fi
ssh -n -o BatchMode=yes -o StrictHostKeyChecking=yes "$APM_RUN_USER@127.0.0.1" true
```

```bash
mkdir -p "$APM_ROOT/mock-source" "$APM_ROOT/mock-inbox"
APM_YESTERDAY="$(date -d yesterday +%F)"
printf 'synthetic transfer fixture\n' > "$APM_ROOT/mock-source/gpdb-${APM_YESTERDAY}_000000.csv"
printf 'synthetic dated fixture\n' > "$APM_ROOT/mock-source/gpdb-2026-07-01_000000.csv"
printf 'mock %s@127.0.0.1 %s %s 22\n' "$APM_RUN_USER" "$APM_ROOT/mock-source" "$APM_ROOT/mock-inbox" \
  > "$APM_ROOT/config/mock-fetch.conf"
daily/fetch-logs.sh --config "$APM_ROOT/config/mock-fetch.conf"
daily/fetch-logs.sh --config "$APM_ROOT/config/mock-fetch.conf" --date 2026-07-01
sha256sum "$APM_ROOT/mock-source/"*.csv "$APM_ROOT/mock-inbox/"*.csv
ls -l "$APM_ROOT/mock-inbox/"*.complete
```

预期昨天与指定日期文件逐项摘要一致，文件到齐后才有对应的空标记。缺少免密登录的失败检查由实施方使用独立测试身份进行，
不修改现有 SSH 登录凭据；失败不得产生齐全标记。常规部署若需要自动传输，给定时器生成命令增加
`--fetch-config 配置文件`，重装并重启 timer；本次模拟源端不加入真实日志的每日运行配置。

### 13.4 运行状态看板与用户记录

本轮直接在第 8 节已配置的 `sql_apm` 中构建，Grafana 数据源和指纹服务沿用现有配置，
不切换 schema、不重建密码。确认 PostgreSQL、Grafana 和指纹服务均正常后开始。

按[检索指南](search-guide.md)亲自完成三种检索的命中和未命中、列表到详情、三个详情区与比较，
并核对“运行状态”与 `daily status` 的日期、版本、步骤、问题一致。
将包身份、步骤结果、耗时与问题保存到[验证记录](kylin-validation-record.md)；
实施方可依据实际证据与用户确认整理记录，无需用户重复手填。
补验记录与用户记录分别保存；若从第 8 步快照接续，用户第 1–8 节的手动记录继续保留。
用户完成后需明确确认结果，实施方代跑不计为用户验收通过。

## 附录 A. 实施方九任务与基准比对

本附录保留首轮实施方九任务的复现方法；不属于本次用户第 9 节起的操作路径。
仅在独立的全新验收库运行，不能直接追加到第 10 节已完成的 daily 库。

保持默认四解析进程和 5 秒归一化期限。辅助工具实际调用 full／rebuild、status／history，
逐次比较原 Alma 业务基准，保存阶段耗时、任务期间内存最高占用、OOM 计数、数据库大小、
磁盘占用和包身份。每次 normalization_timeouts 必须为 0；不能把发布成功当作归一化完整。

新增选批字段取自 snapshot_finished 进度行：completed_batches 是集群全部已完成批次数，
selected_batches 是本次整批入选数，excluded_batches 是两者之差；window_fallback 表示
有完成批次但一个都未选到而回退全部。四个字段与开发机同候选包生成的基准逐项比较。
本九任务均应没有排除和回退。选择只看文件 last_log_at 与窗口起点，事件再按完整窗口过滤。

本附录使用独立演练目录及全新库。把原始 119／120 目录传入该目录的 `logs` 后，先生成本附录配置；
不要使用第 9 节为分批拉取生成的配置目录，也不要覆盖已有用户记录：

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" prepare \
  --logs "$APM_ROOT/logs" --output "$APM_ROOT/config"
(cd "$APM_ROOT/logs" && sha256sum -c "$APM_ROOT/config/logs.SHA256SUMS")
```

交付目录的 `development-baseline/` 中包含开发机基准文件、八个统计数值 `.jsonl.gz` 文件和校验清单。
先在开发机核对交付记录中的清单摘要，再检查文件并传输：

```bash
APM_BASELINE_DIR="$PWD/var/issue52/deliveries/$APM_EXPECTED_COMMIT/development-baseline"
(cd "$APM_BASELINE_DIR" && sha256sum -c SHA256SUMS)
scp -P "$APM_SSH_PORT" "$APM_BASELINE_DIR/development-baseline.json" \
  "$APM_BASELINE_DIR/"*.jsonl.gz "$APM_BASELINE_DIR/SHA256SUMS" "$APM_SSH_TARGET:/data/sql-apm/config/"
```

目标机再核对文件：

```bash
(cd "$APM_ROOT/config" && sha256sum -c SHA256SUMS)
```

它不替换原 Alma 基准，只补充新字段和配置示例预期；指南工具在重新构建前核对数值文件摘要。
下面两个循环只执行一次；set -e 保证任一步失败时停止。

```bash
cd "$APM_APP"
for step in 0 1 2 3 4; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" run --cluster 119 --step "$step" \
    --config "$APM_ROOT/config" --records "$APM_ROOT/records/tasks" --data-root "$APM_ROOT" \
    --workers "$APM_WORKERS" --program-commit "$APM_EXPECTED_COMMIT" \
    --selection-baseline "$APM_ROOT/config/development-baseline.json"
done
for step in 0 1 2 3; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" run --cluster 120 --step "$step" \
    --config "$APM_ROOT/config" --records "$APM_ROOT/records/tasks" --data-root "$APM_ROOT" \
    --workers "$APM_WORKERS" --program-commit "$APM_EXPECTED_COMMIT" \
    --selection-baseline "$APM_ROOT/config/development-baseline.json"
done
```

| 集群／step | 截止日 | 累计导入文件 | 正式分组 | 正式统计行 | 观察统计行 |
| --- | --- | ---: | ---: | ---: | ---: |
| 119/0 | 2026-07-28 | 26 | 51,266 | 509,766 | 164 |
| 119/1 | 2026-07-29 | 28 | 52,897 | 525,074 | 169 |
| 119/2 | 2026-07-30 | 29 | 54,577 | 540,978 | 178 |
| 119/3 | 2026-07-31 | 30 | 53,786 | 537,198 | 180 |
| 119/4 重建 | 2026-07-31 | 30 | 53,786 | 537,198 | 180 |
| 120/0 | 2026-09-16 | 13 | 404,794 | 2,819,989 | 4,040 |
| 120/1 | 2026-09-17 | 17 | 607,309 | 4,076,170 | 4,953 |
| 120/2 | 2026-09-18 | 21 | 676,989 | 4,647,376 | 5,918 |
| 120/3 | 2026-09-19 | 25 | 873,957 | 5,850,925 | 6,748 |

此外逐项比较：每文件导入计数／问题码、训练状态及原因、五类计时、正式／观察五层行数与
分组数、观察资格与原因、六项发布检查、窗口起止、import_attempt 数和发布前驱链。
版本／任务 ID 与时间为每次生成，不要求与 Alma 相同；验证它们在本次查询中的相互对应。
预期九次 passed=true，119 当前第 5 版、120 当前第 4 版，重建不增加导入尝试。

手工查询可随时执行：

```bash
.venv/bin/python -m sql_apm status --cluster 119
.venv/bin/python -m sql_apm history --cluster 119
.venv/bin/python -m sql_apm status --cluster 120
.venv/bin/python -m sql_apm history --cluster 120
```

失败：保留对应 `.log` 与 `.json`，按[流程恢复说明](build-publication.md)检查任务和当前版本。
若实际 CLI 已完成而后续核对失败，不再次 full/rebuild 产生额外版本；先修复记录／核对步骤。
任何无法解释的差异均未通过；产品缺陷另开 Issue，不在本手册中修改产品计算。

## 附录 B. 可选的配置指南演练

本附录会改变当前版本，须在本轮最终验收及证据备份完成后另行安排。
以下工具和版本预期基于附录 A 的独立九任务库，不直接用于本轮 daily 库。

这些示例用于学习配置，按需要执行，不属于九任务本身。先保存九任务全部结果（119 第 5 版，120 第 4 版），再按[配置指南](configuration-guide.md)
依次运行 window、threshold、template、exclusion 四个示例；每项从原配置生成独立配置，
在 119 上重新构建一次，不累加改动。开发机还执行 retention、workers、import 示例。

```bash
for example in window threshold template exclusion; do
  "$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
    --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" \
    --baseline "$APM_ROOT/config/development-baseline.json" run "$example"
done
```

预期分别为排除批次、仅充足性标记数量变化、模板排除计数、时段排除计数；四项结果与开发机
相同产品文件候选、相同命令逐项比较。业务计数、选批、排除原因、样本不足计数和两张统计表的
非对数字段精确相同（排除每次生成的 build_id、partition_id）。log_median、log_mad 逐行
比较，允许绝对差不超过 1e-12，NULL 状态必须相同；这是本轮已确认的跨系统数学库舍入口径，
不是产品计算参数。结果记录每列最大差、差异行数、NULL 差异和越界数；越界或其他字段差异均失败。
门槛修改前后仍在各自机器上精确比较全部统计值，清理前后也不使用此跨机误差界限。
模板原文不输出到公开记录。完成后 119 有 9 个版本，120 仍为 4 个。
遇到差异停止，保留文件；不要再次运行已成功的 rebuild 增加版本。
