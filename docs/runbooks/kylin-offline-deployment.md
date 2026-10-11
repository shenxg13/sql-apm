# Kylin V10 SP2 离线部署与验证

本手册交付 SQL APM `v0.3.0` 预发布，**不用于生产**。目标为已恢复初始快照的
Kylin V10 SP2 x86_64 专用演练机，Python 3.13.16、PostgreSQL 17.10、结构 1.12.0。
候选提交见 PROGRAM_COMMIT，全部原稿与文件摘要见 RELEASE.json；应用版本不等于结构版本。
本版只支持全新安装，不提供从 v0.2.0 升级的步骤；后续 0.x 之间不承诺兼容。

程序包根目录有 INSTALL.html（本手册，含配置指南）、DATABASE.html（结构说明）、
SEARCH.html（检索与看板指南）、RELEASE.html（入口及发布说明）。GitHub Release 附件只有程序包和校验文件；
完整离线包和独立验收包内网交付。裸机安装需要完整离线包。

按 [Issue #52](https://github.com/shenxg13/sql-apm/issues/52) 的要求，实施方先从初始快照完整实测并保存证据；
随后用户从初始快照亲自完成第 1–8 节安装，再以 daily 完成第 9 节起的验收。
两轮均在传输前核对起点、使用离线 RPM、记录包身份与结果，
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

指纹服务日志看 `sudo journalctl -u sql-apm-fingerprint.service`，只有时间、字节数、状态和耗时，没有 SQL 原文。
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

## 9. 配置由验证机拉取开发机日志

本节起使用已安装的默认 `sql_apm` schema，后续操作只走 daily；首轮 full／rebuild 九任务的
实施方证据保留，复现方法移至附录 A。日常构建的版本数不沿用九任务的 5／4。

完成第 1–8 节后可直接从本节继续。若使用**第 8 步完成的快照**供实施方补验，须先确认补验证据
已备份，再由用户恢复同一快照，重设第 1 节环境变量后继续；不重复初始化实例或创建密码。
机器重启后 PostgreSQL 需要手动启动，Grafana 和指纹服务应已自动启动：

```bash
cd "$APM_APP"
if ! sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" status; then
  sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" -l "$APM_ROOT/pgdata/server.log" -w start
fi
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
已安装的 v0.3.0 验收包即可执行，无需替换程序或验收目录。

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
重启后 PostgreSQL 仍需人工启动；先停用 timer，重启、启动数据库，再启用 timer，可核对错过的一次补跑。
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
