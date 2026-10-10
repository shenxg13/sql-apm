# SQL APM 本地开发说明

本页维护实际环境说明及检查命令。产品需求、阶段与待定事项分别见
[交付范围](../../.project-wiki/decisions/project-scope.md)、
[运行环境与组件](../../.project-wiki/decisions/runtime-and-components.md)。
当前以可联网 Alma 环境开展开发；Kylin V10 SP2 演练使用
[离线部署与验证手册](kylin-offline-deployment.md)，验证结果使用
[记录模板](kylin-validation-record.md)。演练结论不能替代生产部署验收。

## 相关操作契约

| 操作 | 对应要求 |
| --- | --- |
| 本地日志及来源、批次清单 | [日志导入](../../.project-wiki/features/log-ingestion.md) |
| 导入、构建、查询任务与诊断 | [命令行与本地配置](../../.project-wiki/features/operator-cli.md) |
| 重试、串行执行和切换版本 | [构建与发布](../../.project-wiki/features/baseline-versions.md) |
| SQL 原文、明细和历史清理 | [存储与留存](../../.project-wiki/contracts/sql-storage.md) |
| SQL 输入与 Grafana 展示 | [检索及历史查看](../../.project-wiki/features/sql-search-and-views.md) |

离线业务 CLI 已交付，展示功能另行实施；数据库初始化／物理结构见下文，
下列质量命令用于仓库 Harness。
开发日志样本位于本地忽略目录 `raw/inbox/hashdata/`（历史目录名，来源语义为 MPP），不是既定生产接收目录。

## Python 项目环境

