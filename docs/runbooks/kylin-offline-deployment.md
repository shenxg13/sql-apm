# Kylin V10 SP2 离线部署与九任务验证

[Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 的演练手册，目标是 Kylin V10 SP2
x86_64 的专用演练机；不是生产部署方案。使用 Python 3.9.5、PostgreSQL 17.10，
程序基准为包含 #29 的 main 提交 `6451d140d44f4e06cc34862c3e5aff7593d7afeb`。
程序归档之外的辅助文件有独立摘要；覆盖已有文件仅限获准增加 `--pg-bin` 的
`scripts/db/verify_publication.py`。产品代码和规则保持基准提交内容。

实施试跑与用户独立执行分别保存[记录模板](kylin-validation-record.md)。
试跑前创建虚拟机快照；试跑完成后用户恢复该快照，独立从头执行本手册。
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
export APM_PYTHON="$APM_ROOT/python-3.9.5/bin/python3.9"
export APM_PG_BIN="$APM_ROOT/postgresql-17.10/bin"
export APM_APP="$APM_ROOT/app"
export APM_SOCKET="$APM_ROOT/socket"
export APM_PORT=5432
export APM_SERVER='填写目标机的LAN地址'
export APM_CLIENT_CIDR='填写允许的客户端CIDR网段'
export APM_BUNDLE="$APM_ROOT/offline-bundle"
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
运行前必须填写上面两项；实施时使用交接提供的目标地址和允许网段，连接信息不写入 Issue。

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
开发机可联网；脚本下载 PG 官方源码和校验文件，核对两个 wheel 的 PyPI 来源与锁定摘要。
Python 官方源码沿用已保存的固定摘要；所有二进制和来源证据留在忽略目录。

```bash
mkdir -p var/issue31
scp -r -P "$APM_SSH_PORT" "$APM_SSH_TARGET:/data/sql-apm/rpm-collection" var/issue31/
.venv/bin/python scripts/deployment/build_bundle.py \
  --output var/issue31/offline-bundle \
  --rpm-collection var/issue31/rpm-collection \
  --python-source var/python-build/Python-3.9.5.tgz \
  --wheel-dir var/parser-probe/wheels --wheel-dir var/ingestion/wheels
scp -P "$APM_SSH_PORT" var/issue31/offline-bundle.tar.gz var/issue31/offline-bundle.tar.gz.sha256 \
  "$APM_SSH_TARGET:/data/sql-apm/"
```

用户独立部署已有交付包时，只执行上述最后一条 scp；不重新收集或生成制品。

预期：生成目录、`.tar.gz` 和同名 `.sha256`；`manifest.json` 逐项记录来源、版本和摘要。
输出目录必须不存在；失败目录不能当作成功离线包。修正来源后用新目录重建，
交付时同步手册中的包名。将压缩包及外部摘要传到目标机 `$APM_ROOT`。
目标机先验证压缩包，再解包并核对每个文件：

```bash
cd "$APM_ROOT"
sha256sum -c offline-bundle.tar.gz.sha256
test ! -e "$APM_BUNDLE"
tar -xzf offline-bundle.tar.gz
cd "$APM_BUNDLE"
sha256sum -c SHA256SUMS
cat PROGRAM_COMMIT
```

预期：全部 OK，提交标识与本手册一致。失败：停止安装，重新传输或核对来源；
不能修改校验清单使损坏输入通过。程序、日志及凭据均不从公网获取。

## 4. 安装系统编译依赖

手册的正常路径先尝试已有 yum 源。实施试跑为验证备用路径，显式选择下面的离线分支。
两条路径只执行其中一条；命令与完整输出保存到本次记录。

```bash
mapfile -t APM_COMPILE_PACKAGES < "$APM_BUNDLE/support/scripts/deployment/compile-packages.txt"
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
cp -a "$APM_BUNDLE/support/." "$APM_APP/"
cp "$APM_BUNDLE/PROGRAM_COMMIT" "$APM_APP/PROGRAM_COMMIT"
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
.venv/bin/python -m unittest discover -s tests -v > "$APM_ROOT/records/unittest.log" 2>&1
.venv/bin/python scripts/db/verify.py --pg-bin "$APM_PG_BIN" > "$APM_ROOT/records/verify.log" 2>&1
.venv/bin/python scripts/db/verify_publication.py --pg-bin "$APM_PG_BIN" \
  > "$APM_ROOT/records/verify-publication.log" 2>&1
```

预期：PG17.10；普通测试 66 项、verify.py 共 266 个 PASS、发布检查 31 项。
三个命令均退出 0；记录实际项数，不以文件存在代替通过。后两项自己创建私有临时实例，
禁用 TCP 并最终停止清理，不连接下面的演练实例。其合成测试中的 trust 仅限私有临时实例，
不复制到演练实例的认证规则。不运行 `*_full`、`tests/parser_probe` 或 Kylin 上的 Harness。
失败：保留原输出，核对二进制路径和链接库；不能因测试失败修改产品行为绕过。

## 7. 建立实例与项目数据库

```bash
if ! getent passwd postgres >/dev/null; then
  sudo useradd --system --user-group --home-dir "$APM_ROOT/postgres-home" --shell /sbin/nologin postgres
fi
sudo install -d -m 0700 -o postgres -g postgres "$APM_ROOT/pgdata" "$APM_ROOT/postgres-home"
sudo install -d -m 0755 -o postgres -g postgres "$APM_SOCKET"
sudo -u postgres "$APM_PG_BIN/initdb" -D "$APM_ROOT/pgdata" -U postgres \
  --auth-local=peer --auth-host=scram-sha-256 --encoding=UTF8 --locale=C
```

预期：专用 postgres 系统用户无需登录密码，空实例初始化成功。
若用户或 PGDATA 已存在，先核对属主及来源，不接管未知用户／数据目录，不重复 initdb。
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
失败：先核对 peer 身份、socket 权限、SCRAM 规则和密码文件，保留已创建对象；
密码步骤失败时保留文件，诊断后重新设置，不随意轮换其他实例的账号。

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
.venv/bin/python scripts/deployment/rehearsal.py prepare \
  --logs "$APM_ROOT/logs" --output "$APM_ROOT/config"
cd "$APM_ROOT/logs"
sha256sum -c "$APM_ROOT/config/logs.SHA256SUMS"
```

预期：55 个文件逐项匹配，119 为 30 个、120 为 25 个，输出 verified_files=55、batches=8。
119 首批 26 文件，29 日两文件、30 和 31 日各一；120 首批 13 文件，后三日各四。
配置沿用 #29 的来源、完整性声明、空模板／排除时段和默认门槛，窗口 30 天。
失败：停止导入，核对源端清单和传输；不改摘要、删掉缺失成员或简化批次。

## 10. 串行完整流程、版本查询与 Alma 比对

下面的辅助工具逐次调用真实 `python -m sql_apm full/rebuild`，每次再调用 status 和 history，
保存日志、查询 JSON、阶段耗时、数据库大小和 /data 磁盘占用。它不会调用任何 `*_full`。
每条任务结束后立即与[Alma 基准](../reports/data/kylin-alma-baseline-2026-10-02.json)比对，
不匹配退出非零。基准源于 #29 R2 最终 head 实测，与程序包产品代码摘要逐文件一致。
基准与已合并报告的九版计数一致；不能用含合成排除时段的旧统计报告替代。

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
  .venv/bin/python scripts/deployment/rehearsal.py run --cluster 119 --step "$step" \
    --config "$APM_ROOT/config" --records "$APM_ROOT/records/tasks" --data-root "$APM_ROOT"
done
for step in 0 1 2 3; do
  .venv/bin/python scripts/deployment/rehearsal.py run --cluster 120 --step "$step" \
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
