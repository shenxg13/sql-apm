# v1.0.0 Kylin 成品编译验证

对应 [Issue #59](https://github.com/shenxg13/sql-apm/issues/59) 第一轮：从初始系统编译一次，
运行全部随包入口，导出后续各轮复用的唯一成品。日期为 2026-10-12，本报告均区分 measured 与未验证。

## 输入与目标机（measured）

用户提供可访问的验证机，说明仅补装 tar。执行前检查到 /data 为空、未安装项目服务、无 postgres 系统用户；
Kylin V10 SP2（Build09-20210524）、x86_64、glibc 2.28、8 vCPU、约 14 GiB 内存、4 GiB swap，
数据盘约 190 GiB，剩余约 189 GiB。SSH 只能核对当前状态，快照恢复由用户操作。

编译候选基于 `2e356c9a44baad209d0971de79d1ec7d55d98fcd`，归档 972,455,308 字节，
SHA-256 为 `6a498837886b5c8fa8246b4443ab4bba577473b8cc8fdc13997d25b0f22e51bb`。
传输前后独立核对归档摘要、PROGRAM_COMMIT 和全部文件清单；没有重新下载或改变源码、wheel、RPM、Grafana 与插件版本。

原始连接信息、完整命令和输出只保存在忽略目录 `var/issue59/target/compile-round/`。
本轮没有导入真实 SQL 日志、创建产品数据库或设置产品账号密码。

## 编译与验收（measured）

运行包内 `scripts/deployment/compile_runtime.sh /data/sql-apm`。该脚本只使用包内签名 RPM 源，
记录编译前后完整包清单、命令与输出，安装 Python 3.13.16、虚拟环境依赖与 PostgreSQL 17.10。
UTC 10:48:27 开始、10:55:06 完成，总耗时 399 秒，退出 0；不是性能门槛。

系统清单从 692 项变为 709 项：新增 18、升级 28、替代移除 hardlink 1 项。
升级的 28 个包与生产手册附录所列 #52 清单一致；原始清单为
`runtime-build/packages-before.tsv`、`packages-after.tsv`，差异为本地 `package-changes.json`。
离线安装不代表系统包未变；只有下一轮二进制安装才要求前后清单完全一致。

运行时自检的 16 个项目全部通过：Python 模块与全部已装依赖、postgres、initdb、psql、
临时实例查询 42 并停止清除、程序清单，以及十个随包入口。
Python 使用 OpenSSL 1.1.1f、SQLite 3.32.3；已装依赖为 pglast 7.18、psycopg2-binary 2.9.10、pip 26.2.1。
十个入口如下，全部退出成功，无跳过：

| 入口 | 结果 |
| --- | --- |
| unit | 104 项普通单元通过 |
| database | 数据库初始化、约束、权限与结构验证通过 |
| publication | 发布与恢复验证通过 |
| smoke | bootstrap、schema、check、full、rebuild、status、history 通过 |
| cleanup | 清理及拒绝情形通过 |
| search | 检索接口验证通过 |
| views | 查询视图与积木验证通过 |
| grafana | 只读数据库接口和看板查询验证通过 |
| daily | 每日运行编排验证通过 |
| daily-recovery | 每日运行恢复与故障验证通过 |

完整记录为 `runtime-build/checks/*.log` 和 `runtime-build/selfcheck.json`。
Grafana 入口验证数据库与查询契约，不表示本轮已安装 Grafana 服务或完成浏览器验收。
本轮按约定没有运行九任务、真实重启或用户安装。

## 唯一成品与证据（measured）

编译脚本退出、日志关闭后执行 `runtime_archive.py export`。成品目录已经复制回开发机，
与目标机给出的 SHA256SUMS 摘要一致，逐文件检查和归档结构检查通过。
本地保留路径为 `var/issue59/target/compile-round/runtime-artifact/`；构建日志另有
`build-records.tar.gz`，未复制源码编译中间目录。后续打包必须复用本目录，不能重新编译替换。

| 对象 | SHA-256 |
| --- | --- |
| runtime.tar.gz | `d271d6a1471c41723724b48afad59f9675e1b324747fbb7dfae283f97340314c` |
| runtime-manifest.json | `470e93f741a5d5f3cb82118baf632ac9468036fe6ffa5ccb698de430c4f92d0c` |
| SHA256SUMS | `cebc5746c8261721f24f35ee3b1de209f79e86cb604cfdd0160b9fa486a260aa` |

运行时归档 94,220,776 字节；成品目录共 31 个文件，合计 97,550,019 字节（含清单与构建证据）。
归档清单共 5,821 项：5,351 个文件、451 个目录、19 个符号链接；文件未压缩内容合计 341,810,641 字节。
成品只含三个约定运行时目录，不含产品数据、真实日志或凭据。保留构建证据中的测试数据均为合成用例。

## 固定前缀位置（measured）

对全部文件字节及链接目标扫描，共 154 个位置包含 `/data/sql-apm`：
Python 89、PostgreSQL 56、虚拟环境 9。下方是与 manifest 完全一致的完整相对路径。
其中启动脚本、激活脚本、pyvenv.cfg、配置元数据、链接与二进制内均有固定前缀。
字节命中可能来自运行路径、构建路径或调试信息；本次扫描不把所有命中推定为实际运行依赖。
安装仍只允许约定目录，未尝试重写这些引用或支持搬迁。

```text
app/.venv/bin/activate
app/.venv/bin/activate.csh
app/.venv/bin/activate.fish
app/.venv/bin/pgpp
app/.venv/bin/pip
app/.venv/bin/pip3
app/.venv/bin/pip3.13
app/.venv/bin/python3.13
app/.venv/pyvenv.cfg
postgresql-17.10/bin/clusterdb
postgresql-17.10/bin/createdb
postgresql-17.10/bin/createuser
postgresql-17.10/bin/dropdb
postgresql-17.10/bin/dropuser
postgresql-17.10/bin/ecpg
postgresql-17.10/bin/initdb
postgresql-17.10/bin/pg_amcheck
postgresql-17.10/bin/pg_archivecleanup
postgresql-17.10/bin/pg_basebackup
postgresql-17.10/bin/pg_checksums
postgresql-17.10/bin/pg_combinebackup
postgresql-17.10/bin/pg_config
postgresql-17.10/bin/pg_controldata
postgresql-17.10/bin/pg_createsubscriber
postgresql-17.10/bin/pg_ctl
postgresql-17.10/bin/pg_dump
postgresql-17.10/bin/pg_dumpall
postgresql-17.10/bin/pg_isready
postgresql-17.10/bin/pg_receivewal
postgresql-17.10/bin/pg_recvlogical
postgresql-17.10/bin/pg_resetwal
postgresql-17.10/bin/pg_restore
postgresql-17.10/bin/pg_rewind
postgresql-17.10/bin/pg_test_fsync
postgresql-17.10/bin/pg_test_timing
postgresql-17.10/bin/pg_upgrade
postgresql-17.10/bin/pg_verifybackup
postgresql-17.10/bin/pg_waldump
postgresql-17.10/bin/pg_walsummary
postgresql-17.10/bin/pgbench
postgresql-17.10/bin/postgres
postgresql-17.10/bin/psql
postgresql-17.10/bin/reindexdb
postgresql-17.10/bin/vacuumdb
postgresql-17.10/include/pg_config.h
postgresql-17.10/include/server/pg_config.h
postgresql-17.10/lib/libecpg.so.6.17
postgresql-17.10/lib/libecpg_compat.so.3.17
postgresql-17.10/lib/libpgcommon.a
postgresql-17.10/lib/libpgcommon_shlib.a
postgresql-17.10/lib/libpgport.a
postgresql-17.10/lib/libpgport_shlib.a
postgresql-17.10/lib/libpgtypes.so.3.17
postgresql-17.10/lib/libpq.a
postgresql-17.10/lib/libpq.so.5.17
postgresql-17.10/lib/libpqwalreceiver.so
postgresql-17.10/lib/pgoutput.so
postgresql-17.10/lib/pgxs/src/Makefile.global
postgresql-17.10/lib/pgxs/src/test/isolation/isolationtester
postgresql-17.10/lib/pgxs/src/test/isolation/pg_isolation_regress
postgresql-17.10/lib/pgxs/src/test/regress/pg_regress
postgresql-17.10/lib/pkgconfig/libecpg.pc
postgresql-17.10/lib/pkgconfig/libecpg_compat.pc
postgresql-17.10/lib/pkgconfig/libpgtypes.pc
postgresql-17.10/lib/pkgconfig/libpq.pc
python-3.13.16/bin/idle3.13
python-3.13.16/bin/pip3
python-3.13.16/bin/pip3.13
python-3.13.16/bin/pydoc3.13
python-3.13.16/bin/python3.13
python-3.13.16/bin/python3.13-config
python-3.13.16/lib/libpython3.13.a
python-3.13.16/lib/pkgconfig/python-3.13-embed.pc
python-3.13.16/lib/pkgconfig/python-3.13.pc
python-3.13.16/lib/python3.13/_sysconfigdata__linux_x86_64-linux-gnu.py
python-3.13.16/lib/python3.13/config-3.13-x86_64-linux-gnu/Makefile
python-3.13.16/lib/python3.13/config-3.13-x86_64-linux-gnu/libpython3.13.a
python-3.13.16/lib/python3.13/config-3.13-x86_64-linux-gnu/python-config.py
python-3.13.16/lib/python3.13/config-3.13-x86_64-linux-gnu/python.o
python-3.13.16/lib/python3.13/lib-dynload/_asyncio.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_bisect.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_blake2.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_bz2.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_codecs_cn.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_codecs_hk.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_codecs_iso2022.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_codecs_jp.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_codecs_kr.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_codecs_tw.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_contextvars.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_csv.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_ctypes.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_ctypes_test.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_curses.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_curses_panel.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_datetime.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_decimal.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_elementtree.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_hashlib.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_heapq.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_interpchannels.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_interpqueues.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_interpreters.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_json.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_lsprof.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_lzma.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_md5.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_multibytecodec.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_multiprocessing.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_opcode.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_pickle.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_posixshmem.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_posixsubprocess.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_queue.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_random.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_sha1.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_sha2.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_sha3.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_socket.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_sqlite3.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_ssl.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_statistics.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_struct.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testbuffer.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testcapi.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testclinic.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testclinic_limited.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testexternalinspection.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testimportmultiple.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testinternalcapi.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testlimitedcapi.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testmultiphase.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_testsinglephase.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_uuid.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_xxtestfuzz.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/_zoneinfo.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/array.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/binascii.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/cmath.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/fcntl.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/grp.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/math.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/mmap.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/pyexpat.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/readline.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/resource.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/select.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/syslog.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/termios.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/unicodedata.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/xxlimited.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/xxlimited_35.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/xxsubtype.cpython-313-x86_64-linux-gnu.so
python-3.13.16/lib/python3.13/lib-dynload/zlib.cpython-313-x86_64-linux-gnu.so
```

## 未验证与接续

本轮测得的是升级编译依赖之后的运行结果。初始系统库上的二进制安装、系统包不变、
错误前缀与真实缺库反例、九任务与基准比对、每日运行／定时器／传输、四看板、账号及真实重启，
必须在用户恢复初始快照后另行执行；用户亲自安装及明确确认是第三轮，不能由本报告替代。
生产机、真实 MPP master、三十天首批成本和 32 GB 参数仍未验证。尚未合并或正式发布。
