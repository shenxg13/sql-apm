# 日志导入 R2 整改验证（2026-09-29）

本报告对应 [PR #20 独立 R2](https://github.com/shenxg13/sql-apm/pull/20#issuecomment-5888010854)
的 R2-F001。实施方已完成整改及下列验证，交独立 R3 核验；本报告不构成独立评审结论。
整改基准为 `9f0bbbebb34d9746bde7bbb2e7b68c5ef8bcfdc8`，目标／merge-base 为
`021bedec746e3fc0cecdb510c957916dc5eb97d2`。Issue D1–D9 和业务范围未变。

## 整改与退出条件

R1 将所有工作进程失败上抛，导致低于大小上限的确定性问题 SQL 也使整个文件反复失败。
R2 按单条输入的发送边界分类：发送成功后发生超时、接收 EOF／OSError 或工作进程返回异常，
销毁进程并在新进程重试一次。第二次成功则正常入库；第二次仍失败则隔离该记录，
指纹状态为 normalization_failed、原因取第二次故障，持久化 fingerprint_normalization_* 问题。
事件保留真实 outcome、完整原文证据和 uncertain 状态，SQL 关联为空，不构造可靠或近似替代。
启动／发送故障仍向文件事务上抛，包含重试启动／发送失败；异常退出 map 时清理全部在途进程。

这是对 R1-F001 恢复策略的细化：保留“瞬时故障不永久降级”和“基础设施故障文件回滚”两个
不变量，依 R2 退出条件允许输入重试后成功或记录隔离。R1 中“超时／退出全部文件失败”的
旧表述由当前[设计](../design/log-ingestion.md#r1-恢复与解释版本边界)和
[操作说明](../runbooks/log-ingestion.md#状态重试与诊断)替代；历史报告保留原实测事实。

| R2-F001 退出条件 | 实测证据 |
| --- | --- |
| 持续失败按记录隔离，其他记录入库 | 50 条普通记录＋1 条真实 100k 项加法表达式（200,008 字节，无模拟故障、默认资源限制），文件 succeeded、批次 complete，51 条事件全部保留，50 条 SQL 可靠；失败项保留 success outcome、完整 200,008 字节证据及 fingerprint_normalization_worker_failed 问题。 |
| 一次性故障不永久降级 | 在首批 2,000 条 COPY 后，对第 2,001 条分别注入一次超时、真实进程退出、返回异常；均恰好启动两个不同 PID 的进程，第二次恢复，2,001 条事件全部可靠，无 fingerprint_normalization_* 问题。 |
| 重复运行故障有界隔离 | 上述三种故障持续出现时均仅尝试两次，前 2,000 条可靠，第 2,001 条以实际 success outcome 保留，问题 effect=isolate_record；热缓存中的结果仍为失败、无可靠 SQL 或近似关联。成功文件再次导入新增 0。 |
| 启动／发送失败回滚 | 在首批 2,000 条 COPY 后分别注入启动 EOF、发送断管，文件／尝试／批次 failed，问题码固定，已 COPY 事件、证据、Analysis 文件关联均回滚；恢复后 2,001 条可靠入库，再次新增 0。 |
| 判别方法和风险公开 | 设计与操作说明明确共两次尝试、第二次失败原因、负载误分类风险、失败缓存及成功文件重导跳过的边界。 |

## 同类排查与交互

- 遍历运行故障来源：工作进程异常返回、接收端 EOF／OSError、看门狗超时进入单输入重试；
  启动／发送异常通过同一 `_send` 路径上抛，map 清理其他在途回复。大小上限、结构拒绝及
  Normalizer 的其他明确记录级返回维持原行为，不增加重试。
- 解析专项验证两次超时的次数上限、失败后进程恢复，以及两进程并发处理问题输入和不同
  正常语句时输出顺序不变；正常结果与直接 Normalizer 逐项相等。启动／发送异常后的清理
  和下一次 map 无旧回复污染继续通过。
- R1-F002 成功成员保护、替换／别名收缩／读取变化、失败文件修复用例继续通过；
  R1-F003 来源解释版本隔离、三个版本漂移及旧 Analysis 与失败文件修复交互继续通过。
- SQL 归一化算法、字典、来源读取／计时配对、字段持久化映射、DDL、迁移和原始需求未改。
  生产代码仅修改 NormalizingPool 的调度及 SqlWriter 对最终失败结果的接收。

## 验证结果（measured）

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | 57 项通过 |
| `PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v` | 183 项通过 |
| `.venv/bin/python scripts/db/verify_ingestion.py` | 125 项通过，包含实际问题 SQL、八种运行故障情景、R1 成员／版本回归及迁移后导入 |
| `.venv/bin/python scripts/db/verify.py` | 86 项结构、56 项 FK 触发器、63 项迁移、18 项直接迁移通过 |

[脱敏证据 JSON](data/log-ingestion-r2-remediation-2026-09-29.json)记录源码／测试脚本摘要及回放计数。
完整 Harness、在线 PR 契约、远程 CI 和最终 SHA 由 PR 整改交接评论记录。
生产日志、临时数据库及详细输出仍在本地忽略目录；私有验证实例已停止并清理。

## 两份完整真实文件（measured）

复用 R1 按每集群最小文件选取的[固定清单](data/log-ingestion-r1-replay-manifest-2026-09-29.json)，
共 22,721,277 字节，不按 SQL 内容筛选。当前代码导入 51,005 条 CSV、23,744 条执行／调用，
两个批次 complete，重复导入新增记录和事件均为 0。逐文件 counts 所有键值与
[原 55 文件证据](data/log-ingestion-full-2026-09-29.json)相等；首次导入 9.159 秒。

```bash
.venv/bin/python scripts/db/verify_ingestion_full.py \
  --root raw/inbox/hashdata \
  --manifest docs/reports/data/log-ingestion-r1-replay-manifest-2026-09-29.json \
  --index var/parser-probe/issue13/full-scan.sqlite \
  --output var/ingestion/r2/replay
```

本轮未重跑 55 文件全量或更新全量性能实测。旧报告保持原测量版本和边界，两份完整文件只
核对普通成功路径，故障分支由上述合成数据回放覆盖。

## 成本与剩余边界

qualitative：正常解析不增加尝试；运行故障最多增加一次新进程启动和单输入解析，避免无限
重试或反复重放整文件。每次解析仍受 5 秒看门狗和 512 MiB 进程上限约束，启动握手另有
30 秒上限；并发范围仍为 1–8 个工作进程。此处不宣称全量性能改善。

两次失败是操作上的分类，不能证明 SQL 本身必然有错；两次系统负载超时也会记录级隔离。
最终失败可由本次 Importer 的 64 MiB 热缓存复用，不能缓存为可靠结果。首次失败原因不另行
持久化。已成功文件重导会跳过，无法借此重新归一化；显式重新解释／历史替换尚未交付。
生产部署、Kylin、训练／统计／发布以及既有跨文件 Execute 等限制仍在当前范围外。