项目运行及兼容验证目标为精确版本 **Python 3.13.16**，版本由项目自行维护，
再次变更须另行确认，见[运行约束](../../.project-wiki/decisions/runtime-and-components.md#项目自行维护-python-版本2026-10-08)。
从官方源码构建至 `var/python-3.13.16/`，再重建 `.venv/`；不包含系统 site-packages，
不改动系统 Python。旧解释器保留在 `var/python-3.9.5/` 供 #49 基准运行，
Issue 关闭后是否删除由用户决定。这些本地目录均由 Git 忽略。

venv 使用源码自带 ensurepip 引导；Python 3.13.16 源码自带 pip 26.2.1。
业务依赖 pglast 7.18 和 psycopg2-binary 2.9.10 已在根 requirements.txt 锁定版本与 wheel 摘要。
Kylin 使用源码内 ensurepip 引导，不要求与 Alma 的包工具版本相同。

在仓库根目录使用：

```bash
source .venv/bin/activate
python --version
python -m pip --version
python -m pip check
```

也可直接调用 `.venv/bin/python`，避免依赖终端激活状态；使用 `deactivate` 退出。
`venv` 沿用创建它的解释器版本，不能通过创建虚拟环境切换 Python 补丁版本。
仓库迁移到其他路径或其他机器时应重新创建环境，不要移动现有虚拟环境后继续使用。

bz2、lzma 的开发头文件沿用本地 `var/python-build/deps/include/`，库搜索路径为
`var/python-build/deps/lib/`，无需安装系统软件。保持默认构建，命令如下：

```bash
APM_REPO="$PWD"
cd "$APM_REPO/var/issue49/python-build/Python-3.13.16"
CPPFLAGS="-I$APM_REPO/var/python-build/deps/include" \
LDFLAGS="-L$APM_REPO/var/python-build/deps/lib" \
  ./configure --prefix="$APM_REPO/var/python-3.13.16" --with-ensurepip=install
make -j4
make install
```

以上从仓库根目录开始，在独立源码构建目录执行，不使用系统解释器安装业务依赖。
旧环境的开发包来源见[历史验证记录](../reports/python-environment-2026-09-25.md)。
新环境执行 `scripts/deployment/check_environment.py` 核对标准库及依赖；
Alma 本机验证不能替代 Kylin 的独立源码构建、自检及真实流程。
正式部署的解释器随项目安装到项目目录，不依赖主机已有的 Python；不得搬运 Alma 的解释器目录。

## PostgreSQL 项目环境

Baseline 存储采用 **PostgreSQL 17**，保存执行记录、SQL 指纹和基线结果。
开发与正式部署的 Baseline 数据库保持相同主版本；部署时选定并记录具体 17.x
补丁版本。当前先准备一个存储实例，使用已有真实日志开发离线 Baseline。

后续测试实时采集时，按需准备独立的 **PostgreSQL 9.4.26** 实例，验证旧版
查询状态、开始时间和锁等待等基础接口。MPP 特有字段需结合现场字段定义
和脱敏样本验证，再补充真实环境联调；普通 PostgreSQL 测试不代表完整兼容验证。

数据库驱动使用已锁定的 psycopg2-binary 2.9.10，使用 CPython 3.13 Linux x86_64 wheel。
本机 PG17.10 工具位于 /usr/pgsql-17/bin；已有[项目初始化、版本升级与临时实例验证入口](database-initialization.md)，
初版业务物理结构随之交付。验证使用自动停止／清理的私有临时实例，未部署生产项目实例；
Kylin 演练从源码离线构建同一 17.10 补丁版本，使用项目目录中的工具路径、专用 postgres
系统用户和 pg_ctl；正式生产部署仍由后续工作落实。

## 质量工具

所需版本以 `scripts/quality/tool-versions.env` 为准；
安装来源和通用步骤见[工具手册](issue-pr-quality-tooling.md)。

本次初始化将缺少的 Node.js、npm、ShellCheck、shfmt 和 markdownlint-cli2
安装到仓库本地忽略目录 `var/harness-tools`，不修改系统工具。
该目录不进入 Git；其他克隆需自行准备合同要求的工具。

在仓库根目录使用这些工具：

```bash
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --check-tools-only
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

如果工具已在 PATH 中，可直接调用上述脚本。
本地检查无需 GitHub 登录或业务服务，流程用例使用模拟客户端。

## 原始资料完整性

Markdown 排版检查仅跳过已有的 SQL Baseline 原文快照。
维护文档照常检查；原文内容与固定来源通过以下命令核对：

```bash
git hash-object .project-wiki/raw/sql-baseline.md
```

预期结果：`d5b8c6e4ef8d317110aec7737d49088d4a9308e4`。
原文参与模板引用检查，其内容和来源元数据维护规则见[知识规范](../../.project-wiki/schema.md)。

## 当前协作方式

从 [AGENTS.md](../../AGENTS.md) 进入开发流程。
GitHub 目标为公开仓库 shenxg13/sql-apm。
首次发布及其验证按[本地初始化流程](../../.harness/workflows/local-bootstrap.md)完成交接；
接入后的新需求按 [GitHub 工作流](../../.harness/workflows/github-planning.md)推进。
后续确认的需求与决策另行记录，保留原文供追溯；许可证选择仍待讨论。

## 精简交付的本地验证

[程序发布说明](program-release.md)定义独立构建环境、精简包与独立验收目录的检查方式。
构建期 Markdown 工具不进入应用依赖。v0.2.0 的本地检查和目标机开始条件见
[程序发布说明](program-release.md)；Issue #45 已确认本轮目标机执行授权，先完成本地门禁并
留下开始记录，不重复请求确认。开发机通过不能代替目标机实测和用户 HTML 检查。

```bash
.venv/bin/python scripts/deployment/check_documents.py
var/issue31/build-venv/bin/python scripts/tests/test_deployment.py
.venv/bin/python scripts/deployment/verify_release_examples.py --app-root APP --output NEW_DIR
.venv/bin/python scripts/deployment/verify_release_full.py \
  --app-root APP --verification-root KIT --logs raw/inbox/hashdata --output NEW_DIR
```

第一项检查配置键集合、每个 JSON 示例、62 张表及所列列名、手册引用的随包命令；
第二项覆盖漏键、坏例、漏表、错列及缺少命令的反例，并检查三个 HTML。
第三项在私有 PG17 用已安装候选包实际执行全部指南 JSON 示例、七个辅助命令及自然日期清理，
最后一项用同一候选在私有 PG17 串行运行真实九任务及七个示例，生成目标机比较基准；
结束时停止实例并保留数据库和受保护输出，失败不自动重试已完成的构建。
若全部任务与示例已成功，仅最后的基准汇总失败，可执行
`verify_release_full.py --app-root APP --output EXISTING_DIR --collect-only`。
该模式只核对包身份并读取固定九任务／七示例记录，失败、缺失或不同候选的记录会被拒绝，
不连接数据库、不重复构建，也不覆盖已有基准。原始失败输出仍须保留并在报告说明。
历史 v0.1.0 的独立九任务与候选对齐证据保留在原报告中，不作为 v0.2.0 已通过的证明。

用户确认需要在保留库补采跨机比较数据时，先用原端口／socket 启动该私有实例，设置
SQL_APM_DSN 指向它，再执行下面的开发机专用命令，结束后停止实例：

```bash
.venv/bin/python scripts/deployment/verify_release_full.py \
  --app-root ORIGINAL_APP --output COMPLETED_RUN --supplement-statistics NEW_BASELINE_DIR
```

该模式验证原候选及产品摘要，用只读事务读取四个已完成构建，并先证明全部统计值仍与原始
记录精确一致，再生成新的基准和八个压缩逐行数值文件；不重建、删除或覆盖原记录。
非对数字段仍精确比较，仅 log_median、log_mad 逐行绝对差不超过 1e-12，NULL 必须一致。
把新基准和同目录数值文件一起交付，分别记录摘要；原基准与补采来源摘要保留用于追溯。
