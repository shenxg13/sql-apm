# SQL APM 首次 GitHub 发布记录

## 已授权范围

2026-09-22，用户要求创建 GitHub 仓库并首次同步，明确选择公开可见性。

- 目标：[shenxg13/sql-apm](https://github.com/shenxg13/sql-apm)。
- 可见性：Public；默认分支：main；本地远程名：origin。
- 发布内容：完整已提交历史、开发模板、项目知识和原始 SQL Baseline 文档，
  以及首次发布所需的协作说明和验证记录。
- 许可证尚未选择，本次不新增 LICENSE。
- 业务需求、架构、技术栈、阈值和验收标准继续待讨论。

本次在已完成的[本地初始化验证](local-bootstrap-validation.md)基础上发布。
其范围属于初始化交接；接入后的新需求使用 GitHub Issue/PR 工作流。

## 发布前核对

- 本地 main 工作区干净，原有两次本地提交完整。
- GitHub CLI 确认登录账号为 shenxg13；CLI 与连接器均未发现同名目标仓库。
- 质量 CI 已配置 push 到 main 触发，调用同一本地离线检查入口。
- 已提交文件不包含本地质量工具安装目录和环境凭据文件。
- 发布前 `scripts/quality/check.sh` 全部 PASS：工具合同、Shell、Markdown、
  模板完整性及五组离线流程回归；`git diff --cached --check` PASS。

## 远程验证

首次推送提交：`419dbbd9553f00ddda26a07fe20c3377004d5ad7`。
该提交包含原有独立本地历史及本次发布说明。

| 核对项 | 已回读结果 |
| --- | --- |
| 仓库身份 | shenxg13/sql-apm |
| 可见性 | Public，private=false |
| 默认分支 | main |
| 模板设置 | is_template=false，普通业务仓库 |
| origin | `https://github.com/shenxg13/sql-apm.git` |
| 首次远程 main | 与首次推送提交一致 |
| 本地跟踪关系 | main 跟踪 origin/main |
| GitHub CLI | 仓库设置与工作流结果读取成功 |
| GitHub 连接器 | 仓库元数据、main 引用及固定提交的原文读取成功 |
| 原始需求文档 | Git blob SHA 保持 d5b8c6e4ef8d317110aec7737d49088d4a9308e4 |
| 首次 Harness quality | completed / success |

首次 CI：[Harness quality · 35685469466](https://github.com/shenxg13/sql-apm/actions/runs/35685469466)，
绑定上述首次推送提交。

六个流程状态标签已创建，并逐项核对名称与颜色：

| 标签 | 颜色 |
| --- | --- |
| status:triage | D4C5F9 |
| status:planned | 0E8A16 |
| status:in-progress | 1D76DB |
| status:blocked | B60205 |
| status:needs-review | FBCA04 |
| status:done | 0E8A16 |

本次补记更新发布证据与知识日志。最终同步提交以 Git 历史为准，
对应 CI 在交接时另行读取；上面的成功结果明确绑定首次推送提交。

## 证据边界

本报告记录首次发布及验证，不代表独立评审或 SQL APM 业务验收。
原始需求文档保持原样，其建议不因发布而成为已确认的产品契约。
