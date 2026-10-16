# v1.1.0 用户验收确认

依据 [B12 人工确认](https://github.com/shenxg13/sql-apm/issues/67#issuecomment-6219052514)，更新
[运行环境](../decisions/runtime-and-components.md)、[命令行交付](../features/operator-cli.md)和
[日志接入](../features/log-ingestion.md)：用户已完成本轮 PuTTY、手册及真实平台界面检查，明确确认无问题。
人工确认与自动验证分别保存在[目标机报告](../../docs/reports/v110-target-validation-2026-10-16.md)。

两份手册补充 pushDays 的既有日期筛选语义，解释首次少一两天是避开源端清理窗口的保守建议。
保留原首次操作建议及日常默认 7 天，不改变产品行为、验收范围或 Harness 流程。
独立评审、合并后检查和正式发布仍待完成。
