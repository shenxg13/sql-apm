# 训练快照与判定诊断

先按[初始化说明](database-initialization.md)准备 1.9.0 结构；1.7.0／1.8.0 可带数据升级，更早非空库须重建，再通过
[导入命令](log-ingestion.md)完成批次。本入口交付②，业务范围见
[训练资格](../../.project-wiki/contracts/training-eligibility.md)和
[判定设计](../design/training-decisions.md)，不创建构建或统计版本。

## 本地配置

JSON 存放于被忽略的 `var/training/`；不执行配置或 `.env`。下面均为合成示例。
类别固定为已确认的七类；首期模板默认空列表，用户运行时维护。
类别 v3 将三个别名归入已有类别，覆盖边界见[训练资格](../../.project-wiki/contracts/training-eligibility.md#三个纯别名的补充确认2026-09-30)。
单独升级类别规则后首次创建快照会准备新缓存，旧快照继续使用原有结果；类别规则本身不要求结构迁移。
MPP 标识改名的重建要求由上面的初始化说明另行约束。

```json
{
  "version": 1,
  "clusters": ["example-cluster"],
  "window": {"cutoff_date": "2026-07-31", "days": 30},
  "templates": [],
  "exclusions": []
}
```

模板条目格式为
`{"id":"example-template","sql":"SELECT * FROM demo WHERE id=1","description":"人工排除说明"}`，
可加 `cluster`、`database`、`execution_user`，只匹配指定范围；不填则不限制相应维度。
单条和整批示例均按本次归一化规则计算结构指纹；不同常量是否相同由该规则决定。
归一化升级时示例自动重新计算，无需重填。无可靠结构指纹的示例使快照命令失败。

时段条目格式为
`{"id":"example-interval","cluster":"example-cluster","start":"2026-07-23T10:00:00+08:00","end":"2026-07-23T10:30:00+08:00","reason":"人工维护说明"}`。
起止必须明确使用 `+08:00`，且 start 小于 end；模板与时段 ID 不重复。
集群须列于本地 clusters；目标集群还须已在存储库登记并有已完成批次。
窗口 days 为正整数，省略时取 30，截止日必须有效。重复 JSON 键、未知字段、空限定值被拒绝。
五层样本门槛默认按已确认值完整记录，不用门槛删样本。#25 增加可选顶层 thresholds：
例如 `{"week":{"basic_count":30,"p95_count":200,"p99_count":1000,"coverage_min":3}}`。
只覆盖明确填写的非负整数，其余使用默认值；层名为 overall/day/week/weekday/hour。
各层 coverage_kind 固定，day 的 coverage_min 只能为 0，不能借配置改变五层覆盖语义。
门槛实际内容封存在配置快照中，由[③统计计算](baseline-statistics.md)使用。

## 结果保留配置

可选顶层 `retention` 只控制[版本结果清理](build-publication.md#版本结果清理)，不属于训练输入：

```json
{"retention": {"months": 2, "clusters": {"119": 12}}}
```

把该项加入既有完整训练 JSON；示例中的 119 必须列在顶层 clusters。
省略 retention 或 months 时默认 2；months 及每个集群覆盖值必须为不小于 1 的整数，
不接受布尔值、浮点数或字符串。未知键、非对象或未登记在顶层 clusters 的覆盖键均拒绝；
固定原因码为 invalid_retention、invalid_retention_months 或 unknown_retention_cluster。
修改该项不改变 full、rebuild、training snapshot 封存的配置内容，也不改变训练规则 ID。
清理命令可使用 full 配置中尚未指定 cutoff_date 的 window，因为清理不使用训练截止日。

## 创建快照

使用与导入相同的 libpq 环境或 `SQL_APM_DSN`，不把密码写入命令参数。
指定目标集群及一组完成批次；可重复 `--batch`。

```bash
.venv/bin/python -m sql_apm training snapshot \
  --config var/training/config.json --cluster example-cluster \
  --batch example-batch
```

同一文件存在多个 Analysis 时必须重复传入 `--analysis ANALYSIS_ID` 明确选定集合；
每个文件恰好一个解释。未知、未完成、失败文件或未使用的解释 ID 均拒绝。
重复文件按文件身份去重，重复导入不会多算事件。
`--schema` 为 training 级选项，须放在 `snapshot`／`summary` 之前。

命令返回 `input_id`、`config_id`、`rule_id`、新增原文结果数及缓存耗时；后续使用这组快照。
任意混用不同命令的输入和配置时，若该规则未处理新输入，数据库报 `training_cache_not_prepared`，
不会将“未准备缓存”误计为源 SQL 指纹失败。
快照一经封存不可修改；修改配置后重新创建快照，已有快照仍使用原规则。
中断前已提交的原文缓存可以复用；未完成的快照事务全部回滚。
快照先取得同集群任务占用；同集群已有任务时返回 `cluster_busy` 并记录忙时拒绝。
缓存准备锁按规则和集群取键，不同集群不因使用同规则而互相拒绝；共享缓存以短事务写入。
完整重建使用[④重新构建命令](build-publication.md)。

模板的任何修改（包括只改说明，或修改其他集群的模板）都会生成新的 `rule_id`。
随后首次为相应输入创建快照会重新计算原文缓存，旧结果保留。建议积累数条模板修改后
一次性提交配置，再创建快照。类别或归一化规则变更也会生成新缓存。
[原实测](../reports/training-decisions-2026-09-29.md)在两个集群共约 138 万条去重原文上，
一版缓存约 720 MB、准备约 10 分钟；这是该输入和本机环境的 measured 数据，
不是每次改模板的固定成本或生产容量保证。
[2026-09-30 用户确认](https://github.com/shenxg13/sql-apm/issues/21#issuecomment-5903278085)
接受这一成本并维持现有设计；自动清理继续暂缓。

## 汇总和逐条查询

```bash
.venv/bin/python -m sql_apm training summary \
  --input INPUT_ID --config-id CONFIG_ID --groups
```

JSON 行只含计数、原因码、摘要和不透明定位；`--groups` 流式输出每组／计时／状态／
计数范围，以及各原因数量。`reason=null` 为事件去重总量，具体原因分别计数。
最后一行给出四种状态、计时类别、原因、类别、模板和时段的汇总。
缺 SQL／时间导致的未评估不等于未命中。窗口外单列，不能加到窗口内排除数。
退出码：成功 0，配置／执行失败 1，Ctrl-C 130。异常文本不包含驱动错误或原始数据。

数据库内查询同一权威函数；参数使用绑定值，下面是合成 ID：

```sql
SELECT state, reason_codes, rule_evaluations, group_id, count_scope
FROM sql_apm.mpp_training_decisions('INPUT_ID','CONFIG_ID','ANALYSIS_ID','OCCURRENCE_ID');
```

省略最后两项可供③批量使用。API 通过 `TrainingStore.decision` 检查快照配对和事件存在性；
SQL 函数的未知 ID 返回空集。判定版本不支持会报 `unsupported_decision_version`。
旧判定代码不保证长期保留，历史可追溯承诺限于已保存计数和规则依据。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v
.venv/bin/python scripts/db/verify_training.py
.venv/bin/python scripts/db/verify.py
```

显式全量验收新建私有 PG17 实例，禁用 TCP；复用已确认的 55 文件清单，
重新导入，再为 119／120 按各截止日建快照，将汇总与 #18 计数对账，
并强制计算完整 Decision 字段，比较两次推导的行数及摘要。
临时时段只在本次验收配置中存在；原文、源文件和配置均不提交。
此检查成本较高，日常回归不自动执行。指定全新输出目录：

```bash
.venv/bin/python scripts/db/verify_training_full.py \
  --root raw/inbox/mpp \
  --output var/training/full-validation
```

实例在完成或异常时停止并删除；脱敏报告保留在指定忽略目录。
真实结果见[原验证报告](../reports/training-decisions-2026-09-29.md)。
类别 v2 的合成回归和原 55 文件索引的有界影响核对见
[整改验证](../reports/training-decisions-r1-remediation-2026-09-30.md)；旧实测仍归属其记录的源码版本。

Issue #23 的执行级比较追加 `--compare-aliases`，在同一私有实例使用冻结 v2 实现准备旧规则，
再生成 v3 快照。两个集群按相同截止日和临时时段与 #21 合并时保留的最终计数逐项核对；
临时完整执行投影证明其他 Decision 字段及非类别原因不变，另列新增类别命中、真正状态转为
excluded 的数量和混合转纯黑名单批次。单条含多个别名时，每别名分项有重叠，去重合计另列。

```bash
.venv/bin/python scripts/db/verify_training_full.py \
  --root raw/inbox/mpp \
  --output var/training/aliases-replay --compare-aliases
```

冻结 v2 文件来自 #21 合并提交并校验字节摘要，仅用于验收，不是产品历史规则执行入口。
额外准备一版缓存和临时比较投影增加验收成本；结果与证据边界见
[别名验证报告](../reports/training-category-aliases-2026-09-30.md)。
