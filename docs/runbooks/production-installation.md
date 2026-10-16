# SQL APM 生产安装手册

适用版本 `v1.1.0`，Kylin V10 SP2 x86_64 全新安装，数据库结构 1.12.0。
只支持新实例；后续版本将提供带数据的原地升级，本版没有升级步骤。
候选包仍须完成发布验收，不能把候选标识当作正式发布。

## 1. 前置检查与参考配置

生产参考为 **8 核或 16 核、32 GB 内存、1 TB 磁盘**，数据盘挂在 `/data`。
资源不设验收门槛。历史实测为 8 vCPU、约 14 GiB 内存、4 GiB swap：
55 个文件、36 个“集群×日期”、15.8 GB 日志，每日运行后数据库 48.4 GB，
两个常驻服务运行期间主机内存采样峰值 6.24 GB，无 OOM。
这是 v0.3.0 的有限输入实测，磁盘需求按样本放大只是估计，没有生产实测依据。
v1.0.0 二进制在同类 8 vCPU、约 14 GiB 内存机器上的同规模输入实测：三个产品服务运行，
主机内存采样最高约 10.86 GB，每日运行后数据库约 48.45 GB，零 OOM。
两轮均为有限样本和 250ms 采样，不能将这些数字作为生产容量或瞬时内存上限。

以 `sfmon` 登录，须具备 sudo 权限。准备好的基础工具包括 bash、tar、sha256sum、
系统 Python 3.7 或更高（只用于解包工具和失败诊断）、sudo、systemd、rpm、acl、SSH 客户端；
缺失时先由部署方准备。二进制路径不调用 yum，不安装编译工具，也不修改系统软件包。
项目运行使用包内 Python 3.13.16，不使用系统 Python 安装依赖。

```bash
set -euo pipefail
export APM_ROOT=/data/sql-apm
export APM_RUN_USER=sfmon
export APM_RUN_GROUP="$(id -gn "$APM_RUN_USER")"
test "$(id -un)" = "$APM_RUN_USER"
test "$(getent passwd "$APM_RUN_USER" | cut -d: -f7)" = /bin/bash
export APM_APP="$APM_ROOT/app"
export APM_VERIFY="$APM_ROOT/verification"
export APM_BUNDLE="$APM_ROOT/offline-bundle"
export APM_PYTHON="$APM_ROOT/python-3.13.16/bin/python3.13"
export APM_PG_BIN="$APM_ROOT/postgresql-17.10/bin"
export APM_SOCKET="$APM_ROOT/socket"
export APM_PORT=5432
export APM_SERVER='填写本机内网IPv4地址'
export APM_CLIENT_CIDR='填写允许访问数据库的客户端CIDR'
export APM_EXPECTED_COMMIT='填写交付记录中的40位提交'
export APM_EXPECTED_BUNDLE_SHA256='填写交付记录中的64位完整包摘要'
export APM_GRAFANA="$APM_ROOT/grafana"
export APM_GRAFANA_PORT=3000
export APM_FINGERPRINT_PORT=3001
export PGPASSFILE="$APM_ROOT/private/pgpass"
export SQL_APM_DSN="host=$APM_SOCKET port=$APM_PORT dbname=sql_apm user=sql_apm"
unset PGPASSWORD PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR
cat /etc/kylin-release
uname -m
getconf GNU_LIBC_VERSION
findmnt -T /data
df -h /data
free -h
command -v tar sha256sum python3 setfacl getfacl ssh scp
python3 --version
sudo ss -ltnp
```

预期 Kylin V10 SP2、x86_64，`/data` 独立挂载，5432／3000／3001 未占用。
确认目录和 postgres 系统账号无其他用途；已有数据目录或未知用户时先核对，不接管或覆盖。
同类生产机的只读预检：850 个已装包，对离线 391 个 RPM 比较，26 个未装、205 个相同、
160 个本机较旧、0 个较新，没有版本冲突；该机不是 APM 机器，结论不能替代本机自检。

## 2. 解包与核对

由交付方通过内网提供完整离线包及独立的摘要、提交标识。GitHub 程序包不足以裸机安装。
先准备空目录并放入 `offline-bundle.tar.gz` 和校验文件；不从外网获取任何运行依赖。

