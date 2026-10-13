# SQL APM 程序发布说明

交付目标为 `v1.0.0` 正式版，数据库结构保持 1.12.0，基线为 #57 合并后的 main
`5494075f13c7030c3f0d1d084a4589a439745b62`。范围和状态以 [Issue #59](https://github.com/shenxg13/sql-apm/issues/59)
在线契约为准；v0.3.0 及更早的标签、附件和历史发布说明保持原样。

## 文件与文档

`scripts/deployment/package-files.json` 定义程序和独立验收包。
程序含业务代码、规则、结构、Grafana／daily 配置及部署工具，测试和演练资源放在 verification。

| 包根文档 | 原稿 |
| --- | --- |
| INSTALL.html | [生产安装手册](production-installation.md) |
| REHEARSAL.html | [演练验证手册](kylin-offline-deployment.md)、验证记录模板 |
| CONFIGURATION.html | 每日运行、配置指南 |
| DATABASE.html | 数据库结构说明和四张 SVG |
| SEARCH.html | 检索与看板使用指南 |
| RELEASE.html | [v1.0.0 发布说明](../releases/v1.0.0.md)，与 GitHub 发布正文同源 |

构建使用锁定的 Python-Markdown 3.8.2，不进入产品依赖。
RELEASE.json 保存提交、版本、原稿摘要、HTML 检查和逐文件摘要，`prerelease=false`；
`kind=candidate` 时 `production_use=false`，合并后的 `kind=release` 才为 true。
HTML 中明确候选与验收状态，离线链接、图与代码块均校验。
`build_release.py` 只从干净固定提交导出，release 还要求提交已经包含在 origin/main 中。

## 连接目标机前

先完成本地实现、文档、检查及可重复的编译候选包。没有 Kylin 成品时不能生成正式完整包，
使用 `--compile-only` 产生 `bundle_type=compilation-bootstrap` 的明确候选。
它用于唯一一次成品编译；不是生产交付包。无需临时替换为 Alma 二进制。

```bash
.venv/bin/python scripts/deployment/check_documents.py
.venv/bin/python scripts/tests/test_installation.py
.venv/bin/python scripts/db/verify_installation.py
var/issue31/build-venv/bin/python scripts/tests/test_deployment.py
.venv/bin/python scripts/tests/test_grafana.py
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

旧的 downloads 和解压依赖已按用户要求清理，先从保留的最终完整包恢复到本轮新输入目录：

```bash
(cd var/issue52/deliveries/f983a311253a9d31029761fccb2e104fd6982bb0 && sha256sum -c offline-bundle.tar.gz.sha256)
mkdir -p var/issue59/inputs
tar -xzf var/issue52/deliveries/f983a311253a9d31029761fccb2e104fd6982bb0/offline-bundle.tar.gz \
  -C var/issue59/inputs --wildcards \
  'offline-bundle/sources/*' 'offline-bundle/wheels/*' 'offline-bundle/manifest.json'
```

输入目录恢复后执行下面的制包命令。

固定提交后，用两个全新目录分别构建并比较程序、验收包和编译候选的摘要：

```bash
APM_BUILD_COMMIT="$(git rev-parse HEAD)"
APM_DELIVERY_DIR="$PWD/var/issue59/deliveries/$APM_BUILD_COMMIT"
var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
  --commit "$APM_BUILD_COMMIT" --version v1.0.0 --kind candidate --output "$APM_DELIVERY_DIR/release"
.venv/bin/python scripts/deployment/build_bundle.py --compile-only \
  --release-dir "$APM_DELIVERY_DIR/release" --output "$APM_DELIVERY_DIR/compilation-bundle" \
  --rpm-collection var/issue31/rpm-collection \
  --python-source var/issue59/inputs/offline-bundle/sources/Python-3.13.16.tgz \
  --postgres-source var/issue31/postgresql-17.10.tar.gz \
  --wheel-dir var/issue59/inputs/offline-bundle/wheels --grafana-files var/issue51/downloads \
  --source-manifest var/issue59/inputs/offline-bundle/manifest.json
```

已有源清单只用于核对固定 URL 和摘要，每个实际输入仍须校验；不下载安装工具来判断其是否存在。

隔离解压 app 和 verification，在 app 创建精确 Python 3.13.16 的新 venv，从锁定 wheel 离线安装，
再串行执行十个入口（每个数据库检查使用自己的临时实例）：

```bash
cd "$APM_APP"
.venv/bin/python scripts/deployment/verify_package.py --installed
for check in unit database publication smoke cleanup search views grafana daily daily-recovery; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" \
    --app-root "$APM_APP" --pg-bin /usr/pgsql-17/bin "$check"
done
```

本轮没有修改业务代码、规则和结构，开发机有界检查验证部署改变；目标机仍须重新执行完整九任务和界面流程。
不能把本地合成检查、历史基准或缺库模拟当作 Kylin 二进制兼容性、自启和重启通过。

## 成品编译与完整包

用户恢复初始快照后，按演练手册第一轮编译，十个随包入口全部通过后导出 runtime-artifact。
成品包括固定目录下的 Python、PostgreSQL 和已安装 wheel 的 venv；记录系统版本、编译前后 RPM 清单、
命令输出、源码／wheel／RPM 来源及摘要、成品逐文件摘要和嵌入前缀的文件清单。
归档保留执行位和受限符号链接；不含 pgdata、日志、凭据或真实输入。

复制回开发机，外部核对 SHA256SUMS 的摘要，再校验内容：

```bash
.venv/bin/python scripts/deployment/runtime_archive.py inspect --artifact "$APM_RUNTIME_ARTIFACT"
```

将上文 `--compile-only` 改为 `--runtime-artifact "$APM_RUNTIME_ARTIFACT"`，其余源和候选保持固定，
用新输出目录生成完整包。两次相同输入的完整包也须字节相同。
清单保存 `runtime_manifest_sha256` 和每个成品文件，Grafana 四个固定组件继续按清单校验。
后续候选／发布可更改文档和程序身份，但必须复用同一成品；依赖锁不符则制包拒绝。
成品输出目录不允许覆盖，不能在后续轮次悄悄重编。

## 目标机与用户验收

用户再次恢复初始快照后，实施方按生产手册完成二进制安装，用开发机样本代替真实首次拉取与首批导入。
安装前后 rpm 清单必须完全相同，十个入口、九任务与基准、每日运行、定时器、传输、检索和四看板均检查。
真实重启核对数据库、Grafana、指纹服务自启和一次错过任务的补跑。
验证错误目录和缺失系统库的停止行为、四个账号的允许／拒绝情形，资源只记录，不设门槛。

指南比较延用既定规则：log_median／log_mad 跨机绝对差最多 1e-12，其他列及 NULL 精确一致；
同机比较仍精确，不能沿用 #52 用户轮接受的门槛配置差异作为本版例外。
成品和各轮包身份、输出与机器记录均复制回开发机按摘要核对；公开报告只写脱敏结果。
用户第三次恢复初始快照，亲自按生产手册完成一轮安装和上线并在 Issue 或 PR 明确确认。
实施方实测不能替代用户验收。目标机不可达时保留剩余门槛，不降低验收、不合并。

## 合并后正式发布

M1–M14 和独立评审完成后才合并；核对精确合并提交触发的 main 检查通过。
M15 在合并后执行，Issue 保持打开。从固定 main 提交重制 `--kind release` 程序和完整包，
复用同一 Kylin 成品，与实测候选逐路径比较。部署脚本也属于产品文件，变化须补验，不能只比较业务目录。

发布说明从 docs/releases/v1.0.0.md 转换仓库相对链接为精确提交链接；保持同一原稿，不另写独立版本。
实际验收完成后更新“已验证、未验证、不提供”，不能把本阶段候选文字原样作为通过结论。
正式发布命令不带 --prerelease：

```bash
git tag -a v1.0.0 "$APM_RELEASE_COMMIT" -m 'SQL APM v1.0.0'
git push origin refs/tags/v1.0.0
gh release create v1.0.0 \
  "$APM_RELEASE_DIR/sql-apm-v1.0.0.tar.gz" \
  "$APM_RELEASE_DIR/sql-apm-v1.0.0.tar.gz.sha256" \
  -R shenxg13/sql-apm --verify-tag --title 'SQL APM v1.0.0' --notes-file "$APM_RELEASE_NOTES"
```

下载公开附件复核摘要、提交和文档；完整包通过内网交付。链接、大小、摘要和成品身份记入 Issue，
M15 完成后才能关闭。不得覆盖既有标签或附件。
