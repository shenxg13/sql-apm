# Python 3.13.16 升级验证

本报告对应 [Issue #49](https://github.com/shenxg13/sql-apm/issues/49)，分支起点为
已包含 #47 的 `6d74a949ef6bae0f5ab21b109ec075e5ddb11f11`。
只更换项目自带解释器和相同依赖版本的 wheel；原始日志、历史报告及 v0.2.0 发布制品不变。
用户授权先实施开发机部分；目标机不可用，裸机安装和 119 首批尚未执行，仍是合并前要求。

**本轮按契约停止。** 新解释器的第六项任务 `120-0` 成功发布，但进程树 RSS 采样峰值由
392,081,408 字节升至 504,160,256 字节，增加 **28.586%**，超过 10% 门槛。
验证程序随即停止并保留数据库及日志。剩余三项、完整总耗时门槛和最终逐表比较尚未完成，
需要用户书面决定后才能继续；没有通过重跑或改变阈值消除这次记录。

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
其余显式真实数据／历史专用检查尚未完成，不能将这些结果当作 P4 全部通过。
分支起点的 `scripts/db/verify*.py` 共 26 个文件，逐项状态见准备机器记录的
`existing_db_verifiers`；其中 `verify_training_aliases.py` 是被全量训练检查调用的辅助模块。
历史 `verify_coverage_migration_full.py` 要求冻结 #29 源码和 1.5.0 数据库，仓库现行说明禁止
用当前初始化器对该带数据旧库直接升级。P4 的历史场景范围已向用户提出澄清，尚未收到确认，
未因此将任何未执行脚本记为通过或自行缩减 Issue 契约。
只读核对还发现：历史类别差分要求解析器依赖与 `b0cc66a` 字节相同，当前 `mpp_parser.py`
已不符合该断言；旧清理全量迁移的完整行比较没有处理 1.10.0 新增的 `search_text`。
这些前提冲突及拟议验收范围已记录在 [Issue 评论](https://github.com/shenxg13/sql-apm/issues/49#issuecomment-6053293159)，
属于尚未确认的建议，未以修改旧断言的方式绕过。

## 九任务与逐表方法

`verify_python_runtime.py` 用固定提交的程序包、新建 venv、全新私有 PG17、相同 512 MiB
shared_buffers／16 MiB work_mem、默认四进程，依次执行 119 五任务及 120 四任务。
源码文件路径、日志清单和批次保持一致，任务输出与数据库在成功或失败时均保留，结束后停止私有实例。
旧解释器九任务已全部通过，归一化超时合计为 0，CLI 总用时为 6,099.850 秒；
57 张表的摘要已完整导出，私有数据库正常停止并保留。新解释器的 119 五项及 `120-0`
均成功发布、通过既有业务计数检查，超时均为 0；随后因 `120-0` 内存超标停止。
新库也已正常停止并保留，尚未生成全表摘要或执行最终比较，不能声称 P6／P7 通过。

| 任务 | 旧 CLI 秒 | 新 CLI 秒 | 旧 RSS MiB | 新 RSS MiB | 内存变化 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 119-0 | 1009.542 | 924.505 | 398.465 | 391.609 | -1.720% |
| 119-1 | 200.395 | 199.221 | 242.508 | 265.223 | +9.367% |
| 119-2 | 215.931 | 134.300 | 242.027 | 264.695 | +9.366% |
| 119-3 | 145.969 | 137.064 | 281.777 | 263.754 | -6.396% |
| 119-4 | 103.310 | 101.966 | 171.102 | 175.441 | +2.536% |
| 120-0 | 1614.687 | 1453.726 | 373.918 | 480.805 | **+28.586%** |
| 120-1 | 920.273 | 未执行 | 360.750 | 未执行 | 未判定 |
| 120-2 | 823.871 | 未执行 | 341.500 | 未执行 | 未判定 |
| 120-3 | 1065.872 | 未执行 | 375.078 | 未执行 | 未判定 |

阶段耗时、逐项计数、采样方法、代码摘要及旧库 57 表摘要见
[运行机器记录](data/python313-runtime-comparison-2026-10-08.json)。新旧输入清单、PG 配置及四个
测量／比较脚本的摘要相同。表中 MiB 用 1,048,576 字节换算，门槛判断使用原始字节数。
新轮不完整，因此没有把六项部分用时与旧轮九项总用时相比较。

```bash
OLD_APP/.venv/bin/python scripts/deployment/verify_python_runtime.py run \
  --app-root OLD_APP --logs raw/inbox/hashdata --output OLD_RUN
NEW_APP/.venv/bin/python scripts/deployment/verify_python_runtime.py run \
  --app-root NEW_APP --logs raw/inbox/hashdata --output NEW_RUN --reference OLD_RUN
.venv/bin/python scripts/deployment/verify_python_runtime.py compare \
  --old OLD_RUN --new NEW_RUN --output COMPARISON.json
```

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
新轮每项完成后立即检查内存 10% 门槛，九任务完成后检查总耗时 10% 门槛。
任何业务差异、非零归一化超时或资源门槛触发均停止并报告，不自动调整规则或比较范围。
此次内存增幅是实测观察，原因尚未定位。现有记录保存整项任务的采样峰值，没有保存逐次
采样时间线，无法仅由这些记录确定峰值发生在哪个阶段。

比较器已用两个独立合成库验证随机 UUID 对应后的全表一致，反例覆盖极小统计值变化、
原因码、任务状态及损坏的派生身份；命令为 `scripts/db/verify_python_comparison.py`。

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
开发机内存门槛已经触发；继续剩余任务或进行新的实测须先取得用户按范围第 13 项的书面决定。
目标机未探测、未传输、未安装，也未运行 119 首批；不能由 Alma 的构建成功推断 Kylin 编译器
和离线 RPM 集合已足够。没有新版本号、标签或 Release 附件。
本地未提交 SQL 原文、数据库副本、主机地址、账号或凭据。详细运行记录保留在忽略目录
`var/issue49/records/`，公共机器记录只保存摘要、计数、版本和测量结果。
