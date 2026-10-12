# SQL APM 来源与模板采纳记录

## 模板来源

- 仓库：[shenxg13/agent-harness](https://github.com/shenxg13/agent-harness)。
- 固定提交：`c8f26d983500e95a8c0fc4ce49f6c763f9d19cb2`。
- [固定提交](https://github.com/shenxg13/agent-harness/commit/c8f26d983500e95a8c0fc4ce49f6c763f9d19cb2)。
- 采纳日期：2026-10-12。
- 本地项目：sql-apm，独立 main 历史。
- 首次于 2026-09-22 从 `859c39d1697241579b9bde443125477f88b1827c` 导入 60 个跟踪文件
  及其可执行权限，合并项目已有知识索引；本次增量采用见下文。
- 最初授权为创建本地仓库；用户后续明确授权首次公开同步至 shenxg13/sql-apm。
- 许可证尚未选择，本次沿用现有文件集，不新增 LICENSE。

更早的模板提取历史保存在
[上游来源记录](https://github.com/shenxg13/agent-harness/blob/859c39d1697241579b9bde443125477f88b1827c/docs/provenance.md)。
其历史发布授权和验证结果仅适用于上游。

## 已有项目知识

[SQL Baseline 原文](../.project-wiki/raw/sql-baseline.md)来自：

- 仓库路径：shenxg13/chat-the-best，topics/sql-baseline.md。
- 固定提交：`a1290c3c76afc074e93e37b9acc970d450d3337b`。
- [固定原文](https://github.com/shenxg13/chat-the-best/blob/a1290c3c76afc074e93e37b9acc970d450d3337b/topics/sql-baseline.md)。
- Git blob SHA：`d5b8c6e4ef8d317110aec7737d49088d4a9308e4`。

原始资料按字节保存，状态为 raw。文中的架构、技术选型和阈值建议仍待讨论。
[资料来源与范围](../.project-wiki/decisions/project-scope.md)记录来源、获取状态和确认边界；
[知识索引](../.project-wiki/index.md)按任务引导到对应主题。

## 本次采用

[Issue #57](https://github.com/shenxg13/sql-apm/issues/57) 于 2026-10-11 确认采用范围与 U1–U13，
在 #52 合并并关闭后，于 2026-10-12 从本地及远程一致的
`95737b14492e7a60de04f40c7e5bd96b83827417` 基线开始实施。
采用上游固定提交 `c8f26d983500e95a8c0fc4ce49f6c763f9d19cb2`，不自动跟踪上游 main。
来源包括上游 #1、#2、#3、#7、#9、#10、#13，原始改动说明见
[上游固定更新日志](https://github.com/shenxg13/agent-harness/tree/c8f26d983500e95a8c0fc4ce49f6c763f9d19cb2/.harness/update-log)。
本项目确认定义采用目标，自动检查与 PR 保存实施证据。合并后验收覆盖该次 main CI、
新 Claude Code 会话入口加载、完成状态转换及实施工作树清理；验收结果由在线 Issue 与 PR 记录。

本次先建五个类型标签并给唯一未关闭的 #57 补 `type:process`，再换状态脚本。
直接采用的 12 个文件以 Git blob 和文件模式逐一比较；范围 C 的剩余差异如下。
上游自己的日志条目不导入，两份本地旧日志只加开头提示并保留原有字节。
新记录见[流程变更](../.harness/update-log/2026-10-12-issue-57-harness-update.md)与
[知识摘要](../.project-wiki/log/2026-10-12-issue-57-harness-update.md)。

## 本地定制

下表穷尽 Issue #57 范围 C 相对本次固定上游版本的差异；“无”表示内容及文件模式一致。
PR 同步列出此表，并保存逐文件比较证据。

| 范围 C 文件 | 与上游固定提交的全部剩余差异及理由 |
| --- | --- |
| `.harness/workflows/review-sync.md` | 无；本地旧采纳日期改用上游通用表述，历史仍由下文 #38 记录。 |
| `.github/review_convergence_comment.md` | 无。 |
| `scripts/tests/test_review_convergence.sh` | 无。 |
| `.harness/tooling-runtime.md` | 无。 |
| `docs/getting-started.md` | 仅在 GitHub 初始化段落明确 SQL APM 使用普通业务仓库，保留项目既有选择。 |
| `.harness/workflows/wiki-update.md` | 仅将“项目有人类导航时维护”改为维护本项目已有导航，保留已确认的必需同步。 |
| `.harness/workflows/knowledge-sync.md` | 仅将人类导航的可选条件改为核对本项目导航，理由同上。 |
| `.harness/checklists/doc-sync.md` | 无。 |
| `.harness/catalog.md` | 仅增加 SQL APM 需求讨论的流程及知识路由行，保留项目主题入口。 |
| `.project-wiki/methods/knowledge-maintenance.md` | 日期为本次采纳日；owners 增加 `docs/README.md`；sources 增加本地重组报告；related 增加项目范围。Source Of Truth 保留 9 月两次用户确认、官方文章依据与重组报告，并改用本项目采用确认。导航表由可选示例改为本地链接；Workflows 第 3 条保留强制导航同步与新设计按实际交付加入；Update Rules 明确 SQL Baseline 原文字节；Open Questions 保留产品事项边界。这些差异保留项目既有知识与确认来源。 |
| `.project-wiki/schema.md` | 无。 |
| `.project-wiki/architecture/agent-development-harness.md` | 日期为本次采纳日；sources 以本地初始化计划为 current、上游提取计划为 historical。Source Of Truth 及 Contracts 的五处上游 Issue 依据改为本项目采用记录；入口条款明确以合并后新会话实测启动加载，证据由在线 Issue 与 PR 保存；项目检查条款由模板空操作改为实际三条命令及脚本导航。差异避免把上游确认或验证冒充本项目事实。 |
| `CLAUDE.md` | 无，全部内容为 `@AGENTS.md` 加换行。 |
| `.github/workflows/quality.yml` | 无，单独调用项目检查入口。 |

其余项目所有内容及原有模板定制保持以下边界：

| 文件或范围 | 定制与理由 |
| --- | --- |
| `README.md`、`AGENTS.md`、`.project-wiki/index.md`、`scripts/README.md`、`docs/README.md` | 保留 SQL APM 产品、知识和操作导航，只补检查入口、工具复用、类型标签、工作树、新日志路由及模板更新与回流说明。 |
| `scripts/quality/project_checks.sh` | 替换上游空操作，保持原 CI 的 Python 版本输出、函数字典校验、单元测试与函数覆盖命令；从脚本位置定位仓库根目录。 |
| Wiki 产品主题、原始资料、日志及工程原则 | 保存 SQL APM 需求、确认来源及本地初始化计划；原始 SQL Baseline 字节不变，旧日志冻结。 |
| 本地初始化计划、开发说明、历史报告 | 保留本项目证据；上游提取历史仅供追溯，不表示本项目验收。 |
| 来源、同步状态与两处新日志 | 记录本次固定采用版本、日期和差异，不导入上游项目自己的日志。 |
| `.markdownlint-cli2.yaml` | 仅排除原始 SQL Baseline 快照的排版检查，以保留原始字节；仍参与引用检查并核对 Git blob。 |
| `sql_apm/`、产品测试、规则、部署及展示资源 | 项目所有的实现，本次不修改；范围 B 通用文件与上游逐字节一致。 |

2026-09-25 的按需阅读和知识维护调整已回流上游；项目确认及导航定制保留，
[迁移记录](reports/knowledge-reorganization-2026-09-25.md)继续保存需求保留证据。
2026-10-05 [Issue #38](https://github.com/shenxg13/sql-apm/issues/38) 的任一轮批准规则
已随上游回流，本次统一采用上游评审协议、评论模板、校验器和回归，不再作为本地偏离。
其采用日期及旧 `pass_to_R3` 记录仍可从[旧流程日志](../.harness/update-log.md)追溯。
后续采用更新时按[人工更新流程](updating.md)审阅上述定制。

## 显式来源残留审计

保留以下更早来源的业务标识，作为显式提取残留检查对象。
本项目合法的账号、SQL 术语和知识路径不属于残留标识。

```json
{"source_markers": ["tm-lab", "tm_lab", "tm-agent", "sologovision", "CNIPA", "trademark", "TM_LAB"]}
```

此配置也供模板自带的残留检测回归使用；正常质量检查允许本项目业务知识。
本次验证结果见[本地初始化验证记录](reports/local-bootstrap-validation.md)。
首次远程同步的授权范围、设置和验证见[首次发布记录](reports/initial-publication.md)。
