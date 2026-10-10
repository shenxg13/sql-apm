# Issue #54 开发机试看步骤与记录

用于合并前的 D22：实施完成后、提请独立评审之前，由用户查看“运行状态”看板和一次模拟的逐日运行并确认。随 PR 保存，
不随程序包交付。自动化检查不能代替这次查看。真实的 SQL 原文、库名、用户名和结果留在开发机上，不要粘到 Issue 或 PR 里。
需要改动的地方按范围变更流程处理。每条命令的含义见[每日运行操作说明](daily-run.md)。

## 环境

环境在 Git 忽略的 `var/issue54/look/` 下。数据库是“逐日回放”得到的那一份的副本：本地 55 个真实日志文件按日期一天一天放标记、
一天执行一次每日运行，共 36 次运行（集群 `119` 是 2026 年 7 月的 29 天，集群 `120` 是 9 月 13 日至 19 日的 7 天）。
在它上面又加了一个合成的集群 `121`，用来看集群不止两个时的样子。

```bash
cd /home/sfmon/code/sql-apm
.venv/bin/python scripts/grafana/setup_dev.py existing --directory var/issue54/look \
  --pgdata var/issue54/replay/daily/pgdata --admin-user apm_admin --pg-port 55540 \
  --downloads var/issue51/downloads
```

- 脚本可以重复执行，结果相同。开发机重启后重新执行同一条命令即可；停止用 `setup_dev.py stop --directory var/issue54/look --pg-port 55540`。
- 在 Windows 的浏览器里打开 `http://127.0.0.1:3000/`，用 `admin` 或 `viewer` 登录，密码在
  `var/issue54/look/private/admin-password` 和 `viewer-password` 里。
- “运行状态”在 `MPP` 文件夹里；“SQL 检索”和“SQL 列表”页面右上角也有到它的链接。请把浏览器窗口调到 1920×920 左右看一遍布局。

下面的命令都在这个终端环境里执行：

```bash
cd /home/sfmon/code/sql-apm
export SQL_APM_DSN="host=$PWD/var/issue54/look/socket port=55540 dbname=sql_apm user=sql_apm"
LOOK=$PWD/var/issue54/look
```

每日运行的配置是 `var/issue54/look/daily/daily.json`，三个接收目录是 `var/issue54/look/inbox/119`、`120` 和 `121`。
为了让动手的步骤不必等很久，这份配置把集群 `120` 的构建间隔设成了 7 天（它的一次构建要十分钟左右），其余都是默认值。
`var/issue54/look/prepared/` 里是为下面各步准备好的合成日志文件（内容是合成的，不是真实日志）。

## 看一遍已有的记录

| 编号 | 看什么 | 预期 | 结果 |
| --- | --- | --- | --- |
| V1 ★ | 打开“运行状态” | 最上面四个数（上次运行的开始时间、结果、上次成功的运行、待处理问题数）；下面依次是各集群现状、待处理问题列表、最近的运行记录。1920×920 的窗口里前三块完整可见，表头都在一行里且不被截断，表格不需要横向拖动 | |
| V2 ★ | “各集群现状” | 三个集群各一行：最新已导入日期、当前版本的截止日和发布时间、上次运行在该集群的结果、待处理问题数 | |
| V3 ★ | “最近的运行记录”，把“显示最近多少次运行”改成 50 | 能看到逐日回放的那些运行：每次运行里一个集群导入了哪一天、构建发布的截止日、原始文件删除的文件数和大小、耗时。`119` 的日志早于 45 天，所以每天导入成功后它的文件随即被删除；`120` 的不到 45 天，没有删除 | |
| V4 | 命令行查询 `.venv/bin/python -m sql_apm daily status --limit 3` | 一个 JSON，内容与页面相同：`clusters`、`problems`、`runs` | |
| V5 | 从“SQL 列表”页面右上角点“运行状态” | 跳到运行状态；再点“SQL 检索”能回去 | |

## 动手做一次

每一步后刷新页面（它也会每分钟自己刷新）。带 ★ 的是必做。

### 一、正常的一天 ★

```bash
cp $LOOK/prepared/a/119/* $LOOK/inbox/119/ && touch $LOOK/inbox/119/2026-08-01.complete
cp $LOOK/prepared/a/121/* $LOOK/inbox/121/ && touch $LOOK/inbox/121/2026-10-08.complete
.venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json
```

