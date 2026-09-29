# 训练快照与判定诊断

先按[初始化说明](database-initialization.md)升级到 1.3.0，并通过
[导入命令](log-ingestion.md)完成批次。本入口交付②，业务范围见
[训练资格](../../.project-wiki/contracts/training-eligibility.md)和
[判定设计](../design/training-decisions.md)，不创建构建或统计版本。

## 本地配置

JSON 存放于被忽略的 `var/training/`；不执行配置或 `.env`。下面均为合成示例。
类别固定为已确认的七类；首期模板默认空列表，用户运行时维护。

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
五层样本门槛在本版按已确认默认值完整记录，不用门槛删样本。

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

命令返回 `input_id`、`config_id`、`rule_id`、新增原文结果数及缓存耗时。
快照一经封存不可修改；修改配置后重新创建快照，已有快照仍使用原规则。
中断前已提交的原文缓存可以复用；未完成的快照事务全部回滚。
同规则的缓存准备并发返回 `training_rule_busy`，待原会话退出后重试。
这不是④的同集群任务调度或重建命令。

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
重新导入，再为 119／120 按各截止日建快照，比较重复汇总和 #18 计数。
临时时段只在本次验收配置中存在；原文、源文件和配置均不提交。
此检查成本较高，日常回归不自动执行。指定全新输出目录：

```bash
.venv/bin/python scripts/db/verify_training_full.py \
  --root raw/inbox/hashdata \
  --output var/training/full-validation
```

实例在完成或异常时停止并删除；脱敏报告保留在指定忽略目录。
真实结果见[验证报告](../reports/training-decisions-2026-09-29.md)。
