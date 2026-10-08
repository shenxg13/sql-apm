# Issue #47 开发机试用步骤与记录

用于合并前 S23，随 PR 保存，不随程序包交付。自动化报告不能代替用户试用确认。
全部命令在开发机仓库根目录执行，使用专用真实数据副本，#45 原库保持关闭。
本文件只用合成标识，真实输入和结果保留 `var/issue47/`，不要把原文、身份或连接信息粘到 Issue／PR。

## 需要准备的内容

- 自己熟悉的一个表名／关键词、两个可同时出现的词、一段含逗号或等号的连续片段。
- 一份自己选择的完整 SQL（或批次），以及只改变格式／可归一业务常量的第二份。
- 试用记录中填“符合／问题描述”和耗时，不填写真实 SQL、数据库、用户或主机地址。
- 已有验收副本：`var/issue47/pgdata`。如不存在，按下面“重建副本”执行；不能直接启动 #45 原件。

## 准备与启动（必做）

```bash
cd /home/sfmon/code/sql-apm
export SQL_APM_DSN="host=$PWD/var/issue47/socket port=55474 dbname=sql_apm user=sql_apm"
/usr/pgsql-17/bin/pg_ctl -D var/issue47/pgdata status
```

`status` 若显示没有运行，执行：

```bash
/usr/pgsql-17/bin/pg_ctl -D var/issue47/pgdata -l var/issue47/server.log \
  -o "-p 55474 -k $PWD/var/issue47/socket -c listen_addresses=''" -w start
scripts/db/initialize.sh check --host "$PWD/var/issue47/socket" --port 55474 --pg-bin /usr/pgsql-17/bin
```

预期结构检查成功，库为1.10.0，仅启用本地独立socket。已经运行时只执行结构检查。

首次准备试用例子和行数快照：

```bash
.venv/bin/python scripts/db/prepare_search_trial.py examples --directory var/issue47/trial
.venv/bin/python scripts/db/prepare_search_trial.py before --directory var/issue47/trial
cp var/issue47/trial/baseline.sql var/issue47/trial/own.sql
vi var/issue47/trial/own.sql
cp var/issue47/trial/own.sql var/issue47/trial/own-variant.sql
vi var/issue47/trial/own-variant.sql
read -r -p '输入自己选择的单个关键词：' APM_TERM
read -r -p '输入第二个词：' APM_TERM2
read -r -p '输入连续片段（可有逗号、等号、空白）：' APM_PHRASE
```

编辑 `own.sql` 为自己选择的完整 SQL；第二份仅改变格式和可归一化的业务常量。
例子生成器只读基础表，写入本地文件。已有 `trial` 目录时保留它，使用另一个目录或直接复用文件，
不要为了重跑覆盖既有记录。行数快照会读取全部表，等待其结束后再测试。

定义记录函数，每次保留 JSON、实际耗时和退出码。
使用 Bash 内置的 `time`，无需安装 `/usr/bin/time`；命令的错误输出仍显示在终端。

```bash
trial() {
  local case_name="$1"
  shift
  local TIMEFORMAT='%R seconds'
  {
    time .venv/bin/python -m sql_apm search "$@" \
      > "var/issue47/trial/$case_name.json" 2>&3
  } 3>&2 2> "var/issue47/trial/$case_name.time"
  local result_code=$?
  jq . "var/issue47/trial/$case_name.json"
  cat "var/issue47/trial/$case_name.time"
  printf 'exit=%s\n' "$result_code"
}
```

若此前因缺少 `/usr/bin/time` 得到 `exit=127`，搜索尚未执行；重新粘贴上面的完整函数，
再重跑失败的 `trial ...` 命令即可，无需重新准备数据库或示例文件。

## 模糊入口（必做）

目的：验证自己选的词能找到候选，空白／大小写／符号的含义清晰。

```bash
trial fuzzy-one find "$APM_TERM"
trial fuzzy-many find "$APM_TERM $APM_TERM2"
trial fuzzy-phrase find "$APM_PHRASE" --mode passage
trial fuzzy-recent find "$APM_TERM" --order recent
trial fuzzy-none find zz_no_search_match_47
trial fuzzy-empty find ''
trial fuzzy-too-many find 'a a a a a a a a a a a a a a a a a a a a a'
```