```bash
sudo install -d -m 0755 -o "$APM_RUN_USER" -g "$APM_RUN_GROUP" "$APM_ROOT" "$APM_ROOT/setup"
cd "$APM_ROOT"
[[ "$APM_EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$APM_EXPECTED_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$APM_EXPECTED_BUNDLE_SHA256" offline-bundle.tar.gz | sha256sum -c -
sha256sum -c offline-bundle.tar.gz.sha256
test ! -e "$APM_BUNDLE"
tar -xzf offline-bundle.tar.gz
(cd "$APM_BUNDLE" && sha256sum -c SHA256SUMS)
test "$(cat "$APM_BUNDLE/PROGRAM_COMMIT")" = "$APM_EXPECTED_COMMIT"
test ! -e "$APM_APP"
test ! -e "$APM_VERIFY"
tar -xzf "$APM_BUNDLE/program.tar.gz"
tar -xzf "$APM_BUNDLE/verification.tar.gz"
(cd "$APM_APP" && sha256sum -c SHA256SUMS)
(cd "$APM_VERIFY" && sha256sum -c SHA256SUMS)
test "$(cat "$APM_APP/PROGRAM_COMMIT")" = "$APM_EXPECTED_COMMIT"
test "$(cat "$APM_VERIFY/PROGRAM_COMMIT")" = "$APM_EXPECTED_COMMIT"
```

所有校验须成功。包根 `INSTALL.html` 是本手册，`REHEARSAL.html` 单独供演练验证使用；
`CONFIGURATION.html`、`SEARCH.html`、`SQLFIND.html`、`DATABASE.html` 和 `RELEASE.html` 均可离线打开。

## 3. 二进制安装与自检

二进制只接受 `/data/sql-apm`，也不允许该路径是指向其他目录的符号链接。
使用交付方在 Kylin 上导出的同一份成品，逐文件摘要位于 `runtime/runtime-manifest.json`。
先运行以下入口；解释器、系统库、临时 PostgreSQL、程序清单和全部随包入口通过后才继续。
这些检查仅使用自动清理的临时数据库，不访问产品实例。

```bash
python3 "$APM_APP/scripts/deployment/runtime_archive.py" install \
  --root "$APM_ROOT" --artifact "$APM_BUNDLE/runtime"
python3 "$APM_APP/scripts/deployment/runtime_check.py" --binary \
  --root "$APM_ROOT" --output "$APM_ROOT/setup/binary-check"
```

