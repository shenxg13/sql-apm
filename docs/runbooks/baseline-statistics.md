# 统计计算操作

本入口交付③：引用②封存快照创建一次构建，完整计算五层统计并保存到 PostgreSQL。
[统计规则](../../.project-wiki/contracts/baseline-statistics.md)定义公式和门槛，
[设计](../design/baseline-statistics.md)说明数值、分区、事务和恢复边界。

## 前提与调用

先按[初始化说明](database-initialization.md#升级到-140)升级到 1.4.0，完成导入，
使用[训练快照命令](training-decisions.md)得到 input_id 与 config_id。
继续使用相同 libpq 环境或 SQL_APM_DSN，不把密码写入命令或提交配置。

```bash
.venv/bin/python -m sql_apm statistics \
  --cluster example-cluster --input INPUT_ID --config-id CONFIG_ID
```

可指定 `--schema`；必须使用同集群的封存快照。未封存、跨集群、未知公式版本、
缺少相应原文缓存的输入不能完成计算。命令不执行发布检查或切换当前版本。

每次开始生成带北京时间日期前缀和随机部分的不透明 Build ID；自动创建该集群当前月份的
统计和覆盖分区，无需提前按月运维。月份由构建开始时间决定，不由日志日期或训练窗口决定。
同一月份所有构建共享分区，层次与计时类别不拆表。空月无构建时不预建。

输出为 JSON 行：build_created、decisions_derived、statistics_progress、results_written 和最终
statistics 汇总。中间 results_written 表示已完成事务内写入；只有最终 state=calculated 且
results_saved=true 表示提交成功。各层行数、计时类别计数、判定状态、计数范围和原因可核对；
五类与五层的数量不相加作为真实执行总数。输出不包含 SQL、数据库名、执行用户或驱动错误文本。

退出码：成功 0，配置／计算／保存失败 1，Ctrl-C 或 SIGTERM 130。
单组不足仍保存全部可计算指标；有排除的空样本桶保存 NULL/no_samples；完全空桶进入 empty_keys。

## 失败与重新计算

失败／中断的计算事务回滚；已登记 Build 与原因保留，results_saved=false。
主进程正常捕获错误记录固定码；异常退出时监护进程使用独立连接记录 worker_disconnected。
同机 SIGTERM／SIGKILL 路径有合成验收。整机或数据库不可用时，不能即时更新状态，
遗留 running／false 也不能解释为完整结果；整体任务恢复和忙时管理属于④。

```bash
.venv/bin/python -m sql_apm statistics \
  --cluster example-cluster --input INPUT_ID --config-id CONFIG_ID \
  --retry-of FAILED_BUILD_ID
```

retry_of 仅接受同集群、同一对快照的 failed／interrupted 构建；新尝试重新计算全部窗口，
不续写失败半成品。修改输入或配置时先另建快照，以新快照发起计算。
这只是一次统计尝试的引用参数；完整流程、重新构建编排、同集群串行和发布由④交付。

## 验证与成本

合成检查自动创建私有 PG17，结束后停止并清理实例：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/db/verify.py
.venv/bin/python scripts/db/verify_statistics.py
```

真实验收是显式高成本操作：重新导入固定清单全部 55 文件，每集群创建快照并计算两次，
核对所有组的五层守恒、覆盖索引、与判定的计数／原因一致，并以固定种子抽样至少 1,000 组
独立复算全部指标。包含最大组、单样本组和含排除组；不设耗时／内存通过门槛。

```bash
.venv/bin/python scripts/db/verify_statistics_full.py \
  --root raw/inbox/hashdata --output var/statistics/new-acceptance
```

输出目录必须新建；原始数据、配置、数据库和中间结果不提交。
`--dsn` 仅供验收维护者连接已重导的私有临时实例进行后半段验证，不是产品导入或计算入口。
每表和每分区精确行数／总大小记录在报告；分区父表大小为所有叶分区总和，不能再与叶表相加。
内存按一秒采样当前 Python 进程树与私有 PostgreSQL 进程树的 PSS，记录各自及合计峰值；
它是有采样间隔的 measured 值，不含导入阶段峰值，不等同于进程 RSS 之和。
