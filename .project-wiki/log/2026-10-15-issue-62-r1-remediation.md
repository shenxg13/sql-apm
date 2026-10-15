# sqlfind R1 输出与验收记录整改

[R1 台账 F-62-001](https://github.com/shenxg13/sql-apm/pull/64#issuecomment-6184300991)要求检索说明保持函数原文，
维护者另[纳入 O-62-002、O-62-003、O-62-004](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6184745033)。
原文控制字符显示经[范围确认](https://github.com/shenxg13/sql-apm/issues/62#issuecomment-6184734459)写入 C37，
保留 TAB、LF 与 CR LF，其他 C0／DEL／C1 可见转义并计数，不改库内原文和编号；断管退出 1 且无英文报错。

[命令行主题](../features/operator-cli.md#sqlfind-运维入口2026-10-13)维护显示规则及 C28 已确认事实，
[检索主题](../features/sql-search-and-views.md#sqlfind-候选与详情摘要2026-10-13)引用相同规则；
试用步骤同步账号配置与入口迁移现状，报告保留早期时点记录并补充用户确认。
O-62-001、O-62-005、O-62-006 按维护者决定不在本轮改动，后续发版性能验证不视为已完成。
