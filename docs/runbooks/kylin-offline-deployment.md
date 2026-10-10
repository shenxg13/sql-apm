# Kylin V10 SP2 离线部署与验证

本手册交付 SQL APM `v0.3.0` 预发布，**不用于生产**。目标为已恢复初始快照的
Kylin V10 SP2 x86_64 专用演练机，Python 3.13.16、PostgreSQL 17.10、结构 1.12.0。
候选提交见 PROGRAM_COMMIT，全部原稿与文件摘要见 RELEASE.json；应用版本不等于结构版本。
本版只支持全新安装，不提供从 v0.2.0 升级的步骤；后续 0.x 之间不承诺兼容。

程序包根目录有 INSTALL.html（本手册，含配置指南）、DATABASE.html（结构说明）、
SEARCH.html（检索与看板指南）、RELEASE.html（入口及发布说明）。GitHub Release 附件只有程序包和校验文件；
完整离线包和独立验收包内网交付。裸机安装需要完整离线包。

按 [Issue #52](https://github.com/shenxg13/sql-apm/issues/52) 的要求，实施方先从初始快照完整实测并保存证据；
随后用户再次恢复初始快照，亲自按本手册完整手动执行并确认。两轮均在传输前核对起点、使用离线 RPM、记录包身份与结果，
不调整系统时钟。初始状态可以包含操作者为解包预先安装的 tar，须在开始记录中写明。
首次传输前先完成制包、随包文档和开发机检查；目标机不是约定起点时先核对，不自行清理。
用户完整验收通过后才能合并，打标签与发布再取得用户对精确制品的确认。

**每日运行默认会清理过期版本结果并删除到期原始文件。** 正式使用前阅读[每日运行](daily-run.md)，
关闭方式为 `cleanup.enabled=false`、`raw_files.retention_days="off"`；演练接收目录使用日志副本，原始证据另存。

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
以下在已获授权的目标机准备目录与 tar；tar 是解包工具，应在开始前由操作者准备；缺失则停止，不在本流程启用网络源。编译依赖在第 4 节离线安装。

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
APM_DELIVERY_DIR="$PWD/var/issue52/deliveries/$APM_BUILD_COMMIT"
var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
  --commit "$APM_BUILD_COMMIT" --version v0.3.0 --kind candidate \
  --output "$APM_DELIVERY_DIR/release"
.venv/bin/python scripts/deployment/build_bundle.py \
  --output "$APM_DELIVERY_DIR/offline-bundle" \
  --release-dir "$APM_DELIVERY_DIR/release" \
  --rpm-collection var/issue31/rpm-collection \
  --python-source var/issue52/downloads/Python-3.13.16.tgz \
  --postgres-source var/issue33/delivery-final/offline-bundle/sources/postgresql-17.10.tar.gz \
  --wheel-dir var/issue52/downloads/wheels --grafana-files var/issue51/downloads
```

首次组装从官方元数据核对两个 cp313 wheel 的来源和摘要。后续可增加
`--source-manifest 本次完整包目录/manifest.json` 离线复用已核对来源，仍逐项检查摘要和
RPM 签名证据；旧 cp39 包的清单不能提供新 wheel 的来源。
<!-- developer-only:end -->

按本次交付记录填写外部摘要和精确提交，不用包内自行声明的值替代。在开发机传输：

```bash
set -euo pipefail
APM_DELIVERY_DIR="$PWD/var/issue52/deliveries/$APM_EXPECTED_COMMIT"
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

预期全部 OK，`PROGRAM_COMMIT` 与交付记录一致；根目录四份 HTML 与程序包内对应文档相同。
失败停止安装，核对来源或重传，不修改摘要绕过。程序、业务日志及凭据不在目标机上联网获取。

## 4. 安装系统编译依赖

本轮只执行禁用网络源的离线 RPM 路径，命令和完整输出保存到本次记录。

```bash
mapfile -t APM_COMPILE_PACKAGES < "$APM_BUNDLE/support/compile-packages.txt"
```

```bash
# 先再验证 RPM 签名；不能接受 NOKEY 或只有 digest 没有 signature。
for package in "$APM_BUNDLE"/rpms/*.rpm; do rpm --checksig "$package"; done
sudo yum --disablerepo='*' \
  --repofrompath="apm-offline,file://$APM_BUNDLE/rpms" --enablerepo=apm-offline \
  --setopt=apm-offline.gpgcheck=1 --setopt=install_weak_deps=False \
  install -y "${APM_COMPILE_PACKAGES[@]}"
```

离线路径禁用所有已配置网络源，仅启用这次命令的 file:// 本地源；不修改 yum 配置文件。
完整 RPM 集合中的无关包不因存在于目录而全部安装；yum 按根包解析需要的依赖。
预期：安装成功，`rpm -q "${APM_COMPILE_PACKAGES[@]}"` 全部存在。
失败：保留事务错误，检查依赖或版本冲突；不加 `--skip-broken`、`--allowerasing` 或关闭签名。
该集合覆盖本次初始主机和收集时源状态，不证明不同初始安装的生产主机一定可用。

## 5. 程序与 Python 离线构建

```bash
cd "$APM_ROOT"
test ! -e "$APM_APP"
tar -xzf "$APM_BUNDLE/program.tar.gz"
test ! -e "$APM_VERIFY"
tar -xzf "$APM_BUNDLE/verification.tar.gz"
(cd "$APM_APP" && sha256sum -c SHA256SUMS)
(cd "$APM_VERIFY" && sha256sum -c SHA256SUMS)
test "$(cat "$APM_APP/PROGRAM_COMMIT")" = "$APM_EXPECTED_COMMIT"
test "$(cat "$APM_VERIFY/PROGRAM_COMMIT")" = "$APM_EXPECTED_COMMIT"
mkdir -p "$APM_ROOT/build" "$APM_ROOT/records"
cd "$APM_ROOT/build"
tar -xzf "$APM_BUNDLE/sources/Python-3.13.16.tgz"
cd Python-3.13.16
./configure --prefix="$(dirname "$(dirname "$APM_PYTHON")")" --with-ensurepip=install \
  > "$APM_ROOT/records/python-configure.log" 2>&1
make -j4 > "$APM_ROOT/records/python-make.log" 2>&1
make install > "$APM_ROOT/records/python-install.log" 2>&1
"$APM_PYTHON" --version
"$APM_PYTHON" -m venv "$APM_APP/.venv"
cd "$APM_APP"
PIP_CONFIG_FILE=/dev/null .venv/bin/python -m pip --isolated --disable-pip-version-check \
  install --no-index --find-links "$APM_BUNDLE/wheels" --require-hashes -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python scripts/deployment/check_environment.py
.venv/bin/python scripts/deployment/verify_package.py --installed
```

预期：精确 Python 3.13.16，pip 由源码内 ensurepip 提供，两个锁定 wheel 安装成功，
`pip check` 无冲突，模块与离线功能自检输出 passed=true。不使用系统 Python 运行项目。
失败：查构建日志中的 missing modules／编译错误；缺 bz2、lzma、sqlite3、ssl 等必须补齐依赖
并重建，不能忽略。HTTP 可达性不属于离线解释器检查；可选 tkinter、nis、dbm 不作为门槛。

## 6. PostgreSQL 编译与环境自检

```bash
cd "$APM_ROOT/build"
tar -xzf "$APM_BUNDLE/sources/postgresql-17.10.tar.gz"
cd postgresql-17.10
./configure --prefix="$(dirname "$APM_PG_BIN")" --with-openssl \
  > "$APM_ROOT/records/pg-configure.log" 2>&1
make -j4 > "$APM_ROOT/records/pg-make.log" 2>&1
make install > "$APM_ROOT/records/pg-install.log" 2>&1
"$APM_PG_BIN/psql" --version
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  unit > "$APM_ROOT/records/unittest.log" 2>&1
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" database > "$APM_ROOT/records/verify.log" 2>&1
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" publication > "$APM_ROOT/records/verify-publication.log" 2>&1
```

再运行 smoke 和 search；合成清理入口在第 12 节空闲时运行。

```bash
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" smoke > "$APM_ROOT/records/verify-smoke.log" 2>&1
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" search > "$APM_ROOT/records/verify-search.log" 2>&1
```

还需执行新增的权限／指纹服务、看板静态检查和每日运行检查：

```bash
for check in views grafana daily daily-recovery; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
    --pg-bin "$APM_PG_BIN" "$check" > "$APM_ROOT/records/verify-$check.log" 2>&1
done
```

预期：PG17.10，各入口退出 0；普通测试 104 项、数据库验证 279 个 PASS、发布检查 33 项、smoke 和 search 通过。
合成清理检查 23 项；实际项数及输出摘要保存在机器记录。
开头 APPLICATION 应指向 APM_APP，不能只检查输出文件存在。测试在 verification 中，
核心代码从 app 加载；数据库检查各自创建禁用 TCP 的私有临时实例，退出后停止清理，
不连接演练库。不在目标机运行 Harness 或未列出的开发全量探针。
失败保留原输出，区分环境与业务差异，不改产品规则绕过。

## 7. 建立实例与项目数据库

```bash
if ! getent passwd postgres >/dev/null; then
  sudo useradd --system --user-group --home-dir "$APM_ROOT/postgres-home" --shell /sbin/nologin postgres
fi
command -v setfacl getfacl
sudo install -d -m 0750 -o postgres -g postgres "$APM_ROOT/pgdata" "$APM_ROOT/postgres-home"
sudo install -d -m 2755 -o postgres -g "$APM_RUN_GROUP" "$APM_SOCKET"
sudo -u postgres "$APM_PG_BIN/initdb" -D "$APM_ROOT/pgdata" -U postgres \
  --allow-group-access --auth-local=peer --auth-host=scram-sha-256 --encoding=UTF8 --locale=C
sudo find "$APM_ROOT/pgdata" "$APM_ROOT/postgres-home" -type d \
  -exec setfacl -m "u:$APM_RUN_USER:r-x,d:u:$APM_RUN_USER:r-x" {} +
sudo find "$APM_ROOT/pgdata" "$APM_ROOT/postgres-home" -type f \
  -exec setfacl -m "u:$APM_RUN_USER:r--" {} +
```

预期：专用 postgres 系统用户无需登录密码，空实例初始化成功。
若用户或 PGDATA 已存在，先核对属主及来源，不接管未知用户／数据目录，不重复 initdb。
`setfacl`／`getfacl` 在本次目标机已具备；缺失时由用户从已配置源安装 `acl`，
不将其加入编译依赖清单。sfmon 须无需 sudo 即可遍历 APM_ROOT 全部目录并读取普通文件，
包括 private 和 pgdata；其原有写权限保留，postgres 文件只增加 sfmon 的读权限。
PGDATA 保持 postgres 属主；命名 ACL 及默认 ACL 配合
[`--allow-group-access`](https://www.postgresql.org/docs/17/app-initdb.html)，
使 PG 创建目录／文件时采用 0750／0640，并继承 sfmon 的读取权限。
socket 目录用 sfmon 主组和 setgid 使锁文件继承可读组；不要给该目录设置只读默认 ACL，
否则新 socket 节点的命名 ACL 会阻止连接。目录不给 sfmon 写权限，socket 节点沿用下方 0777，
数据库连接仍受 SCRAM／peer 规则约束。
参数明确写入本实例配置，不改系统服务；以下是 8 vCPU／14 GiB 演练机的起始配置：

```bash
sudo -u postgres tee -a "$APM_ROOT/pgdata/postgresql.conf" >/dev/null <<PGCONF
listen_addresses = '127.0.0.1,$APM_SERVER'
port = $APM_PORT
unix_socket_directories = '$APM_SOCKET'
unix_socket_permissions = 0777
password_encryption = 'scram-sha-256'
shared_buffers = '512MB'
work_mem = '16MB'
maintenance_work_mem = '128MB'
max_connections = 30
max_wal_size = '2GB'
min_wal_size = '256MB'
timezone = 'Asia/Shanghai'
log_statement = 'none'
log_min_error_statement = 'panic'
PGCONF
sudo -u postgres tee "$APM_ROOT/pgdata/pg_hba.conf" >/dev/null <<PGHBA
local all postgres peer
local sql_apm sql_apm,sql_apm_ro scram-sha-256
local all all reject
host sql_apm sql_apm,sql_apm_ro 127.0.0.1/32 scram-sha-256
host sql_apm sql_apm,sql_apm_ro $APM_CLIENT_CIDR scram-sha-256
host all all 0.0.0.0/0 reject
host all all ::0/0 reject
PGHBA
sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" \
  -l "$APM_ROOT/pgdata/server.log" -w start
sudo -u postgres "$APM_APP/scripts/db/initialize.sh" bootstrap \
  --host "$APM_SOCKET" --port "$APM_PORT" --admin-user postgres --admin-database postgres \
  --pg-bin "$APM_PG_BIN"
cd "$APM_APP"
.venv/bin/python scripts/deployment/create_password.py --socket "$APM_SOCKET" \
  --port "$APM_PORT" --tcp-host "$APM_SERVER" --passfile "$PGPASSFILE"
scripts/db/initialize.sh schema --host "$APM_SOCKET" --port "$APM_PORT" --pg-bin "$APM_PG_BIN"
scripts/db/initialize.sh check --host "$APM_SOCKET" --port "$APM_PORT" --pg-bin "$APM_PG_BIN"
"$APM_PG_BIN/psql" -X -w -h "$APM_SOCKET" -p "$APM_PORT" -U sql_apm -d sql_apm \
  -c 'SELECT current_user, current_database(), version();'
stat -c '%a %n' "$PGPASSFILE"
```

预期：bootstrap、schema、check 成功，结构版本 1.12.0；项目账号通过 socket 密码认证，
密码文件权限 600。生成器不显示密码，仅通过匿名管道交给本机管理员并发送 SCRAM verifier。
凭据只保存在程序目录外的 private/；不放进 Git、命令参数、报告或 shell 历史。
private 由 sfmon 创建并拥有，目录 0700、pgpass 0600 已允许 sfmon 读取；
不为满足此要求放宽 pgpass 的组权限，否则 [libpq 会忽略密码文件](https://www.postgresql.org/docs/17/libpq-pgpass.html)。
失败：先核对 peer 身份、socket 权限、SCRAM 规则和密码文件，保留已创建对象；
密码步骤失败时保留文件，诊断后重新设置，不随意轮换其他实例的账号。

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

```bash
systemctl is-active firewalld || true
sudo ss -ltnp
sudo -u postgres "$APM_PG_BIN/psql" -X -h "$APM_SOCKET" -p "$APM_PORT" -d postgres \
  -c 'SELECT type,database,user_name,address,auth_method,error FROM pg_hba_file_rules;'
# 仅当 firewalld 正在运行时，增加限定源网段的规则：
if systemctl is-active --quiet firewalld; then
  sudo firewall-cmd --add-rich-rule="rule family=ipv4 source address=$APM_CLIENT_CIDR port port=$APM_PORT protocol=tcp accept"
  sudo firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=$APM_CLIENT_CIDR port port=$APM_PORT protocol=tcp accept"
fi
```

预期：实例同时监听 127.0.0.1 和指定内网地址；两个账号均可通过密码从允许的客户端网段连接，其余来源拒绝。
允许网段可以填整个内网；仍保留密码认证，不使用 trust。
防火墙未运行时不为本次演练启动它；运行时先检查是否已有过宽的端口规则，
有冲突则由维护者处理，不因新增窄规则就声称访问已受限。

从另一台允许网段内的开发机，用本机仓库外 0600 的 PGPASSFILE 连接明确目标：

```bash
psql -X -w -h TARGET_IP -p 5432 -U sql_apm -d sql_apm \
  -c 'BEGIN READ ONLY; SELECT current_user,current_database(),version(); COMMIT;'
```

预期：正确密码成功，另用临时 0600 文件中的错误密码重试必须失败。不要把密码写入命令行。
该账号拥有全部项目对象，查询验证保持只读；用户另用 DBA 工具查询为可选，本轮由实施方完成远程认证检查。
本次不启用 TLS；接受 LAN 中查询与结果明文传输，生产部署另行决定。
连接失败：区分路由、监听、防火墙、HBA 和密码，不关闭认证来排查。

```bash
# 按需启停；无 systemd 托管。
sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" status
sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" -m fast -w stop
sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" -l "$APM_ROOT/pgdata/server.log" -w start
```

预期：状态与启动／停止结果一致；失败先保留 server.log 和 pgdata，不删除仍运行的实例。

### 8.1 设置只读数据库账号和 Grafana 密码

bootstrap 已创建 `sql_apm_ro`。它只有查询权限，单条查询默认超时 2 分钟；可通过 bootstrap 的
`--readonly-role`、`--readonly-timeout` 指定其他角色和时限。下面以默认角色为例。
在执行账号自己的终端输入密码，输入不回显；不把密码写在命令行或录屏中。

```bash
export APM_GRAFANA="$APM_ROOT/grafana"
export APM_GRAFANA_PORT=3000
export APM_FINGERPRINT_PORT=3001
"$APM_APP/.venv/bin/python" - <<'PY'
from getpass import getpass
from pathlib import Path
import os
root = Path(os.environ['APM_ROOT']) / 'private'
root.mkdir(mode=0o700, exist_ok=True)
for name in ('readonly-password', 'grafana-admin-password', 'grafana-viewer-password'):
    path = root / name
    if path.exists():
        raise SystemExit('password file already exists: ' + name)
    first = getpass(name + ': ')
    second = getpass('再次输入: ')
    if first != second or len(first) < 12 or any(c in first for c in ':\\\r\n'):
        raise SystemExit('密码须相同，至少 12 个字符，不含冒号、反斜杠或换行')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(first + '\n')
PY
```

以下步骤需要 sudo：管理员经本地 peer 设置只读账号密码，再把生成的连接文件交给执行账号。
临时目录只用于这次密码设置，操作完成后删除其中两个临时文件。

```bash
APM_PASSWORD_STAGE="$(sudo -u postgres mktemp -d /tmp/sql-apm-readonly.XXXXXX)"
sudo install -m 0600 -o postgres -g postgres "$APM_ROOT/private/readonly-password" "$APM_PASSWORD_STAGE/password"
sudo -u postgres "$APM_APP/.venv/bin/python" "$APM_APP/scripts/grafana/readonly_password.py" \
  --admin-dsn "host=$APM_SOCKET port=$APM_PORT dbname=postgres user=postgres" \
  --port "$APM_PORT" --password-file "$APM_PASSWORD_STAGE/password" --passfile "$APM_PASSWORD_STAGE/pgpass"
sudo install -m 0600 -o "$APM_RUN_USER" -g "$APM_RUN_GROUP" "$APM_PASSWORD_STAGE/pgpass" "$APM_ROOT/private/readonly.pgpass"
sudo rm -- "$APM_PASSWORD_STAGE/password" "$APM_PASSWORD_STAGE/pgpass"
sudo rmdir -- "$APM_PASSWORD_STAGE"
PGPASSFILE="$APM_ROOT/private/readonly.pgpass" "$APM_PG_BIN/psql" -X -w \
  -h 127.0.0.1 -p "$APM_PORT" -U sql_apm_ro -d sql_apm \
  -c 'SELECT current_user, current_setting('\''statement_timeout'\'');'
```

预期使用只读账号连接成功、超时为 2min。另用该账号尝试在项目 schema 创建临时演练表，应得到权限拒绝；
允许网段内的外部机器分别以两个账号测试正确和错误密码，并从不允许来源测试 HBA 拒绝，结果单独记录。
测试连接文件保持 0600，不能把凭据放进报告。只读账号可读全部 SQL 原文，分享查询结果前须检查敏感内容。

**查看和更换项目账号随机密码。** 密码在 `$PGPASSFILE` 的每行最后一项，可在自己未录屏的本地编辑器里查看。
更换时先暂停每日定时器和写入任务，用项目账号运行下面的交互命令，再在编辑器中同步更新 passfile 的各行密码：

```bash
"$APM_PG_BIN/psql" -X -w -h "$APM_SOCKET" -p "$APM_PORT" -U sql_apm -d sql_apm -c '\password sql_apm'
chmod 600 "$PGPASSFILE"
```

密码更新后另开连接验证，失败先修正 passfile，不重复初始化。只读账号密码变更用上述管理员步骤，
同时更新它的原始密码文件与 passfile，并重启 Grafana 和指纹服务。

### 8.2 离线安装 Grafana、插件和指纹服务

```bash
cd "$APM_APP"
.venv/bin/python scripts/grafana/install.py files --home "$APM_GRAFANA" \
  --files "$APM_BUNDLE/grafana" --pg-port "$APM_PORT" --port "$APM_GRAFANA_PORT" \
  --service-port "$APM_FINGERPRINT_PORT" --user "$APM_RUN_USER" \
  --app-root "$APM_APP" --python "$APM_APP/.venv/bin/python" \
  --admin-password-file "$APM_ROOT/private/grafana-admin-password" \
  --db-password-file "$APM_ROOT/private/readonly-password" \
  --service-passfile "$APM_ROOT/private/readonly.pgpass"
sudo install -m 0644 "$APM_GRAFANA/systemd/sql-apm-grafana.service" \
  "$APM_GRAFANA/systemd/sql-apm-fingerprint.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sql-apm-grafana.service sql-apm-fingerprint.service
systemctl is-active sql-apm-grafana.service sql-apm-fingerprint.service
```

等待 Grafana 启动后创建查看账号和“用户自定义”文件夹；此步骤可重复执行。

```bash
.venv/bin/python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

url = 'http://127.0.0.1:' + os.environ['APM_GRAFANA_PORT'] + '/api/health'
for attempt in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if json.load(response).get('database') == 'ok':
                break
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(2)
else:
    raise SystemExit('Grafana 未就绪，请先检查服务日志。')
print('Grafana 已就绪')
PY
.venv/bin/python scripts/grafana/install.py accounts --port "$APM_GRAFANA_PORT" \
  --admin-password-file "$APM_ROOT/private/grafana-admin-password" \
  --viewer-password-file "$APM_ROOT/private/grafana-viewer-password" --viewer-login viewer
if systemctl is-active --quiet firewalld; then
  sudo firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=$APM_CLIENT_CIDR port port=$APM_GRAFANA_PORT protocol=tcp accept"
  sudo firewall-cmd --add-rich-rule="rule family=ipv4 source address=$APM_CLIENT_CIDR port port=$APM_GRAFANA_PORT protocol=tcp accept"
fi
```

预期：Grafana 13.2.3、Business Forms 6.3.5、Infinity 4.1.1、PostgreSQL 数据源 13.0.4，签名校验有效，
启动不下载插件。Grafana 默认 HTTP 3000；指纹服务只监听 127.0.0.1:3001；两者均经回环用只读数据库账号连接。
浏览器打开 `http://目标机地址:3000`，匿名访问应要求登录；默认 admin/admin 不可用。
admin 可管理自己的看板，viewer 只查看。MPP 下的检索、详情、列表、运行状态四个看板和两个数据源由配置装入。
真实数据导入前空列表正常；真实检索操作及结果含义见[检索指南](search-guide.md)。

指纹服务日志看 `journalctl -u sql-apm-fingerprint.service`，只有时间、字节数、状态和耗时，没有 SQL 原文。
Grafana 日志位于 `$APM_GRAFANA/logs`，启动错误也可用 journalctl 查看。

```bash
sudo ss -ltnp
sudo systemctl restart sql-apm-fingerprint.service
sudo systemctl restart sql-apm-grafana.service
```

产品更新后须重启指纹服务以加载新规则。需要停用时用 `systemctl stop`，长期停用用 `disable --now`。
两者 enable 后随主机启动，被杀后由 systemd 拉起；验收时分别重启机器和强制停止主进程验证。
PostgreSQL 仍由人工通过 pg_ctl 启动，重启机器后先启动数据库，再检查数据源与每日补跑；不额外引入数据库托管。

**HTTPS（可选）。** 部署方准备证书与私钥，在 `$APM_GRAFANA/conf/grafana.ini` 的 `[server]` 下设置
`protocol=https`、`cert_file=/证书绝对路径`、`cert_key=/私钥绝对路径` 后重启 Grafana；私钥仅服务用户可读。
安装程序重新生成配置会覆盖手工设置，重新生成前保存并随后重新应用。默认 HTTP，不自动申请证书。

**备份。** Grafana 用自己的 SQLite 文件保存账号、文件夹和用户自定义看板。
暂停 Grafana 后备份整个 data 目录及配置、版本清单；备份目录包含账号信息和可能含原文的用户看板，应限制访问。

```bash
APM_BACKUP="$APM_ROOT/backups/grafana-$(date +%Y%m%d-%H%M%S).tar.gz"
install -d -m 0700 "$APM_ROOT/backups"
sudo systemctl stop sql-apm-grafana.service
umask 077
tar -czf "$APM_BACKUP" -C "$APM_GRAFANA" data conf
sudo systemctl start sql-apm-grafana.service
sha256sum "$APM_BACKUP"
```

恢复时停止 Grafana，先保留现有目录，再用相同组件版本恢复 data/conf 和属主、权限，最后启动并核对账号及看板。
备份不包含 PostgreSQL 数据库，也不包含程序目录外 private 中的密码文件；这些按部署方的独立备份安排保存。

## 9. 传输日志并生成批次配置

开发机先按仓库固定 manifest 核对 55 个文件；令 APM_LOGS 指向实际输入目录，再通过 scp
将其中的 119 和 120 复制到目标机 APM_ROOT/logs，只有指定演练机接收真实日志。
本次开发机原文件仍在历史目录 raw/inbox/hashdata；目录名称不改变产品的 MPP 来源标识。
不将日志、数据库、原始 SQL 或详细诊断加入 Git。

目标机先执行 `install -d -m 0700 "$APM_ROOT/logs"`，然后在开发机执行：

```bash
APM_LOGS="$PWD/raw/inbox/hashdata"
APM_SOURCE_CHECK="var/issue52/source-check-$(date +%Y%m%dT%H%M%S)"
.venv/bin/python scripts/deployment/rehearsal.py prepare \
  --logs "$APM_LOGS" --output "$APM_SOURCE_CHECK"
scp -r -P "$APM_SSH_PORT" "$APM_LOGS/119" "$APM_LOGS/120" \
  "$APM_SSH_TARGET:/data/sql-apm/logs/"
```

每轮使用新的核验目录，保留先前输出；验证机恢复快照不会清除开发机上的首轮记录。

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" prepare \
  --logs "$APM_ROOT/logs" --output "$APM_ROOT/config"
cd "$APM_ROOT/logs"
sha256sum -c "$APM_ROOT/config/logs.SHA256SUMS"
```

预期：55 个文件逐项匹配，119 为 30 个、120 为 25 个，输出 verified_files=55、batches=8。
119 首批 26 文件，29 日两文件、30 和 31 日各一；120 首批 13 文件，后三日各四。
配置沿用 #29 的来源、完整性声明、空模板／排除时段和默认门槛，窗口 30 天。
失败：停止导入，核对源端清单和传输；不改摘要、删掉缺失成员或简化批次。

## 10. 默认四进程九任务与基准比对

保持默认四解析进程和 5 秒归一化期限。辅助工具实际调用 full／rebuild、status／history，
逐次比较原 Alma 业务基准，保存阶段耗时、任务期间内存最高占用、OOM 计数、数据库大小、
磁盘占用和包身份。每次 normalization_timeouts 必须为 0；不能把发布成功当作归一化完整。

新增选批字段取自 snapshot_finished 进度行：completed_batches 是集群全部已完成批次数，
selected_batches 是本次整批入选数，excluded_batches 是两者之差；window_fallback 表示
有完成批次但一个都未选到而回退全部。四个字段与开发机同候选包生成的基准逐项比较。
本九任务均应没有排除和回退。选择只看文件 last_log_at 与窗口起点，事件再按完整窗口过滤。

先把交付记录中的开发机基准文件保存到 APM_ROOT/config/development-baseline.json，
同时将随基准交付的八个统计数值 `.jsonl.gz` 文件放在同一 config 目录，并核对交付记录的摘要。
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

## 11. 可选的配置指南演练

本节会改变当前版本；本轮先跳到第 12、13 节，完成每日回放比较后再按需执行本节。

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

## 12. 空闲时合成清理验证

确认九任务和已选择执行的示例结束，当前没有其他 CPU 密集任务。此命令创建自己的私有临时 PG，
不连接演练库，包括预览、保护、锁等待、分批恢复和进程终止回放。

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" cleanup > "$APM_ROOT/records/verify-cleanup.log" 2>&1
```

预期退出 0，保存实际检查项数。锁等待的时间断言接近 10 秒；失败保留原始输出并分析。
若只有该时间上界超出，也不能自行放宽测试，按 Issue 契约提请用户决定。

## 13. 每日运行的手动演练

完成九任务后直接执行本节，先保存最终统计快照；如需第 11 节可选示例，放在本节之后执行。
日常操作的配置、问题处理和默认值见[每日运行](daily-run.md)；本节用同一批真实日志的**副本**演练，
在独立 schema `sql_apm_daily` 中重新导入，保持九任务库及其记录不变。两个 schema 共用同一实例、数据库和账号。
预留额外约一套日志与导入库、两个最终版本的磁盘空间；不要并行执行九任务和每日回放。

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" snapshot \
  --app-root "$APM_APP" --output "$APM_ROOT/records/manual-results"
sudo -u postgres "$APM_APP/scripts/db/initialize.sh" bootstrap --host "$APM_SOCKET" --port "$APM_PORT" --pg-bin "$APM_PG_BIN" \
  --schema sql_apm_daily --admin-user postgres --admin-database postgres
scripts/db/initialize.sh schema --host "$APM_SOCKET" --port "$APM_PORT" --pg-bin "$APM_PG_BIN" --schema sql_apm_daily
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" prepare \
  --config "$APM_ROOT/config" --output "$APM_ROOT/daily-replay"
```

预期 55 个文件副本核验成功，初始没有齐全标记；配置继承九任务来源和 30 天训练窗口，默认四进程、
自动清理开启，先关闭原始文件删除便于复查。逐天核对已复制的文件，再亲自放齐全标记：

```bash
.venv/bin/python - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['APM_ROOT']) / 'daily-replay/inbox'
for cluster in ('119', '120'):
    folder = root / cluster
    for day in sorted({p.name[5:15] for p in folder.glob('gpdb-*.csv*')}):
        (folder / (day + '.complete')).touch(exist_ok=False)
        print(cluster, day)
PY
.venv/bin/python -m sql_apm daily run --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm_daily \
  > "$APM_ROOT/records/daily-first.log" 2>&1
.venv/bin/python -m sql_apm daily status --schema sql_apm_daily \
  > "$APM_ROOT/records/daily-first-status.json"
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" snapshot --app-root "$APM_APP" \
  --schema sql_apm_daily --output "$APM_ROOT/records/daily-results"
.venv/bin/python "$APM_VERIFY/scripts/deployment/daily_rehearsal.py" compare \
  --manual "$APM_ROOT/records/manual-results" --daily "$APM_ROOT/records/daily-results" \
  --output "$APM_ROOT/records/manual-daily-comparison.json"
```

预期首轮逐日导入两个集群、各发布一个版本；截止日分别为 2026-07-31 和 2026-09-19，
比较 `passed=true`，同机两张统计表的全部统计值和 NULL 状态逐项一致。
只有另行比对开发机与验证机时才使用 `--cross-machine`，允许两列对数统计绝对差不超过 1e-12。
导入失败不重新创建批次，
先保留日志、按原因处理，再运行同一命令，已经成功的文件不重复导入。
要练习“只放第一天，运行，再放后面的日期”，也使用同一配置；最终全部日期齐全后比对结果应相同。

### 13.1 安装可选定时器

先用下面命令生成文件，把 `--at` 改成服务器本地时间中接下来几分钟的时刻，观察实际启动。
普通账号生成文件；复制、重新加载、启用和停用需要 root。

```bash
.venv/bin/python scripts/daily/install.py --app-root "$APM_APP" --python "$APM_APP/.venv/bin/python" \
  --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm_daily \
  --dsn "$SQL_APM_DSN" --passfile "$PGPASSFILE" --user "$APM_RUN_USER" \
  --at 17:00 --max-hours 12 --check-seconds 10 --output "$APM_ROOT/daily-units"
sudo cp "$APM_ROOT/daily-units/sql-apm-daily.service" "$APM_ROOT/daily-units/sql-apm-daily.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sql-apm-daily.timer
systemctl list-timers sql-apm-daily.timer
```

到点后查 `journalctl -u sql-apm-daily.service` 和 `daily status --schema sql_apm_daily`，
预期启动方式为定时，已经导入的日期和当前版本保持不变。改时刻重新生成两文件并执行
`sudo systemctl daemon-reload`、`sudo systemctl restart sql-apm-daily.timer`。
停用用 `sudo systemctl disable --now sql-apm-daily.timer`；停止当前运行另用 `sudo systemctl stop sql-apm-daily.service`。
重启后 PostgreSQL 仍需人工启动；先停用 timer，重启、启动数据库，再启用 timer，可核对错过的一次补跑。
不通过修改服务器日期触发演练。最长运行、重叠和强制结束的自动故障记录由实施方另存。

### 13.2 自动删除只针对演练副本

保留首轮统计和状态记录后，将演练配置的原始文件保留天数改成 1，执行一次。
历史样本已经早于此界限，预期已成功导入的 55 个副本及齐全标记被删除；原始日志目录保持不变。

```bash
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ['APM_ROOT']) / 'daily-replay/daily.json'
x = json.loads(p.read_text())
x['raw_files']['retention_days'] = 1
p.write_text(json.dumps(x, indent=2) + '\n')
PY
.venv/bin/python -m sql_apm daily run --config "$APM_ROOT/daily-replay/daily.json" --schema sql_apm_daily \
  > "$APM_ROOT/records/daily-delete.log" 2>&1
.venv/bin/python -m sql_apm daily status --schema sql_apm_daily \
  > "$APM_ROOT/records/daily-delete-status.json"
```

检查清理和删除两个步骤均有记录；当前月份受保护时结果清理为零是正常情况。
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

九任务与每日回放各使用一个 schema。要在四看板查看本次每日回放的真实数据与运行记录，
重复第 8.2 节的 `install.py files` 命令时增加 `--schema sql_apm_daily`，随后
复制新生成的两个 systemd 文件到 `/etc/systemd/system/`、执行 `sudo systemctl daemon-reload` 和
`sudo systemctl restart sql-apm-fingerprint.service sql-apm-grafana.service`。账号和 SQLite 看板数据保留，
不重复创建密码。数据源的默认 schema 来自只读账号在数据库中的 search_path，第 13 节 bootstrap 已将其设为 sql_apm_daily。
切回九任务数据时，先按第 13 节的 sudo bootstrap 命令改用 `--schema sql_apm`，再以同样的 schema 重新生成、复制单元并重启。

按[检索指南](search-guide.md)亲自完成三种检索的命中和未命中、列表到详情、三个详情区与比较，
并核对“运行状态”与 `daily status` 的日期、版本、步骤、问题一致。
将包身份、步骤结果、耗时与问题填入[验证记录](kylin-validation-record.md)。
实施方完成后先保存全部证据，再通知用户恢复快照；用户的第二轮从本手册第 1 节重新开始。
