# 完整日志批次导入

本入口实现“只导入”。先按[数据库说明](database-initialization.md)初始化或核对 PostgreSQL 17
结构 1.2.0，再用 Python 3.9.5 安装根目录锁定依赖：

```bash
.venv/bin/python -m pip install --require-hashes -r requirements.txt
```

离线环境先准备与锁定哈希一致的两个 wheel，再使用 `--no-index --find-links WHEEL_DIR`。
开发环境 CPython 3.9／Linux x86_64 验证不代替 Kylin 部署验证。

## 登记与调用

配置为 JSON 数据，建议保存在被忽略的 `var/ingestion/`。不执行配置内容，也不读取 `.env`。
下面均为合成名称；人工登记真实映射、日期、文件清单，并确认文件已经关闭、复制完成且齐全。
路径相对于配置文件所在目录。来源构建和时区必须属于当前已核查映射。

```json
{
  "version": 1,
  "clusters": ["example-cluster"],
  "sources": {
    "example-source": {
      "cluster": "example-cluster",
      "build": "HashData Warehouse 3.13.13",
      "timezone": "UTC+08:00",
      "declaration": "人工确认单 Master、构建和时区的依据"
    }
  },
  "batches": {
    "example-batch": {
      "source": "example-source",
      "files_confirmed_complete": true,
      "dates": ["2026-07-23"],
      "files": [
        {
          "path": "../../raw/inbox/example.csv",
          "origin_key": "人工确认的源文件唯一标识，可省略",
          "closed_and_copied": true
        }
      ]
    }
  }
}
```

`origin_key` 是人工确认的同源文件身份，不自动采用路径或文件名。相同键的内容变更会暂停。
缺少该键仍支持内容去重和首尾连续记录重叠检测，覆盖限制见[设计](../design/log-ingestion.md)。
改名重提使用新批次标识；相同来源、成功内容仍跳过。已有批次的来源、日期和路径清单冻结，
不允许通过缩短清单把失败批次改成完整；补齐原路径缺失的文件后可以原批次重试。

连接使用 libpq 的 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSFILE` 或
`SQL_APM_DSN` 环境变量。密码用权限 0600 的 passfile，不放在命令参数或配置中。
DSN 仅连接 Baseline 存储库，导入器不连接生产实例，也不执行日志内 SQL。

```bash
.venv/bin/python -m sql_apm import \
  --config var/ingestion/config.json \
  --source example-source --batch example-batch --workers 4
```

可选 `--schema`，默认为 `sql_apm`；进程数 1–8，默认 4。每个 SQL 工作进程限制
512 MiB 地址空间、单次 5 秒；流式写入缓冲按 2,000 条或约 8 MiB 字符量刷新。
限制失败不会变为可靠指纹。批次成功退出 0，不完整／冲突退出 1，Ctrl-C 退出 130。

## 状态、重试与诊断

JSON 行输出仅含固定原因码、数量、摘要和不透明文件 ID，不输出 SQL、数据库名或用户名。
`file_read` 是尚未提交进度；`file_finished` 才表示该文件尝试的结果。最终
`batch_finished` 提供完整批次结果和新增记录／事件数，问题条数与事件次数分别统计。

| 情况 | 记录及处理 |
| --- | --- |
| 未登记来源、重复 JSON 键、不明确映射或未确认清单 | 写入前拒绝，修正配置后重试。 |
| `batch_manifest_changed`／`source_mapping_changed` | 已登记身份不可悄悄改写；人工核对后明确新身份或新批次。 |
| `cluster_busy` | 同集群立即拒绝，不排队；活动会话释放锁后再试。 |
| `duplicate_skipped` | 引用此前 succeeded 尝试；不新增证据或事件，满足清单完成项。 |
| `file_unreadable`、`csv_boundary`、`csv_columns` | 文件失败，批次未完成；修复输入／路径后重试。 |
| `origin_content_changed`、`record_edge_overlap` | 文件 conflict、批次 conflict，阻止后续发布；人工核对，不自动抽取增量或合并。 |
| `file_changed_during_read` | 两次摘要或读取期间文件属性不一致，回滚文件。 |
| `normalization_worker_failed`、`file_processing_failed` | 文件回滚并留失败尝试；先排查运行环境，再安全重试。 |
| `execute_start_missing`、`execute_start_ambiguous` 等 | 已知调用仍保留，类别为 NULL；问题记录解释原因。 |
| SQL 缺失／结构拒绝 | 保留事件的实际 outcome 和证据；近似按返回结果独立保存。 |

`import_batch`、`batch_entry`、`import_attempt` 保存批次／文件历史，`problem` 和
`problem_evidence` 提供原因与文件定位，`source_file.declaration_evidence` 保存文件计数和冲突摘要。
`analysis.evidence_manifest` 保存冻结配置依据；人工日期声明不证明源端没有漏拷。

进程中断回滚当前文件；下一次取得同集群锁后，将遗留 running 尝试标为 interrupted，
再完整重放。已成功文件跳过。SQL 原文／规则／近似缓存元数据独立提交，可能先于文件证据
持久化；它们不代表执行或可发布批次，重试精确复用。没有训练判定、构建或版本切换。

## 验证

合成验收自动建立、停止并清理私有 PG17 实例，无 TCP 监听，不连接现有服务：

```bash
.venv/bin/python -m unittest discover -s tests/ingestion -v
.venv/bin/python scripts/db/verify_ingestion.py
```

全量验收显式访问本地生产日志，私有实例和原文只留在临时／忽略目录；输出为脱敏计数。
此操作成本较高，常规回归不自动运行。使用新的输出目录避免覆盖之前证据：

```bash
.venv/bin/python scripts/db/verify_ingestion_full.py \
  --root raw/inbox/hashdata \
  --manifest docs/reports/data/log-supplement-manifest-2026-09-28.json \
  --index var/parser-probe/issue13/full-scan.sqlite \
  --output var/ingestion/full-validation
```

报告分别统计历史诊断原文、可靠原文、片段／近似、事件及问题，不将历史诊断出现次数
直接当作执行数。RSS 以 0.5 秒采样汇总进程树，包含共享页重复计数，不等于 PSS 或峰值瞬时值。
