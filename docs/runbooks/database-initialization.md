# PostgreSQL 项目初始化、升级与恢复

本入口针对已有可用 PostgreSQL 17，创建项目账号、数据库、schema 和
[物理结构](../design/postgresql-storage.md)，当前结构版本为 1.1.0。不安装实例、不修改 pg_hba.conf 或现有实例配置。
所有连接显式指定 host/socket、port、database、user；先确认目标实例。

## 先决条件与身份

- Bash、GNU sha256sum、PostgreSQL 17 psql。脚本不依赖 Python 驱动或 ORM。
- 已有管理员能够 CREATE ROLE、CREATE DATABASE 并指定项目 owner；
  若使用非超级管理员，由实例维护者准备必要权限，本脚本不提升管理员或项目账号。
- 一个项目登录角色兼任库／schema／对象 owner、app、admin、Grafana。
  新建角色为 LOGIN、NOSUPERUSER、NOCREATEDB、NOCREATEROLE、NOREPLICATION、
  NOBYPASSRLS、NOINHERIT；重用角色须可登录、无上述实例级能力且无继承成员关系。
- 名称分别由 --database、--schema、--role 指定，默认 sql_apm。
  项目名称为 1～63 个小写 ASCII 字母、数字、下划线，以字母开头；
  禁止 pg_ 前缀及 postgres/template0/template1/public/information_schema。
  管理员数据库接受同样的简单名称形式，但允许 postgres 等维护库。
- 数据库用 UTF8、libc C locale、template0；项目对象显式使用配置 schema。
  后续查询使用 schema 限定名，或在事务内设定该 schema 的 search_path。
  时间写入必须有偏移；显示可执行 `SET TIME ZONE INTERVAL '+08:00'`。
  HashData 日期计算显式按 UTC+8，不依赖默认时区。

## 凭据与首次执行

脚本用 psql -X 忽略 psqlrc，-w 避免无凭据时挂起，ON_ERROR_STOP 遇错退出。
保留 libpq 的受保护 PGPASSFILE／既有认证；不接受密码命令行参数，
不 source 本地环境文件。PGOPTIONS、PGSERVICE、PGSERVICEFILE、PGHOSTADDR
被清除，避免隐式配置覆盖指定目标或 DDL 行为。TLS 参数仍由实例连接要求提供。

新账号创建时没有密码；本工具不设默认密码，不修改已有密码。
使用密码认证时分两步，以下主机／端口为占位示例，须替换为明确目标：

```bash
scripts/db/initialize.sh bootstrap \
  --host /path/to/explicit/socket --port 55473 \
  --admin-user existing_admin --admin-database postgres \
  --pg-bin /usr/pgsql-17/bin
```

已有管理员用同一显式目标打开交互 psql，执行 `\password sql_apm` 设置密码；
将连接凭据保存在仓库外、权限 0600 的密码文件，通过 PGPASSFILE 指定，
不要把真实密码放进命令参数、仓库或常规日志。实例的认证规则须允许该账号登录，
必要配置由已有实例管理员负责。

然后以项目账号实际连接：

```bash
scripts/db/initialize.sh schema \
  --host /path/to/explicit/socket --port 55473 \
  --pg-bin /usr/pgsql-17/bin
```

若项目账号已经可通过已有认证方式登录，可以使用 `all` 一次运行两阶段。
再次执行 all 会核对并复用账号／数据库；只维护库内结构可以一直使用 schema，
不再需要管理员凭据。三个项目名称不同也可使用，例如：

```bash
scripts/db/initialize.sh all \
  --host /path/to/explicit/socket --port 55473 \
  --admin-user existing_admin --admin-database postgres \
  --database apm_data --schema apm_store --role apm_user \
  --pg-bin /usr/pgsql-17/bin
```

## 重跑与结构核对

`schema` 会对比列、类型、空值、默认值、约束、索引、所有权等 catalog 结构。
结构版本和 schema.sql 的 SHA-256 也须一致；升级库允许保留摘要正确的 1.0.0 历史记录。不能直接绕开入口运行 schema.sql。

- 兼容的已存在表复用；完整兼容表的子集可补齐缺失表并保留已有数据。
- 同名表的列／约束／索引不完整或不兼容时报错，不自动修补可能有歧义的对象。
- 项目 schema 为本工具专用；额外表、视图、函数、自定义类型或触发器会阻止重跑。
  项目账号仍可执行 DDL，但持久结构变化须进入版本演进，不能让检查静默接受漂移。
- 对象核对先完成，再执行建表。库内 DDL 和结构版本记录同一事务提交。
- `check` 使用同样的项目连接，检查完成后回滚，不修改项目数据或结构；
  它仍需创建事务内预期 schema 的权限，不是只读事务命令。
- 预期 schema 名含当前 PostgreSQL backend PID，只创建全新名字；事务中精确删除。
  检查失败会回滚，不遗留该 schema。不会删除／重建项目 schema 或清空业务表。

```bash
scripts/db/initialize.sh check \
  --host /path/to/explicit/socket --port 55473 \
  --pg-bin /usr/pgsql-17/bin
```

