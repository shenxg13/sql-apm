# SQL 分词性能与隔离计数验证（2026-10-04）

本报告对应 [Issue #34](https://github.com/shenxg13/sql-apm/issues/34)，
[机器记录](data/sql-scanning-2026-10-04.json)保存计数、耗时与摘要。冻结旧代码基线为
`b9078852a445d44d5ddea601f022200cf037f374`，运行代码候选为
`5c84dda5f96d5b4c8db5649fe1c71fb070c190de`。实现保持依赖、算法和结构版本；以下区分
代码依据、已执行的合成验证和仍需完成的真实验收，不能据局部检查宣称全量等价。

## 实现与等价边界

`sql_apm/sql/scanning.py` 将每个非 ASCII 字符替换为一个 `q` 后调用原扫描器，避免
pglast 7.18 对每个词重复查找多字节位置映射。SQL 词的内容、语法树和归一化继续用原文。
PostgreSQL 扫描器把高位字节视为标识符字母；替换保留 ASCII 分隔符、空白、注释和引用边界。
含替换字符的关键字还原为 `IDENT/NO_KEYWORD`，没有包含 `q` 的扫描前瞻关键字。

占位字母特意使用 `q`：`x` 会使 `中'abc'` 变成十六进制字符串，并使 `0中12` 变成数字；
`b`、`e`、`n`、`u` 等也可能生成特殊字符串前缀。非 ASCII 美元标签可能发生终止标签碰撞，
一律回退；非法编码、NUL 和扫描错误也回到原扫描器，保留异常及字符位置。美元标签预检查
即使匹配到注释内形态也保守回退；回退路径不承诺线性时间。

三处适配器扫描均通过新入口。`mpp-adapter/9`、`sql-normalization/5`、pglast 7.18 及
requirements 哈希、默认四解析进程、5 秒期限、结构 1.6.0 均保持不变。
普通路径的掩码、词跨度检查和位置处理为线性；全流程还包含原语法解析与归一化，不能
把分词路径的复杂度结论推广为整个 SQL 处理无其他成本。

解析池在即将报告超时时重新 `poll()`；同步重启期间已返回的结果正常接收。
已发送的输入仍最多重试一次，第二次失败才隔离。确定性模拟中，第二进程在自身期限前
返回，但第一进程的同步重启使最初就绪集合过期；冻结旧代码失败，新代码通过。

`status.current` 从当前 Build 的固定 input_file 查询两项隔离记录数，零值也返回整数。
后续文件在纳入新版本前不影响当前计数；没有当前版本时仍为 null，history 不增加字段。
展示不增加发布门槛；成功文件重导仍跳过，不重新解析历史超时记录。

## 合成验证

- `.venv/bin/python -m unittest discover -s tests -v`：66 项通过。
- `PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v`：
  检查原有解析用例及新分词／解析池回归。首次未设置可选 SQLGlot 依赖路径时，入口测试
  因候选解析器不可用失败；补齐已安装依赖路径后重跑，197 项全部通过。
- `.venv/bin/python scripts/db/verify_publication.py`：私有 PG17 验证发布与新计数，
  包括重复 SQL 的多记录计数、零值、其他集群、后续导入、发布继续通过及公开输出脱敏。
- `.venv/bin/python scripts/db/verify.py`：私有 PG17 验证当前结构及迁移，数据库结构未修改。
- 最小 1,000 条真实索引重放：分词和完整归一化结果全部相同；此结果仅为工具试运行，
  不替代 P1／P2 的全部 1,497,418 条验收。

## 全量验证方法与完成边界

原文索引为本地忽略的 `var/parser-probe/issue13/full-scan.sqlite`，55 文件日志位于
`raw/inbox/hashdata/`。原文保持只读；报告只输出计数、原因、摘要与耗时。
`scanning_equivalence` 逐条比较扫描词数／位置／名称／关键字类别和异常详情，并比较完整
归一化结果（含状态、原因、指纹、上下文、近似结果、结构及诊断）。冻结旧适配器与当前
归一化共用依赖的字节逐一核验，避免把不同环境误当算法差异。

```bash
.venv/bin/python -m sql_apm.diagnostics.scanning_equivalence \
  --index var/parser-probe/issue13/full-scan.sqlite \
  --reference var/issue34/reference --output var/issue34/equivalence --workers 4

.venv/bin/python scripts/db/verify_scanning_full.py \
  --app-root var/issue34/reference --logs raw/inbox/hashdata \
  --output var/issue34/alma-before

.venv/bin/python scripts/db/verify_scanning_full.py \
  --logs raw/inbox/hashdata --output var/issue34/alma-after
```

全量比较以固定块保存计数与差异定位，可在原文／代码摘要完全相同时恢复；参考实现不设
产品 5 秒期限，不能把旧实现超时当成等价。119 首批使用新的私有 PG17，按原 Alma 的
冻结业务计数核对，并记录各阶段实测耗时；比较期间避免并发的全量等价任务干扰性能。

### P4 八条样本的确认

原诊断只保留了长度，未保存八条原文 ID／摘要。用户于本轮明确确认使用已固定的
同长度段八条，并已[同步在线 P4](https://github.com/shenxg13/sql-apm/issues/34#issuecomment-5977015737)。
从 119 首批文件关联的 329 条 53,379–53,440 字节原文中，按（字节长度、原文字节）排序，
取 `round(n*328/7)`、n=0..7。每条 ID、摘要及字符量保存在
[固定选择集](data/sql-scanning-performance-selection-2026-10-04.json)。不宣称它们与历史八条相同。

```bash
.venv/bin/python -m sql_apm.diagnostics.scanning_benchmark \
  --index var/parser-probe/issue13/full-scan.sqlite --reference var/issue34/reference \
  --selection docs/reports/data/sql-scanning-performance-selection-2026-10-04.json \
  --output var/issue34/performance.json
```

同一进程按每条原文交替测量旧／新完整解析，重复三次，记录原始秒数并核对结构相同。

### 开发机首批实测

开发环境为 AlmaLinux 9.8 x86_64、Core Ultra 9 275HX、Python 3.9.5、pglast 7.18、
PostgreSQL 17.10。旧、新代码分别使用干净私有实例，按顺序运行；没有并发全量比较任务。
OS 文件缓存和外部负载未严格控制，此处是运行对比，不是硬件基准。

| 项目 | 冻结旧版 | 固定候选 |
| --- | ---: | ---: |
| 导入秒数 | 1,057.064 | 758.830 |
| 快照秒数 | 47.239 | 45.769 |
| 构建秒数 | 167.725 | 162.044 |
| 完整流程及查询秒数 | 1,274.365 | 968.954 |
| 导入文件／日志记录 | 26／5,124,686 | 26／5,124,686 |
| 正式分组 | 51,266 | 51,266 |
| 归一化超时 | 0 | 0 |

导入减少约 28.2%，总耗时减少约 24.0%；正式、观察统计各层、全部文件计数、六项发布
检查、成功尝试及版本链均与原 Alma 基准一致。新代码命令省略 `--workers`，实际默认四进程。
两次均通过 1.6.0 结构检查，实例已自动停止清理。P7 已满足。

P1／P2 全量比较、P4 八条测量、P8 Kylin 干净实例首批仍需最终实测记录。
Kylin 尚需本轮 SSH 交接；不能继承 #31 的单进程结果来替代本次四进程验收。
部署手册的并发建议将在本次 Kylin 结果确认后同步。完整 Harness 与在线 PR 契约在
最终交付面完成后运行，Issue 在全部验收前保持未完成。