预期：前几项 `match_kind=text`，各结构一行，最多50行且有 `total_structures`；默认记录数倒序，
recent 按最近时间倒序。多词可分散出现；`--mode passage` 把整个输入当作一段，去空白后需连续匹配，逗号／等号按字面。
引号在两种方式下都是普通字符（[#51](https://github.com/shenxg13/sql-apm/issues/51) 起，原“引号表示整段”的写法作废）。
无匹配为零结构；空输入与21项退出1，原因分别为 `empty_search_input`、`too_many_search_terms`。
请按需 `search text --sql-id` 查看候选原文，判断含符号片段是否符合自己的输入。

记录：单词＿＿；多词＿＿；整段／符号＿＿；排序＿＿；无匹配与非法输入＿＿；耗时＿＿。

## 精确检索与身份选择（必做）

目的：覆盖四种结果、格式／业务值归并和用户自选 SQL。

```bash
trial exact-own exact --file var/issue47/trial/own.sql
trial exact-variant exact --file var/issue47/trial/own-variant.sql
trial exact-baseline exact --file var/issue47/trial/baseline.sql
trial exact-only-records exact --file var/issue47/trial/records-only.sql
trial exact-not-seen exact --file var/issue47/trial/not-seen.sql
trial exact-unreliable exact --file var/issue47/trial/unreliable.sql
```

预期：准备的四份例子依次得到 `has_baseline`、`records_without_baseline`、`not_seen`、
`unreliable_fingerprint`；未出现过仍有指纹，无法生成可靠指纹有原因。
自己两份 SQL 的指纹应相同（只有既定可归一常量才合并；LIMIT等控制值不在此承诺中）。
如自己的 SQL 尚未入库，仍记录未命中是否清晰，再用 baseline 例子继续详情测试。
`records-only.sql` 生成器会报告是否找到该类例子；没有时在试用记录注明，保留合成验收覆盖。

选择自己的命中列表中一行；下面默认第0行，可把 `[0]` 改成所选行。自己未命中时把文件改为 `exact-baseline.json`：

```bash
APM_SELECTED=var/issue47/trial/exact-own.json
export APM_FP=$(jq -r .fingerprint "$APM_SELECTED")
export APM_CLUSTER=$(jq -r '.hits[0].scope_id' "$APM_SELECTED")
export APM_DATABASE=$(jq -r '.hits[0].database' "$APM_SELECTED")
export APM_USER=$(jq -r '.hits[0].execution_user' "$APM_SELECTED")
trial direct find "$APM_FP"
trial fuzzy-filter find "$APM_TERM" --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER"
```

预期：直查命中列表与精确检索一致；筛选后的文本候选只统计该身份的匹配记录。
命中行给出五类计时、未知计时、记录数、首末时间及是否有当前基线。

记录：四种状态＿＿；自选 SQL＿＿；改格式／业务值＿＿；身份选择＿＿；直查／筛选＿＿；耗时＿＿。

## 批次提示（必做）

目的：理解整个输入算一个请求，单条提示不等于批次命中。

```bash
cp var/issue47/trial/own.sql var/issue47/trial/batch.sql
printf '\n; SELECT * FROM sql_apm_issue47_never_seen_trial_20261007;\n' >> var/issue47/trial/batch.sql
trial batch exact --file var/issue47/trial/batch.sql
```

预期：整批未命中，`statement_hints` 中列出子语句结果；`hints_are_batch_match=false`。
自己输入原本是批次时提示可能多于两条；最多20条，超过时 `hints_truncated=true`。
选做：自行写21条完整语句确认截断；输入 `?`／`:name`／`#{x}` 确认不转换占位符。

记录：提示是否易懂＿＿；结果＿＿；耗时＿＿。

## 基线与版本（必做）

目的：选好身份后无须再输入 SQL，能查看摘要、其余层次和保留历史版本。

```bash
trial versions versions --cluster "$APM_CLUSTER"
trial baseline baseline --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER" --fingerprint "$APM_FP"
for layer in day week weekday hour; do
  trial "baseline-$layer" baseline --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER" --fingerprint "$APM_FP" --layer "$layer"
done
APM_VERSION=$(jq -r '.versions | map(select(.rules_match and (.results_cleaned|not))) | last | .build_id' var/issue47/trial/versions.json)
trial baseline-history baseline --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER" --fingerprint "$APM_FP" --version "$APM_VERSION"
trial versions-after versions --cluster "$APM_CLUSTER"
```

预期：摘要五类计时均出现，无样本明确标为 `no_samples`；17指标和基础／P95／P99条件可读。
不同层次按对应桶显示，版本中训练窗口、构建和发布时间清晰。查询前后 `is_current=true` 的版本不变。
历史规则不同／已清理条目仍显示但不能选；当前副本未必含这两类，选做输入对应版本观察明确错误，
合成自动化已覆盖这两个边界。没有当前基线的身份也可查看历史记录，不能把它误认为导入失败。

记录：摘要＿＿；四层＿＿；无样本解释＿＿；历史版本＿＿；当前指针＿＿；耗时＿＿。

## 执行历史（必做）

目的：检查默认7天、五类计时／未知计时、明细、翻页、小时／天汇总及训练判定。

```bash
trial executions executions --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER" --fingerprint "$APM_FP" --version "$APM_VERSION"
APM_START=$(jq -r .start_at var/issue47/trial/executions.json)
APM_END=$(jq -r .end_at var/issue47/trial/executions.json)
trial page-one executions --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER" --fingerprint "$APM_FP" --start "$APM_START" --end "$APM_END" --limit 2
APM_CURSOR=$(jq -c .next_cursor var/issue47/trial/page-one.json)
if [ "$APM_CURSOR" != null ]; then
  trial page-two executions --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER" --fingerprint "$APM_FP" --start "$APM_START" --end "$APM_END" --limit 2 --cursor "$APM_CURSOR"
fi
for bucket in hour day; do
  trial "timeline-$bucket" executions --cluster "$APM_CLUSTER" --database "$APM_DATABASE" --user "$APM_USER" --fingerprint "$APM_FP" --start "$APM_START" --end "$APM_END" --bucket "$bucket"
done
```

预期：明细包含时间、耗时、状态、单条／整批、来源定位和原文标识；不在每条附原文。
六组始终存在，Parse／Bind／Execute标明阶段或调用，不与请求整体相加；未知耗时是NULL。
两页无相同记录，汇总按状态计数且已知耗时的P50／P95／最大值合理。
训练判定区分参与、排除及原因、不在版本输入范围、无法复算；自己的数据不一定同时具有全部情形。
选做：把 START/END 改为自己关心的带时区时间，查看不同于训练窗口的历史。

失败记录（必做，使用之前生成的仅记录例子）：

```bash
APM_FAILURE=var/issue47/trial/exact-only-records.json
trial failures executions --cluster "$(jq -r '.hits[0].scope_id' "$APM_FAILURE")" \
  --database "$(jq -r '.hits[0].database' "$APM_FAILURE")" --user "$(jq -r '.hits[0].execution_user' "$APM_FAILURE")" \
  --fingerprint "$(jq -r .fingerprint "$APM_FAILURE")"
```

预期：失败／取消／超时在未知计时组，耗时和推算开始未知，不补零。

记录：默认窗口＿＿；字段＿＿；翻页＿＿；汇总＿＿；失败＿＿；训练判定＿＿；耗时＿＿。

## 整体确认与停止（必做）

```bash
.venv/bin/python scripts/db/prepare_search_trial.py after --directory var/issue47/trial
/usr/pgsql-17/bin/pg_ctl -D var/issue47/pgdata -m fast -w stop
```

预期 `all_business_row_counts_equal=true`。测试期间不要另行导入、构建或清理，否则先说明并发操作的影响。
记录：数据不变＿＿；各步骤耗时可接受性＿＿；JSON字段是否易懂＿＿；待解决问题＿＿。
性能10／3／1／5秒是目标，不是通过／失败的硬门禁。请将脱敏结论写入 Issue #47 或 PR，明确是否确认试用。

## 重建副本（仅副本不存在时，选做）

这两步全量扫描与复制成本较高，先确认 #45 数据目录关闭且有足够空间（本轮约56GiB原库）。
脚本拒绝覆盖已有输出，生成独立socket；原件不启动。

```bash
.venv/bin/python scripts/db/verify_search_full.py prepare --source-data var/issue45/development-real/pgdata --directory var/issue47
.venv/bin/python scripts/db/verify_search_full.py audit --directory var/issue47
```

完成后副本为关闭状态，回到“准备与启动”。独立全量核对报告和详细输出保留该忽略目录。