预期：命令逐行输出 JSON，约两分钟后结束，退出码 0（`echo $?`）。页面上：上次运行的结果为“完成（手工启动）”；`119` 的最新已导入日期和
当前版本的截止日都变成 2026-08-01，运行记录里这一行“导入成功的日期”是 08-01、“原始文件删除”是“已删除 1 天 1 个文件”；
`121` 导入 10-08 并发布；`120` 没有新的事。结果：

### 二、几种需要人处理的情况 ★

```bash
cp $LOOK/prepared/b/120/* $LOOK/inbox/120/                      # 120 的 9 月 20 日：有文件，不放标记
touch $LOOK/inbox/119/2026-08-02.complete                        # 119：有标记，没有文件
touch $LOOK/inbox/121/$(date +%F).complete                       # 121：标记的日期是今天
cp $LOOK/prepared/b/121/* $LOOK/inbox/121/ && touch $LOOK/inbox/121/2026-10-09.complete
chmod 000 $LOOK/inbox/121/gpdb-2026-10-09_000000.csv             # 121 的 10 月 9 日：文件读不了
echo x > $LOOK/inbox/121/notes.txt                               # 一个不相干的文件
.venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json; echo "exit=$?"
```

预期：退出码 1。页面上“待处理问题数”为 4，列表里是：`120` 有文件但没有齐全标记（09-20）、`119` 有齐全标记但没有文件（08-02）、
`121` 标记日期不早于今天、`121` 导入失败或有冲突的日期（10-09，文件读不了），每条后面有“怎样处理”。上次运行的结果是“完成，有失败”。
运行记录里 `121` 这一行“其他文件数”是 1。结果：

### 三、处理掉，再运行一次 ★

```bash
touch $LOOK/inbox/120/2026-09-20.complete                        # 确认 120 那天的文件齐全
rm $LOOK/inbox/119/2026-08-02.complete $LOOK/inbox/121/$(date +%F).complete $LOOK/inbox/121/notes.txt
chmod 644 $LOOK/inbox/121/gpdb-2026-10-09_000000.csv
.venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json; echo "exit=$?"
```

预期：退出码 0，“待处理问题数”回到 0，列表显示“（没有待处理的问题）”。`121` 自动重试并导入了 10-09，随后发布；
`120` 导入了 09-20，但“构建发布”一栏是“未到构建间隔”（这份配置里它的间隔是 7 天，最新日期只比当前版本的截止日晚 1 天）。结果：

### 四、同一时间只有一个，集群正忙时跳过

```bash
cp $LOOK/prepared/c/119/* $LOOK/inbox/119/ && touch $LOOK/inbox/119/2026-08-02.complete
.venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json > /tmp/first-run.log &
sleep 3; .venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json; echo "second exit=$?"; wait
```

预期：第二个立即输出 `daily_run_active` 并以 1 退出；第一个照常做完（约两分钟），导入并发布 08-02。页面上只多出一次运行。结果：

### 五、中止一次运行

```bash
cp $LOOK/prepared/d/119/* $LOOK/inbox/119/ && touch $LOOK/inbox/119/2026-08-03.complete
.venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json &
sleep 2; kill %1; wait %1; echo "exit=$?"
```

预期：退出码 130，通常一秒之内退出，命令正在执行一条较长的语句时也一样。页面上上次运行的结果是“被中止”，
待处理问题里多一条“上次运行没有正常结束”。再执行一次命令（不带 `&`），它接着把 08-03 做完，问题消失。结果：

### 六、导入之后文件被改动

```bash
cp $LOOK/prepared/a/121/gpdb-2026-10-08_000000.csv /tmp/kept.csv
sed -i 's/SELECT/select/' $LOOK/inbox/121/gpdb-2026-10-08_000000.csv     # 大小不变，内容变了
.venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json; echo "exit=$?"
cp /tmp/kept.csv $LOOK/inbox/121/gpdb-2026-10-08_000000.csv              # 换回导入时的那一份
.venv/bin/python -m sql_apm daily run --config $LOOK/daily/daily.json; echo "exit=$?"
```

预期：第一次退出码 1，待处理问题里多一条 `121` 的“导入失败或有冲突的日期”（10-08，导入之后文件有增减或内容变化），文件没有被删除；
第三个数“上次成功的运行”仍是上一次成功的那次，不随这次“完成，有失败”的运行变化。换回原文件后第二次退出码 0，问题消失。结果：

