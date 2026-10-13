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

1. 帮助、提示、错误均可读；未设置 DSN 也能运行。
2. 候选先给这次检索、筛选、排序、结构总数和显示行数；宽窗口每个候选一行，80 列没有表格溢出。
3. 选 `1` 看详情：完整指纹、原文、身份、当前基线与样本不足说明、最近执行。长原文不会被程序强制折行。
4. 详情按 `i 序号` 换可用身份，`t 类别` 换提示中的计时类别，`f` 看全文，`b` 回列表，`n` 开始新检索，`q` 退出。
5. 用自己熟悉的词和片段分别走按词、整段；完整 SQL 可从本地熟悉的 SQL 粘贴。
   按词回车结束；整段／完整 SQL 粘贴完后再按一次回车、Ctrl-D。空行、引号、反斜杠、制表符和中文应保留。
6. 试空输入、21 个词、无结果、无效标记；错误之后交互仍可继续。Ctrl-C 取消当前步骤，之后可按提示退出。

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
