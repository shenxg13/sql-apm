# 日志导入 R1 整改验证（2026-09-29）

本报告对应 [PR #20 的独立 R1](https://github.com/shenxg13/sql-apm/pull/20#issuecomment-5887465880)
三个阻断项。实施方已完成整改及下列验证，退出条件仍交独立 R2 核验；不构成独立评审结论。
R1 基准为 `425ae2d894764635d93e824bfbb069818b239ec8`，目标／merge-base 为
`021bedec746e3fc0cecdb510c957916dc5eb97d2`。当前 D1–D9 契约未变。

## 退出条件与实现

| 问题 | 整改 | 实际验证 |
| --- | --- | --- |
| R1-F001（P2） | 采用评审给出的方案 b：工作进程超时／退出使文件失败回滚，启动失败采用同一恢复边界；不提交降级事件、不缓存工作进程失败结果。 | 在首批 2,000 条已 COPY 后，对第 2,001 条分别制造真实子进程超时、异常退出、启动 EOF。三次文件／尝试／批次均失败，问题码准确，证据／事件／Analysis 文件引用全部回滚；恢复后每次产生 2,001 条可靠事件，再次导入为 0。 |
| R1-F002（P2） | 将已有摘要扫描移至写入前，保护最终尝试为 succeeded／duplicate_skipped 的成员。成功成员消失则暂停本次批次，保留旧条目和最终尝试，以 block_publication 问题记录新旧 file_id。 | 覆盖成功路径换新内容、换成其他批次已导入内容、旧成员最初为 duplicate_skipped 三种情况；均 conflict、旧成员／尝试不变、无新增事件，重复尝试仍冲突，恢复原内容后可重复跳过。 |
| R1-F003（P3） | 来源适配独立声明 hashdata-csv-reader/1；Analysis 复用前比较来源／profile 及 mapping、parser、association 版本，差异固定返回 analysis_version_mismatch。 | 三个版本列逐项漂移均被拒绝，旧值不改、任务不新增；另验证失败文件修复后仍不能进入旧实验版本 Analysis，恢复匹配版本后可正常重试。SQL 解析版本仍为 Normalization 的 mpp-adapter/9。 |

具体行为和恢复步骤由[设计](../design/log-ingestion.md#r1-恢复与解释版本边界)及
[操作说明](../runbooks/log-ingestion.md#状态重试与诊断)维护。

## 同类排查与整改交互

- F001：启动握手 EOF／启动异常和发送端管道失败均转为固定原因码。map 中途抛出异常时
  关闭全部子进程，避免其他在途回复被下一文件错误复用；专项测试让第二个工作进程启动失败，
  验证第一个进程也被清理，之后新输入的指纹与独立 Normalizer 一致。另覆盖发送端断管后的恢复。
- F002：保留成功成员的保护贯穿预检、失败尝试和清单收尾，不仅修改最终 DELETE。
  覆盖两个成功成员收缩为同一内容（没有新 file_id）的变体。预检后文件发生普通属性变化时
  文件失败，并保留旧成功 final_attempt_id；下一次重试仍能识别成员替换。
- 失败文件修复、不可读占位补齐、仍保留旧成功内容的 ALIASES 用例均继续通过。
  清单按内容集合去重，不把合法别名强制改成一条路径一份事件。
- F003：拒绝发生在新任务／尝试或事件写入前；覆盖“旧 Analysis＋已修复失败输入”的交互。
  本入口不自动改写历史版本或生成 supersedes。恢复测试中对版本字段的改写仅为合成故障注入，
  不是操作员修复历史数据的步骤。
- 归一化语义、SQL 解析器、字典、五类解释和持久化字段映射均未更改。
  reader 中的函数／类 AST 与 R1 基准一致，仅新增来源解析版本常量；DDL、迁移和原始需求未改。
  Normalizer 的其他明确记录级返回保持原行为，成功文件重导仍不会重新归一化。

## 验证结果（measured）

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | 57 项通过 |
| `PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v` | 182 项通过 |
| `.venv/bin/python scripts/db/verify_ingestion.py` | 89 项通过（原 49 项及新增 40 项）；包含实际进程故障和真实连续迁移后导入 |
| `.venv/bin/python scripts/db/verify.py` | 86 项结构、56 项 FK 触发器、63 项迁移、18 项直接迁移通过 |

[脱敏证据 JSON](data/log-ingestion-r1-remediation-2026-09-29.json)保存源码／验证脚本摘要、
测试数量及真实回放计数。完整 Harness、在线 PR 契约和远程 CI 的固定提交结果见 PR 整改交接评论。
生产日志、临时实例及详细本地输出继续留在忽略目录，验证实例均已停止并删除。

## 两个完整真实文件回放（measured）

从原 55 文件清单按字节数选择每集群最小的完整文件；不是按 SQL 内容筛选或抽取局部行。
[选择清单](data/log-ingestion-r1-replay-manifest-2026-09-29.json)保存原清单摘要和两个文件的字节数／SHA-256。
共 22,721,277 字节，51,005 条 CSV、23,744 条执行／调用。
原生导入工具在私有 PG17 上处理完整文件，两个批次都 complete；再次导入新增记录／事件均为 0。
按 file_id 对比[原全量报告](data/log-ingestion-full-2026-09-29.json)的每个文件 counts 字典，
全部键值相等，包括 duration 来源、计时、状态、指纹及其他问题计数。

```bash
.venv/bin/python scripts/db/verify_ingestion_full.py \
  --root raw/inbox/hashdata \
  --manifest docs/reports/data/log-ingestion-r1-replay-manifest-2026-09-29.json \
  --index var/parser-probe/issue13/full-scan.sqlite \
  --output var/ingestion/r1/replay
```

实际调用使用内容相同的本地选择清单。首次导入 9.287 秒；这是两文件回放时间，不能与
55 文件性能指标直接比较。本轮没有重跑全部 55 文件或重新测量全量资源；原始全量证据保留不改。
整改主要影响故障、成员变更与版本准入；有界真实回放验证普通成功路径，故障分支由合成回放覆盖。

## 成本与剩余边界

成员预检复用原有 SHA-256 扫描，不额外读取一遍文件；保存 O(文件数) 的摘要和属性，
开始归一化前先完成批次摘要阶段。使用前的属性核对针对文件普通变化，不以恶意伪造文件元数据为威胁模型。
文件失败仍需重放整文件；工作进程持续无法满足既有资源限制时重试可能再次失败。
结构拒绝及近似隔离、Sync 成功证据限制、跨文件 Execute、边缘重叠覆盖和未交付的训练／统计／发布保持原边界。