## 从 1.0.0 升级到 1.1.0

本次只提供 MPP 专属表前缀迁移。`all`／`schema` 不隐式升级旧库；
已有 1.0.0 库必须使用 `upgrade`，空库按前述初始化步骤直接建立 1.1.0。
旧 DDL 固定保存在 `sql_apm/storage/versions/1.0.0.sql`，入口核对其已发布摘要；
不能修改该文件，也不能修改旧库的版本记录来绕过校验。

1. 确认目标、维护窗口及可用备份；暂停访问这批表的写入和查询，避免阻塞改名。
2. 使用拥有项目对象的统一账号执行下述命令，不需要管理员参数。
3. 执行 `check` 核对目标结构；应用 SQL 切换到新名称后再恢复访问。

```bash
scripts/db/initialize.sh upgrade \
  --host /path/to/explicit/socket --port 55473 \
  --database apm_data --schema apm_store --role apm_user \
  --pg-bin /usr/pgsql-17/bin

scripts/db/initialize.sh check \
  --host /path/to/explicit/socket --port 55473 \
  --database apm_data --schema apm_store --role apm_user \
  --pg-bin /usr/pgsql-17/bin
```

默认名称时省略 database/schema/role 三个参数。迁移入口先验证完整 1.0.0 结构、
所有者及唯一旧版本记录；完成 14 张表、专属索引及约束改名，再核对完整目标结构。
版本表保留 1.0.0 的原 SHA-256 和 applied_at，新增 1.1.0 记录，与改名一起提交。
重复 `upgrade` 对已是 1.1.0 的库只校验，不改写数据或版本时间。

DDL 锁等待上限为 5 秒；迁移需要维护窗口串行执行，不保证在线无阻塞。
超时、连接中断或 SQL 失败时，本次改名和版本登记均回滚；修正后重复同一命令。
若提交时失去连接，重新执行会通过结构及版本判断实际完成状态。
已经成功提交后的反向降级未提供；业务调用需匹配新名称，不能继续使用旧表名。

遇到旧结构漂移、错误摘要或同名目标对象时入口拒绝升级。先定位原因并修正目标或
恢复已确认的合法结构，不通过清空表、删除版本记录或手工重命名部分对象绕过检查。
`config_snapshot` 与 `problem` 保留数据和名字，其引用目标随专属表改名保持有效。
旧名兼容视图、其他系统建表、后续版本升级及分区迁移不在这个命令的范围内。

## 失败及恢复

| 阶段／诊断 | 已完成部分与处理 |
| --- | --- |
| 缺少显式目标、名称无效、工具缺失 | 在连接前失败；修正参数或工具路径。 |
| 认证／连接失败 | 核对同一目标的账号与认证规则；网络错误不直接表示密码失效。 |
| bootstrap 的角色／库不兼容 | 报具体对象；不接管、不重置角色配置。核对选错名称还是已有对象需要人工迁移。 |
| CREATE DATABASE 失败 | 角色可能已提交，库可能不存在；修正已有管理员权限／磁盘等原因后重跑。 |
| schema 登录失败 | 角色和库保留；准备项目认证后运行 schema。 |
| schema 所有者、表定义、索引或版本冲突 | 报具体对象及 catalog 差异类别，项目结构／数据不被修复性改写；恢复正确脚本或经独立授权修正已有对象后重跑。 |
| 库内 DDL 中断／失败 | 本阶段全部回滚，角色／库不回滚；修正原因后重跑。 |
| 同版本完整重跑 | 保留业务数据、账号密码及版本首次应用时间，返回 OK。 |

账号创建所在 DO 块可回滚；CREATE DATABASE 不在事务块内，不能宣称整个初始化
是一笔事务。库内的 1.0.0 → 1.1.0 升级则由一笔事务完成；
未来版本继续显式交付迁移和验证，不能修改已部署的旧 DDL 冒充兼容重跑。

## 临时实例验证

```bash
.venv/bin/python scripts/db/verify.py --pg-bin /usr/pgsql-17/bin
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

verify.py 需要普通操作系统用户及 PG17 的 initdb/pg_ctl/psql。
它自行创建 /tmp 下唯一目录、私有 socket 和数据目录，禁用 TCP，
端口固定为该私有 socket 内的 55473，绝不连接默认 5432。
临时初始管理员仅属于该 disposable 实例；项目账号还会实际经过 SCRAM 验证，
随机凭据文件仅在临时目录内，结束后清理。

正常、验证异常及 SIGINT/SIGTERM 均执行停止与清理；启动阶段中断时也检查本次
postmaster.pid。只有确认停止后才删除本次精确目录。
若停止本身失败，保留目录和原始错误供排查，不删除运行中数据库，也不触碰其他实例。
验证不覆盖 SIGKILL、宿主机断电或操作系统失效下的自动清理。

验证包含新库安装、默认及自定义名称的含数据旧库升级、重跑、冲突拒绝和中途失败回滚。
输入只使用仓库内人工样例及合成变体；无生产日志、真实凭据、外部数据库和网络。
stdout 报实际版本、逐项 PASS 及清理目录，失败退出非零。
