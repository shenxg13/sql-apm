# 构建编排 R1 整改验证（2026-10-02）

[Issue #29](https://github.com/shenxg13/sql-apm/issues/29)、[PR #30](https://github.com/shenxg13/sql-apm/pull/30)
的实施方整改证据，对应 [R1 完整台账](https://github.com/shenxg13/sql-apm/pull/30#issuecomment-5937040961)。
整改起点和首轮 head 均为 `6f484ebf1dfa2a400c8ebdca25a977b8dacdc8d6`；
目标与合并基点为 `4944c647d4e250961ba69c9b0fb2ffe5e704c1a4`。
本文不构成独立 R2 结论，已消耗的正式评审轮数仍为 1。

## 台账与退出条件

| 项目 | 修正 | 验证 |
| --- | --- | --- |
| R1-F001 | 两份手册改为 1.6.0；快照说明改为同集群任务锁、按规则和集群的缓存锁；修正一处存储版本、五处④交付状态及三个脚本／错误信息／测试标签 | 当前态有界检索、数据库 266 项和完整 Harness；冻结版本与历史报告不改 |
| R1-F002 | 选择（a）：当月分区必需，次月预建遇共享分组表锁忙时跳过；保存点回滚释放部分已取得的锁，后续构建再尝试 | 真实跨集群结果事务、首次当月等待与读取、释放后补建、两个分组表各自冲突用例 |
| R1-F003 | 仅 import／full 可首次登记集群；rebuild／snapshot／statistics 对未登记集群返回 unknown_cluster，不写 scope／task | 三个实际命令的反例、两个首次导入命令的正例、只读查询不写入；原有忙时拒绝用例继续通过 |

实现边界与锁成本由[编排设计](../design/build-publication.md#覆盖分区与成本)维护，
具体操作和首次当月可能等待的条件见[操作说明](../runbooks/build-publication.md#覆盖与分区)。
新增回归位于 `tests/database/publication_repair.py`，由 `verify_publication.py` 调用。

## 并发实测与同类检查

以下是私有 PostgreSQL 17 合成数据的 measured 证据，不作为生产耗时承诺。

- C2 真正运行 StatisticsStore.calculate，在结果已写入但事务未提交时等待输入。
  由 pg_locks 确认两个共享构建分组表均持有 ROW EXCLUSIVE 锁。
- 删除测试库内 C1 无构建引用、无统计行的次月叶分区，保留当月分区；随后实际执行 C1 rebuild。
  命令在 **0.272 秒**完成发布，C2 此时仍为 idle in transaction，次月登记尚不存在。
  该断言直接证明发布无需等 C2 结束；时间只是本次有界测量。
- 同时为已导入但从未构建的另一集群执行首次统计。pg_locks 确认它在共享正式分组表上
  等待 SHARE ROW EXCLUSIVE 锁；普通分组读取在两秒语句超时内完成。释放 C2 后首次构建完成。
- 再次执行 C1 rebuild，补建此前跳过的次月正式及观察分区，发布成功；最终 catalog 检查通过。
- 分别只持有正式或观察构建分组表的写锁，确认可选预建均跳过。
  尤其在第二把锁冲突时，另一个连接在调用者事务尚未提交前仍可 NOWAIT 取得两表写锁，
  证明保存点回滚释放了先取得的排他锁。
- 父表锁同样先以 ONLY／NOWAIT 按 ATTACH 顺序取得；分别持有任一父表的 DDL 锁，
  可选预建都直接跳过，未先持有引用表锁去等待父表，避免与首次必需建表的锁顺序反转。

同类扫查覆盖所有 Task 入口：full／import 保留首次登记，三个不经导入的入口先查询已登记集群，
status／history 保持只读；同集群 busy_rejected 和其他集群入场用例继续通过。
检索当前知识、手册、设计、脚本与测试中的 1.5.0、旧错误码、④待交付及覆盖表表述；
保留明确描述迁移路径、冻结 DDL、历史确认及旧报告的文字。

## 验证结果与证据边界

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | 66 项通过 |
| `.venv/bin/python scripts/db/verify_publication.py` | 31 项通过（原 24 项及新增 7 项） |
| `.venv/bin/python scripts/db/verify_training.py` | 300 项通过 |
| `.venv/bin/python scripts/db/verify_statistics.py` | 32 项通过 |
| `.venv/bin/python scripts/db/verify_ingestion.py` | 125 项通过 |
| `.venv/bin/python scripts/db/verify_observations.py` | 11 项通过 |
| `.venv/bin/python scripts/db/verify.py` | 266 项通过；新建、连续迁移、重跑及 catalog 检查 |

完整 Harness、在线 PR 契约与最终提交 CI 的结果在 PR 交接中记录。
本轮未改统计公式、判定、发布检查、DDL 对象定义及迁移行为；migrate.sql 只改错误文字。
未重新导入 55 文件；原[九任务实测](build-publication-2026-10-01.md)和 R1 独立全量复验
继续作为原固定 head 的证据，不把其资源数值标为整改后实测。本次变更采用上述有界并发、
命令级和全部相关数据库专项验证；原始需求、rules/、冻结版本和旧报告保持字节不变。

统计专项首跑因旧的跨集群用例没有登记对照集群而在新的 unknown_cluster 检查提前退出。
现已显式登记对照集群，保留原“已登记的另一集群不能引用本集群快照”的拒绝断言；
未登记集群的行为由新命令级用例覆盖。补跑全部通过，失败日志保留在本地忽略目录。
所有私有实例均已停止并清理。

## 已确认观察与后续边界

[用户 O1／O2 确认](https://github.com/shenxg13/sql-apm/issues/29#issuecomment-5937111169)
已写入[版本正文](../../.project-wiki/features/baseline-versions.md#发布输入与补导确认2026-10-02)、
导入／命令主题及知识日志：失败或冲突只阻止当批，后续发布只使用已完成批次，不额外提示缺天；
较早批次 full 可使窗口倒退，补导时由操作者显式传入截止日。当前 Issue 正文不变，确认来源单独互链。

O3 仅澄清表述：数值重验依赖 catalog 中仍存在的 CHECK 定义，独立于其是否已 VALIDATE；
未新增发布检查。O4 留存清理和 O5 验收增强仍为原评审中的非阻塞观察，本轮未扩展实现。
