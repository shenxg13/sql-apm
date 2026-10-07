# Kylin V10 SP2 离线部署与验证

本手册交付 SQL APM `v0.2.0` 预发布，**不用于生产**。目标为已恢复初始快照的
Kylin V10 SP2 x86_64 专用演练机，Python 3.9.5、PostgreSQL 17.10、结构 1.9.0。
候选提交见 PROGRAM_COMMIT，全部原稿与文件摘要见 RELEASE.json；应用版本不等于结构版本。
v0.1.0 的数据库必须重建；后续 0.x 之间不承诺兼容。

程序包根目录有 INSTALL.html（本手册，含配置指南）、DATABASE.html（结构说明）、
RELEASE.html（入口及发布说明）。GitHub Release 附件只有程序包和校验文件；
完整离线包和独立验收包内网交付。裸机安装需要完整离线包。

按 [Issue #45](https://github.com/shenxg13/sql-apm/issues/45) 已确认范围，本轮由实施方从裸机
执行一次，系统编译依赖走离线 RPM 路径，不要求用户独立重跑。先完成三份文档、制包和
开发机检查，在 Issue／PR 留下候选提交、摘要、本地结果、目标机初始状态和执行计划，
即可开始本轮已授权的传输和实测（含最后拨时钟）；目标机不是初始状态时停止，不自行清理。
实测完成后用户按[记录模板](kylin-validation-record.md)检查三份 HTML。
打标签与发布另行取得用户对精确制品的确认。

## 1. 参数与前置检查

以下在目标机以具备免密 sudo 的执行账号运行；每个新终端先重新填写本节变量。
解释器路径和允许客户端网段只在这里定义；不从 `.env` 执行配置。

```bash
set -euo pipefail
export APM_ROOT=/data/sql-apm
export APM_RUN_USER=sfmon
export APM_RUN_GROUP="$(id -gn "$APM_RUN_USER")"
test "$(id -un)" = "$APM_RUN_USER"
export APM_PYTHON="$APM_ROOT/python-3.9.5/bin/python3.9"
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
```

## 2. 准备基础工具与离线依赖

依赖没有变化，复用开发机保存的源码、wheel、初始软件包状态收集的 RPM 及签名、来源、
摘要证据，不重复收集或修改既有制品。完整离线包含 RPM 仓库索引。
以下在已获授权的目标机准备目录与 tar；tar 是解包工具，编译依赖在第 4 节离线安装。

```bash
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" "$APM_ROOT" "$APM_ROOT/setup"
if command -v tar >/dev/null 2>&1; then
  rpm -q tar
else
  sudo yum install -y tar
fi
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
APM_DELIVERY_DIR="$PWD/var/issue45/deliveries/$APM_BUILD_COMMIT"
var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
  --commit "$APM_BUILD_COMMIT" --version v0.2.0 --kind candidate \
  --output "$APM_DELIVERY_DIR/release"
.venv/bin/python scripts/deployment/build_bundle.py \
  --output "$APM_DELIVERY_DIR/offline-bundle" \
  --release-dir "$APM_DELIVERY_DIR/release" \
  --rpm-collection var/issue31/rpm-collection \
  --python-source var/issue33/delivery-final/offline-bundle/sources/Python-3.9.5.tgz \
  --postgres-source var/issue33/delivery-final/offline-bundle/sources/postgresql-17.10.tar.gz \
  --wheel-dir var/issue33/delivery-final/offline-bundle/wheels \
  --source-manifest var/issue33/delivery-final/offline-bundle/manifest.json
```

`--source-manifest` 复用已记录的官方来源，仍逐项核对固定摘要和 RPM 签名证据，不联网。
<!-- developer-only:end -->

按本次交付记录填写外部摘要和精确提交，不用包内自行声明的值替代。在开发机传输：

```bash
set -euo pipefail
APM_DELIVERY_DIR="$PWD/var/issue45/deliveries/$APM_EXPECTED_COMMIT"
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

预期全部 OK，`PROGRAM_COMMIT` 与交付记录一致；根目录三份 HTML 与程序包内对应文档相同。
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
tar -xzf "$APM_BUNDLE/sources/Python-3.9.5.tgz"
cd Python-3.9.5
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

预期：精确 Python 3.9.5，pip 由源码内 ensurepip 提供，两个锁定 wheel 安装成功，
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

再运行 smoke；合成清理入口在第 12 节空闲时运行。

```bash
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" smoke > "$APM_ROOT/records/verify-smoke.log" 2>&1
```

预期：PG17.10，各入口退出 0；普通测试 72 项、数据库验证 276 个 PASS、发布检查 33 项、smoke 通过。
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
listen_addresses = '$APM_SERVER'
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
local sql_apm sql_apm scram-sha-256
local all all reject
host sql_apm sql_apm $APM_CLIENT_CIDR scram-sha-256
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

预期：bootstrap、schema、check 成功，结构版本 1.9.0；项目账号通过 socket 密码认证，
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
fi
```

预期：实例监听指定 LAN 地址；演练实例规则没有 trust、没有全网 SCRAM 放行。
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

## 9. 传输日志并生成批次配置

开发机先按仓库固定 manifest 核对 55 个文件；令 APM_LOGS 指向实际输入目录，再通过 scp
将其中的 119 和 120 复制到目标机 APM_ROOT/logs，只有指定演练机接收真实日志。
本次开发机原文件仍在历史目录 raw/inbox/hashdata；目录名称不改变产品的 MPP 来源标识。
不将日志、数据库、原始 SQL 或详细诊断加入 Git。

目标机先执行 `install -d -m 0700 "$APM_ROOT/logs"`，然后在开发机执行：

```bash
APM_LOGS="$PWD/raw/inbox/hashdata"
.venv/bin/python scripts/deployment/rehearsal.py prepare \
  --logs "$APM_LOGS" --output var/issue45/source-check
scp -r -P "$APM_SSH_PORT" "$APM_LOGS/119" "$APM_LOGS/120" \
  "$APM_SSH_TARGET:/data/sql-apm/logs/"
```

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

先把交付记录中的开发机基准文件保存到 APM_ROOT/config/v020-development-baseline.json，
同时将随基准交付的八个统计数值 `.jsonl.gz` 文件放在同一 config 目录，并核对交付记录的摘要。
它不替换原 Alma 基准，只补充新字段和配置示例预期；指南工具在重新构建前核对数值文件摘要。
下面两个循环只执行一次；set -e 保证任一步失败时停止。

```bash
cd "$APM_APP"
for step in 0 1 2 3 4; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" run --cluster 119 --step "$step" \
    --config "$APM_ROOT/config" --records "$APM_ROOT/records/tasks" --data-root "$APM_ROOT" \
    --workers "$APM_WORKERS" --program-commit "$APM_EXPECTED_COMMIT" \
    --selection-baseline "$APM_ROOT/config/v020-development-baseline.json"
done
for step in 0 1 2 3; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" run --cluster 120 --step "$step" \
    --config "$APM_ROOT/config" --records "$APM_ROOT/records/tasks" --data-root "$APM_ROOT" \
    --workers "$APM_WORKERS" --program-commit "$APM_EXPECTED_COMMIT" \
    --selection-baseline "$APM_ROOT/config/v020-development-baseline.json"
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

## 11. 配置指南的四个重新构建示例

先保存九任务全部结果（119 第 5 版，120 第 4 版），再按[配置指南](configuration-guide.md)
依次运行 window、threshold、template、exclusion 四个示例；每项从原配置生成独立配置，
在 119 上重新构建一次，不累加改动。开发机还执行 retention、workers、import 示例。

```bash
for example in window threshold template exclusion; do
  "$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/guide_examples.py" \
    --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/guide" \
    --baseline "$APM_ROOT/config/v020-development-baseline.json" run "$example"
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

确认九任务和四示例已结束，当前没有其他 CPU 密集任务。此命令创建自己的私有临时 PG，
不连接演练库，包括预览、保护、锁等待、分批恢复和进程终止回放。

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" --app-root "$APM_APP" \
  --pg-bin "$APM_PG_BIN" cleanup > "$APM_ROOT/records/verify-cleanup.log" 2>&1
```

预期退出 0，保存实际检查项数。锁等待的时间断言接近 10 秒；失败保留原始输出并分析。
若只有该时间上界超出，也不能自行放宽测试，按 Issue 契约提请用户决定。

## 13. 最后一步：自然日期与模拟日期清理

本节只适用于本次已授权的专用演练机。九任务、指南示例和合成验收必须已完成且无失败。
先记录系统时钟、同步服务、运行任务和数据库状态；确认当前月份仍是本轮实测月份，
没有其他应用依赖本机时钟。所有模拟日期生成的任务和版本须在报告中明确标记。

先用自然日期预览并执行两个集群，工具核对结果没有改变，退出码为 0；执行会正常新增
清理任务及月份审计，这不属于版本结果变更。预览自身不新增记录。

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/cleanup_rehearsal.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/cleanup" natural
```

确认并暂停时间同步，再拨到 2027-01。先检查 chronyd、其他 NTP 服务和虚拟机工具；
若仍有其他同步源或无法停用，停止本节并报告，不反复强行改时钟。

```bash
date --iso-8601=seconds | tee "$APM_ROOT/records/clock-before.txt"
timedatectl | tee "$APM_ROOT/records/time-sync-before.txt"
systemctl is-active chronyd || true
systemctl is-active ntpd systemd-timesyncd vmtoolsd || true
sudo timedatectl set-ntp false
sudo systemctl stop chronyd
test "$(systemctl is-active chronyd || true)" = inactive
test "$(timedatectl show -p NTP --value)" = no
sudo date --set='2027-01-15 12:00:00 +0800'
sleep 5
date --iso-8601=seconds | tee "$APM_ROOT/records/clock-simulated.txt"
test "$(date +%Y-%m)" = 2027-01
```

在发布新月份前，预览应把实测月份标为 protected（受保护），执行不删除。

```bash
.venv/bin/python "$APM_VERIFY/scripts/deployment/cleanup_rehearsal.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/cleanup" protected
```

两个集群各按原训练配置重新构建并发布一次，把当前版本移动到新月份。
之后先以较大保留月数 12 预览（旧月 retained），再按默认 2 预览（旧月 expired），
核对范围后执行真实删除。以下辅助命令严格按此顺序调用真实 CLI 并保留每条输出：

```bash
.venv/bin/python "$APM_VERIFY/scripts/deployment/cleanup_rehearsal.py" \
  --app-root "$APM_APP" --config "$APM_ROOT/config" --records "$APM_ROOT/records/cleanup" execute
"$APM_APP/scripts/db/initialize.sh" check --host "$APM_SOCKET" --port "$APM_PORT" --pg-bin "$APM_PG_BIN"
.venv/bin/python -m sql_apm history --cluster 119 --limit 100
.venv/bin/python -m sql_apm history --cluster 120 --limit 100
```

预期：实测月份两张统计月分区消失、构建分组关联无残留，当前版本结果逐行摘要不变；
history 的实测月所有版本显示已清理，结构仍为 1.9.0。记录释放字节、月份耗时、
排他锁时长、分组关联删除行数／耗时及前后数据库大小。失败保留现场，不提前清理输出。

此后该机不恢复快照不再用于其他验收。是否拨回时钟由用户决定；实施方不自动拨回，
报告写明结束时日期、时间同步状态和模拟日期产生的记录。
