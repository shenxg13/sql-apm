# Kylin V10 SP2 离线部署与九任务验证

[Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 的演练手册，目标是 Kylin V10 SP2
x86_64 的专用演练机；不是生产部署方案。使用 Python 3.9.5、PostgreSQL 17.10，
应用版本为 `v0.1.0`，以预发布形式交付，不用于生产。#33 将修改落库标识，下一版需要
全新数据库；0.x 预发布版本之间不保证数据库兼容。产品基准为包含 #29 的 main
`6451d140d44f4e06cc34862c3e5aff7593d7afeb`；候选包的实施提交见 `PROGRAM_COMMIT`，
文件摘要、产品基准和候选／发布类型见 `RELEASE.json`。应用版本不等于数据库结构版本。

精简程序包只包含运行所需代码、规则与 PostgreSQL 许可证、SQL／迁移资源、必要工具和
本 HTML 手册；测试和历史探针在独立验收目录。GitHub Releases 仅提供程序包及其校验文件，
完整离线包（Python、PG、wheel、RPM、程序和验收资源）继续内网交付。
只下载程序包不能完成裸机离线安装，须另备内网依赖包。正式发布在评审合并后，
经用户明确确认才打标签并发布；本手册不执行发布动作。

实施试跑与用户独立执行分别保存[记录模板](kylin-validation-record.md)。
候选包由实施方重走前，用户先恢复初始快照，并明确确认可以开始目标机试跑。
实施方完成第 1–9 节及第 10.1 节的 119 首批后，用户再次恢复快照，独立从头执行本手册的九任务。
此前原包九任务证据只有在候选产品文件逐项摘要相同时才能继承，不能替代新包部署验证。
两次合计须验证 yum 源与离线 RPM 两种系统包安装路径，并在随后完成两项源码编译。
试跑选择离线 RPM，用户重跑选择 yum 源。其余项目制品两次都离线安装。

用户恢复快照前，将已交付的离线包、外部摘要和试跑记录保留在开发机。
恢复后从第 1 节开始；第 2 节的依赖收集和第 3 节的制包属于一次性的制品准备，
独立部署复用已交付制品，按第 2 节末尾说明准备目录和 tar，执行第 3 节的传输、
解包及校验，再从第 4 节的 yum 源路径继续完成全部构建、自检、初始化和九任务。
不要在开发机已有的输出目录上重新执行制包命令。

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
export APM_SSH_TARGET='执行账号@目标机地址'
export APM_SSH_PORT=22
```

## 2. 收集编译依赖与准备基础工具

本节收集只做一次，必须在快照初始软件包状态、任何安装之前执行。收集脚本来自本 PR，
在开发机先用 scp 传入以下目录（scp 的目的主机采用第 1 节约定）：

```bash
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" "$APM_ROOT" "$APM_ROOT/setup"
# 将 scripts/deployment/collect-rpms.sh 和 compile-packages.txt 传到 setup 后：
bash "$APM_ROOT/setup/collect-rpms.sh" "$APM_ROOT/rpm-collection"
```

在开发机执行的传输命令：

```bash
scp -P "$APM_SSH_PORT" scripts/deployment/collect-rpms.sh scripts/deployment/compile-packages.txt \
  "$APM_SSH_TARGET:/data/sql-apm/setup/"
```

预期：`COLLECTED ... signed RPMs; installed package state unchanged`。
脚本使用主机已配置的源及 `yum download --resolve --alldeps`，包括已安装的根包和依赖；
逐包执行 RPM 签名检查、保存来源 URL／NEVRA／SHA-256，并比较收集前后包清单。
参考 [DNF 下载参数](https://dnf-plugins-core.readthedocs.io/en/latest/download.html)。
失败：保留收集目录和错误；缺包、NOKEY、签名不符都不能通过关闭签名检查继续。
恢复正确仓库／发行者公钥后，另行核对并清理本次准确的收集目录再重跑。

编译依赖的确切根包见随包交付的 `compile-packages.txt`：

```text
gcc gcc-c++ make perl bison flex pkgconf zlib-devel libffi-devel
readline-devel openssl-devel bzip2-devel xz-devel sqlite-devel
util-linux-devel ncurses-devel libicu-devel
```

Kylin 的 UUID 头文件属于 util-linux-devel，pkg-config 属于 pkgconf。
PG 构建保留 ICU、readline、zlib、OpenSSL；不构建 PL/Python 或其他外部过程语言。
[PG17 编译要求](https://www.postgresql.org/docs/17/install-requirements.html)为编译清单依据。

编译依赖之外的工具由用户安装；目标机已有 bash、gzip、scp、sha256sum 和 yum download。
本次需要补充 tar 和生成本地 RPM 仓库索引的 createrepo_c；不要求 git 或 rsync：

```bash
sudo yum install -y tar createrepo_c
createrepo_c "$APM_ROOT/rpm-collection/rpms"
```

预期：两个工具可调用，生成 `rpms/repodata/repomd.xml`。失败：检查已配置源，
不要把 Alma 的 RPM 搬到 Kylin；工具准备未通过就不解包。用户恢复快照重跑时，
离线包已包含 repodata，只需要 tar；这两个工具不纳入编译依赖离线包。

用户恢复快照后复用离线包时，先在目标机准备目录和解包工具，不重复收集 RPM：

```bash
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" "$APM_ROOT" "$APM_ROOT/setup"
sudo yum install -y tar
tar --version
```

预期：目录可写且 tar 可调用；工具安装失败先处理 yum 源，不继续解包。

## 3. 在开发机生成并传输离线包

将收集目录用 scp 取回开发机被忽略的 `var/issue31/rpm-collection`。
开发机可联网；首次准备时脚本下载 PG 官方源码和校验文件，核对两个 wheel 的 PyPI 来源与锁定摘要。
Python 官方源码沿用已保存的固定摘要；所有二进制和来源证据留在忽略目录。

开发机先用独立构建环境安装锁定的 Markdown 工具及 Python 3.9 所需的传递依赖；
该环境仅生成 HTML，不进入应用 `.venv` 或运行依赖。工具来源和摘要见
`scripts/deployment/build-requirements.txt`，版本为 Python-Markdown 3.8.2。

```bash
APM_BUILD_COMMIT="$(git rev-parse HEAD)"
APM_DELIVERY_DIR="$PWD/var/issue31/deliveries/$APM_BUILD_COMMIT"
.venv/bin/python -m venv var/issue31/build-venv
var/issue31/build-venv/bin/python -m pip --isolated --disable-pip-version-check \
  download --require-hashes --only-binary=:all: \
  -r scripts/deployment/build-requirements.txt -d var/issue31/build-wheels
var/issue31/build-venv/bin/python -m pip --isolated --disable-pip-version-check \
  install --no-index --find-links var/issue31/build-wheels --require-hashes \
  -r scripts/deployment/build-requirements.txt
var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
  --commit "$APM_BUILD_COMMIT" --version v0.1.0 --kind candidate \
  --output "$APM_DELIVERY_DIR/release" \
  --previous-program var/issue31/offline-bundle/program.tar.gz
```

预期：代码和制包工具已提交，以完整提交号区分交付目录，输出目录原先不存在；产生精简 `app/`、独立 `verification/`、
两个压缩包及摘要、`build-result.json` 和 `product-files-comparison.json`。
逐文件产品比较 `all_equal=true` 才能继承原九任务证据。任何不同都需说明和补验，
不能修改原基准来通过。`app/INSTALL.html` 是单文件手册；浏览器人工体验仍需记录。

组装完整离线包时复用已校验且保存在开发机的源码、wheel、RPM 和来源清单：

```bash
.venv/bin/python scripts/deployment/build_bundle.py \
  --output "$APM_DELIVERY_DIR/offline-bundle" \
  --release-dir "$APM_DELIVERY_DIR/release" \
  --rpm-collection var/issue31/rpm-collection \
  --python-source var/issue31/offline-bundle/sources/Python-3.9.5.tgz \
  --postgres-source var/issue31/offline-bundle/sources/postgresql-17.10.tar.gz \
  --wheel-dir var/issue31/offline-bundle/wheels \
  --source-manifest var/issue31/offline-bundle/manifest.json
```

`--source-manifest` 复用原包记录的官方来源，输入仍逐个检查固定源码／wheel 摘要和 RPM
原签名记录与摘要；不访问网络。首次收集新材料时省略此参数，并按实际输入填写路径。
失败目录不能当作成功离线包，修正后使用新目录。旧包和已有试跑证据保留，不覆盖。
交付记录必须给出实际目录、完整程序提交、离线包 SHA-256 以及程序／HTML 摘要。
不要把保留旧制品的 `slim-candidate`、`slim-ready` 等目录当作“最新包”的固定别名。

**以下从开发机传输开始属于目标机试跑；实施方必须先取得用户明确确认。**
用户独立部署复用交付包时，从传输开始，不重复收集、生成或发布制品：

```bash
# 在开发机填写本次交付记录中的值；复用现成包不使用当前仓库 HEAD 推断。
APM_EXPECTED_COMMIT='填写交付记录中的40位程序提交'
APM_EXPECTED_BUNDLE_SHA256='填写交付记录中的64位离线包摘要'
APM_DELIVERY_DIR="$PWD/var/issue31/deliveries/$APM_EXPECTED_COMMIT"
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

预期全部 OK，`PROGRAM_COMMIT` 与交付记录一致；根目录 `INSTALL.html` 与程序包内手册相同。
失败停止安装，核对来源或重传，不修改摘要绕过。程序、业务日志及凭据不在目标机上联网获取。

## 4. 安装系统编译依赖

手册的正常路径先尝试已有 yum 源。实施试跑为验证备用路径，显式选择下面的离线分支。
两条路径只执行其中一条；命令与完整输出保存到本次记录。

```bash
mapfile -t APM_COMPILE_PACKAGES < "$APM_BUNDLE/support/compile-packages.txt"
# 正常路径：用户恢复快照后的独立执行使用。
sudo yum install -y "${APM_COMPILE_PACKAGES[@]}"
```

若已有源不可用，或本次明确验证离线分支，执行：

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

预期：PG17.10；普通测试 66 项、verify.py 共 266 个 PASS、发布检查 31 项。
三个命令均退出 0，开头 `APPLICATION` 指向 `$APM_APP`；记录实际项数，不以文件存在代替通过。
验收目录保存原合成测试及其探针，核心模块从精简 `app` 加载；不把测试或探针复制回 `app`。后两项自己创建私有临时实例，
禁用 TCP 并最终停止清理，不连接下面的演练实例。其合成测试中的 trust 仅限私有临时实例，
不复制到演练实例的认证规则。不运行 `*_full`、`tests/parser_probe` 或 Kylin 上的 Harness。
失败：保留原输出，核对二进制路径和链接库；不能因测试失败修改产品行为绕过。

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

预期：bootstrap、schema、check 成功，结构版本 1.6.0；项目账号通过 socket 密码认证，
密码文件权限 600。生成器不显示密码，仅通过匿名管道交给本机管理员并发送 SCRAM verifier。
凭据只保存在程序目录外的 private/；不放进 Git、命令参数、报告或 shell 历史。
private 由 sfmon 创建并拥有，目录 0700、pgpass 0600 已允许 sfmon 读取；
不为满足此要求放宽 pgpass 的组权限，否则 [libpq 会忽略密码文件](https://www.postgresql.org/docs/17/libpq-pgpass.html)。
失败：先核对 peer 身份、socket 权限、SCRAM 规则和密码文件，保留已创建对象；
密码步骤失败时保留文件，诊断后重新设置，不随意轮换其他实例的账号。

### 7.1 已有部署补齐读取权限

按上方完成的新安装直接执行第 7.2 节。已有部署先确认本实例没有运行中的导入／构建，
安排一次短暂停机；以下仅适用于本手册的专用目录、用户和无外置表空间布局。
先核对目录、符号链接和 ACL，备份后修正；不重新 initdb，不更换密码。

```bash
test "$APM_ROOT" = /data/sql-apm
test "$APM_SOCKET" = "$APM_ROOT/socket"
test "$(id -un)" = "$APM_RUN_USER"
test "$(stat -c %U "$APM_ROOT/pgdata")" = postgres
test "$(stat -c %U "$PGPASSFILE")" = "$APM_RUN_USER"
command -v setfacl getfacl
sudo find "$APM_ROOT" -xdev -type l -ls
install -d -m 0700 "$APM_ROOT/records"
APM_ACL_BACKUP="$(mktemp "$APM_ROOT/records/permissions-before.XXXXXX.acl")"
sudo getfacl -R -p "$APM_ROOT" > "$APM_ACL_BACKUP"
sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" -m fast -w stop
sudo find "$APM_ROOT" -xdev -type d ! -user "$APM_RUN_USER" ! -path "$APM_SOCKET" \
  -exec setfacl -m "u:$APM_RUN_USER:r-x,d:u:$APM_RUN_USER:r-x" {} +
sudo find "$APM_ROOT" -xdev -type f ! -user "$APM_RUN_USER" \
  -exec setfacl -m "u:$APM_RUN_USER:r-X" {} +
sudo find "$APM_ROOT" -xdev -type d -user "$APM_RUN_USER" ! -perm -0500 -exec chmod u+rx {} +
sudo find "$APM_ROOT" -xdev -type f -user "$APM_RUN_USER" ! -perm -0400 -exec chmod u+r {} +
sudo setfacl -k "$APM_SOCKET"
sudo setfacl -m "u:$APM_RUN_USER:r-x" "$APM_SOCKET"
sudo chgrp "$APM_RUN_GROUP" "$APM_SOCKET"
sudo chmod g+s "$APM_SOCKET"
test ! -w "$APM_SOCKET"
sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" \
  -l "$APM_ROOT/pgdata/server.log" -w start
```

预期：PGDATA 为 postgres 属主、0750；重启后新建的数据文件采用 0640 并继承读取 ACL。
socket 目录为 2755，无默认 ACL；新 socket 为 0777、锁文件为 0640，组为 sfmon 主组。
已有 private／pgpass 仍归 sfmon、保持 0700／0600。ACL 失败先处理文件系统支持或实际路径；
不以 `chmod -R 777` 替代。需要回退时先停本实例，核对备份范围，使用
`sudo setfacl --restore="$APM_ACL_BACKUP"` 恢复已有路径，再启动；备份不包含之后新建的文件。
外部工具若显式创建 0600 文件、覆盖 ACL 或移入不继承 ACL 的目录，须重新执行权限检查并修正。

### 7.2 以 sfmon 核对读取权限

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
用户在 DBA 工具中填写目标地址、端口、数据库和同一项目账号，密码自行从受保护文件读取，
执行同样的只读查询并记录结果。该账号拥有全部项目对象，查询验证保持只读。
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

开发机先按仓库固定 manifest 核对 55 个文件；通过 scp 将 `raw/inbox/hashdata/119` 和
`120` 复制到目标机 `$APM_ROOT/logs/`，仅该主机及 /data 接收真实日志。
不将日志、数据库、原始 SQL 或详细诊断加入 Git。

目标机先执行 `install -d -m 0700 "$APM_ROOT/logs"`，然后在开发机执行：

```bash
.venv/bin/python scripts/deployment/rehearsal.py prepare \
  --logs raw/inbox/hashdata --output var/issue31/source-check
scp -r -P "$APM_SSH_PORT" raw/inbox/hashdata/119 raw/inbox/hashdata/120 \
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

## 10. 串行完整流程、版本查询与 Alma 比对

本节九任务用于用户独立执行，默认保持单解析进程。实施方候选包重走时使用第 10.1 节，
只运行 119 首批四进程验证，不执行下面两个九任务循环。

下面的辅助工具逐次调用真实 `python -m sql_apm full/rebuild`，每次再调用 status 和 history，
保存日志、查询 JSON、阶段耗时、数据库大小和 /data 磁盘占用。它不会调用任何 `*_full`。
每次导入后读取结果中的 `normalization_timeouts`，须为 0；非零会使辅助工具拒绝通过。
每条任务结束后立即与[Alma 基准](../reports/data/kylin-alma-baseline-2026-10-02.json)比对，
不匹配退出非零。基准源于 #29 R2 最终 head 实测，与程序包产品代码摘要逐文件一致。
基准与已合并报告的九版计数一致；不能用含合成排除时段的旧统计报告替代。
归一化超时意味着部分执行缺少可靠指纹，六项发布检查通过也不能说明数据齐全。
已成功导入的文件不会自动重新解析；修改并发后直接重跑或 rebuild 不能修复这些记录，
须保留证据并在确认重置后从干净数据库重新导入。不要将出现超时的版本当作有效基线。

部署辅助工具默认使用一个解析进程（`--workers 1`）。本机四进程首批曾出现归一化超时，
八条样本单进程均通过、四进程均超时，见[试跑报告](../reports/kylin-offline-deployment-2026-10-02.md)。
产品自身的默认并发和 5 秒限制没有修改；增加并发前须重新验证计数，不根据 vCPU 数直接推定。

例如 119/0 实际调用如下 full 命令，119/4 调用下列 rebuild。使用辅助工具执行后，
不要再重复执行这些等价命令，否则会生成额外版本：

```bash
.venv/bin/python -m sql_apm full --config "$APM_ROOT/config/import-119.json" \
  --source daily-119 --batch 119-0 --training-config "$APM_ROOT/config/training-119.json" --workers 1
.venv/bin/python -m sql_apm rebuild --cluster 119 \
  --training-config "$APM_ROOT/config/training-119.json" --cutoff-date 2026-07-31
```

```bash
cd "$APM_APP"
for step in 0 1 2 3 4; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" run --cluster 119 --step "$step" \
    --config "$APM_ROOT/config" --records "$APM_ROOT/records/tasks" --data-root "$APM_ROOT"
done
for step in 0 1 2 3; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" run --cluster 120 --step "$step" \
    --config "$APM_ROOT/config" --records "$APM_ROOT/records/tasks" --data-root "$APM_ROOT"
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

本次 SSD 迁移前人工中断了 120/1，恢复时先保存旧日志，再从同一步重试，不重跑已发布步骤。
若存在这类已核对的历史中断，辅助命令可显式增加 `--interrupted-attempt ATTEMPT_ID`；
默认不接受任何中断。参数中的 ID 必须与该集群全部历史中断精确对应，失败、重复跳过、
未知 ID 或新增中断都拒绝通过。成功导入次数仍与原基准比较，总尝试次数和历史中断
单独保留在 `import_attempt_audit` 中；正式／观察统计、文件计数和版本链的比对不变。
恢复日志使用新文件，旧 `.log` 先保留到单独的中断证据目录，不覆盖或删除数据库审计记录。

### 10.1 实施方 SSD 四进程首批验证

此步骤只在用户恢复初始快照、明确确认开始目标机试跑，且第 1–9 节通过之后执行。
首次部署使用独立记录目录；不要先执行单进程九任务，否则不再是四进程首批验证。

```bash
cd "$APM_APP"
.venv/bin/python "$APM_VERIFY/scripts/deployment/rehearsal.py" run \
  --cluster 119 --step 0 --workers 4 --config "$APM_ROOT/config" \
  --records "$APM_ROOT/records/ssd-workers4" --data-root "$APM_ROOT"
```

工具记录四进程、超时计数、正式分组、全部 Alma 比较、耗时和版本链。
预期零超时且与基准一致；该结果用于实测判断，不预先认定磁盘是原因。
若一致，记录结论，存储较慢主机仍建议单进程；九任务手册参数不改。
若退出非零，先判断 CLI／环境失败还是业务计数差异，保留全部日志和 JSON，
不得为了继续而修改基准。对超时或计数差异，先保存失败数据库和记录，
再按第 11 节重置项目并从第 7–9 节恢复，使用新的记录目录、`--workers 1`
重跑 119 首批。单进程必须通过，产品问题经用户确认后另建 Issue；本 Issue 不修复。
完成后先汇报结果，再由用户恢复快照并独立执行单进程九任务。

## 11. 可选并行与重置

只有九任务及其记录全部通过后，才可选择两个集群同时重建；这会分别增加一个版本。
运行前没有其他写入，必要时按[分区说明](build-publication.md#覆盖与分区)预建当月分区。

```bash
cd "$APM_APP"
.venv/bin/python -m sql_apm rebuild --cluster 119 --training-config "$APM_ROOT/config/training-119.json" \
  --cutoff-date 2026-07-31 > "$APM_ROOT/records/parallel-119.log" 2>&1 &
apm_pid119=$!
.venv/bin/python -m sql_apm rebuild --cluster 120 --training-config "$APM_ROOT/config/training-120.json" \
  --cutoff-date 2026-09-19 > "$APM_ROOT/records/parallel-120.log" 2>&1 &
apm_pid120=$!
wait "$apm_pid119"
wait "$apm_pid120"
```

预期两个退出 0、均 published，history 变成 6／5 版；结果与各自前版计数一致。
失败：保存日志，核对是否同集群存在额外任务；不把可选步骤的版本混入九任务基准。

完整用户独立执行采用虚拟机快照恢复，快照恢复由用户进行；先把离线包和记录留在开发机。
仅需重跑项目时，可停掉本实例后精确清理下列目录；这会删除全部演练数据与版本：

```bash
test "$APM_ROOT" = /data/sql-apm
test ! -L "$APM_ROOT"
sudo -u postgres "$APM_PG_BIN/pg_ctl" -D "$APM_ROOT/pgdata" -m fast -w stop
test ! -e "$APM_ROOT/pgdata/postmaster.pid"
# 先保存 records，再清理本实例的数据、凭据、配置、记录和 socket。
sudo rm -rf -- /data/sql-apm/pgdata /data/sql-apm/socket /data/sql-apm/private \
  /data/sql-apm/config /data/sql-apm/records
```

预期：仅列出的目录消失，离线包、已校验日志、程序和两项编译安装保留；从第 7 节重建。
若 pg_ctl 停止失败、pid 仍存在或路径是链接，停止清理并检查真实目标。该重跑不等于裸机验收；
不删除 postgres 系统用户，不卸载系统包，不操作 /data/sql-apm 之外的数据。
