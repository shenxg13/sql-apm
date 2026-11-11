# SQL APM v1.3.1 制包与发布

本版契约为 [Issue #84](https://github.com/shenxg13/sql-apm/issues/84)，以 #82 合并后的固定提交为基线。程序／验收包与完整离线包均从同一提交构建；正式发布在用户验收和独立评审后进行。

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
.venv/bin/python scripts/db/verify_query_refresh.py
.venv/bin/python scripts/db/verify_import_collection.py
PATH="$PWD/var/harness-tools/bin:$PWD/.venv/bin:$PATH" scripts/quality/check.sh
```

数据库检查使用私有临时实例。不再复制全量样本演练升级；先用摘要证明
`sql_apm/storage/migrations/1.14.0-to-1.15.0.sql`、`sql_apm/storage/schema.sql` 与 #82 的合并提交
`db7d3fdfb43d0bfd3498245c938a29d66a153e89` 相同，然后继承其 0.624 秒迁移和三个时机零差异的证据。
Kylin 服务、运行时和本版手册演练以验证机实测为准。原始证据库不原地启动；
本机磁盘以 `df -h /mnt/c` 为准，至少保留 20 GiB。

## 固定运行时与离线输入

复用正式 v1.3.0 的完整包，核对已交付摘要；旧归档不覆盖。

```bash
(cd var/releases/v1.3.0 && sha256sum -c offline-bundle.tar.gz.sha256)
test ! -e var/issue84/inputs
mkdir -p var/issue84/inputs
tar -xzf var/releases/v1.3.0/offline-bundle.tar.gz -C var/issue84/inputs
APM_INPUTS="$PWD/var/issue84/inputs/offline-bundle"
.venv/bin/python scripts/deployment/runtime_archive.py inspect --artifact "$APM_INPUTS/runtime"
mkdir var/issue84/rpm-collection
cp "$APM_INPUTS/rpm-evidence/"* var/issue84/rpm-collection/
ln -s "$APM_INPUTS/rpms" var/issue84/rpm-collection/rpms
```

`runtime.tar.gz` 必须为 `d271d6a1471c41723724b48afad59f9675e1b324747fbb7dfae283f97340314c`，
来源为 v1.0.0 的一次编译；Python 3.13.16 含虚拟环境，PostgreSQL 17.10。
不重新编译，不更换 wheel、RPM、Grafana 或插件。

## 可重复构建

先提交已审查的修改。合并前使用 candidate；正式 release 要求提交已经进入 origin/main。

```bash
APM_BUILD_COMMIT="$(git rev-parse HEAD)"
APM_DELIVERY_DIR="$PWD/var/issue84/deliveries/$APM_BUILD_COMMIT"
for round in first repeat; do
  var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
    --commit "$APM_BUILD_COMMIT" --version v1.3.1 --kind candidate \
    --output "$APM_DELIVERY_DIR/$round/release"
  .venv/bin/python scripts/deployment/build_bundle.py \
    --release-dir "$APM_DELIVERY_DIR/$round/release" \
    --output "$APM_DELIVERY_DIR/$round/offline-bundle" \
    --runtime-artifact "$APM_INPUTS/runtime" \
    --rpm-collection var/issue84/rpm-collection \
    --python-source "$APM_INPUTS/sources/Python-3.13.16.tgz" \
    --postgres-source "$APM_INPUTS/sources/postgresql-17.10.tar.gz" \
    --wheel-dir "$APM_INPUTS/wheels" --grafana-files "$APM_INPUTS/grafana" \
    --source-manifest "$APM_INPUTS/manifest.json"
done
.venv/bin/python -c 'import filecmp,sys; assert filecmp.cmp(sys.argv[1],sys.argv[2],shallow=False)' "$APM_DELIVERY_DIR/first/release/sql-apm-v1.3.1.tar.gz" "$APM_DELIVERY_DIR/repeat/release/sql-apm-v1.3.1.tar.gz"
.venv/bin/python -c 'import filecmp,sys; assert filecmp.cmp(sys.argv[1],sys.argv[2],shallow=False)' "$APM_DELIVERY_DIR/first/release/sql-apm-verification-v1.3.1.tar.gz" "$APM_DELIVERY_DIR/repeat/release/sql-apm-verification-v1.3.1.tar.gz"
.venv/bin/python -c 'import filecmp,sys; assert filecmp.cmp(sys.argv[1],sys.argv[2],shallow=False)' "$APM_DELIVERY_DIR/first/offline-bundle.tar.gz" "$APM_DELIVERY_DIR/repeat/offline-bundle.tar.gz"
```

与 v1.3.0 离线包逐路径比较摘要，差异仅允许程序包、验收包、文档及对应身份／摘要清单。
运行时、编译源码、wheel、RPM、仓库索引和 Grafana 四组件必须相同。
升级入口进入程序包，UPGRADE.html 与其他七份 HTML 同时检查链接、代码块和随包命令路径。
发布正文从 `docs/releases/v1.3.1.md` 生成 `release-notes.md`，仅将相对链接改为固定提交链接。

独立检查工具包 1.3.0 不放入产品包；复用已有 Kylin 模块，两次构建到新目录并比较归档：

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

验证机保留 v1.3.0 数据，程序身份为旧候选。先逐文件核对其余代码、SQL 和看板与正式包相同，
仅把 11 个身份／文档文件及验收包对齐为正式 v1.3.0；保留前后摘要，代码、数据库和服务不动。
记录版本、数据和服务状态，按[生产升级手册](production-upgrade.md)执行。
先演练真实迁移失败，确认事务回滚后 v1.3.0 可用，再正式升级；完成结构、自检、三种检索、
四看板、sqlfind 和 daily status，执行独立一致性 SQL，所有差异应为 0。
从已保留原始样本复制两个集群后续一天，做一次导入加构建的每日运行；
前后使用随工具包的采集 SQL，窄记录 seq_scan 增量应为 0。按固定 `--at` 做定向补测并脱敏打包。

`resume` 仅沿用夹具覆盖；连接错误 `STOP:` 和 failed 单元处理的命令／路径未变时，继承 #79 同机演练。
若修改这些命令或路径必须重新执行。其他手册命令和路径均在验证机执行。
不重跑九任务、不重启、不重做全新安装。用户只需阅读候选包的 UPGRADE.html，
在 Issue 或 PR 中明确确认，之后才交独立评审；用户不在验证机上操作。
验证报告区分 measured、modeled、继承和未验证；公开手册和报告不包含访问地址、主机名或私有身份。

## 合并后发布与生产复测

V1–V8 满足并独立评审通过后才合并，核对精确 main 合并提交的 CI。
**发布前定稿发布说明。** 合并后、正式制包前，另开只改文档的 PR 定稿 `docs/releases/v1.3.1.md`。
同一个文档 PR 完成仓库 v1.3.0 发布说明的日期勘误，以及数据库结构、存储设计和存储 wiki
三处各版本对应结构的事实（v1.3.1／1.15.0、v1.3.0／1.14.0、v1.2.0／1.13.0、v1.1.0／1.12.0）。
删除候选措辞，在“已验证”补齐验证机演练、#82 开发机结果和已完成的用户验收；
“未验证”保留生产效果和本版未重演的 Kylin 全新安装，“不提供”保留备份、回退和旧版本直升边界。
经独立评审合并并核对该提交 CI 后，固定包含定稿的正式提交，按上文重制 `--kind release`；与实测候选逐文件比较，产品变化须补验。
正式发布不带 prerelease 标志：

```bash
git tag -a v1.3.1 "$APM_RELEASE_COMMIT" -m 'SQL APM v1.3.1'
git push origin refs/tags/v1.3.1
gh release create v1.3.1 \
  "$APM_RELEASE_DIR/sql-apm-v1.3.1.tar.gz" \
  "$APM_RELEASE_DIR/sql-apm-v1.3.1.tar.gz.sha256" \
  -R shenxg13/sql-apm --verify-tag --title 'SQL APM v1.3.1' \
  --notes-file "$APM_RELEASE_DIR/release-notes.md"
```

下载附件复核摘要，完整离线包按用户选择交付。旧标签和附件不动；只在 v1.3.0 GitHub 发布正文末尾
追加一行带日期的勘误，指向 v1.3.1 发布说明，不重建旧附件或修改包内文档。保留修改前正文及摘要。

用户执行生产升级，升级后的首次每日运行前后各采一次 JSON，两次之间不重置统计；
程序和结构身份、用户确认的结构检查及自检共同判定升级完成。生产升级记录不带出。
按集群报告窄记录 seq_scan 增量、逐文件提交前和整天每条记录耗时、输入规模、日志日数、
新增原文数及并发查询情况。窄记录扫描目标为 0，耗时目标为 v1.2.0 参照的 1.2 倍以内。

日志导入到前一天后执行[定向补测](performance-check.md#定向补测)，不跑全部用例三轮。
记录一天／七天实际覆盖；一天和七天排行（各集群及全部集群）、指定集群最高频身份七天详情和
200 行明细目标均为 5 秒，30 天及首次读取只记录。记录最高频身份是否仍为 #79 原身份。
所有未达项由用户决定后续处理并记入 Issue。V9–V11 完成前 Issue 保持打开。
