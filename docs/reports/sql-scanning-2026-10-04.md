# SQL 分词性能与隔离计数验证（2026-10-04）

本报告对应 [Issue #34](https://github.com/shenxg13/sql-apm/issues/34)，
[机器记录](data/sql-scanning-2026-10-04.json)保存计数、耗时与摘要。冻结旧代码基线为
`b9078852a445d44d5ddea601f022200cf037f374`，运行代码候选为
`5c84dda5f96d5b4c8db5649fe1c71fb070c190de`。实现保持依赖、算法和结构版本；以下区分
代码依据、全量／开发机验证和已完成的 Kylin 实机验收。

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
  因候选解析器不可用失败；补齐已安装依赖路径后重跑，199 项全部通过。
- `.venv/bin/python scripts/db/verify_publication.py`：私有 PG17 验证发布与新计数，
  包括重复 SQL 的多记录计数、零值、其他集群、后续导入、发布继续通过及公开输出脱敏。
- `.venv/bin/python scripts/db/verify.py`：私有 PG17 验证当前结构及迁移，数据库结构未修改。
- `var/issue31/build-venv/bin/python scripts/tests/test_deployment.py`：16 项通过，
  包括候选提交不符、包文件变更拒绝，以及历史命令继续拒绝原 Alma 产品摘要漂移。
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
  --reference var/issue34/reference --output var/issue34/equivalence --workers 8

.venv/bin/python scripts/db/verify_scanning_full.py \
  --app-root var/issue34/reference --logs raw/inbox/hashdata \
  --output var/issue34/alma-before

.venv/bin/python scripts/db/verify_scanning_full.py \
  --app-root var/issue34/candidate --logs raw/inbox/hashdata \
  --output var/issue34/alma-after
```

全量比较以固定块保存计数与差异定位，可在原文／代码摘要完全相同时恢复；参考实现不设
产品 5 秒期限，不能把旧实现超时当成等价。119 首批使用新的私有 PG17，按原 Alma 的
冻结业务计数核对，并记录各阶段实测耗时；比较期间避免并发的全量等价任务干扰性能。

首轮工具在输入 ID 313026 上因 Python 字典深比较达到递归上限而停止，未将异常当作等价。
改为项目已有的显式栈规范序列化后，完整字节比较可处理深层结构；合成回归覆盖深层相同、
深层差异、原因／近似差异及布尔／整数类型差异，失败输入单独重放通过。
旧分块保留在本地诊断目录，最终全量从头以八个诊断进程重跑，不混用旧工具计数。
此修正仅影响验收工具，产品运行代码及已完成的首批实测不变。

最终完整运行退出码为 0，用时 733.504 秒（八个诊断进程，不改变产品默认并发）。
1,497,418 条输入的分词和完整归一化结果分别全部相同，差异为 0，P1／P2 已满足。
原扫描成功 1,494,542 条，错误 2,876 条；错误类型及参数也逐条相同。
归一化结果为 reliable 1,486,516 条、unsupported_syntax 10,902 条，新旧一致。

| 路径 | 输入条数 | 说明 |
| --- | ---: | --- |
| ASCII 原生快路径 | 1,148,686 | 不生成掩码，不计为回退 |
| 非 ASCII 输入 | 348,732 | 包括下列回退 |
| 非法编码回退 | 224 | surrogateescape 字符由原扫描器处理 |
| 掩码扫描报错后回退 | 1,036 | 用原扫描恢复原始异常 |
| 非 ASCII 美元标签回退 | 0 | 合成用例覆盖，本批未出现 |
| 非 ASCII 且含 NUL 回退 | 0 | 本批未出现 |

每块计数和输入／源码摘要留存于本地忽略的 `var/issue34/equivalence/`，
最终汇总原样纳入机器记录的 `equivalence`。旧失败工具的分块不参与汇总。

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
全量比较结束后单独运行，退出码为 0，24 组新旧解析结构全部相同。

| 输入 ID | 字节长度 | 旧版中位秒数 | 新版中位秒数 | 旧／新耗时比 |
| --- | ---: | ---: | ---: | ---: |
| 33452 | 53,379 | 2.733790 | 0.035257 | 77.5 |
| 33769 | 53,438 | 2.762316 | 0.034830 | 79.3 |
| 193204 | 53,438 | 2.785482 | 0.034369 | 81.0 |
| 221627 | 53,438 | 2.778105 | 0.030520 | 91.0 |
| 33638 | 53,438 | 2.777081 | 0.035116 | 79.1 |
| 64349 | 53,438 | 2.793303 | 0.035094 | 79.6 |
| 197559 | 53,438 | 2.767354 | 0.034759 | 79.6 |
| 197169 | 53,440 | 2.787305 | 0.034965 | 79.7 |

24 次旧版解析累计 66.411553 秒，新版累计 0.816227 秒，累计耗时比约 81.4。
原始重复测量和解析结构摘要保存在机器记录的 `performance_eight`；此比值仅代表固定八条。

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

八条解析测量与首批导入修改前后对比共同满足 P4。完整 Harness 与在线 PR 契约检查
通过；最终提交后的检查记录在 PR 的验证结果中。

### Kylin 首批实测

用户补充 SSH 连接交接并明确授权传输首批 26 份日志后恢复实测。只读检查时 `/data`
为空、没有旧部署或 PostgreSQL 实例；在 `/data/sql-apm-issue34` 独立目录中复用已校验的
离线 RPM、源码和 wheel，构建 Python 3.9.5 与 PostgreSQL 17.10。实际环境为 Kylin V10、
glibc 2.28、VMware 上的 i7-13700K／8 vCPU，pglast 7.18。

执行的程序包来自固定候选 `5c84dda5f96d5b4c8db5649fe1c71fb070c190de`，SHA-256 为
`f47a611be60a398a34825d2527be440a9a75aae0c0e84a39ae5474df21f427fd`。包校验及环境功能检查
通过，目标机 66 项普通测试和 33 项发布检查通过；全部构建、传输和自检在首批计时前结束。
只传输了 119 首批 26 个文件，共 3,824,972,246 字节，摘要逐项匹配原始 55 文件清单；
所生成首批配置与开发机原完整配置中的 `119-0` 完全相同，不改变样本或业务基准。

验收辅助工具使用独立 `acceptance` 目录，原程序包和验收包保留。复现命令如下；
工具从指定程序目录加载运行代码，默认四解析进程，未传 `--workers`：

```bash
APM_TRIAL=/data/sql-apm-issue34
SQL_APM_APP_ROOT="$APM_TRIAL/app" PYTHONPATH="$APM_TRIAL/app" \
  "$APM_TRIAL/app/.venv/bin/python" "$APM_TRIAL/acceptance/scripts/db/verify_scanning_full.py" \
  --app-root "$APM_TRIAL/app" --logs "$APM_TRIAL/logs" \
  --output "$APM_TRIAL/records/p8" --pg-bin "$APM_TRIAL/postgresql-17.10/bin" \
  --instance-parent "$APM_TRIAL" --kylin-settings --first-batch-119
```

干净私有实例也位于 `/data`，沿用 #31 的 shared_buffers 512 MB、work_mem 16 MB、
maintenance_work_mem 128 MB、max_connections 30、max/min_wal_size 2 GB／256 MB。
私有 socket 位于 0700 目录、TCP 关闭；不继承已有数据库或版本，不修改原业务基准。
目标机参数实读、程序与输入摘要、完整对比结果保存在机器记录的 `kylin_first_batch`。

| 项目 | #31 单进程 | #31 四进程 | 本次默认四进程 |
| --- | ---: | ---: | ---: |
| 导入秒数 | 2,413.367 | 1,824.886 | 862.134 |
| 快照秒数 | 58.563 | 54.957 | 54.584 |
| 构建秒数 | 200.526 | 201.436 | 202.031 |
| 完整流程及核对秒数 | 2,674.911 | 2,083.681 | 1,121.232 |
| 归一化超时 | 0 | 882 | 0 |
| 正式分组 | 51,266 | 51,152 | 51,266 |
| 正式统计行 | 509,766 | 507,939 | 509,766 |

本次完整流程约 18.7 分钟，比历史单进程减少 58.1%、历史四进程减少 46.2%；历史四进程
结果存在超时和缺失分组，不能视为通过的基准。前后快照、构建耗时接近，主要差异在导入。
这是同一演练机上的历史运行对比，虚拟机快照、OS 缓存和外部负载未严格控制，不推广为
所有主机或所有 SQL 的性能保证，也未继续诊断宿主多核表现。

26 文件／5,124,686 条日志／2,547,876 次执行、正式及观察五层计数、训练原因、每文件
问题码、六项发布检查、成功尝试和版本链全部与 Alma 基准相同。`status.current` 两项
隔离计数均为整数 0；结构 1.6.0 检查通过，完整命令退出 0。P8 已满足。
私有数据库已停止并清理，运行环境、日志和报告留在独立目录，证据已回收至开发机。

据本次结果，[部署手册](../runbooks/kylin-offline-deployment.md#101-分词修复版默认四进程首批验证)
对包含修复且完成首批验收的包采用默认四进程，原 #31 包仍保留单进程适用边界。
九任务辅助工具显式接收交付提交并完整校验包，业务计数仍使用原 Alma 数值；
旧命令的原产品摘要保护保持不变。随包 HTML 同步新的并发说明，不增加产品依赖。
本轮只重放 119 首批，不宣称完成新版九任务；历史超时数据不因升级自动修复。P9 已同步。
