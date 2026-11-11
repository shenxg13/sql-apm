# 带历史导入的查询派生增量维护

本报告对应 [#82](https://github.com/shenxg13/sql-apm/issues/82)。开发结构 1.15.0 只替换维护函数；
不新增持久表、列或索引，不改写迁移数据。生产升级和 v1.3.1 发布另行交付。

## 复现与原因（measured）

[修复前结论](https://github.com/shenxg13/sql-apm/issues/82#issuecomment-6788620393)已在修改产品代码前发布。
冻结旧程序为 `bd46786ddf35a7447934c01c79a604b022c04a31`，结构 1.14.0。
仅复制已停止的开发库，原目录未启动；副本位于本地忽略目录 `var/issue82/reproduce/`。

历史为 7,279,390 条窄记录、7,434,804 条事件，无需扩充。
新日期合成文件含 10,000 条事件／新原文，触发五次原文元数据提交及一次文件提交。
输入 SHA-256 为 `170aecb5b33899b8a5e51a9dac4720b299069238385c6cc2efa518a59a136768`。

| 计数 | 导入前 | 导入后 | 增量 |
| --- | ---: | ---: | ---: |
| mpp_query_record seq_scan | 19 | 25 | 6 |
| mpp_query_record seq_tup_read | 50,955,730 | 87,352,681 | 36,396,951 |
| mpp_query_pending 插入／删除 | 0／0 | 6／6 | 6／6 |
| mpp_occurrence seq_scan | 287 | 287 | 0 |

实际 auto_explain 启用 nested、analyze、buffers，关闭逐算子 timing，阈值 10ms。
`mpp_query_refresh` 的 `query_old_records` UNION 第二支选择：

```text
Hash Join: actual rows=0, loops=1
  Hash Cond: r.rule_key=c.rule_key AND r.sql_key=k.sql_key
  Seq Scan on mpp_query_record: actual rows=7,279,390, loops=1
    Shared Hit Blocks=0; Shared Read Blocks=161,746
  Hash
    Nested Loop: estimated rows=2,835; actual rows=2,000
      Seq Scan on changed_fingerprint
      Index Scan on text_key using sql_id
```

五个元数据提交都读取全部历史并返回零行；第一支由空 changed_event 驱动，历史探测 loops=0。
除五次全扫外，计数还多一次扫描／一行读取，与文件提交时空变化集启动扫描后短路一致。
该次 `query_old_records` 低于 10ms 采集阈值，历史库日志未单独记录其计划；
小库回归另取到了文件提交路径“Seq Scan actual rows=1”的嵌套计划。
逻辑上的变化键连接可被优化器重排为历史表驱动，临时表估算不能表达新原文尚无事件。
本次 17.586 秒包含计划采集，只用于定位。

证据为 `context.json`、`before.json`、`after.json`、`server.log`、`plans.json` 和 `plan-summary.jsonl`。
原始日志、SQL 和完整计划仅本地保留。

## 修复与边界

两个维护函数用 LATERAL／OFFSET 0 保持变化键驱动的参数化历史探测；
成员、窄记录、身份及汇总删除使用完整键，结构计数更新也只操作受影响结构。
身份的四种 NULL 组合分别探测已有唯一索引，避免只按结构／集群前缀读取其他身份。
删除边界需要重算最早／最晚时，只读受影响原文或身份的已有记录。

原文元数据事务先取全局锁；文件事务仍在最后一次 SqlWriter 调用后、延迟维护时取锁。
不提前锁住文件事务，不修改 PostgreSQL 全局参数。显式 TRUNCATE 的全量重建语义保留。

冻结 1.14.0 DDL。1.14.0→1.15.0 迁移只含两个 CREATE OR REPLACE FUNCTION；
旧 1.13.0→1.14.0 相对 schema 引用绑定冻结目标，旧迁移字节不变。
导入连接门禁同步到 1.15.0；发布升级入口仍由后续发版任务处理。

## 验收方法

`verify_query_refresh.py` 用 30,000 条历史事件和 5,000 份历史原文，分别执行冻结旧函数与当前函数。
元数据、文件两条路径按嵌套计划断言历史顺序扫描，而非耗时；旧函数必须出现，候选必须没有。
每次还用独立 SQL 重算派生结果。小库能区分两版，是因为即使空指纹变化集使扫描读一行即结束，
实际计划中仍有执行过的 Seq Scan；不需要等待历史达到生产规模。
迁移用例比较所有业务行摘要以及 xmin／ctid，覆盖“值未变但被改写”的情况。

全量验证入口为 `scripts/db/verify_query_refresh_full.py`；所有阶段串行，计时阶段不采集计划。
基线程序固定为 `32f6758d682936b9530848e17431e7d08f156db3`（1.13.0），
修复前程序固定为上述 `bd46786`。同源历史副本中导入同一新日期文件。
输入保留原始四文件摘要，在副本中移动日期并为 SQL 添加统一普通注释，保留记录类型和重复分布，
使新日期包含未导入的原文；这是明确变换的开发重放，不冒充新的生产日志。
验收包装器使用相同 UUID 种子，使代表原文的 min(sql_id) 选择可重复；产品随机 ID 实现不变。
派生结果导出仍排除全部 bigint 内部键，用完整业务字段生成每行 SHA-256，排序后逐行比较，保留重复行的重数。

## 实测证据

三种代码使用 PG 17.10、Python 3.13.16，四个归一化 worker，512 MiB shared_buffers、16 MiB work_mem。
每个计时实例单独启动、单独停止；计时不并发其他验收，不启用 auto_explain，不清理系统页缓存。
这是一次配对的整批 CLI 耗时，不声称是冷缓存分位数或生产机器实测。
候选计时前做过升级及独立重算，各实例的系统缓存温度未统一；结果保留这一测量限制。

同批输入共 1,598,215 条 CSV 记录、705,998 条事件；四份原文件摘要与变换后摘要见
`var/issue82/real-input/provenance.json`。各版本实例、命令、时间和前后计数在 `var/issue82/paired/`。
按全部 CSV 记录作分母，结果如下；相同输入也使“每条记录”比例等于整批耗时比例。

| 代码／结构 | 完整导入秒 | ms／CSV 记录 | 相对 1.13.0 | 窄记录 seq_scan／seq_tup_read 增量 |
| --- | ---: | ---: | ---: | ---: |
| 1.13.0 | 358.921768 | 0.224577 | 基线 | 无派生表 |
| 修复前 1.14.0 | 779.871644 | 0.487964 | +117.28% | 831／6,202,875,191 |
| 修复后 1.15.0 | 429.235725 | 0.268572 | **+19.59%** | **0／0** |

修复后比修复前减少 **44.96%**；本次配对达到 I5 的 +20% 目标，余量仅约 1.47 秒，
不能据单次结果声称跨机器、缓存或输入分布都稳定低于阈值。
旧版累计 828 次维护，窄记录 seq_scan +831、seq_tup_read +6,202,875,191，原事件 seq_scan +3。
除元数据批次的扫描外，大文件提交的事件分支也发生全扫；基线原事件 seq_scan 增量为 0。
修复后同为 828 次维护，原事件 seq_scan 也为 0，新增窄记录 691,417 条。
逐文件提交前时间之和依次为 357.948909、743.695882、412.626107 秒；
与完整导入相差 0.972859、36.175762、16.609618 秒。此差额还含启动等开销，不能全归为触发器时间。

带 7,279,390 条窄记录的 1.14.0 库升级耗时 **0.624439 秒**，结构检查通过。
239 个表／分区／索引的 relfilenode 和大小均未变化；迁移后的八类独立差分全部为 0。
小库同时验证全部业务行的值及 xmin／ctid 没有变化，旧回执保留、重跑通过。
本次迁移检查耗时单列，不能把独立全量重算时间算成升级时间。

### 派生结果逐行比较

配对导入后、后续诊断导入前，九类自然行全部相等：

| 数据 | 两侧行数 |
| --- | ---: |
| text_keys／memberships（各） | 1,675,085 |
| structures | 743,219 |
| identities | 995,255 |
| records | 7,970,807 |
| identity_totals | 1,238,240 |
| text_totals | 1,675,718 |
| state／pending | 1／0 |

完整排序行摘要流和逐行比较结果保留在 `paired/{before,after}/natural-*.rows.gz`、
`natural-exports.json` 及 `paired/before/natural-comparison.json`。
窄记录摘要流 SHA-256 为 `d32baa475127c68e631a5bc338509823925bd29d2356b2f9626fc18664a85cdb`。

### 同输入嵌套计划与读取块数

修复后再次导入 I1 的同一 10,000 条合成新日期输入，五次元数据提交加一次文件提交。
使用的历史副本已完成配对导入，故此时为 7,970,807 条窄记录／8,140,802 条事件，
略大于 I1 的 7,279,390／7,434,804；没有通过缩小历史获得结果。
`mpp_query_pending` 插入／删除均 +6，窄记录与原事件的 seq_scan／seq_tup_read 均 **+0／+0**。

修复后的 auto_explain 阈值为 0，收集所有 nested/analyze/buffers 计划。两条路径涉及的七张历史派生表
及 `mpp_occurrence` 均无实际执行的 Seq Scan；顺序读取限于临时变化集、单行状态或 pending 表。
元数据提交对 `mpp_query_record_text_idx` 每批探测 2,000 次，各返回 0 行；
文件提交对 `mpp_query_record_key_idx` 探测 10,000 次，各返回 0 行，对原事件完整键探测 10,000 次，各返回 1 行。
指纹路径对原事件用 `sql_id` 的参数化 bitmap/index 探测，只访问该原文关联事件。

下面的“块访问”是 Shared Hit + Shared Read 的累计次数，包含重复命中，不是唯一块数。
只比较同一个语句根节点或历史表算子，避免把父子节点相加造成重复计量。

| 同输入路径／计量位置 | 修复前块访问 | 修复后块访问 | 修复前／后 Shared Read |
| --- | ---: | ---: | ---: |
| 五次元数据 `query_old_records` 语句根节点合计 | 848,744 | 90,014 | 808,474／4 |
| 上述语句中的窄记录表算子合计 | 808,730 | 40,000 | 808,474／4 |
| 文件 `query_source_events` 根节点 | 140,007 | 150,007 | 4／6 |
| 上述语句中的原事件表算子 | 60,000 | 60,000 | 1／2 |

窄记录表的元数据读取块访问减少 **95.05%**。文件源事件路径仍随 10,000 个变化键增长；
完整键约束带来额外字典探测，所以此处总块访问略增，不能声称所有语句块数都下降。
修复后文件 `query_old_records` 根节点为 40,004 次块访问、4 次 Shared Read；
旧版该语句未过采集阈值，不编造同项块数。Shared Read 还受缓存影响，判断消除全扫以实际计划及计数为准。
详细计数、嵌套计划和比较保存在 `paired/after/plans-*`、`plan-comparison.json`；诊断耗时不进入 I5。

### 独立重算及升级后续流程

`query_records_consistency.sql` 的八类差分在迁移后、配对新日期导入后、生成指纹后均为 **0**，
分别见 `paired/after/{upgrade,after-import,lifecycle}-consistency.json`。
生成指纹验证先删除新合成集群中 16 份尚未进入训练缓存的指纹，再执行真实 training snapshot，
核对重建指纹的全部业务字段与原值一致；该命令同时为新集群建立 10,000 份训练缓存。

随后真实 rebuild 完成快照、五层结果、六项检查及发布。清理验证把这个集群的完整构建复制到旧月份，
实际释放旧结果分区 90,112 字节及对应排行索引，当前版本指针、当前构建全部结果摘要和查询 revision 均不变。
这是在历史库上执行的有界合成清理，未重建或清理其他历史集群；详细证据为 `paired/after/lifecycle-*`。
所有私有实例在验收后停止；本地保留输入、摘要、计划和数据库副本，交接时磁盘可用约 185 GiB，
满足本轮用户要求的至少 20 GiB 余量。

## 生产规模模型（modeled）

模型只把历史扩到约 36,330,000 条窄记录／37,060,000 条事件，保持上述输入、828 次维护、
机器、输入处理及新增写入成本不变，用于说明规模效应；不是生产墙钟预测或生产验收。
旧版实测顺序读取 6,202,875,191 行／831 次，相当于平均每次 7,464,350 行。
相同扫描次数在生产规模约读取 **30,190,230,000 行**，约为开发实测 **4.867 倍**。
修复后的顺序扫描项为 0；参数化索引探测次数由变化键及其已有记录决定，索引深度和缓存命中仍可能影响耗时。

令 B=358.922、F=429.236、O=779.872 秒。用 F−B 作为新增维护总成本，
O−F 近似被修复的历史扫描成本；这两项还混有计划差异、写入和缓存噪声，不能解释为精确算子计时。
固定索引探测单位成本时，修复后模型仍约 **429 秒（+19.6%）**；
把 F−B 全部按 `ln(36,330,000)/ln(7,279,390)=1.102` 放大作敏感性估计，
则为 **436 秒（+21.6%）**。这不是置信区间，也不保证真实 B-tree 高度按连续对数变化。
再将 O−F 按 4.867 放大，旧版约 **2,136–2,143 秒**，为基线约 **5.95–5.97 倍**。

该粗模型与生产“历史越大，每个元数据批越慢”的方向一致；不能用它声称生产已回到 +20%。
实际生产日志的新原文比例、每天文件数量、缓存与硬件不同，需按下述交接实测校准。

## 回归与复跑入口

开发结构 SHA-256：`464a6515509fa2a3149b49f1bbe605950b7496dd6ec037776dd1783e375bf71f`。
本次执行的验收均使用仓库 `.venv/bin/python` 和 `/usr/pgsql-17/bin`：

| 入口 | 结果／覆盖 |
| --- | --- |
| `scripts/quality/project_checks.sh` | 158 项测试及函数覆盖检查通过 |
| `scripts/db/verify.py` | 结构、约束、连续迁移及带数据迁移通过 |
| `scripts/db/verify_query_refresh.py --output FILE` | 两处提交的旧版扫描／新版键探测及无改写迁移通过 |
| 同上加 `--candidate-schema sql_apm/storage/versions/1.14.0.sql` | 预期失败，捕获实际历史 Seq Scan；证明回归能拒绝旧代码 |
| `scripts/db/verify_query_records.py` | NULL 四组合、更新／删除／回滚、指纹、TRUNCATE、提交等待顺序及带数据升级通过 |
| `scripts/db/verify_ingestion.py` | 125 项通过 |
| `scripts/db/verify_query_performance.py --concurrency-only` | 导入与训练、训练与训练并发结果等同串行 |
| `scripts/db/verify_publication.py` | 33 项通过 |
| `scripts/db/verify_cleanup.py` | 23 项通过，含中断／锁与清理恢复 |
| `scripts/deployment/check_documents.py` | 文档与结构契约通过 |
| `scripts/quality/check.sh` | 完整离线 Harness 通过；交接另运行 `--pr` 在线契约检查 |

旧版本 DDL／迁移与 `bd46786` 逐字节一致，冻结 1.14.0 与该提交的 schema 字节一致。
测试日志在 `var/issue82/verify-*.log`、`project-check.log`、`harness-*.log`；负例失败日志单独保留。

完整历史验收采用以下命令形态；各阶段使用新的 `--stage`，不覆盖已有证据。
调用方先复制已经停止的历史实例到 `DIRECTORY/pgdata`，不能指向原证据实例。

```sh
.venv/bin/python scripts/db/verify_query_refresh_full.py import \
  --directory DIRECTORY --app APP --stage paired \
  --config var/issue82/real-input/import.json --batch issue82-paired --seed 82
.venv/bin/python scripts/db/verify_query_refresh_full.py upgrade \
  --directory DIRECTORY --app . --stage upgrade
.venv/bin/python scripts/db/verify_query_refresh_full.py exports \
  --directory DIRECTORY --app APP --stage natural
.venv/bin/python scripts/db/verify_query_refresh_full.py compare \
  --directory BEFORE --other AFTER --app . --stage natural
.venv/bin/python scripts/db/verify_query_refresh_full.py consistency \
  --directory AFTER --app . --stage after-import
.venv/bin/python scripts/db/verify_query_refresh_full.py import \
  --directory AFTER --app . --stage plans \
  --config var/issue82/input/import.json --batch issue82-new-day --plans
.venv/bin/python scripts/db/verify_query_refresh_full.py lifecycle \
  --directory AFTER --app . --stage lifecycle \
  --config var/issue82/input/import.json --batch issue82-new-day
```

1.13.0／1.14.0 使用各自冻结 APP；候选先 upgrade 再 import。所有计时 import 结束后才做
exports、compare、consistency、plans、lifecycle。诊断角色 GUC 在 finally 恢复，所有实例在 finally 停止。

## 生产核对交接

后续 v1.3.1 发版任务升级后、每日运行之前和之后各取一次计数及运行明细；本 Issue 不连接生产。
可沿用本地 `import-slowdown-2026-11-11/collect_after_run.sql` 的字段，调整运行时间筛选：

- 两张表：`mpp_query_record`、`mpp_query_pending` 的 seq_scan、seq_tup_read、n_tup_ins、n_live_tup；
  同时记录采集时间及数据库统计重置时间。两次之间不能重置统计。
- 每日集群 import 总耗时、逐天 seconds／added_records／file_count／byte_count，
  逐文件 started_at、finished_at、reliable_record_count、字节数；保留提交前计时与整天耗时的差额。
- `finished_at` 写在文件事务提交前，不包含延迟维护；应同时对照每天完整耗时，不能只看逐文件差值。

判据为窄记录 seq_scan 增量为 **0**，每条记录导入耗时回到 v1.2.0 附近。
按 119／120／163 分开比较，报告输入规模、原文新颖度及是否存在并发查询等影响；
扫描异常时保留计数和时间边界再定位，不把未验证结果写成通过。

Issue #73 的 **+18.3% 只适用于当时空库四文件测量**。生产约 3,633 万窄记录、3,706 万事件下的代价
属于 modeled，必须由后续发版的上述实测校准。v1.3.0 发布说明和升级手册的预期更正、
Issue #79 的七天／一天窗口补测及 O-79-004 文档定稿都交给后续 v1.3.1 发版 Issue。
