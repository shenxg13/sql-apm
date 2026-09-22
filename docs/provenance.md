# SQL APM 来源与模板采纳记录

## 模板来源

- 仓库：[shenxg13/agent-harness](https://github.com/shenxg13/agent-harness)。
- 固定提交：`859c39d1697241579b9bde443125477f88b1827c`。
- [固定提交](https://github.com/shenxg13/agent-harness/commit/859c39d1697241579b9bde443125477f88b1827c)。
- 采纳日期：2026-09-22。
- 本地项目：sql-apm，独立 main 历史。
- 固定来源包含 60 个跟踪文件；导入其文件与可执行权限，合并项目已有知识索引。
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
[知识索引](../.project-wiki/index.md)记录来源、获取状态和确认边界。

## 本地定制

| 文件或范围 | 定制 |
| --- | --- |
| README、AGENTS、任务目录 | 指向 SQL APM 知识和当前本地初始化范围 |
| Wiki 索引、日志、规范 | 保留已有原文，整合模板入口；快照位于 Wiki 的 raw 目录，元数据记录在索引 |
| 通用流程架构与工程原则实体 | 当前来源指向本项目初始化计划，上游计划标为历史 |
| 上游提取计划与验证报告 | 保留固定版本链接，明确其历史证据边界 |
| 来源、同步状态与更新日志 | 记录模板版本和项目定制 |
| Markdown 检查配置 | 仅排除原始 SQL Baseline 快照的排版检查，以保留其原始字节 |
| 本地初始化计划、开发说明、验证报告 | 记录本项目实际初始化范围和本地证据 |

通用脚本、流程回归、工具版本合同、Issue/PR 模板和 CI 入口保持上游实现。
原文仍参与模板引用检查，并通过 Git blob SHA 单独核对内容完整性。
后续采用上游更新时，应按[人工更新流程](updating.md)审阅上述定制。

## 显式来源残留审计

保留以下更早来源的业务标识，作为显式提取残留检查对象。
本项目合法的账号、SQL 术语和知识路径不属于残留标识。

```json
{"source_markers": ["tm-lab", "tm_lab", "tm-agent", "sologovision", "CNIPA", "trademark", "TM_LAB"]}
```

此配置也供模板自带的残留检测回归使用；正常质量检查允许本项目业务知识。
本次验证结果见[本地初始化验证记录](reports/local-bootstrap-validation.md)。
首次远程同步的授权范围、设置和验证见[首次发布记录](reports/initial-publication.md)。
