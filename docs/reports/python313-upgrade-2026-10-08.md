# Python 3.13.16 升级验证

本报告对应 [Issue #49](https://github.com/shenxg13/sql-apm/issues/49)，分支起点为
已包含 #47 的 `6d74a949ef6bae0f5ab21b109ec075e5ddb11f11`。
只更换项目自带解释器和相同依赖版本的 wheel；原始日志、历史报告及 v0.2.0 发布制品不变。
用户授权先实施开发机部分；目标机不可用，裸机安装和 119 首批尚未执行，仍是合并前要求。

**开发机比较已按确认后的契约通过。** 两轮九任务均成功发布、归一化超时均为 0，总用时条件通过。
首次完整比较发现 11 个代表引用不同并停止；用户确认该列的严格等价规则后，在保留两库重新导出，
57 表比较通过，11 组代表均满足全部等价约束，其余列和表差异为零。没有重跑任务或修改产品选取逻辑。
两次历史停止、原报告及新证据分别保留；尚余目标机 P9–P11 和 P12 的目标机记录。

**首次内存停止及继续决定。** 新解释器的第六项任务 `120-0` 成功发布，但进程树 RSS 采样峰值由
392,081,408 字节升至 504,160,256 字节，增加 **28.586%**，超过 10% 门槛。
验证程序随即停止并保留数据库及日志，没有通过重跑消除这次记录。
用户随后明确“我的生产环境对内存使用并不太敏感，建议继续”；
[已确认决定](https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6054199090)
及在线正文将内存改为只记录，允许在保留的新库完成剩余三项，不要求追加内存诊断。
真实输入字符、业务结果、零超时及总用时 10% 的要求保持有效。

## 来源与环境（observed / measured）

认领日期为 2026-10-08，按 [python.org 发布页](https://www.python.org/downloads/release/python-31316/)
固定 Python 3.13.16。下载的 gzip 源码包与该页 SHA-256 完全一致；未额外声称完成签名验证。

| 输入 | 官方来源 | SHA-256 |
| --- | --- | --- |
| Python-3.13.16.tgz | python.org 发布页 | `cfac63bddf956deafb1172ca131ae5dcaafd6f95056086e233fca205593ed427` |
| pglast 7.18 cp313 Linux x86_64 wheel | [PyPI 元数据](https://pypi.org/pypi/pglast/7.18/json) | `6c3c4ca0d88e2f5dc9d261a1a718be02426f14758c13b1665c431f756e6e5f1e` |
| psycopg2-binary 2.9.10 cp313 Linux x86_64 wheel | [PyPI 元数据](https://pypi.org/pypi/psycopg2-binary/2.9.10/json) | `8608c078134f0b3cbd9f89b34bd60a943b23fd33cc5f065e8d5f840061bd0673` |

开发机为 AlmaLinux 9.8 x86_64，GCC 11.5.0；解释器安装在项目忽略目录
`var/python-3.13.16/`。配置沿用手册的 `--prefix` 和 `--with-ensurepip=install`，
`make -j4`，默认构建；没有启用自由线程、JIT 或 PGO。
首次构建未发现 bz2／lzma 开发头文件；随后沿用旧环境已核验的本地头文件与库搜索路径，
重新构建并通过标准库检查，未安装系统软件。原始失败及重建日志保留。
可选 `_dbm`、`_gdbm`、`_tkinter`、`_uuid` 未构建，不作为产品功能宣称。

`.venv/` 和 `var/issue31/build-venv/` 均由 3.13.16 重新创建，分别从本地 wheel 按哈希离线安装。
运行依赖版本不变，构建依赖仍为既有锁定版本，`pip check` 无冲突。
旧解释器 `var/python-3.9.5/` 保留；旧 venv 只作备份，基准运行使用新建的 3.9.5 venv。
标准库自检覆盖压缩、SSL、SQLite、时区、Decimal、ctypes、UUID 和 spawn 子进程。
相同自检在旧解释器下因精确版本不符被拒绝。

## 改动边界

相对分支起点，产品包定义内的 `sql_apm/`、`rules/`、`scripts/db/initialize.sh` 未改动；
`requirements.txt` 只更新 cp313 wheel 哈希和版本说明。
仓库中的五个历史诊断不进入产品包，其版本判断改为 3.13.16；不据此声称历史实验已重跑。
`normalization_id` 仍由原规则上下文生成，没有增加解释器信息或修改规则。

部署自检调整精确版本，并显式关闭 SQLite 连接以消除新解释器的 ResourceWarning。
统计验证脚本显式关闭已退出子进程的 stdout；制包脚本显式使用 tar 的 data 提取过滤器，
避免默认行为在 Python 3.14 改变的弃用告警。这些修复只影响验证／制包工具，不改变产品逻辑。
九任务测量在既有主机内存采样外增加 CLI 及全部后代进程的 RSS 总和采样。
新增比较工具只用于验证，不装入产品程序包。

## Unicode 全码位检查（measured）

比较 Python 3.9.5 / Unicode 13.0.0 与 Python 3.13.16 / Unicode 15.1.0 的全部
1,114,112 个码位（含代理码位），操作清单如下。

| 操作 | 产品使用位置及意义 | 结果不同的码位数 |
| --- | --- | --- |
| `str.lower` / `str.upper` | lexical、MPP 适配器、近似前缀、函数字典／导入消息 | 各 40 |
| `str.isalpha` | MPP Token.word 的词判断 | 5,485 |
| `str.isalnum` | 近似数字边界 | 5,535 |
| `str.isdigit` | 导入源码行号 | 30 |
| `str.isspace` / 正则 `\s` | 默认 strip／lstrip／split、词法空白及日志消息 | 0 |
| 正则 `\w` | `\b` 边界所依赖的 word 属性 | 5,535 |
| 正则 `\d` | 数字字符属性对照；产品数字语法主要使用 `[0-9]` | 30 |
| 26 个 ASCII 字母的 `re.I` 匹配集合 | 导入 duration／execute 等 ASCII 消息模式 | 0 |

`.isascii()`、固定 ASCII 大小写 translate、显式码位范围和代理／NUL 判断不依赖 Unicode
数据库，不作为属性变化。`strip`／`lstrip` 和无参数 `split` 的空白语义由 isspace 覆盖；
正则中的 ASCII 字面量匹配逐字母覆盖。多字符 SQL 的最终行为仍由后续九任务核对。

并集为 5,535 个码位，完整操作前后值见
[差异清单](data/python313-unicode-delta-2026-10-08.json)。机器文件用紧凑 JSON 保存，
与扫描时的格式化清单解析后完全相同；[扫描记录](data/python313-unicode-scan-2026-10-08.json)
分别记录两种序列化的摘要。

对固定清单的 55 个 CSV 完整读到 EOF 并核对逐文件 SHA-256，全部 14,816,626 条非空
SQL 字段没有命中；其余 CSV 字段也没有命中。原文未输出或提交。
初次扫描使用逐码位正则过慢，在首个文件完成前中止，改为完全相同集合的连续区间后重新全扫；
合成测试对全部码位证明区间表达式没有增加或漏掉字符。没有变更输入或缩小扫描范围。
该结论只适用于这 55 个文件，未来输入的剩余风险已写入
[归一化设计](../design/sql-normalization.md#unicode-已知限制)。

```bash
var/python-3.9.5/bin/python3.9 scripts/deployment/verify_python_unicode.py snapshot --output OLD.jsonl.gz
.venv/bin/python scripts/deployment/verify_python_unicode.py snapshot --output NEW.jsonl.gz
.venv/bin/python scripts/deployment/verify_python_unicode.py compare --old OLD.jsonl.gz --new NEW.jsonl.gz --output DELTA.json
.venv/bin/python scripts/deployment/verify_python_unicode.py scan \
  --delta DELTA.json --logs raw/inbox/hashdata --output SCAN.json
```

## 既有检查

新解释器下已通过 74 项普通单元测试、30 项制包测试、函数字典及覆盖检查、数据库结构／迁移验证，
以及导入（125）、训练（300）、统计、观察（11）、发布（33）、清理（23）、检索、MPP 标识和窗口合成验证。
工具以 `-W default` 运行。未发现产品代码自身触发的弃用告警；两个验证工具的资源未关闭告警
按上文修复，并已通过受影响检查的补验；tar 提取过滤器修复后再次通过全部 30 项制包测试。
独立候选包的 unit、database、publication、smoke、cleanup、search 六个入口全部通过；
统计脚本修复后的 32 项补验通过，上述七份最终日志没有弃用或资源未关闭告警。
按[用户确认后的 P4](https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6054215812)，
上述单元、制包、十个合成数据库入口和全部六个随包入口满足当前既有检查要求。
其余十五个验证脚本不要求执行，仍明确记为未执行，未算作通过。
分支起点的 `scripts/db/verify*.py` 共 26 个文件，逐项状态见准备机器记录的
`existing_db_verifiers`；其中 `verify_training_aliases.py` 是被全量训练检查调用的辅助模块。
历史 `verify_coverage_migration_full.py` 要求冻结 #29 源码和 1.5.0 数据库，仓库现行说明禁止
用当前初始化器对该带数据旧库直接升级。
只读核对还发现：历史类别差分要求解析器依赖与 `b0cc66a` 字节相同，当前 `mpp_parser.py`
已不符合该断言；旧清理全量迁移的完整行比较没有处理 1.10.0 新增的 `search_text`。
这些前提冲突及原建议保留在 [Issue 评论](https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6053293159)。
准备记录已逐项补充十五个入口的输入／旧结构／参考实现前提及未执行原因；
其中 `verify_window_replay.py` 虽使用合成日志，也依赖窗口改动前的历史 checkout，
不属于本次要求通过的十个入口。没有修改历史断言或把九任务描述成这些专项已通过。

## 九任务与逐表方法

`verify_python_runtime.py` 用固定提交的程序包、新建 venv、全新私有 PG17、相同 512 MiB
shared_buffers／16 MiB work_mem、默认四进程，依次执行 119 五任务及 120 四任务。
源码文件路径、日志清单和批次保持一致，任务输出与数据库在成功或失败时均保留，结束后停止私有实例。
旧解释器九任务已全部通过，归一化超时合计为 0，CLI 总用时为 6,099.850 秒；
新解释器在首次停止后完成剩余三项，九任务均成功发布、通过既有业务计数和版本链检查，
每次导入的归一化超时为 0，隔离计数相同。两库的 57 表摘要均已完整导出，私有数据库
正常停止并保留。首次完整逐表比较只在 `mpp_observation_group` 发现差异，当时未通过 P6；
总用时及逐任务内存记录满足已确认后的 P7。随后 P6 按用户确认的代表规则重新验证，见下文。

| 任务 | 旧 CLI 秒 | 新 CLI 秒 | 旧 RSS MiB | 新 RSS MiB | 内存变化 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 119-0 | 1009.542 | 924.505 | 398.465 | 391.609 | -1.720% |
| 119-1 | 200.395 | 199.221 | 242.508 | 265.223 | +9.367% |
| 119-2 | 215.931 | 134.300 | 242.027 | 264.695 | +9.366% |
| 119-3 | 145.969 | 137.064 | 281.777 | 263.754 | -6.396% |
| 119-4 | 103.310 | 101.966 | 171.102 | 175.441 | +2.536% |
| 120-0 | 1614.687 | 1453.726 | 373.918 | 480.805 | **+28.586%** |
| 120-1 | 920.273 | 850.400 | 360.750 | 349.539 | -3.108% |
| 120-2 | 823.871 | 806.827 | 341.500 | 444.961 | **+30.296%** |
| 120-3 | 1065.872 | 1205.608 | 375.078 | 376.742 | +0.444% |

阶段耗时、逐项计数、采样方法、代码摘要、两库 57 表摘要及差异定位见
[运行机器记录](data/python313-runtime-comparison-2026-10-08.json)。新旧初始记录的输入清单、PG 配置及
四个工具摘要相同；续跑与最终比较驱动的版本另列如下。表中 MiB 用 1,048,576 字节换算，
内存变化使用原始字节数计算。
新轮九任务总用时 **5,813.617 秒**，旧轮 **6,099.850 秒**，比值为 0.9530754，
本次总和约少 4.692%，未触发慢 10% 的条件。`120-3` 单项为 1,205.608 秒，
比旧值 1,065.872 秒慢；契约只对九任务总和设置耗时停止条件。
`120-0` 与 `120-2` 的 RSS 分别增加 28.586% 和 30.296%，按确认后契约如实记录。
这些是共享开发机和中断续跑下的观察，不外推成解释器的一般性能保证。

```bash
OLD_APP/.venv/bin/python scripts/deployment/verify_python_runtime.py run \
  --app-root OLD_APP --logs raw/inbox/hashdata --output OLD_RUN
NEW_APP/.venv/bin/python scripts/deployment/verify_python_runtime.py run \
  --app-root NEW_APP --logs raw/inbox/hashdata --output NEW_RUN --reference OLD_RUN
.venv/bin/python scripts/deployment/verify_python_runtime.py compare \
  --old OLD_RUN --new NEW_RUN --output COMPARISON.json
```

首次运行使用 `3acf304` 的工具；新轮前六项保留原始记录。在已正常关闭的停止现场上，
以独立提交 `28b31ad798f347011f6f66a6d7bde93cadd0cb5e` 的 `resume_python_runtime.py`
完成续跑准备：核对已安装候选包、原四个脚本摘要、六项检查点和 PG 配置，完整复制停止库及日志，
再次核对 55 文件摘要及生成的全部配置后，启动同一私有实例，只执行 `120-1` 至 `120-3`。
备份用时 42.435 秒，输入复核 8.763 秒；均不计入 CLI 总用时。没有回放已成功的六项。
原检查点摘要、续跑工具摘要、用户决定链接和开始负载记录在 `continuation` 字段。

```bash
# TOOL_CHECKOUT 固定为 28b31ad798f347011f6f66a6d7bde93cadd0cb5e；路径均为绝对路径。
NEW_APP/.venv/bin/python TOOL_CHECKOUT/scripts/deployment/resume_python_runtime.py \
  --app-root NEW_APP --run NEW_RUN --reference OLD_RUN \
  --backup NEW_BACKUP_DIR --logs ORIGINAL_LOGS
```

这次中断包含正常关闭、等待用户决定和重启，PG shared_buffers 重新建立，OS 缓存未归一化；
因此总用时为原六项加续跑三项的 CLI 时间，不含中断、备份、重启或摘要导出，
不能把它描述成连续无中断运行或隔离环境下的性能基准。继续方式已获用户明确批准。
九任务及首次完整导出期间，`acceptance.py`、`rehearsal.py`、`python_comparison.py` 三个采样／执行／导出文件始终不变。
首次驱动摘要保留在两轮的 `verification_sha256`；续跑控制器另记摘要。
随后按确认后的契约更新通用驱动，取消内存停止并保留旧阈值的超出清单；
最终比较记录单独保存新版控制器的 `comparison_driver_sha256`，没有覆盖首次源码摘要。
六个不启动数据库的回归用例通过，覆盖内存仅记录，以及总用时、超时、表差异、任务缺失和条件漂移的拒绝。

比较器覆盖 schema 的 57 张逻辑表，分区父表包含全部分区数据。完整行保留 NULL、数组、JSON、
bytea、SQL 原文和所有统计值，在数据库侧生成每行 SHA-256，再按行摘要排序流式计算表摘要及行数。
原文不离开数据库；无数值容差，不能沿用 #45 的跨机对数容差。

随机身份逐列建立对应关系，而非删除关联：task 按集群及串行顺序，build／snapshot／config／
publication 按所属任务，attempt 按批次及文件，SQL／近似输入按原文字节摘要，fingerprint／
近似结果按输入和规则，problem 按内容及全部证据／attempt 关系。派生 fingerprint 和近似结果 ID
还核对原生成公式；全部非空引用必须有映射。稳定规则标识、文件／记录／分组 ID 原值比较。
具体列由 `python_comparison.py` 的 `column_mapping` 及导出的 `mapped_identifiers` 完整列出。

仅排除下列执行时间字段，逐列原因由比较器 `OMITTED` 和导出记录保存：

| 表 | 排除列 | 原因 |
| --- | --- | --- |
| schema_version | applied_at | 初始化发生时刻 |
| import_attempt | started_at、finished_at | 导入执行时刻 |
| input_snapshot | frozen_at | 快照封存时刻 |
| build | started_at、finished_at | 构建执行时刻 |
| publication | at | 发布执行时刻 |
| current_version | last_success_at | 最近发布执行时刻 |
| task | started_at、finished_at、stage_seconds | 任务执行时刻；阶段耗时单独比较 |
| mpp_cleanup_month | started_at、finished_at、exclusive_seconds、group_seconds | 清理执行时刻及耗时；九任务无清理行 |

内存方法为每 250 ms 读取 CLI 和递归子进程 `/proc/.../status` 的 VmRSS 并求和，记录采样峰值；
不包括独立 PostgreSQL 进程，共享页可能重复计数，也不宣称捕获间隔内瞬时峰值。
两轮使用相同测量代码；另保留主机内存、swap、OOM 和开始／结束负载。
本轮数据库检查与两遍测量串行执行。共享开发机上的其他会话未隔离；旧首任务期间另有小型
制包／Harness 检查，新首任务期间补跑约 0.3 秒的制包回归。本地保留进程和主机负载快照，
不据此宣称主机完全空闲。
首次运行按当时契约在每项后检查内存 10% 门槛，并于 `120-0` 停止。
用户确认后的续跑只记录逐任务内存变化，九任务完成后仍检查总耗时 10% 门槛。
任何业务差异、非零归一化超时或总耗时门槛触发均停止并报告，不自动调整规则或比较范围。
此次内存增幅是实测观察，原因尚未定位。现有记录保存整项任务的采样峰值，没有保存逐次
采样时间线，无法仅由这些记录确定峰值发生在哪个阶段。

比较器已用两个独立合成库验证随机 UUID 对应后的全表一致，反例覆盖极小统计值变化、
原因码、任务状态及损坏的派生身份；命令为 `scripts/db/verify_python_comparison.py`。

## 逐表差异定位（measured / inferred）

**观察。** 两库的 `mpp_observation_group` 均为 1,074 行，分组 ID 集合相同。
在原比较器已完成随机 ID 对应后，仍有 **11 行的 `result_id` 引用不同**；另外八列逐列摘要一致。
这不是只比较到了随机 ID 字符串：原比较器已把引用换为原文字节摘要、规则和结构原因，
差异表示这些组引用了不同原文的近似结果。被选中结果均为 available，除 `result_id`、`input_id`
外的近似结果全部字段一致，含规则、近似值、状态、原因、规范化表示、诊断和替换记录。
每个受影响组有 2 或 4 个已存结果满足同一规则／近似值复合外键；这个数量不是首次构建时的
实际候选集。其余 56 表的完整行摘要一致，包括原文、近似输入／结果及事件关联、所有正式／观察
统计、构建分组关系、计数和发布状态。

**定位方法。** 接受比较失败后，只为两份保留库临时启动只读诊断连接，关闭 autovacuum，
每条查询限制 15 秒；对 1,074 个组的每列在 PG 中生成摘要，再使用原有 `result_id` 映射比对。
只输出字段名、计数、布尔值和摘要，没有读取到客户端的 SQL 原文、数据库名称或用户名称。
启动与只读查询合计 4.860 秒，不含收尾停库；连接关闭后两个实例均正常停止。细项和诊断脚本摘要保存在运行机器记录的
`observation_reference_diagnosis`。此前仅在 119 首项的 28 组上做过有界预检，当时没有多候选组，
该预检没有覆盖本次完整九任务的 1,074 组，也未当作完整等价验收。

**源码事实。** [观察构建](../../sql_apm/storage/observations.py)按
`DISTINCT ON (observation_group_id) ... ORDER BY observation_group_id,result_id` 选取代表，
`ON CONFLICT DO NOTHING` 保留最初选取的引用；[导入器](../../sql_apm/storage/ingestion.py)
的近似 input_id 使用 UUID，result_id 又由该身份派生。这两处源码相对升级前起点字节未变。
当前观察查询按分组近似值和分组 ID 读取统计；本次完整统计表摘要相同。

**推断与限制。** 现有机制允许两个全新库在等价近似结果之间选中不同原文的代表，
与本次观察相符；没有追加同解释器两轮回放，不能据此断言是 Python 升级造成或排除其所有影响。
首次比较时的 P6 要求关系精确相同，范围第 13 项要求遇到差异停止；该次失败结论继续保留。
当时没有删列、替换映射或重跑比较，而是返回需求澄清。
用户随后选择“采纳实施方建议”，[确认评论与已同步的在线正文](https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6056030974)
仅为代表引用定义下述等价规则；本次恢复按新契约实施，不改产品代表选取逻辑。

## 已确认的代表等价方法

比较工具固定在 `63ca6f8ae6ae999e6c322bab8686b47675020b40`。每个观察组都先检查代表真实存在、
available、规则／近似值与组相符，且有同组维度下的事件以它为近似结果。
候选来自 `mpp_occurrence_approximate` 与 `mpp_occurrence` 的真实关联，匹配集群、profile、
数据库、执行用户、规则、近似值和计时类型；事件的 NULL 计时按产品规则对应 `unknown`。
候选数为符合这些条件的不同结果数，不是仅满足复合外键的全库结果数，也不声称回溯首次构建候选集。

通过上述约束后，仅把 `mpp_observation_group.result_id` 换成被引用结果完整 JSONB 去掉
`result_id`、`input_id` 后的 SHA-256，再纳入完整行摘要。其余八列、另外 56 表的列值和
标识映射与原工具相同，所有近似输入／结果及事件关系仍按原文字节身份精确比较，不设数值容差。
公开记录保留代表不同的组摘要及新旧候选数；完整逐组摘要保留在本地导出文件中。

正反例通过两个独立的合成私有库执行，明确指定同组两条不同原文作为代表，避免测试依赖随机排序。
正例覆盖等价代表更换和未知计时；15 项反例覆盖悬空、不可用、规则／值不符、各组维度不符、
缺少事件、只有其他组事件、非标识诊断／替换数不同、其他组列变化以及事件关联仍须精确比较。
另保留四个原有反例及八项运行门槛／重新导出来源检查。
合成测试中曾尝试只读事务导出，但 PostgreSQL 拒绝 `CREATE TEMP TABLE AS`；该失败日志保留。
正式导出沿用原工具只写事务临时表的方式，产品表只执行 SELECT，导出后回滚并正常停库。

`reexport_python_runtime.py` 要求原九任务完整且私有库已停止，核对配置后临时关闭 autovacuum、
建立 REPEATABLE READ 事务，并在新目录保存全部导出。它逐表断言其他 56 表与该库首次摘要一致，
核对原报告字节摘要未变，记录新比较器及导出驱动摘要。原任务记录、测量代码摘要和首次失败不被覆盖。
这一轮只有两库摘要导出，没有执行任何任务；成本为两次全库读取及临时排序，失败保留现场、不自动重试。
只重查 11 组不能满足完整 P6，重跑九任务也不能消除既有随机选取机制，故按已批准方案复用保留库。

```bash
.venv/bin/python scripts/db/verify_python_comparison.py
.venv/bin/python scripts/tests/test_python_runtime.py
.venv/bin/python scripts/deployment/reexport_python_runtime.py --run OLD_RUN --output NEW_OLD_EXPORT
.venv/bin/python scripts/deployment/reexport_python_runtime.py --run NEW_RUN --output NEW_NEW_EXPORT
.venv/bin/python scripts/deployment/verify_python_runtime.py compare \
  --old NEW_OLD_EXPORT --new NEW_NEW_EXPORT --output NEW_COMPARISON.json
```

**实测结果。** 旧／新两次导出分别耗时 469.380／460.045 秒，包含启动与完整导出，
不含最后停库；两次退出码均为 0，实例均正常停止。每库 1,074 个组的代表均通过存在、
available、规则／值相符和同组真实事件关联检查；其余 56 表不仅新旧相同，也各自与首次导出摘要相同。
原两份 `report.json` 的字节摘要、全部任务记录及测量源码摘要均未变。

新的完整比较退出码为 0，`passed=true`、`compared_tables=57`、`different_tables=[]`，
总用时及零超时条件继续通过。代表对应至不同原文的组仍为 11 个，其结果全部非标识字段相同；
组内其余八列、原文／近似输入／结果全集、事件关联、统计和状态均按原口径精确比较。

| 同组真实事件关联的候选数 | 旧库组数 | 新库组数 | 代表选择不同的组数 |
| --- | --- | --- | --- |
| 1 | 1,063 | 1,063 | 0 |
| 2 | 7 | 7 | 7 |
| 4 | 4 | 4 | 4 |

[运行机器记录](data/python313-runtime-comparison-2026-10-08.json) 的格式更新为 `/3`：
`runs` 保留原任务及首次完整导出；`historical_comparisons` 保留原失败，`historical_stops` 保留两次停止；
`reexports` 保存新 57 表摘要、来源、候选分布及受影响 11 组的摘要细项；`comparison` 为新契约下的通过结果。
全部 1,074 组的原始导出明细保留本地，公开记录保存该文件的完整 SHA-256。
此处 `complete=true` 仅指开发机九任务比较完成，不表示目标机验收或 Issue 已完成。

## 可重复核对版本残留

```bash
git grep -n -E '3\.9\.5|cp39|python3\.9' -- . \
  ':!docs/reports' ':!tests/parser_probe' ':!docs/releases' ':!.project-wiki/log.md'
```

允许保留的语境是：本报告的对照方法、旧解释器备份路径、旧 cp39 来源不能复用的说明，
运行知识中明确被取代的两条决定及历史实测、SQL 指纹知识中的历史实验、冻结 #13 的复现步骤，
历史逻辑契约验证记录和文档导航中的旧报告标题。它们均不把 3.9.5 作为当前运行版本。
历史 CSV NUL 兼容处理及其说明保持原样，未借升级改变行为。

## 未验证边界

固定候选提交 `3acf30484e96f9938ede3a8e7fe693b4a2c879e4` 的程序包、独立验收包和完整离线包
均已重复制包并逐字节一致。完整包为 488,258,554 字节、417 项，只有 3.13.16 源码和两个 cp313 wheel，
未包含 3.9.5 源码或 cp39 wheel；PG 源码和 RPM 集合沿用原材料。包内产品文件相比基线只有
requirements.txt 不同，固定 normalization_id 与旧解释器完全相同。摘要与当前检查结果见
[准备机器记录](data/python313-preparation-2026-10-08.json)。完整离线 Harness 与在线 PR 契约检查通过，
独立包的全部六个入口已通过。
首次内存停止及用户继续决定均保留；P4 已按确认后的范围核对，P7 总用时和内存记录通过。
P6 已依用户确认的代表规则补正反例、重新导出并通过；原失败记录没有被改成通过。
P12 的开发机证据已补齐，目标机证据仍待执行后补充。
目标机未探测、未传输、未安装，也未运行 119 首批；不能由 Alma 的构建成功推断 Kylin 编译器
和离线 RPM 集合已足够。没有新版本号、标签或 Release 附件。
本地未提交 SQL 原文、数据库副本、主机地址、账号或凭据。详细运行记录保留在忽略目录
`var/issue49/records/`，公共机器记录只保存摘要、计数、版本和测量结果。
