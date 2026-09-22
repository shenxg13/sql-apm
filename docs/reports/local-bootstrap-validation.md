# SQL APM 本地初始化验证记录

## 范围

本报告对应[本地初始化计划](../../.harness/plans/local-bootstrap.md)，
记录本项目实际检查，不继承上游模板的验证结论。

2026-09-22，工作区检查已完成。业务需求、运行模型、业务架构和产品验收标准仍待讨论。

## 已核对证据

- 固定模板提交为 `859c39d1697241579b9bde443125477f88b1827c`。
- 60 个上游文件已核对 Git blob；导入 59 个文件，保留并合并已有 Wiki 索引。
- 原始 SQL Baseline 文档 Git blob 保持
  `d5b8c6e4ef8d317110aec7737d49088d4a9308e4`。
- 所需质量工具按合同版本安装，工具检查已通过。
- 原文表格分隔符触发 MD060 排版检查；为保留原始字节，仅对该快照排除排版检查。

## 工作区检查结果

| 命令或核对 | 结果 |
| --- | --- |
| `scripts/quality/check.sh --check-tools-only` | PASS：最低及精确工具版本均满足合同 |
| `scripts/quality/check.sh` | PASS：Bash、ShellCheck、shfmt、Markdown、模板完整性及五组流程回归 |
| `git diff --cached --check` | PASS：暂存内容无空白错误 |
| 原文 Git blob SHA | PASS：与固定来源一致 |
| 模板脚本和 GitHub 文件 | PASS：20 个文件的内容和权限与固定模板一致 |
| 暂存文件和忽略目录 | PASS：64 个文件，工具安装目录未进入 Git |
| 分支和远程 | PASS：main 分支，未配置 remote |

Markdown 排版检查覆盖 43 份维护文档；原文快照单独核对内容。
模板检查覆盖 64 个跟踪文件及 2 份通用知识实体。

## 工具环境

| 工具 | 实际版本 |
| --- | --- |
| Bash | 5.1.8 |
| Git | 2.52.0 |
| jq | 1.6 |
| GNU coreutils | 8.32 |
| ShellCheck | 0.10.0 |
| shfmt | 3.13.1 |
| Node.js | 24.19.0 |
| npm | 11.17.0 |
| markdownlint-cli2 | 0.23.2 |

缺少的工具安装于 `var/harness-tools`，该目录由 Git 忽略。
ShellCheck 和 shfmt 下载摘要与模板工具合同一致。
Node.js 官方 Linux x64 压缩包摘要与同版本官方 SHASUMS256.txt 一致：

```text
14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647
```

## 首次提交后的核对

首次提交后将执行独立本地克隆，回读提交和原文摘要，并记录干净检出检查结果。

## 证据边界

本报告记录实施自检。独立评审、业务验证、真实 GitHub 操作和远程 CI 尚未执行。
