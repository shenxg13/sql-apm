# SQL APM 项目知识索引

产品当前处于需求沟通阶段；先交付离线基线，再交付 SQL 检索和 Grafana 展示。
已确认细节完整保存在下列主题页；页面标为 active 不表示产品功能已经实现。
根据当前任务选择相关主题，跨域时沿条款链接补读，不要求每次阅读全部页面。

## 按任务选择主题

| 主题 | 何时阅读 |
| --- | --- |
| [项目范围、资料来源与交付顺序](decisions/project-scope.md) | 首次了解项目、确认范围、设计后续接入或核对原始资料时阅读。 |
| [运行环境、组件与资源边界](decisions/runtime-and-components.md) | 准备环境、选择依赖或讨论组件职责时阅读。 |
| [SQL 原文、明细与留存](contracts/sql-storage.md) | 设计 SQL 原文、执行／调用明细或历史版本存储时阅读。 |
| [HashData 日志事实与证据边界](contracts/log-evidence.md) | 调查日志格式、来源行号或解释现有样本覆盖时阅读。 |
| [日志导入、来源与异常处理](features/log-ingestion.md) | 修改导入、文件识别、批次完整性或导入诊断时阅读。 |
| [训练资格、黑名单与排除时段](contracts/training-eligibility.md) | 调整训练筛选、单条／整批单位、黑名单或排除时段时阅读。 |
| [计时分类、分组与时间归属](contracts/timing-and-grouping.md) | 实现日志计时识别、Execute 配对、分组或时间归属时阅读。 |
| [SQL 结构指纹与归一化](contracts/sql-fingerprints.md) | 实现指纹、函数字典或 SQL 输入检索匹配时阅读。 |
| [统计指标、训练窗口与样本门槛](contracts/baseline-statistics.md) | 实现聚合、统计公式、窗口或样本不足判断时阅读。 |
| [基线构建、版本与发布](features/baseline-versions.md) | 实现构建、规则更新、任务串行、失败重试或版本发布时阅读。 |
| [命令行与本地配置操作](features/operator-cli.md) | 设计导入、构建、状态或诊断命令及本地配置时阅读。 |
| [SQL 检索、Grafana 与历史展示](features/sql-search-and-views.md) | 实现 SQL 检索、基线／历史展示，或讨论后续 activity 参照时阅读。 |

## 流程、操作与来源

- [Python 源码布局与模块职责](architecture/source-layout.md)：新增模块、调整目录或检查依赖方向时阅读；区分目标布局与已实现目录。

- [知识维护方法](methods/knowledge-maintenance.md)：确认记录、按需阅读与单处维护。
- [Agent 开发流程](architecture/agent-development-harness.md)与[工程原则](decisions/engineering-principles.md)：协作及工程边界。
- [开发说明](../docs/runbooks/local-development.md)：环境准备与实际检查命令。
- [原始资料](raw/sql-baseline.md)：按字节保留的讨论输入；[来源和确认边界](decisions/project-scope.md#原始资料)。
- [知识日志](log.md)：历史确认与修订的追溯记录，不作为当前要求的替代。
- [知识规范](schema.md)：新增或调整知识实体时使用。
- [本次重组与完整性核对](../docs/reports/knowledge-reorganization-2026-09-25.md)：原章节的迁移位置和检查证据。
