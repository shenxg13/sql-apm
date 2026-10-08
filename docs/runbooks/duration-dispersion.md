# Duration 覆盖与合并耗时诊断

本地只读命令 `sql_apm.diagnostics.duration_dispersion` 用于
[Issue #15](https://github.com/shenxg13/sql-apm/issues/15) 的耗时证据。
使用 Python 3.13.16 和已有解析诊断依赖；不连接业务数据库、不执行生产 SQL、不构建基线。

## 输入、维度与分母

输入是[七天全量索引](log-supplement.md)及其记录的关闭 CSV 文件。
`extract` 从索引的 files 表读取清单，完整读取文件并核验字节数、SHA-256、
记录数及读取前后文件状态。索引只读，运行前后摘要须相同。
每条带 SQL 的 duration 日志只计一个样本：优先独立 SQL 字段，其次 message 中的
内联 SQL，最后 internal 字段；不同字段不重复计一次耗时。原文字节摘要必须在索引中存在。

分组为集群、数据库哈希、执行用户哈希、计时来源位置、结构指纹。
数据库和执行用户采用 SHA-256；不把原名称写入缓存或报告。
来源位置使用既有固定格式的源码文件名及行号；不能识别的来源计为 OTHER，并明确计数。
日期取 CSV 日志记录日期，不减去耗时；多个文件同日只贡献一个活跃日。

覆盖要求同时达到 7 个活跃日和 30／200／1,000 个 duration 样本。
主分母为该集群全部带 SQL 的 duration 记录，包括指纹拒绝／失败；
辅助分母仅包含有可靠指纹的记录。这里没有 Execute 首次／续取配对，没有训练资格过滤，
不能将结果称为有效执行覆盖或基线可用率。

新增合并按 v5 分组、v4 子组比较。
使用冻结快照的“仅 SELECT”和“SELECT＋JOIN”阶段摘要，在每个实际五维分组内
按固定顺序互斥归因：仅 SELECT 已能形成整个最终组时归入 SELECT 列表；
否则 SELECT＋JOIN 已能形成时归入 JOIN ON；剩余已通过最终结构核对的归入集合分支恢复。
混合变化归入最后所需步骤，不当成三个独立反事实方案；全体汇总和三类分别报告。每个 v4 子组至少有 5 个样本，
每个 v5 组至少有两个符合条件的 v4 子组，才纳入可比集合。
计算各子组耗时中位数的最大／最小倍数，按 ≤2、>2–5、>5–10、>10 分桶。
权重为符合条件的子组样本数；不足 5 条的子组不贡献权重。
中位数为中间值或中间两值平均；全部中位数为零时倍数记为 1，
零与正数比较进入 >10 桶，不输出非有限 JSON 数值。

另外提供与需求诊断同口径的“v4 WHERE 合并”参照：按原文 input_id→v4 指纹划分子组。
该参照实际包含 v4 的全部既有规则（例如函数及写入值）；不推断原文差异只出现在 WHERE。
同样使用至少两个、每组至少 5 条的子组及上述样本权重，按两个集群分别报告。

## 命令

提取独立于归一化规则，可供同一来源索引的多个完整快照比较复用：

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.duration_dispersion extract \
  --source var/parser-probe/issue13/full-scan.sqlite \
  --root raw/inbox/mpp \
  --cache var/parser-probe/issue15/durations.sqlite --workers 8

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.duration_dispersion report \
  --cache var/parser-probe/issue15/durations.sqlite \
  --before var/parser-probe/issue15-v4/var/v4.sqlite \
  --after var/parser-probe/issue15/v5.sqlite \
  --output var/parser-probe/issue15/dispersion.json
```

两份规则快照由 [normalization_diff](../design/sql-normalization.md#规则变更分组差分)
生成。报告先执行完整 v4→v5 强制结构审计；不完整快照、来源不一致、缺少 duration 输入、
拆分、状态／原因变化或结构不符均拒绝。报告以临时 SQLite 表聚合，不改写快照或耗时缓存。
输出包含规则上下文、输入摘要、提取文件证据、统计口径和汇总，不含 SQL、Hint 或 AST。

## 资源、失败与恢复

提取使用 1–8 个进程，默认 4；每进程地址空间上限 512 MiB，禁用 core dump，
每 10,000 条样本批量写私有分片，再合并至本地 SQLite 缓存。
文件扫描须读到 EOF，不适用单条 SQL 的 5 秒解析预算；提取不执行解析器。
报告采用磁盘 SQLite 分组，中位数需要暂存当前分组的耗时；没有固定性能门槛。

缓存须位于当前 checkout 忽略的 `var/` 下。已有输出拒绝覆盖，
中断保留 `complete: false` 的缓存及可能的分片目录；换新路径重跑。
只有全部文件及来源检查通过才标记 complete。提取完成后可单独重跑报告。
运行期间保持来源索引、日志和工具源码不变。输出同时记录提取模块及直接复用模块的源码摘要；
代码升级后可复用已完成、规则无关的缓存，提取代码与当前报告代码分别标识。

成功退出 0；验证／处理失败输出固定 `duration_diagnostic_failed` 并退出 1；
参数解析错误退出 2。异常原文、SQL 和未哈希的数据库／用户名不写入诊断输出。
普通测试无需访问真实日志；专项合成测试覆盖维度隔离、数量与日期边界、
中位数倍数分桶、拒绝分母、文件核验、版本审计和输出脱敏。
