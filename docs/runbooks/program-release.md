# SQL APM 程序发布说明

交付目标为 `v1.1.0` 正式版，结构保持 1.12.0；基线为 #65 合并后的 main
`35f5826ce8b6276c6a61a77f818157e164142b3c`。范围和状态以 [Issue #67](https://github.com/shenxg13/sql-apm/issues/67)
在线契约为准。只支持全新安装；v1.0.0 及更早标签、附件、历史发布说明保持原样。

## 文件与文档

`scripts/deployment/package-files.json` 定义程序和独立验收包。程序增加可执行的 `bin/sqlfind`、
`scripts/sqlfind/configure.py`，保留 `daily/push-logs.sh`、`daily/intake-logs.sh`、收取配置示例及其 Python 实现。
测试和演练资源放在 verification；新增 sqlfind 数据库／终端检查和推送／收取合成链路检查。

| 包根文档 | 原稿 |
| --- | --- |
| INSTALL.html | [生产安装手册](production-installation.md) |
| REHEARSAL.html | 历史完整演练手册及记录模板，本版只更新版本指向 |
| CONFIGURATION.html | 每日运行、配置指南 |
| DATABASE.html | 数据库结构说明和四张 SVG |
| SEARCH.html | 检索与看板使用指南 |
| SQLFIND.html | [sqlfind 使用说明](sqlfind-guide.md) |
| RELEASE.html | [v1.1.0 发布说明](../releases/v1.1.0.md)，与 GitHub 发布正文同源 |

构建使用锁定的 Python-Markdown 3.8.2，不进入产品依赖。RELEASE.json 保存提交、版本、原稿摘要、
HTML 检查和逐文件摘要，`prerelease=false`。合并前为 `kind=candidate`、`production_use=false`；
合并后的正式构建才使用 `kind=release`。构建器只从干净固定提交导出，release 还要求提交已包含在 origin/main。
原稿没有完成的验证必须写明待完成，不能预先写为通过。

## 开发机检查

使用已有、版本符合契约的本地工具；所有数据库验证使用私有临时实例，不连接已有库。
先执行单元、数据库、推送／拉取／定时器和制包检查，保存输出及退出码：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/deployment/check_documents.py
.venv/bin/python scripts/tests/test_installation.py
.venv/bin/python scripts/tests/test_sqlfind_configure.py
var/issue31/build-venv/bin/python scripts/tests/test_deployment.py
.venv/bin/python scripts/db/verify.py
.venv/bin/python scripts/db/verify_sqlfind.py
.venv/bin/python scripts/db/verify_daily.py
.venv/bin/python scripts/db/verify_daily_recovery.py
.venv/bin/python scripts/daily/verify_push.py --sshd-root var/issue54/sshd --timer
.venv/bin/python scripts/daily/verify_fetch.py --sshd-root var/issue54/sshd
.venv/bin/python scripts/daily/verify_timer.py --sshd-root var/issue54/sshd
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

## 复用成品与重复制包

本版只沿用 v1.0.0 的 Kylin 成品，不重新编译。`runtime.tar.gz` 的 SHA-256 必须为
`d271d6a1471c41723724b48afad59f9675e1b324747fbb7dfae283f97340314c`。
成品编译记录见 [v1.0.0 编译报告](../reports/v100-runtime-build-2026-10-12.md)；来源为提交
`2e356c9a44baad209d0971de79d1ec7d55d98fcd`，Python 3.13.16（含 venv）和 PostgreSQL 17.10。
完整包清单保留原编译提交并注明 v1.0.0 来源；成品归档及编译证据本身不改。

先核对保留的 v1.0.0 完整包，再将输入恢复到新的忽略目录（不要覆盖已有目录）：

```bash
(cd var/releases/v1.0.0 && sha256sum -c offline-bundle.tar.gz.sha256)
test ! -e var/issue67/inputs
mkdir -p var/issue67/inputs
tar -xzf var/releases/v1.0.0/offline-bundle.tar.gz -C var/issue67/inputs
APM_INPUTS="$PWD/var/issue67/inputs/offline-bundle"
.venv/bin/python scripts/deployment/runtime_archive.py inspect --artifact "$APM_INPUTS/runtime"
```

RPM 收集工具的输入布局与包内布局不同；恢复证据目录并引用原 RPM，不重新下载：

```bash
mkdir var/issue67/rpm-collection
cp "$APM_INPUTS/rpm-evidence/"* var/issue67/rpm-collection/
ln -s "$APM_INPUTS/rpms" var/issue67/rpm-collection/rpms
```

固定提交后，在两个全新目录分别构建：

```bash
APM_BUILD_COMMIT="$(git rev-parse HEAD)"
APM_DELIVERY_DIR="$PWD/var/issue67/deliveries/$APM_BUILD_COMMIT"
for round in first repeat; do
  var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
    --commit "$APM_BUILD_COMMIT" --version v1.1.0 --kind candidate \
    --output "$APM_DELIVERY_DIR/$round/release"
  .venv/bin/python scripts/deployment/build_bundle.py \
    --release-dir "$APM_DELIVERY_DIR/$round/release" \
    --output "$APM_DELIVERY_DIR/$round/offline-bundle" \
    --runtime-artifact "$APM_INPUTS/runtime" \
    --rpm-collection var/issue67/rpm-collection \
    --python-source "$APM_INPUTS/sources/Python-3.13.16.tgz" \
    --postgres-source "$APM_INPUTS/sources/postgresql-17.10.tar.gz" \
    --wheel-dir "$APM_INPUTS/wheels" --grafana-files "$APM_INPUTS/grafana" \
    --source-manifest "$APM_INPUTS/manifest.json"
done
cmp "$APM_DELIVERY_DIR/first/release/sql-apm-v1.1.0.tar.gz" "$APM_DELIVERY_DIR/repeat/release/sql-apm-v1.1.0.tar.gz"
cmp "$APM_DELIVERY_DIR/first/release/sql-apm-verification-v1.1.0.tar.gz" "$APM_DELIVERY_DIR/repeat/release/sql-apm-verification-v1.1.0.tar.gz"
cmp "$APM_DELIVERY_DIR/first/offline-bundle.tar.gz" "$APM_DELIVERY_DIR/repeat/offline-bundle.tar.gz"
```

每个输入仍须按摘要校验；使用 `--source-manifest` 全离线复用来源 URL。
逐文件比较 v1.0.0 与新包：运行时、源码、wheel、391 个 RPM、仓库索引和 Grafana 四组件必须完全相同。
只允许程序包、验收包、文档及相应身份／摘要清单变化；记录新增文档和全部差异。
本版新增产品能力，不能用 `--previous-program` 与 v1.0.0 比较来宣称产品完全相同。

将程序和验收归档解压到独立新目录，在 app 创建精确 Python 3.13.16 的 venv，离线安装锁定 wheel。
以解包后的解释器串行运行十一个自动入口，检查真正交付的 app：

```bash
cd "$APM_APP"
.venv/bin/python scripts/deployment/verify_package.py --installed
for check in unit database publication smoke cleanup search views grafana daily daily-recovery sqlfind; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" \
    --app-root "$APM_APP" --pg-bin /usr/pgsql-17/bin "$check"
done
```

`unit` 含收取中断恢复和推送边界检查，`sqlfind` 含真实 PG17、伪终端和临时 home 登录配置。
另在开发机用已解包的 sshd 运行可选 `push` 入口（`--sshd-root` 须为绝对路径），可加 `--timer`：

```bash
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/run_verification.py" \
  --app-root "$APM_APP" --pg-bin /usr/pgsql-17/bin \
  --sshd-root "$APM_SSHD_ROOT" --timer push
```

此入口只使用合成 master 和本机私有 SSH，不安装 sshd，也不代表跨机器验证；目标机的真实推送另按契约执行。

## 目标机与用户验收

用户恢复一次初始快照后，实施方按[生产手册](production-installation.md)完成二进制全新安装；
成品摘要、自检和十一个自动入口通过，安装前后系统包清单相同。其后按 #67 验证：

- 开发机扮演 master，经真实 SSH 推到演练机 `sfmon`；两个集群各选连续至少 3 天完整日志，
  保存文件名、大小及 SHA-256。源端用忽略目录下硬链接副本，不用 raw/inbox 作任一端工作目录。
  推送脚本原样使用，平台变量、su／hostname 和日期仅由测试环境提供；覆盖较大 pushDays 首推和日常推送。
- 手工收取、`daily run`，每个样本日期只导入一次，计数与清单相符，无推送引起的问题。
  实际启动一次 `sql-apm-daily.service`，核对先收取、后每日运行，再启用定时器。
- 按手册配置 sqlfind，一次输出和交互各检索；使用说明的示例照原样执行，抽查与同选择看板详情数值一致。
- 部分样本里找原文最多的结构，多次测打开详情和 `v` 的耗时，记录原文数与中位数（measured，仅该批数据）。
  不设门槛；明显影响使用时反馈用户决定，不在发版中悄悄修改产品。
- 生产手册上线后核对全部通过，包括三种检索与四个看板。记录包身份、命令、系统清单与输出，复制回开发机核对。
- 用户在该环境以 sfmon 经 PuTTY 试用，80×24 和最大化各一次，核对 sqlfind 配置和推送接入，
  平台步骤再次对照真实界面；在 Issue 或 PR 明确确认。

不重跑九任务、不重启、不重新编译；目标机及用户验收不可由开发机检查替代。
开发机准备完成后保留目标机及用户验收作为合并前门槛。

## 合并后正式发布

B1–B14 和独立评审完成后才合并，核对精确合并提交触发的 main 检查。B15 在合并后执行，Issue 保持打开。
从固定 main 提交重制 `--kind release` 程序和完整包，复用同一成品；与实测候选逐路径比较，产品变化须补验。
构建器从 `docs/releases/v1.1.0.md` 同时生成包内 RELEASE.html 和输出目录的 `release-notes.md`，
后者仅将相对链接转为该提交链接，摘要写入 build-result.json。
实际验收后更新“已验证、未验证、不提供”，发布命令不带 `--prerelease`：

```bash
APM_RELEASE_NOTES="$APM_RELEASE_DIR/release-notes.md"
git tag -a v1.1.0 "$APM_RELEASE_COMMIT" -m 'SQL APM v1.1.0'
git push origin refs/tags/v1.1.0
gh release create v1.1.0 \
  "$APM_RELEASE_DIR/sql-apm-v1.1.0.tar.gz" \
  "$APM_RELEASE_DIR/sql-apm-v1.1.0.tar.gz.sha256" \
  -R shenxg13/sql-apm --verify-tag --title 'SQL APM v1.1.0' --notes-file "$APM_RELEASE_NOTES"
```

下载公开附件复核摘要、提交与文档；完整离线包按用户选择交付，链接及摘要记入 Issue。
B15 完成前不关闭 Issue；不得覆盖旧标签或附件。
