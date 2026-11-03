# SQL APM v1.3.0 制包与发布

本版契约为 [Issue #79](https://github.com/shenxg13/sql-apm/issues/79)，以 #73 合并后的固定提交为基线。程序／验收包与完整离线包均从同一提交构建；正式发布在用户验收和独立评审后进行。

## 开发机检查

沿用兼容的本地工具与锁定构建环境，不安装或更换生产依赖。

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/tests/test_upgrade.py
.venv/bin/python scripts/tests/test_installation.py
var/issue31/build-venv/bin/python scripts/tests/test_deployment.py
.venv/bin/python scripts/deployment/check_documents.py
.venv/bin/python scripts/db/verify.py
.venv/bin/python scripts/db/verify_query_records.py
PATH="$PWD/var/harness-tools/bin:$PWD/.venv/bin:$PATH" scripts/quality/check.sh
```

数据库检查使用私有临时实例。实际升级演练另在完整样本的 1.13.0 副本中执行；
保留 #72 的原始样本库不动，比较既有表与构建结果，记录时间、空间和工具包结果。
Kylin 服务、运行时和完整手册演练以验证机实测为准，不用开发机代替。

## 固定运行时与离线输入

复用正式 v1.2.0 的完整包，核对已交付摘要；旧归档不覆盖。

```bash
(cd var/releases/v1.2.0 && sha256sum -c offline-bundle.tar.gz.sha256)
test ! -e var/issue79/inputs
mkdir -p var/issue79/inputs
tar -xzf var/releases/v1.2.0/offline-bundle.tar.gz -C var/issue79/inputs
APM_INPUTS="$PWD/var/issue79/inputs/offline-bundle"
.venv/bin/python scripts/deployment/runtime_archive.py inspect --artifact "$APM_INPUTS/runtime"
mkdir var/issue79/rpm-collection
cp "$APM_INPUTS/rpm-evidence/"* var/issue79/rpm-collection/
ln -s "$APM_INPUTS/rpms" var/issue79/rpm-collection/rpms
```

`runtime.tar.gz` 必须为 `d271d6a1471c41723724b48afad59f9675e1b324747fbb7dfae283f97340314c`，
来源为 v1.0.0 的一次编译；Python 3.13.16 含虚拟环境，PostgreSQL 17.10。
不重新编译，不更换 wheel、RPM、Grafana 或插件。

## 可重复构建

先提交已审查的修改。合并前使用 candidate；正式 release 要求提交已经进入 origin/main。

```bash
APM_BUILD_COMMIT="$(git rev-parse HEAD)"
APM_DELIVERY_DIR="$PWD/var/issue79/deliveries/$APM_BUILD_COMMIT"
for round in first repeat; do
  var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
    --commit "$APM_BUILD_COMMIT" --version v1.3.0 --kind candidate \
    --output "$APM_DELIVERY_DIR/$round/release"
  .venv/bin/python scripts/deployment/build_bundle.py \
    --release-dir "$APM_DELIVERY_DIR/$round/release" \
    --output "$APM_DELIVERY_DIR/$round/offline-bundle" \
    --runtime-artifact "$APM_INPUTS/runtime" \
    --rpm-collection var/issue79/rpm-collection \
    --python-source "$APM_INPUTS/sources/Python-3.13.16.tgz" \
    --postgres-source "$APM_INPUTS/sources/postgresql-17.10.tar.gz" \
    --wheel-dir "$APM_INPUTS/wheels" --grafana-files "$APM_INPUTS/grafana" \
    --source-manifest "$APM_INPUTS/manifest.json"
done
.venv/bin/python -c 'import filecmp,sys; assert filecmp.cmp(sys.argv[1],sys.argv[2],shallow=False)' "$APM_DELIVERY_DIR/first/release/sql-apm-v1.3.0.tar.gz" "$APM_DELIVERY_DIR/repeat/release/sql-apm-v1.3.0.tar.gz"
.venv/bin/python -c 'import filecmp,sys; assert filecmp.cmp(sys.argv[1],sys.argv[2],shallow=False)' "$APM_DELIVERY_DIR/first/release/sql-apm-verification-v1.3.0.tar.gz" "$APM_DELIVERY_DIR/repeat/release/sql-apm-verification-v1.3.0.tar.gz"
.venv/bin/python -c 'import filecmp,sys; assert filecmp.cmp(sys.argv[1],sys.argv[2],shallow=False)' "$APM_DELIVERY_DIR/first/offline-bundle.tar.gz" "$APM_DELIVERY_DIR/repeat/offline-bundle.tar.gz"
```

与 v1.2.0 离线包逐路径比较摘要，差异仅允许程序包、验收包、文档及对应身份／摘要清单。
运行时、编译源码、wheel、RPM、仓库索引和 Grafana 四组件必须相同。
升级入口进入程序包，UPGRADE.html 与其他七份 HTML 同时检查链接、代码块和随包命令路径。
发布正文从 `docs/releases/v1.3.0.md` 生成 `release-notes.md`，仅将相对链接改为固定提交链接。

独立检查工具包 1.2.0 不放入产品包；复用已有 Kylin 模块，两次构建到新目录并比较归档：

```bash
for round in first repeat; do
  var/issue31/build-venv/bin/python scripts/performance/build.py \
    --module var/issue70/module --output "$APM_DELIVERY_DIR/performance-$round"
done
.venv/bin/python -c 'import filecmp,sys; assert filecmp.cmp(sys.argv[1],sys.argv[2],shallow=False)' "$APM_DELIVERY_DIR/performance-first/performance-toolkit.tar.gz" \
    "$APM_DELIVERY_DIR/performance-repeat/performance-toolkit.tar.gz"
```

开发机使用匹配本机 PG17 的独立模块；目标机使用 Kylin 模块。模块只供检查，不安装到产品运行时。
两处升级后分别跑工具包、打包并通过脱敏扫描，旧结果格式和脱敏规则保留，新增首次读取数值字段须通过公开元数据白名单检查。

## 验证机与用户验收

验证机准备为带数据正式 v1.2.0，升级前记录版本、12 个固定输入文件的身份、数据和服务状态。
按[生产升级手册](production-upgrade.md)逐步执行；先做一次迁移失败演练，证明回滚及旧版可用，
再正式升级；演练数据库连接错误和 failed 单元恢复。核对结构、自检、三种检索、四个看板、sqlfind 和 daily status，随后运行一次导入加构建的每日任务。
运行适配工具包并保留结果；不重跑九任务、不重启、不重做全新安装。

用户对照验证机阅读升级手册，并以 sfmon 用 PuTTY 试用 sqlfind 的一次输出和交互；
在 Issue 或 PR 明确确认后才交独立评审。
验证报告区分 measured、modeled 和未验证；公开手册和报告不包含访问地址、主机名或私有身份。

## 合并后发布与生产复测

T1–T9 满足并独立评审通过后才合并，核对精确 main 合并提交的 CI。
**发布前定稿发布说明。** 合并后、正式制包前，另开只改文档的 PR 定稿 `docs/releases/v1.3.0.md`。
删除候选措辞，在“已验证”补齐双机演练、#73 开发机结果和已完成的用户验收；
“未验证”保留生产效果和本版未重演的 Kylin 全新安装，“不提供”保留备份、回退和旧版本直升边界。
经独立评审合并并核对该提交 CI 后，固定包含定稿的正式提交，按上文重制 `--kind release`；与实测候选逐文件比较，产品变化须补验。
正式发布不带 prerelease 标志：

```bash
git tag -a v1.3.0 "$APM_RELEASE_COMMIT" -m 'SQL APM v1.3.0'
git push origin refs/tags/v1.3.0
gh release create v1.3.0 \
  "$APM_RELEASE_DIR/sql-apm-v1.3.0.tar.gz" \
  "$APM_RELEASE_DIR/sql-apm-v1.3.0.tar.gz.sha256" \
  -R shenxg13/sql-apm --verify-tag --title 'SQL APM v1.3.0' \
  --notes-file "$APM_RELEASE_DIR/release-notes.md"
```

下载附件复核摘要，完整离线包按用户选择交付。旧标签和附件不动。
用户执行生产升级，先完成一次每日运行，确保最近七天有数据，再按与 v1.2.0 全量复测相同设置运行工具包：
全部用例、每项三次、含首次执行读取指标、计划和重点分析。
本次不带出生产升级记录；由性能结果包的 v1.3.0／1.14.0 身份和数据证据判定升级完成，
结构检查和自检以用户确认为准，不比较生产迁移耗时与模型和演练实测。
与 v1.2.0 第二轮复测逐项比较，写明窗口实际覆盖天数、最高频身份是否仍为原身份、
缺失计时七天明细、首次读取块数／耗时、库大小／事件数和剩余空间。
Issue #73 已接受的四类剩余差距以及排行函数首次超过目标只记录；其他未达项由用户决定后续。
T10–T12 完成前 Issue 保持打开。
