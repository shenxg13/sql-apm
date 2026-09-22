# SQL APM 项目知识

当前处于需求沟通阶段。先保存原始资料，后续通过讨论逐项确认具体需求。

## 原始资料

- [SQL Baseline 系统方法论](raw/sql-baseline.md)：原文快照，作为需求讨论的参考。
- 来源：[shenxg13/chat-the-best · topics/sql-baseline.md](https://github.com/shenxg13/chat-the-best/blob/main/topics/sql-baseline.md)。
- 固定版本：[a1290c3c76afc074e93e37b9acc970d450d3337b](https://github.com/shenxg13/chat-the-best/blob/a1290c3c76afc074e93e37b9acc970d450d3337b/topics/sql-baseline.md)。
- 原文件 Git blob SHA：`d5b8c6e4ef8d317110aec7737d49088d4a9308e4`。
- 保存日期：2026-09-22。
- 获取状态：固定版本已读取，原文件 Git blob SHA 已核对。
- 处理状态：raw，作为原始输入保留，尚未提升为已确认的产品契约。

## 资料状态与使用约定

原始文档按上述版本原样保存。文中的“已确认”“推荐”“第一版”等表述保留其
来源语境，不自动成为 sql-apm 项目已经确认的需求或决策。

架构、技术选型、实施范围、阈值参数和验收标准均留待后续沟通确认。
先前讨论中的实施建议也不视为已接受方案。

后续确认的需求与决策另行记录，并引用本快照；原始资料保持原样以便追溯。

## 已采用的开发基础

- [Agent 开发流程](architecture/agent-development-harness.md)。
- [通用工程原则](decisions/engineering-principles.md)。
- [本地初始化范围](../.harness/plans/local-bootstrap.md)。
- [本地开发与检查](../docs/runbooks/local-development.md)。
- [模板来源及本地定制](../docs/provenance.md)。
- [本地初始化验证](../docs/reports/local-bootstrap-validation.md)。
- [知识更新记录](log.md)。

上述内容描述开发协作方式，不定义 SQL APM 的业务架构。
项目运行模型暂未确认。

## 知识规范与模板

阅读[实体规范](schema.md)，在具体需求确认后按需建立有来源的项目知识。

- [Architecture](templates/architecture.md)。
- [Module](templates/module.md)。
- [Feature](templates/feature.md)。
- [Contract](templates/contract.md)。
- [Decision](templates/decision.md)。
- [Method](templates/method.md)。
- [Operating model](templates/operating-model.md)：尚未采用的运行模型模板。