## 定时器和传输脚本

这两部分已由自动化在开发机实测（用户级 systemd 上的临时单元、回环地址上以普通用户运行的 sshd），结果在
[实测报告](../reports/daily-run-2026-10-10.md)里；目标机上的安装和实测属于 #52。想看生成的单元文件长什么样：

```bash
.venv/bin/python scripts/daily/install.py --output /tmp/sql-apm-units --config $LOOK/daily/daily.json \
  --dsn "$SQL_APM_DSN" --user "$USER" --at 17:00
cat /tmp/sql-apm-units/sql-apm-daily.service /tmp/sql-apm-units/sql-apm-daily.timer
```

## 需要你知道的实现选择

这些是实施时的处理，不改动契约文字；有不同意见可以在查看时提出。

1. **上次运行的时间和结果放在最上面，只显示一次。** 契约第 38 项把它列在“每个集群一行”里，第 40 项又要求描述整张表的数值不做成每行相同的一列。
   各集群表里保留的是“上次运行在该集群的结果”和“该集群的待处理问题数”，这两项每行不同。
2. **其余三个看板的右上角加了到“运行状态”的链接。** 契约没有要求，为的是不必去文件夹里找。
3. **齐全标记叫 `年-月-日.complete`，命令叫 `daily run` 和 `daily status`。** 这两处契约留给实施确定。
4. **连接存活检查的间隔放在环境变量里**（`SQL_APM_CONNECTION_CHECK_SECONDS`），因为现有各命令没有共同的配置文件。
   定时时刻和最长运行时间写在 systemd 单元里，由生成脚本的参数给出。
5. **已导入的文件有没有变化按内容判断**（R1 整改后；原先按文件名和大小判断，大小不变的改动会被漏掉）。为了不每天重读全部文件，
   设备号、inode、大小和两个时间都与上次核对时相同的文件视为没动过；任何一个变了就重读并与导入时保存的校验值比较；删除之前一律重读。
6. **上一次运行期间到了下一个定时时刻**：不会同时启动第二个；上一次结束后 systemd 会立即补跑一次（实测）。这次补跑通常无事可做。
7. **整窗没有有效样本的集群每次运行都会再构建一次**，直到有了样本。这是“条件仍然成立就重试”的直接结果。
8. **导入一天早于保留天数的旧日志时，它的文件在同一次运行里就被删除。** 保留天数是按数据日期算的。
9. **包清单里加入了每日运行的程序文件**，否则程序包里的命令无法导入、配置指南引用的脚本不在包内。验收包的检查入口、
   随包手册章节和目标机实测仍由 #52 承担。
10. **数据库结构说明的表清单补上了五张新表**，因为既有的文档检查要求表清单与结构一致。图示、函数和版本沿革三部分没有动，由 #52 更新。

## 用户查看之后的变化（R1 整改）

用户在 2026-10-10 查看的是整改之前的版本。独立评审第一轮提出 8 项问题，整改后页面和命令的这些地方与当时看到的不同：

- 最上面第三个数由“上次正常结束的运行”改为“上次成功的运行”：只有跑完并且没有任何失败或被跳过集群的运行才算。
  待处理问题“太久没有正常结束的运行”相应改为“太久没有成功的运行”。
- 待处理问题不再因为一次运行跳过了集群或中途停下而消失；要等后面的运行真正处理掉。
- 已导入的日期的文件有没有变化按内容判断（上面第 5 条）；删除原始文件之前把文件重读核对。
- 集群正被占用时，这次运行不删除它的原始文件。删除中途被打断时下一次运行接着删完。
- 停止信号立即生效，命令正在等语句时也一样；定时启动的运行到达最长运行时间后记为“被中止”，不再是“未正常结束”。
- 传输脚本不再重新拉取或改动已有齐全标记的日期。生成单元文件时，URI 形式连接串里的密码同样被拒绝。

查看环境 `var/issue54/look/` 已随整改更新到同一份程序和结构，上面的步骤（含新增的第六步）可以照做。

## 记录

| 日期 | 查看人 | 结论 | 备注 |
| --- | --- | --- | --- |
| 2026-10-10 | 用户 | 确认，同意提交 R0 | 没有提出修改；“动手做一次”的五步没有在查看环境里执行，由实施会话在合成数据的副本上演练过。记录见 [Issue 评论](https://github.com/shenxg13/sql-apm/issues/54#issuecomment-6094912194) |
