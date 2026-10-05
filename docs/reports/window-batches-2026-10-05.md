# 文件实际时间与窗口选批验证

本报告对应 [Issue #35](https://github.com/shenxg13/sql-apm/issues/35)。基线为
`c5c123d5aab80d7a77a90d0ecccb129ba070f21b`；改动不删除历史数据，不修改训练判定、
统计公式或六项发布检查。结构版本为 1.8.0，逻辑契约保持 1.0.0。

## 实现与验证边界

来源读取器以原有时间解析函数聚合全部记录的第 0 列。文件成功事务保存
`first_log_at`／`last_log_at`；无有效时间为空，首次失败回滚，成功重复跳过保留首次值。
自动构建在完整批次中选择至少一个成功文件时间未知或最晚时间不早于窗口起点的批次。
不设上界，不使用声明日期，不拆分批次；无候选时退回原规则。显式快照选批不改变。

observed：先前构建封存全部完整批次，窗口外事件仍经过判定。
inferred：开始时间等于锚点时间减非负耗时，保留上述批次能覆盖窗口内执行；
保留未知时间是保守边界。日常读取量预期随窗口批次而非全部历史增长（modeled），
但多日首批、未知时间及无候选回退都会扩大输入。

verified：合成专项覆盖时间无效／无时区、全部无效、非执行记录极值、失败回滚、重复跳过；
覆盖旧批次、跨起点、正好起点、未来、未知时间、混合文件整批保留与失败批次。
两种构建进度均包含三项计数和回退标记，且无 SQL、源数据库名或执行用户。
无候选回退产生 `no_samples` 并保留当前版本；显式快照仍可封存旧批次。

## 迁移与冻结证据

1.7.0 DDL 按字节冻结，SHA-256 为
`a8e0e11223c9e07fc1729e841298e5fe11da73f990ca51dc504e071430c0dbc7`。
改动前已保存各版本 DDL 和已发布迁移脚本的 SHA-256，改动后逐项核对一致。
原始需求快照 Git blob 仍为 `d5b8c6e4ef8d317110aec7737d49088d4a9308e4`。

私有 PG17 已验证新建、空库 1.7.0／1.6.0／1.0.0 至 1.8.0、含导入与统计数据的
1.7.0 升级和同版本重跑。含数据用例逐表比较全部旧列，原版本回执及时间不变，
旧文件时间均为 NULL。新约束拒绝单侧 NULL 和逆序，允许相等时间。
1.6.0 及更早非空库仍拒绝进入 MPP 命名上下文；此前带数据迁移回归继续使用冻结 1.6.0 入口。

## 合成及常规检查

环境为 Python 3.9.5、PostgreSQL 17.10。数据库检查只创建关闭 TCP 的私有实例并自动清理。

| 检查 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 69 项通过 |
| `python -m unittest discover -s tests/ingestion -v` | 8 项通过 |
| `PYTHONPATH=var/parser-probe/site-packages python -m unittest discover -s tests/parser_probe -v` | 202 项通过 |
| `PYTHONPATH=tests python -m unittest discover -s tests/baseline -v` | 4 项通过 |
| `python scripts/db/verify.py` | 存储、历史迁移、1.8.0 迁移、重跑与清理通过 |
| `python scripts/db/verify_ingestion.py` | 125 项通过 |
| `python scripts/db/verify_training.py` | 300 项通过 |
| `python scripts/db/verify_publication.py` | 33 项通过 |
| `python scripts/db/verify_window.py` | 文件时间、整批选入、显式选批、回退和输出通过 |
| `python scripts/db/verify_mpp_naming.py` | 稳定来源键导出器检测正式／观察指标改动与精确恢复 |
| `PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh` | 完整离线 Harness 通过 |

表中 `python` 均为 `.venv/bin/python`。首次运行解析及统计专项未设置专用 `PYTHONPATH`，
分别缺少可选 SQLGlot 与测试 oracle；按既有说明设置后通过，没有改变依赖或降低检查。
沙箱内私有 PG 启动失败，转为经审批的沙箱外执行后通过，不涉及已有数据库。

## 真实日志对照方法

固定输入为 [55 文件清单](data/log-supplement-manifest-2026-09-28.json)，
实际只读 `raw/inbox/hashdata/` 下的原文件，不移动或重写输入。
验证先完整读取文件并核对 SHA-256，记录实际时间边界；候选入库值须逐文件相同。
基线和候选各用全新私有 PG17 执行 #29 九任务顺序：119 首批加三日批和一次重建，
120 首批加三日批。每次导出正式／观察分组成员及全部层、桶、指标和空值原因，
稳定成员键由文件摘要、记录号、物理行范围和单位组成，排除随机 ID 与构建时间。
逐项比较六项检查、发布结论和选入批次；119 第四次 `outside_window` 须为 55,808。

另为两版各建全新实例，把 119 按每天一批导入，以 2026-07-31 截止、7 天窗口重建。
候选未选入批次的全部文件最晚时间须早于起点；正式与观察结果、检查、发布结论相同。
状态、计数归属、原因摘要差值须逐项等于旧规则下未选入批次事件的判定计数。
两版均核对构建前后三张导入表行数不变；候选对未选入事件调用判定函数须返回零行。

```bash
.venv/bin/python scripts/db/verify_window_full.py \
  --app-root var/issue35/reference --logs raw/inbox/hashdata \
  --output var/issue35/baseline-run
.venv/bin/python scripts/db/verify_window_full.py \
  --app-root var/issue35/candidate --logs raw/inbox/hashdata \
  --output var/issue35/candidate-run --candidate
.venv/bin/python scripts/db/verify_window_full.py --compare \
  var/issue35/baseline-run/report.json var/issue35/candidate-run/report.json
```

全量结果正在生成，W5–W7 在报告补齐前不视为通过。原文、详细命令输出和私有连接留在忽略目录。
计时采用现有 `decisions_derived.seconds`（含构建登记、判定物化、摘要与分组登记）及
任务 `stage_seconds.build`，不把该进度值声称为单条 SQL 的独占执行时间，也不设置性能门槛。
本机测量不能推断 Kylin 性能；本 Issue 不要求 Kylin 重跑或重新生成离线包。
