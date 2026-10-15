# sqlfind 开发机试用

用于 [Issue #62](https://github.com/shenxg13/sql-apm/issues/62) 的 C28 用户试用，正式请求评审前完成。
本页供开发机使用，不是生产安装或离线包手册。真实 SQL、身份和结果只在本机查看，不粘到公开 Issue。

## 已准备的环境

程序目录为 `/home/sfmon/code/sql-apm`，入口是根目录 `sqlfind`。
试用库位于 `var/issue62/trial/pgdata`，只监听本机私有 socket，端口 55462。
选取原样复制的两份较小完整日志，共 23,744 条记录、1,860 份原文；不是全量数据库或完整生产训练窗口。
副本、来源文件路径、字节数、SHA-256 和构建记录保存在 `var/issue62/trial/provenance.json`，目录由 Git 忽略。
原始样本未改动；只读密码保存在该目录的 0600 `pgpass` 中。

检查或重新启动已有试用实例：

```bash
cd /home/sfmon/code/sql-apm
.venv/bin/python -B scripts/sqlfind/prepare_trial.py status
# 仅在未运行时启动
.venv/bin/python -B scripts/sqlfind/prepare_trial.py start
```

需要重新准备时，使用新 `--directory`，给出一到四个完整本地文件的 `--sample`。
脚本只在仓库 `var/` 内创建环境，拒绝覆盖已有目录；不复制或重建全量库。

## 由 sfmon 执行一次配置

本轮自动检查只使用临时 home，尚未替真实 `sfmon` 修改登录文件。
请在开发机以 `sfmon` 执行以下命令，工具会列出改动的 home 文件：

```bash
/home/sfmon/code/sql-apm/.venv/bin/python -B \
  /home/sfmon/code/sql-apm/scripts/sqlfind/configure.py install \
  --host /home/sfmon/code/sql-apm/var/issue62/trial/socket \
  --port 55462 --database sql_apm --user sql_apm_ro \
  --passfile /home/sfmon/code/sql-apm/var/issue62/trial/pgpass
```

不需要输入密码，不会更改已有的 `psql` 设置。相同配置可重复执行。
关闭并重新登录一个 PuTTY 会话，以确认新的登录环境已生效；若当前会话手动设置过
`SQL_APM_DSN` 或 `PGPASSFILE`，新会话应不带这些临时覆盖值。

## 用 PuTTY 试用

PuTTY 的 Window → Translation → Remote character set 选择 **UTF-8**；
主机使用 UTF-8 locale。先用 80 列 × 24 行，再最大化窗口各看一次。
内容不分屏，通过滚轮回看；若回滚内容不足，可在 PuTTY 的 Window 设置增加 scrollback 行数。

在程序目录以外开始：

```bash
cd /tmp
sqlfind --help
sqlfind 'select'
sqlfind 'select' --show 1
sqlfind 'select' --show 1 --limit 5
sqlfind
```

按顺序确认：

1. 帮助、提示、错误均可读；未设置 DSN 时使用已安装的只读连接配置。
   启动 `sqlfind` 后在方式菜单输入 `?` 看交互帮助；候选列表和详情菜单也各试一次。
   三处页面的常驻命令列表都应每个命令一行；详情中 `v`、`v 编号`、`v matched`、`v all` 分别显示。
   帮助按操作位置分组，每个命令一行，打印后仍可继续当前操作；`o` 调整排序，`l` 只重看列表。
   在详情设置历史范围、选择原文后查看帮助，用 `f` 核对原文选择；再用 `h` 回车保留各项，核对历史设置仍保留。
   检索输入中的问号仍是原文；程序外的 `sqlfind --help` 无需连接配置即可查看启动参数。
2. 候选先给这次检索、筛选、排序、结构总数和显示行数；宽窗口每个候选一行，80 列没有表格溢出。
3. 选 `1` 看详情：完整指纹、原文、身份、当前基线与样本不足说明、最近执行。长原文不会被程序强制折行。
4. 详情按 `i 序号` 换可用身份，`t 类别` 换提示中的计时类别，`f` 看全文，`b` 回列表，`n` 开始新检索，`q` 退出。
5. 用自己熟悉的词和片段分别走按词、整段；完整 SQL 可从本地熟悉的 SQL 粘贴。
   按词回车结束；整段／完整 SQL 粘贴完后再按一次回车、Ctrl-D。空行、引号、反斜杠、制表符和中文应保留。
6. 试空输入、21 个词、无结果、无效标记；错误之后交互仍可继续。Ctrl-C 取消当前步骤，之后可按提示退出。
7. 在详情输入 `t requesx`，按退格删除 `x`、补上 `t`，回车后应切换到请求整体（需在可用类别里）。
   退格应同时擦掉屏幕字符；也可在检索输入里试中文退格。方向键目前忽略，不移动光标或调取历史，
   不应出现 `[A`、`[B` 等字符。更新代码后需退出并重新启动 `sqlfind` 再试。
8. 在详情输入 `h`，按提示把条数改为 `100`，时间范围选 `2`，填开始 `2026-09-19 23:00:00`、
   结束 `2026-09-20 00:00:00`，历史排序选 `1`；应看到这个北京时间范围内最新的最多 100 条，包含开始、不包含结束，
   并显示范围内总数和本次实际显示数。按样本日期选择范围，没有记录时应显示 0 条。
   换身份、计时或开始新检索后设置应保留，当前基线及训练窗口不随历史范围改变。
   再输入 `h` 后用回车保留；试非法条数或日期，应能重新输入；用 `q` 或 Ctrl-C 取消，应保留原设置。
   在设置提示中输入 `r` 恢复 20 条、最近一次执行往前 7 天与最新排序。退出程序后设置不保存。

9. 在候选列表输入 `o`，选 `2` 最近执行，检查排序说明和候选顺序；开始新检索后应保留排序。
   `o` 中回车保留，q／Ctrl-C 取消；重排后的候选标记可能变化，选择前重新看列表。
10. 选一个原文数大于 1 的候选，输入 `v`。核对“本次命中共多少份”和“当前身份、计时和时间内出现多少份”分开显示。
    原文表包含编号、当前范围记录数、最近执行及“原文不同部分”（共同开头、结尾省略）。
    选择开头相同、取值不同的 SQL，确认可从差异列区分，包括当前记录数为 0 的原文；较长差异中间明确标记省略。输入 `v 编号`（例如 `v 3`，以实际编号为准），
    应显示这一份原文及它的执行历史；`f` 看全文。历史表每行的原文编号须能对应列表。
11. 输入 `h`，保持或调整条数、时间，最后选 `2` 最慢在前。确认按耗时从大到小，而非只对原来显示的记录重排。
    输入 `v matched` 回到本次命中的原文，`v all` 看整个结构，再用 `v` 查看相应目录。
    切原文保留时间、排序和条数；换身份／计时保留原文选择；空范围明确显示 0 条，基线不变。
    试无效原文编号和 Ctrl-C 取消查询，应保留原选择；返回列表再选同一 SQL 也应保留选择。
12. 连续执行 `v`、`f`、`h` 等操作，用滚轮回看：每段都应有操作编号、命令名及起止标记，结束之后才出现操作菜单。
    刚启动时选择方式应直接进入输入，提交检索才出现“操作 1”；新检索和退出不应出现空框或消耗编号，`b` 重显列表仍有边界。
    试无效编号、取消设置或 Ctrl-C 取消查询，确认提示位于同一输出段内，结束后仍可继续操作。
    `f` 只显示原文全文及编号／字符数等说明，不重复身份、基线和历史；单份选择显示该份，集合范围显示一份示例。
    较长原文仍完整显示，“原文开始／结束”间不插入输出边界或强制折行。

非交互可按相同选择核对原文及历史。先查看原文列表，再使用列表中的编号；每次调用重新检索，数据变化可能改变编号。

```bash
./sqlfind --help
./sqlfind 'select' --show 1 --view originals --original all
./sqlfind 'select' --show 1 --original 1 --view text
./sqlfind 'select' --show 1 --original 1 --view history --limit 100 \
  --history-order slowest \
  --history-start '2026-09-19 00:00:00' --history-end '2026-09-20 00:00:00'
```

`--help` 应分通用启动参数、非交互查询参数、进入交互模式，并说明在菜单中用 `?` 看详细操作。
用交互的同一结构、身份、计时、原文、时间和排序对照结果；独立历史和原文列表须给出必要上下文，且没有操作编号或菜单。
`--start`／`--end` 仍只筛选候选；历史时间用上述独立参数。省略 `--view` 仍打印整页详情，
`--full-text` 只让详情原文不截断，`--view text` 才是单独全文。默认历史仍为最近一次执行往前 7 天、最新 20 条。

一次输出也支持文件、管道和 here-document，例如：

```bash
sqlfind --mode exact <<'SQL'
SELECT id, name
FROM example_table WHERE status = 'ready';
SQL
```

这个合成 SQL 通常没有命中，退出 0 属于正常。两次检索之间标记可能变化，固定对象可直接输入其结构指纹。
默认只读账号单条查询超时为 120 秒；试用样本规模不代表全量性能。

## 撤销与停止

```bash
/home/sfmon/code/sql-apm/.venv/bin/python -B \
  /home/sfmon/code/sql-apm/scripts/sqlfind/configure.py uninstall
/home/sfmon/code/sql-apm/.venv/bin/python -B \
  /home/sfmon/code/sql-apm/scripts/sqlfind/prepare_trial.py stop
```

撤销仅去掉 sqlfind 自己的设置，密码文件和试用数据保留；重新登录后生效。
如果登录片段被人工改动，工具会拒绝猜测覆盖，请先核对该片段再撤销。

## 确认记录

试用后在 Issue #62 留下确认，注明 PuTTY 版本、UTF-8、80×24 与最大化窗口是否均正常、
三种输入与交互是否符合预期；遇到问题只描述操作、症状和脱敏信息。
自动验证通过不能代替本项，未收到用户确认前不请求正式评审。
