# SQL APM 程序发布说明

当前交付目标为 `v0.3.0` 预发布，不用于生产；产品基准为包含 #51、#54 的 main
`a3fde9092b96497327133caf05cec173ba721dec`。v0.2.0 的制品、标签和历史记录保持原样。
范围、状态及验收以 [Issue #52](https://github.com/shenxg13/sql-apm/issues/52) 在线契约为准。

## 文件边界与文档

`scripts/deployment/package-files.json` 定义程序和独立验收资源集合。程序只包含运行代码、
规则、SQL／迁移、Grafana／daily 配置、必要部署工具和四份 HTML；开发材料与测试放在 verification。
`build_release.py` 接受 `vMAJOR.MINOR.PATCH`（不接受前导零），拒绝未提交变更，
从固定提交导出，按固定顺序、模式和零时间戳生成可重复的 tar.gz。

| 程序包根文件 | 原稿 |
| --- | --- |
| INSTALL.html | 部署手册、每日运行、配置指南、验证记录模板 |
| DATABASE.html | 数据库结构说明和四张 SVG |
| SEARCH.html | 检索与看板使用指南、查询函数契约 |
| RELEASE.html | [v0.3.0 发布说明](../releases/v0.3.0.md)，兼作文档入口 |

RELEASE.json 记录版本、提交、候选／发布类型、prerelease=true、production_use=false，
`documents` 中记录每份文档标题、全部 Markdown／SVG 原稿摘要、HTML 检查结果；
`entrypoint` 指向 RELEASE.html。files 记录程序逐文件摘要。
HTML 用锁定 Python-Markdown 3.8.2 生成，无外部资源请求、图内嵌、锚点可达、代码块原样保留。
GitHub 参考链接仅在用户点击时联网；四份 HTML 之间使用本地链接。

## 开发机构建与检查

复用已安装的独立 `var/issue31/build-venv`；首次建立时按锁定的
`scripts/deployment/build-requirements.txt` 下载并离线安装构建依赖，产品运行依赖不增加。
程序与完整包命令见[手册第 3 节](kylin-offline-deployment.md#3-获取传输与核对本次离线包)。
输出使用 `var/issue52/deliveries/完整提交/`，不可复用旧目录。

```bash
.venv/bin/python scripts/deployment/check_documents.py
var/issue31/build-venv/bin/python scripts/tests/test_deployment.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/db/verify.py
.venv/bin/python scripts/db/verify_publication.py
.venv/bin/python scripts/db/verify_cleanup.py
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

文档检查对照产品校验器中的键集合和 schema.sql 的表／列以及查询函数签名，逐个校验 JSON 示例，
核对手册命令在程序或验收包中。制包测试包含漏键、坏例、漏表、错列、函数参数和返回列漂移、缺脚本的反例。
另外必须执行全部指南示例，不把 JSON 校验等同于真实命令通过。
重复制包使用同一固定提交、同一输入和不同输出目录；程序、验收和完整包应字节相同。

在仓库外或隔离目录解压程序和验收包，用精确 Python 3.13.16 建立全新 venv，
从离线 wheels 按 requirements.txt 安装。令 APM_APP、APM_VERIFY 指向这两个目录：

```bash
cd "$APM_APP"
.venv/bin/python scripts/deployment/verify_package.py --installed
for check in unit database publication smoke cleanup search views grafana daily daily-recovery; do
  .venv/bin/python "$APM_VERIFY/scripts/deployment/run_verification.py" \
    --app-root "$APM_APP" --pg-bin /usr/pgsql-17/bin "$check"
done
```

十个入口通过后，用同一候选程序在全新私有实例运行开发机九任务及全部指南示例，
生成新增选批字段和四示例的机器基准。归一化超时非零或业务基准差异均拒绝通过。
开发机与目标机均不修改系统日期；真实日志每日回放与九任务先后进行，保留独立记录。
指南跨机比较采用已确认口径：两列 log_median、log_mad 逐行绝对差不超过 1e-12，
其他字段精确相同，并核对 NULL 状态和记录最大差／差异行数。同机门槛前后及清理前后仍全部精确不变。

## 目标机开始与证据绑定

四份文档、制包和开发机检查完成后，在 Issue／PR 留下精确提交、制品摘要、本地结果、
目标机初始状态的最新只读探测和执行计划。用户已授权本轮实施方全流程演练，记录发出后即可开始。
目标机不是初始状态或不可达时停止报告，不自行重置。

按随包手册完成默认四进程九任务、每日真实日志回放、定时器与模拟传输、服务与访问检查、
开发机浏览器访问目标机的检索和四看板检查。每次结果绑定实际候选提交与包摘要，无法解释的差异保留现场。

最终候选制包用 `--previous-program 已实测程序包` 比较产品文件：sql_apm、rules、requirements.txt、
grafana、daily 以及所有随包部署脚本（只排除包清单验证器自身）。新增、删除、改名也算不同。
全部相同才能继承结果；文档和验收资源变更仍需补验受影响步骤。产品文件改变时重新确定重跑范围。

实施方先保存全部证据；用户再次恢复初始快照后，实施方重新只读核对起点。
用户亲自按随包手册手动重走安装、账号、九任务、每日运行、定时器、模拟传输、检索和四看板。
两轮分别使用[记录模板](kylin-validation-record.md)，用户明确确认完整通过后才能合并。

## 合并后发布

R1–R17、R19–R21 及独立评审完成后合并，R18 在合并后执行，Issue 保持打开。
从确认的 main 提交以 `--kind release` 重制包，与最终验收候选比较；产品文件相同，
其他差异只限提交标识与派生元数据。不同则补验。

把精确提交、v0.3.0 标签、两个附件的文件名、大小、SHA-256 和发布说明交给用户。
取得明确确认后才执行下面的形式，不覆盖已有标签或附件：

```bash
git tag -a v0.3.0 "$APM_RELEASE_COMMIT" -m 'SQL APM v0.3.0 预发布'
git push origin refs/tags/v0.3.0
gh release create v0.3.0 \
  "$APM_RELEASE_DIR/sql-apm-v0.3.0.tar.gz" \
  "$APM_RELEASE_DIR/sql-apm-v0.3.0.tar.gz.sha256" \
  -R shenxg13/sql-apm --verify-tag --prerelease \
  --title 'SQL APM v0.3.0 预发布' --notes-file "$APM_RELEASE_NOTES"
```

发布说明由 docs/releases/v0.3.0.md 的同一原稿生成；Release 页面链接转换为对应精确提交
或交付文件的可达链接，并仅在 Release 页面和内网交付记录追加程序包摘要及下载校验命令，
避免自引用摘要进入包。实测完成前不填写通过结论。

发布后实际下载两个附件，核对摘要、清单、四份 HTML、标签及提交；完整离线包包含同一
程序包。将链接、大小和摘要写入 Issue／PR，R18 完成前不关闭 Issue。
