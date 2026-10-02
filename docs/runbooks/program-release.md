# 精简程序包与预发布交付

[Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 固定首版为 `v0.1.0` 预发布，
不用于生产。[安装手册](kylin-offline-deployment.md)负责 Kylin 操作；本文负责开发机制包、
本地检查及合并后发布。打标签和发布前必须取得用户明确确认。

## 制品边界

| 制品 | 内容和去向 |
| --- | --- |
| `sql-apm-v0.1.0.tar.gz` 及同名 `.sha256` | 仅此两项作为 GitHub Release 自定义附件；解压得到 `app/`。 |
| `app/INSTALL.html` | Markdown 生成的单文件安装及操作手册；含必要关联章节和人工记录模板。 |
| `sql-apm-verification-v0.1.0.tar.gz` | 合成测试、固定基准及验收入口，只进入内网完整离线包。 |
| `offline-bundle.tar.gz` 及同名 `.sha256` | 源码、wheel、RPM、精简程序和验收材料，继续内网交付。 |

程序内容由 `scripts/deployment/package-files.json` 的必要文件规则决定。
保留日常 SQL 归一化／近似诊断入口、规则及上游许可证、数据库全部必要 SQL／历史迁移资源。
开发流程、历史报告、构建工具和测试不进入 `app`；`.venv` 在目标机按离线流程创建。
项目许可证的选择不在本 Issue 范围内。

## 构建与本地检查

先按安装手册第 3 节准备 `var/issue31/build-venv`，运行依赖仍只由根目录
`requirements.txt` 管理。构建工具锁定在 `scripts/deployment/build-requirements.txt`：
[Python-Markdown 3.8.2](https://pypi.org/project/Markdown/3.8.2/)及其 Python 3.9 传递依赖，
均锁版本和 wheel SHA-256；不进入目标机运行依赖。

所有制品从固定 Git 提交读取，制包工具自身也须匹配该提交，拒绝把未提交变更混入制品。
默认 `--kind candidate`；`--kind release` 另检查提交属于本地已获取的 `origin/main`，
发布前必须获取并核对远程最新状态。构建时间、属主及文件排序固定，重复构建内容相同。

```bash
var/issue31/build-venv/bin/python scripts/tests/test_deployment.py
var/issue31/build-venv/bin/python scripts/deployment/build_release.py \
  --commit HEAD --kind candidate --version v0.1.0 \
  --output var/issue31/release-candidate \
  --previous-program var/issue31/offline-bundle/program.tar.gz
```

`--previous-program` 指向保留的原试跑程序归档，逐一比较候选产品文件（含规则、SQL
历史版本、依赖清单、初始化脚本）同路径摘要。`all_equal=true` 才支持 K8／K9 证据继承，
原九任务本身的记录不改；新包安装、自检、119 首批和 HTML 人工确认仍需实际完成。
差异报告只含路径与摘要，不含业务日志或凭据。

新目录解压精简程序和验收包，以项目 Python 3.9.5 创建隔离 `.venv`，从内网 wheel
缓存离线安装根目录锁定依赖；使用以下入口检查实际解压的程序，不能在开发仓库根目录
运行后声称包可独立运行。示例变量由执行者填写为开发机隔离目录：

```bash
"$APM_APP/.venv/bin/python" "$APM_APP/scripts/deployment/verify_package.py" --installed
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/run_verification.py" \
  --app-root "$APM_APP" unit
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/run_verification.py" \
  --app-root "$APM_APP" --pg-bin /usr/pgsql-17/bin database
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/run_verification.py" \
  --app-root "$APM_APP" --pg-bin /usr/pgsql-17/bin publication
"$APM_APP/.venv/bin/python" "$APM_VERIFY/scripts/deployment/run_verification.py" \
  --app-root "$APM_APP" --pg-bin /usr/pgsql-17/bin smoke
```

数据库检查仅创建各自私有、禁用 TCP 的临时 PG17 实例，退出后清理；不连接现有数据库。
`smoke` 通过实际 CLI 执行小样例 full、rebuild、status、history 并核对版本和无重复导入。
原开发机直接运行 `scripts/db/verify*.py` 的默认行为保留。

`RELEASE.json` 记录版本、提交、构建类型、手册原稿摘要和程序逐文件摘要。
`verify_package.py` 检查缺文件、多文件、符号链接和摘要；`--installed` 仅额外允许
`.venv` 和 Python 的 `__pycache__`。HTML 构建检查无外部阅读资源请求、所有页内锚点可达、
代码块逐字一致；用户断网浏览器的目录、复制、搜索、打印结果另填人工记录，不由自动结果代替。

## 目标机试跑暂停点

开发、制包和本地验证完成后，先向用户报告包的提交／摘要、本地检查结果、恢复快照要求，
以及受影响安装步骤和 SSD 四进程 119 首批方案。用户明确确认之前不向目标机传输、安装、
启动验证或执行导入。试跑及失败后单进程回放的步骤见安装手册第 10.1 节。

## 合并后发布

合并前须完成当前 Issue 的 K1–K15、K17 及独立评审；K16 在合并后完成。
从确认的 main 提交重新以 `--kind release` 制包，检查与已验收候选包的差异，更新内网完整包；
除提交／版本等元数据外，任何行为或手册操作变化都须补验。

先把精确提交、标签、两个附件的文件名／大小／SHA-256 和中文发布说明交给用户，
取得打标签和发布的明确确认后再执行。以下是届时的操作形式，本阶段不执行：

```bash
git tag -a v0.1.0 "$APM_RELEASE_COMMIT" -m 'SQL APM v0.1.0 预发布'
git push origin refs/tags/v0.1.0
gh release create v0.1.0 \
  "$APM_RELEASE_DIR/sql-apm-v0.1.0.tar.gz" \
  "$APM_RELEASE_DIR/sql-apm-v0.1.0.tar.gz.sha256" \
  -R shenxg13/sql-apm --verify-tag --prerelease \
  --title 'SQL APM v0.1.0 预发布' --notes-file "$APM_RELEASE_NOTES"
```

发布说明必须说明不用于生产、下一版因 #33 修改落库标识而需要全新数据库，
以及四进程实测结论和解析并发建议。未完成实测前不能填写结果。
当前候选包的[目标机试跑报告](../reports/slim-release-kylin-trial-2026-10-03.md)已记录
SSD 四进程仍有 882 次超时、正式分组少 114 个；发布说明不能写成迁移 SSD 已解决问题。
不覆盖已有标签或附件；已存在时先核对是否为同一交付。
发布后实际下载程序和校验文件，核对 SHA-256、包清单、HTML、标签及提交；
内网完整包须包含同一程序包。把下载链接和摘要记入 Issue／PR，K16 完成前不关闭 Issue。