预期退出码 0，最后为 `every runtime and packaged acceptance entry passed`。
失败会输出失败项目，详细输出在指定目录；**立即停下**，不初始化产品数据库。
它不会自动安装 RPM 或编译。先处理缺失库、错误目录、文件损坏等具体原因；决定改用
[编译路径](#附录-a-手工编译路径)时再执行附录，不覆盖未知或已有的运行时目录。

## 4. 建立实例、数据库和账号

只在无现有数据的新目录执行。postgres 系统用户使用正常 shell，不设登录密码。
已有账号时先核对用途、home、shell、属组和其运行中的服务，不能直接改变未知账号。

```bash
if ! getent passwd postgres >/dev/null; then
  sudo useradd --system --user-group --home-dir "$APM_ROOT/postgres-home" --shell /bin/bash postgres
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
参数只写入本实例；以下默认值已经在 8 vCPU／约 14 GiB 的机器验证：

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
"$APM_APP/.venv/bin/python" "$APM_APP/scripts/deployment/install_postgres.py" \
  --root "$APM_ROOT" --port "$APM_PORT" --output "$APM_ROOT/setup/postgres"
sudo install -m 0644 "$APM_ROOT/setup/postgres/sql-apm-postgresql.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start sql-apm-postgresql.service
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
密码文件权限 600。密码由操作者输入两次，不回显、不随机生成；只拒绝不一致、空密码或换行。
短密码、冒号、反斜杠以及首尾空格／制表符都按输入原值保存，脚本自动转义 pgpass；管理员只向 PostgreSQL 发送 SCRAM verifier。
凭据只保存在程序目录外的 private/；不放进 Git、命令参数、报告或 shell 历史。
private 由 sfmon 创建并拥有，目录 0700、pgpass 0600 已允许 sfmon 读取；
不为满足此要求放宽 pgpass 的组权限，否则 [libpq 会忽略密码文件](https://www.postgresql.org/docs/17/libpq-pgpass.html)。
失败：先核对 peer 身份、socket 权限、SCRAM 规则和密码文件，保留已创建对象；
设置数据库失败时不改已有连接文件；若数据库已更新而文件写入失败，修复文件权限后用同一密码重跑。

为只读账号输入密码，并写入同一个交互连接文件和独立的服务凭据：

```bash
cd "$APM_APP"
.venv/bin/python scripts/deployment/create_password.py --role sql_apm_ro \
  --socket "$APM_SOCKET" --port "$APM_PORT" --tcp-host "$APM_SERVER" \
  --passfile "$PGPASSFILE" --service-passfile "$APM_ROOT/private/readonly.pgpass" \
  --password-file "$APM_ROOT/private/readonly-password"
```

数据库超级用户只有 `local all postgres peer`，不设数据库密码；HBA 的最后两条拒绝规则
拒绝所有远程超级用户连接。只读角色不能修改项目数据，默认语句超时 2 分钟。
允许网段中的两个项目角色仍须密码；检查防火墙已有规则，只开放指定客户端范围的 5432。

### 4.1 登录后直接使用 psql

以下配置只由两个账号各自的登录 profile 读取，不写入 `/etc/profile`，不改变产品连接串。
复制到已核对的 home；如 postgres 是新建账号，其 home 应是下面的目录。

```bash
test "$(getent passwd postgres | cut -d: -f6)" = "$APM_ROOT/postgres-home"
test "$(getent passwd postgres | cut -d: -f7)" = /bin/bash
install -m 0644 "$APM_ROOT/setup/postgres/sfmon-psql.sh" "$HOME/.sql-apm-psql.sh"
touch "$HOME/.bash_profile"
grep -qxF '. "$HOME/.sql-apm-psql.sh"' "$HOME/.bash_profile" || \
  printf '%s\n' '. "$HOME/.sql-apm-psql.sh"' >> "$HOME/.bash_profile"
sudo install -m 0644 -o postgres -g postgres "$APM_ROOT/setup/postgres/postgres-psql.sh" \
  "$APM_ROOT/postgres-home/.sql-apm-psql.sh"
sudo -H -u postgres bash <<'PROFILE'
touch "$HOME/.bash_profile"
grep -qxF '. "$HOME/.sql-apm-psql.sh"' "$HOME/.bash_profile" ||
  printf '%s\n' '. "$HOME/.sql-apm-psql.sh"' >> "$HOME/.bash_profile"
PROFILE
```

重新登录 sfmon 后直接执行 `psql -X -w -c 'SELECT current_user;'`，应为 `sql_apm`；
`psql -X -w -U sql_apm_ro -c 'SELECT current_user;'` 应为 `sql_apm_ro`，两者均不提示密码。
`sudo su - postgres` 后直接 `psql -X -w -c 'SELECT current_user;'` 应为 `postgres`。
退出 postgres 会话再继续安装。裸 `psql` 默认是可读写项目属主，日常只查看时用 `psql -U sql_apm_ro`。

### 4.2 PostgreSQL 内存参数

| 参数 | 默认，已经验证 | 32 GB 建议，未经验证 |
| --- | --- | --- |
| shared_buffers | 512MB | 4GB |
| work_mem | 16MB | 32MB |
| maintenance_work_mem | 128MB | 512MB |
| max_connections | 30 | 30 |
| max_wal_size | 2GB | 4GB |
| min_wal_size | 256MB | 512MB |

建议值影响速度和资源，不改变统计规则；32 GB 组合尚未实测。work_mem 会随查询节点、并行工作进程和连接数叠加，
不是每个连接的总额度。修改 `postgresql.conf` 后用 `sudo systemctl restart sql-apm-postgresql`。
出现内存压力或 OOM 时，先停止定时器、等任务退出，把上述值改回默认，再重启并检查运行记录。

## 5. Grafana 与指纹服务

Grafana 的 admin、viewer 密码须至少 4 字符，这是保留的上游限制；本项目不增加其他长度或字符规则。
可包含冒号、反斜杠及首尾空格／制表符，不会自动去掉空白。为两个账号分别输入两次密码，不回显：

```bash
cd "$APM_APP"
.venv/bin/python scripts/deployment/credentials.py --output "$APM_ROOT/private/grafana-admin-password"
.venv/bin/python scripts/deployment/credentials.py --output "$APM_ROOT/private/grafana-viewer-password"
```

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

Grafana 使用生成的 systemd 服务启动；该入口在每次启动时重新读取受保护密码文件，保持输入原值。
人工前台诊断也使用 `scripts/grafana/install.py start`，传入与上面相同的 `--home`、
`--admin-password-file`、`--db-password-file`；不要直接运行 Grafana 二进制，后者缺少此密码读取步骤。
密码不写入命令参数、单元文件或生成的配置。数据库密码只传给当前 Grafana 进程的环境，不写入账号登录环境。

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
浏览器打开 `http://目标机地址:3000`，匿名访问应要求登录；使用操作者设置的账号密码。
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
两者 enable 后随主机启动，被杀后由 systemd 拉起；上线后核对三项服务状态。
两个服务排在 PostgreSQL 服务之后启动；数据库就绪后才继续。

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

## 6. PostgreSQL 开机自启

数据库已通过 systemd 启动；现在启用开机自启。人工启停统一用 systemctl，勿同时用 pg_ctl 管理产品实例。

```bash
sudo systemctl enable sql-apm-postgresql.service
systemctl is-enabled sql-apm-postgresql.service sql-apm-grafana.service sql-apm-fingerprint.service
systemctl is-active sql-apm-postgresql.service sql-apm-grafana.service sql-apm-fingerprint.service
sudo systemctl status sql-apm-postgresql.service --no-pager
```

PostgreSQL 完成 `pg_ctl -w start` 后才放行 Grafana、指纹服务和每日运行服务。
机器重启后数据库和两个常驻服务自动启动，启用了 Persistent 定时器时补一次错过的运行。
若数据库启动失败，每日运行仍直接失败，不重试；先查 `pgdata/server.log` 和 journal，修复后人工运行一次。
停库维护前先停定时器并等待当前运行结束；恢复时先启动数据库再启用定时器。

## 7. 配置

编辑下面三份文件，使用自己的集群、来源、构建串和日志目录；来源声明必须与实际系统一致。
下列 C1／C2 只是配置例子。`batches` 保持空，由每日运行逐天登记。

```bash
install -d -m 0750 "$APM_ROOT/config" "$APM_ROOT/inbox/C1" "$APM_ROOT/inbox/C2"
cat > "$APM_ROOT/config/import.json" <<'JSON'
{
  "version": 1,
  "clusters": ["C1", "C2"],
  "sources": {
    "mpp-C1": {"cluster": "C1", "build": "HashData Warehouse 3.13.13", "timezone": "UTC+08:00", "declaration": "操作者已核对来源"},
    "mpp-C2": {"cluster": "C2", "build": "HashData Warehouse 3.13.13", "timezone": "UTC+08:00", "declaration": "操作者已核对来源"}
  },
  "batches": {}
}
JSON
cat > "$APM_ROOT/config/training.json" <<'JSON'
{
  "version": 1,
  "clusters": ["C1", "C2"],
  "window": {"days": 30},
  "retention": {"months": 2}
}
JSON
cat > "$APM_ROOT/config/daily.json" <<'JSON'
{
  "version": 1,
  "import_config": "import.json",
  "training_config": "training.json",
  "sources": {
    "mpp-C1": {"directory": "/data/sql-apm/inbox/C1"},
    "mpp-C2": {"directory": "/data/sql-apm/inbox/C2"}
  },
  "workers": 4
}
JSON
```

默认构建间隔 1 天；basic／P95／P99 数量门槛为 30／200／1000，覆盖至少 7 天。
完整取值与修改方法见[配置指南](configuration-guide.md)。**默认自动清理旧结果，原始文件保留 45 天**；
如要关闭，daily.json 增加 `"cleanup":{"enabled":false}` 与 `"raw_files":{"retention_days":"off"}`。
配置改变影响后续构建，不改已有版本。密码只在受保护文件中，不能放进 JSON 或连接串。

## 8. 推送接入

生产以 MPP master 推送、SQL APM 收取为主。完整参数和失败处理见[MPP 端推送](daily-run.md#mpp-端推送)。
先完成本节和首批手工运行，再启用定时器；两个集群各配置一个平台计划。

**落地与公钥。** 在 SQL APM 服务器以 `sfmon` 创建落地根；它与接收目录须在同一文件系统，
彼此不能相同或包含，不得设为程序目录或接收目录的上级。

```bash
install -d -m 0700 "$APM_ROOT/landing"
install -d -m 0700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
```

把每台 master 上 `appUser` 的公钥逐行追加到 `sfmon` 的 `authorized_keys`，保留已有内容；
每条用 `from=` 限定对应 master 的地址，以下仅示意，须替换占位内容：

```text
from="MASTER_ADDRESS" ssh-ed25519 MASTER_PUBLIC_KEY
```

`.ssh` 和 `authorized_keys` 属主为 `sfmon`，只交换公钥、不复制私钥。
在每台 master 上以 `appUser` 第一次手工执行 `ssh sfmon@SQL_APM_ADDRESS`，
通过可信渠道核对主机密钥指纹后接受。自动计划运行前确认免密可用；不关闭主机密钥校验。

**收取配置。** 以 `sfmon` 复制示例后编辑：

```bash
cp "$APM_APP/daily/intake-logs.conf.example" "$APM_ROOT/config/intake-logs.conf"
chmod 600 "$APM_ROOT/config/intake-logs.conf"
```

改成下面的形式；将 `MASTER_C1`、`MASTER_C2` 替换为各 master 上 `hostname` 的完整输出，
来源名和接收目录与第 7 节一致。配置只按文本读取，不执行。

```text
landing /data/sql-apm/landing
expire_hours 24
source mpp-C1 MASTER_C1 /data/sql-apm/inbox/C1
source mpp-C2 MASTER_C2 /data/sql-apm/inbox/C2
```

**平台计划。** 复制公用模板“文件备份到中转机_Linux V1.0”，只编辑副本：
将包内 `daily/push-logs.sh` 的完整内容粘贴为 Shell 内容，保留原有内置参数和入参名字，
新增可为空的 `pushDays`。两个集群各创建或调整一个计划，填写：

| 参数 | 内容 |
| --- | --- |
| `backupFilePath` | 对应 master 的日志目录；多目录用空白分隔，目录名不能含空白，不展开通配符 |
| `backHomePath` | 两计划均为 `/data/sql-apm/landing` |
| `uploadServerIp` | SQL APM 服务器地址 |
| `appUser` | master 上能读取日志并执行 SSH/SCP 的账号；远端固定登录 `sfmon` |
| `pushDays` | 首次按下一节填写；日常留空取 7 天，允许 1–366 |
| `RCLONE_DEST_PATH`、`RCLONE_BUCKET_NAME`、`PLAN_ID` | 保留平台提供的值，每次推送使用新的多级路径 |

核对落地根、bucket 和路径：脚本会以 `sfmon` 权限清空本次推送目录后写入，不能指向已有数据。
建议平台每天 16:00 推送，SQL APM 17:00 运行；双方时区用北京时间。
主机名未在收取配置中登记时，收取报告错误并保留推送，不自动猜测来源。

## 9. 首批日志导入与首次构建

先不要启用定时器。首次将平台 `pushDays` 设为比 master 保留天数少一到两天，
例如保留 25 天时填 23 或 24，避免最旧一天在推送中被清除。手工执行两个计划，核对平台输出的日期、文件数和字节数。
以 `sfmon` 手工收取，检查每个日期的结果；只有核对齐全才会生成 `YYYY-MM-DD.complete`：

```bash
SQL_APM_PYTHON="$APM_APP/.venv/bin/python" "$APM_APP/daily/intake-logs.sh" \
  --config "$APM_ROOT/config/intake-logs.conf"
```

不要推今天；不要在已经导入的日期里追加、更名或替换文件。
首批可以是三十天，但 120 三十天首批的时间与空间成本尚未实测，执行时观察资源。

```bash
cd "$APM_APP"
export PGPASSFILE="$APM_ROOT/private/pgpass"
export SQL_APM_DSN="host=$APM_SOCKET port=$APM_PORT dbname=sql_apm user=sql_apm"
.venv/bin/python -m sql_apm daily run --config "$APM_ROOT/config/daily.json"
.venv/bin/python -m sql_apm daily status --limit 10
.venv/bin/python -m sql_apm status --cluster C1
.venv/bin/python -m sql_apm status --cluster C2
```

每日运行逐天导入，每个集群最后构建一次。预期各集群运行成功、有当前版本、无未处理问题；
样本不足的组可以发布，但整窗没有有效样本则不切换版本。失败看原因码，保留现场，解决后运行同一命令。
手工运行没有定时器的 12 小时时限，重复运行不会重复导入成功日期。
旧于保留期限的原始文件可能在首次成功导入后立即删除，执行前确认保留配置符合备份安排。

想补满 30 天时，从永久备份把剩余日期的全部原始文件复制到对应接收目录，保持原名；
逐个核对文件数、大小和日期，确认齐全后才放对应的 `YYYY-MM-DD.complete`，再执行一次上方 `daily run`。
复制与放标记的方法见[接收目录](daily-run.md#二接收目录和齐全标记)。不得向已经导入的日期补文件。
首批完成后清空平台 `pushDays`，回到日常 7 天；`raw_files.retention_days` 不要小于推送天数。

## 10. 启用定时器

先确认平台推送、手工收取与每日运行均成功，再安装定时器，示例为每日 17:00：

```bash
cd "$APM_APP"
.venv/bin/python scripts/daily/install.py --output "$APM_ROOT/setup/daily" \
  --app-root "$APM_APP" --python "$APM_APP/.venv/bin/python" --user "$APM_RUN_USER" \
  --config "$APM_ROOT/config/daily.json" --dsn "$SQL_APM_DSN" --passfile "$PGPASSFILE" \
  --intake-config "$APM_ROOT/config/intake-logs.conf" --at 17:00
sudo install -m 0644 "$APM_ROOT/setup/daily/"*.service "$APM_ROOT/setup/daily/"*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start sql-apm-daily.service
sudo journalctl -u sql-apm-daily.service --no-pager -n 100
sudo systemctl enable --now sql-apm-daily.timer
systemctl list-timers sql-apm-daily.timer --all
```

先实际运行一次服务，核对日志中先收取、后 `daily run`，再启用定时器。
每次运行先收取落地目录，再执行每日运行；收取失败不阻止处理已齐全的日期，须同时查看收取输出。
定时任务排在 PostgreSQL 后启动，最长 12 小时，停止后下一次继续；不同时运行第二份。
停用使用 `sudo systemctl disable --now sql-apm-daily.timer`；它不会停止已经开始的运行。
查看行为、日志和失败恢复见[每日运行](daily-run.md)。

## 11. sqlfind 一次性配置

以 `sfmon` 执行，使用第 4 节已设置的只读账号和已有密码文件；不重新生成密码，不改变 `psql` 设置：

```bash
"$APM_APP/.venv/bin/python" -B "$APM_APP/scripts/sqlfind/configure.py" install \
  --host "$APM_SOCKET" --port "$APM_PORT" --database sql_apm --user sql_apm_ro \
  --passfile "$PGPASSFILE"
```

工具列出改动的 home 文件，相同参数可重复执行。保留安装终端，另开一个新的 PuTTY 登录会话，确认没有临时设置的
`SQL_APM_DSN` 或 `PGPASSFILE` 覆盖；第 4 节登录环境设置的生产密码文件可以继续使用。
在任意目录运行：

```bash
sqlfind --help
sqlfind 'select'
```

帮助应正常显示，检索应列出候选或明确提示无命中。两种用法、三种检索和结果说明见[sqlfind 使用说明](sqlfind-guide.md)。
撤销时以同一账号执行下列命令，再重新登录；只撤销本工具的配置，保留密码文件及其他登录设置：

```bash
/data/sql-apm/app/.venv/bin/python -B /data/sql-apm/app/scripts/sqlfind/configure.py uninstall
```

若登录片段已被手工改动，工具会拒绝猜测覆盖，先核对再撤销。

## 12. 上线后核对

下面的安装命令返回原安装终端执行，沿用第 1 节的变量；`sqlfind` 在第 11 节新登录会话运行。
浏览器登录，按[使用指南](search-guide.md)核对三种检索和四个看板。
额外核对一次 `sqlfind 'select'` 检索；在运行状态看板查看每个来源的“最新导入日期”，应与实际到齐的日期一致；
查看 `journalctl -u sql-apm-daily.service` 中收取脚本的逐推送、逐日期输出，不能只凭服务成功判定日志已到齐。
确认只读账号写入被拒绝、非允许来源连接被拒绝；不得通过关闭认证解决连接问题。

```bash
psql -X -w -c 'SELECT pg_size_pretty(pg_database_size(current_database()));'
df -h /data
systemctl is-active sql-apm-postgresql.service sql-apm-grafana.service sql-apm-fingerprint.service
systemctl is-enabled sql-apm-daily.timer
cd "$APM_APP"
.venv/bin/python -m sql_apm daily status --limit 10
```

首批导入和首次构建后记录数据库大小、磁盘剩余；之后每天同一时刻记录，按日增量估算可用时长。
空间不够时，评估调小原始文件保留天数或结果保留月数；清理前核对预览，不能删除仍在运行的数据库文件。

产品**不提供 PostgreSQL 备份或恢复**，须由部署方安排；原始日志默认只保留 45 天，
数据库损坏后，更早的执行历史无法仅靠接收目录重建。参考配置与采样数字不构成容量保证。

密码轮换：先暂停定时器并等待写入任务结束，重新执行第 4 节对应角色的 create_password.py 命令，
输入新密码两次。脚本同时更新交互和服务的密码文件，不手工编辑转义后的字段。
只读密码变化后通过 `systemctl restart sql-apm-grafana sql-apm-fingerprint` 重启两个服务，
使其重新读取文件原值，再测试两个数据库账号、Grafana 数据源和指纹服务后恢复定时器。
Grafana 账号密码通过管理界面修改，并用 credentials.py 的 `--replace` 同步受保护文件；
仅修改 admin 密码文件不会修改已初始化的 Grafana SQLite 账号。

## 附录 A. 手工编译路径

**本版没有重新验证编译路径。只有操作者在二进制自检失败后明确选择才执行。** 本路径会安装编译工具并升级系统软件包。
Issue #52 在演练机实际新装 18、升级 28 个包；同类生产机估计升级约 31 个包，其内网源只有旧版本。
v1.0.0 编译前后清单也测得新装 18、升级 28 个包，另移除被 util-linux 替代的 hardlink 包。
升级后会偏离同类主机的补丁基线，随后安装要求旧版本精确匹配的软件包可能失败。
版本比较没有冲突不等同于这项长期影响不存在。请先由部署方评估，并安排系统级恢复途径。

Issue #52 事务升级的 28 个包如下（measured，仅该机；不含新增的 18 个包）：

| 包 | 架构 | 升级后版本 |
| --- | --- | --- |
| bzip2 | x86_64 | 1.0.8-3.p01.ky10 |
| cpp | x86_64 | 7.3.0-20190804.35.p09.ky10 |
| e2fsprogs | x86_64 | 1.45.6-11.ky10 |
| gcc | x86_64 | 7.3.0-20190804.35.p09.ky10 |
| krb5-libs | x86_64 | 1.18.2-23.ky10 |
| libblkid | x86_64 | 2.35.2-14.p09.ky10 |
| libfdisk | x86_64 | 2.35.2-14.p09.ky10 |
| libgomp | x86_64 | 7.3.0-20190804.35.p09.ky10 |
| libicu | x86_64 | 62.1-6.p01.ky10 |
| libmount | x86_64 | 2.35.2-14.p09.ky10 |
| libsmartcols | x86_64 | 2.35.2-14.p09.ky10 |
| libstdc++ | x86_64 | 7.3.0-20190804.35.p09.ky10 |
| libuuid | x86_64 | 2.35.2-14.p09.ky10 |
| ncurses | x86_64 | 6.2-9.ky10 |
| ncurses-base | noarch | 6.2-9.ky10 |
| ncurses-libs | x86_64 | 6.2-9.ky10 |
| openssl | x86_64 | 1:1.1.1f-4.p31.ky10 |
| openssl-libs | x86_64 | 1:1.1.1f-4.p31.ky10 |
| perl | x86_64 | 4:5.28.3-14.ky10 |
| perl-devel | x86_64 | 4:5.28.3-14.ky10 |
| perl-libs | x86_64 | 4:5.28.3-14.ky10 |
| pkgconf | x86_64 | 1.7.3-2.ky10 |
| sqlite | x86_64 | 3.32.3-9.ky10 |
| util-linux | x86_64 | 2.35.2-14.p09.ky10 |
| util-linux-devel | x86_64 | 2.35.2-14.p09.ky10 |
| xz | x86_64 | 5.2.5-2.p01.ky10 |
| zlib | x86_64 | 1.2.11-23.ky10 |
| zlib-devel | x86_64 | 1.2.11-23.ky10 |

固定的 391 个 RPM 和 17 个编译根包、源码与 wheel 均在完整包内。编译步骤沿用 v0.3.0，
不启用网络源、不跳过签名。失败的二进制目录由操作者核对后移到程序目录外保留；
下面的三个目标必须不存在，不能向既有运行时叠加安装。

```bash
test ! -e "$APM_ROOT/python-3.13.16"
test ! -e "$APM_ROOT/postgresql-17.10"
test ! -e "$APM_APP/.venv"
mapfile -t APM_COMPILE_PACKAGES < "$APM_BUNDLE/support/compile-packages.txt"
for package in "$APM_BUNDLE"/rpms/*.rpm; do rpm --checksig "$package"; done
sudo yum --disablerepo='*' \
  --repofrompath="apm-offline,file://$APM_BUNDLE/rpms" --enablerepo=apm-offline \
  --setopt=apm-offline.gpgcheck=1 --setopt=install_weak_deps=False \
  install -y "${APM_COMPILE_PACKAGES[@]}"
mkdir -p "$APM_ROOT/build" "$APM_ROOT/records"
cd "$APM_ROOT/build"
tar -xzf "$APM_BUNDLE/sources/Python-3.13.16.tgz"
cd Python-3.13.16
./configure --prefix="$(dirname "$(dirname "$APM_PYTHON")")" --with-ensurepip=install
make -j4
make install
"$APM_PYTHON" -m venv "$APM_APP/.venv"
cd "$APM_APP"
PIP_CONFIG_FILE=/dev/null .venv/bin/python -m pip --isolated --disable-pip-version-check \
  install --no-index --find-links "$APM_BUNDLE/wheels" --require-hashes -r requirements.txt
cd "$APM_ROOT/build"
tar -xzf "$APM_BUNDLE/sources/postgresql-17.10.tar.gz"
cd postgresql-17.10
./configure --prefix="$(dirname "$APM_PG_BIN")" --with-openssl
make -j4
make install
python3 "$APM_APP/scripts/deployment/runtime_check.py" \
  --root "$APM_ROOT" --output "$APM_ROOT/setup/compilation-check"
```

全部检查退出 0 后回到第 4 节。编译路径允许自定前缀，但所有变量、配置、登录环境与服务路径
必须一致；固定二进制不能搬到自定前缀。原有失败输出保留，不把编译成功当作二进制自检通过。

## 附录 B. 备选：从 master 拉取

以下原第 8 节的拉取步骤原样保留；拉取脚本与 `v1.0.0` 相同，本版没有重新做目标机验证。
采用拉取时用本附录代替第 8 节的推送接入，首批按 `--date` 逐天拉取后执行第 9 节 `daily run`；
第 10 节生成器将 `--intake-config` 换成 `--fetch-config "$APM_ROOT/config/fetch-logs.conf"`。

先核对每个 master 的 SSH 主机指纹，由 sfmon 配好公钥互信；源账号只需列目录和读日志的权限。
在本机手工测试连通与免密，保持主机密钥检查，不使用 StrictHostKeyChecking=no。
实际 master 的地址、账号与目录仅写本地配置，不放进公开报告。

```bash
export APM_SOURCE='源账号@master地址'
export APM_SOURCE_LOGS='/master日志目录'
export APM_FIRST_DATE='填写已经结束的YYYY-MM-DD'
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$APM_SOURCE" 'true'
cp "$APM_APP/daily/fetch-logs.conf.example" "$APM_ROOT/config/fetch-logs.conf"
chmod 600 "$APM_ROOT/config/fetch-logs.conf"
```

编辑 fetch-logs.conf，每个来源一行：`来源名 源账号@主机 源日志目录 本机接收目录 [SSH端口]`，
与 daily.json 的接收目录一致。先只拉一个已经结束的日期：

```bash
"$APM_APP/daily/fetch-logs.sh" --config "$APM_ROOT/config/fetch-logs.conf" --date "$APM_FIRST_DATE"
find "$APM_ROOT/inbox" -maxdepth 2 -type f -name "*$APM_FIRST_DATE*" -printf '%P\t%s\n'
```

逐个来源与 master 上当日文件清单对照文件数和每个文件的大小，并确认只在全部复制成功后出现
`YYYY-MM-DD.complete`。核对无误才允许下节导入、随后启用自动拉取。
真实 master 上的首次拉取由部署方执行；脚本与真实目录／文件命名不符时停止并反馈，另开 Issue，
不临时改脚本或手工放标记掩盖问题。传输脚本的行为与 v0.3.0 相同。
