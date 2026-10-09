# Grafana 检索与看板开发说明

本说明服务开发、评审和后续的制包工作，不随程序包交付。已确认范围以
[Issue #51](https://github.com/shenxg13/sql-apm/issues/51) 为准；离线安装、使用者文档和目标机实测由
[Issue #52](https://github.com/shenxg13/sql-apm/issues/52) 交付。检索规则和基础查询函数见
[SQL 检索与查询层开发说明](sql-search.md)。

## 组成与文件位置

| 内容 | 位置 | 进程序包 |
| --- | --- | --- |
| 固定的版本、下载地址和摘要（清单） | `grafana/components.json` | 是 |
| Grafana 配置模板 | `grafana/grafana.ini.template` | 是 |
| 数据源、看板装入的配置模板 | `grafana/provisioning/*.template` | 是 |
| 三个随包看板 | `grafana/dashboards/mpp-*.json` | 是 |
| systemd 单元文件模板 | `grafana/systemd/*.template` | 是 |
| 安装：核对摘要、解压、生成配置；创建账号和文件夹 | `scripts/grafana/install.py` | 是 |
| 设置只读账号密码 | `scripts/grafana/readonly_password.py` | 是 |
| 指纹服务 | `sql_apm/service/fingerprint.py` | 是 |
| 看板生成程序（三个 JSON 的来源） | `scripts/grafana/build_dashboards.py` | 否 |
| 下载并核对安装文件（联网） | `scripts/grafana/fetch.py` | 否 |
| 开发机搭建 | `scripts/grafana/setup_dev.py` | 否 |
| 界面端到端检查及其浏览器准备 | `scripts/grafana/verify_e2e.py`、`prepare_browser.sh` | 否 |

`install.py` 不联网，#52 可直接用于离线安装：把清单所列的四个文件放进一个目录即可。

## 固定的组件

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Grafana | 13.2.3 | 官方 Linux x86_64 安装包，解压即用 |
| `grafana-postgresql-datasource` | 13.0.4 | 官方 PostgreSQL 数据源。Grafana 13 的安装包不含它，联网时会在启动时自动下载 |
| `volkovlabs-form-panel`（Business Forms） | 6.3.5 | 检索页的多行输入框和方式开关 |
| `yesoreyeram-infinity-datasource`（Infinity） | 4.1.1 | 由 Grafana 后端把完整 SQL 转发给指纹服务 |

配置里 `preinstall_disabled = true`，Grafana 启动时不下载任何插件；`allow_loading_unsigned_plugins`
为空，签名校验保持开启。不得往插件目录里添加任何文件：Grafana 按签名清单核对目录内容，多一个文件
就拒绝加载。升级 Grafana 时须同时核对三个插件的兼容版本并更新清单。

## 安装步骤与目录

```bash
# 1. 管理员创建只读账号（与项目账号同一步；对旧库也执行一次），项目账号建表或升级并授权
scripts/db/initialize.sh all --host SOCKET --port PORT --admin-user ADMIN --admin-database postgres
# 2. 操作者把只读账号的密码写进自己的文件（0600），由管理员连接设置
python scripts/grafana/readonly_password.py --admin-dsn 'host=SOCKET port=PORT dbname=postgres user=ADMIN' \
  --port PORT --password-file PRIVATE/db-password --passfile PRIVATE/service-pgpass
# 3. 核对摘要、解压、生成配置（不联网）
python scripts/grafana/install.py files --home HOME --files FILES --pg-port PORT \
  --db-password-file PRIVATE/db-password --admin-password-file PRIVATE/admin-password \
  --service-passfile PRIVATE/service-pgpass
# 4. 启动指纹服务和 Grafana（开发机直接启动；目标机用 HOME/systemd 下生成的单元文件）
# 5. 创建查看账号和“用户自定义”文件夹，设置默认主页、时区和语言
python scripts/grafana/install.py accounts --admin-password-file PRIVATE/admin-password \
  --viewer-password-file PRIVATE/viewer-password
```

`HOME` 下的布局：`grafana-13.2.3/`（解压的发行文件）、`plugins/`、`conf/`（`grafana.ini` 和
`provisioning/`）、`dashboards/mpp/`（三个随包看板）、`data/`（Grafana 自己的文件数据库
`grafana.db`，账号、文件夹和使用者自己的看板都在里面）、`logs/`、`systemd/`（生成好的单元文件）。
重复执行 `files` 只覆盖配置文件和三个随包看板，不动 `data/`。密码只从文件读取，
配置里以 `$__file{…}` 引用，不出现在参数、生成的文件和输出里。

开发机用 `scripts/grafana/setup_dev.py synthetic|existing --directory DIR` 一条命令完成上述全部步骤，
见[试用文档](../runbooks/grafana-trial.md)。

## 只读账号

- `initialize.sh bootstrap` 由管理员创建第二个可登录角色，默认名为项目账号加 `_ro`
  （`--readonly-role` 可改），并设置三项默认值：单条查询超时（`--readonly-timeout`，默认 `120s`）、
  `default_transaction_read_only=on`、在项目库内的 `search_path`。再次执行 bootstrap 可修改超时。
- 授权属于结构 1.11.0：项目账号把 schema 的 USAGE 和每张非分区表的 SELECT 授给它。分区经父表读取，
  之后新建的结果分区不需要授权。结构检查会比对授权，丢失的授权在 `schema` 时补回、在 `check` 时报出。
- 默认值可以被会话修改，真正的防线是权限：没有任何写权限，也不拥有任何对象；bootstrap 同时收回了
  项目库上 PUBLIC 的 TEMPORARY 权限，所以它连临时表也建不了。
- 升级旧库的顺序：先 `bootstrap`（管理员），再 `upgrade`（项目账号）。

## 指纹服务

`python -m sql_apm fingerprint-service [--port 3001] [--schema sql_apm]`，连接串取 `SQL_APM_DSN`，
密码取 `PGPASSFILE`。只接受回环地址；`--host` 给出其他地址会被拒绝。

| 请求 | 说明 |
| --- | --- |
| `POST /v1/exact` | 请求体 `{"sql_b64": "…", "cluster": null, "database": null, "user": null}`；`sql_b64` 是 SQL 的 UTF-8 字节的 base64（标准或 URL 安全写法均可） |
| `GET /v1/health` | 返回当前装入的规则和输入上限 |

`/v1/exact` 的回答与 `search exact` 的 JSON 相同，另加三项：`exact_sql_id`（库里与输入一字不差的原文
的标识，没有则为空）、`rules`（装入的规则，含 `normalization_id`）、`input`（收到的字节数和 SHA-256）。

- 规则在启动时装入；产品升级后须重启服务。检索页发现服务的规则与库里当前版本的规则不同时会提示。
- 输入超过 512 KB 时回答“无法生成可靠指纹”（`input_size_limit`），与命令行一致；请求体过大时不读取。
- 日志每个请求一行 JSON：时间、输入字节数、结果类别、耗时。不含 SQL 原文。
- 每次请求单独建立只读连接；请求串行处理。数据库不可用时回答 503。
- 判断“一字不差”时，换行的 CR LF 与 LF 两种写法视为相同，原因见下文“文本传输”。

## 看板用的查询函数

结构 1.11.0 新增一组 `mpp_view_*` 函数，供看板和使用者自己的看板调用；名称和含义保持稳定。
表中省略参数前缀 `p_`；`normalization` 用 `mpp_view_rules()` 取得，`identity` 是身份标记。

| 函数 | 用途 |
| --- | --- |
| `mpp_view_rules()` | 不经 Python 取得当前规则标识：最近发布的当前版本所用的规则 |
| `mpp_view_encode(text)`、`mpp_view_decode(token)` | 文本与标记互转（无填充的 URL 安全 base64） |
| `mpp_view_identity_token(scope, database, user)`、`mpp_view_identity(token)` | 身份（集群＋数据库＋执行用户）与标记互转 |
| `mpp_view_label(kind, code)` | 计时类别、状态、原因等代码的中文名 |
| `mpp_view_search(normalization, mode, input, [scope, database, user, start, end, order, exact_sql_id])` | 三种方式统一的候选列表；`exact` 方式的输入是结构指纹 |
| `mpp_view_search_note(normalization, mode, input, [fingerprint, state, reason, exact_sql_id, …])` | “这次检索”的说明，含到达数据库的输入长度和摘要 |
| `mpp_view_hints(normalization, hints, […])` | 整批未命中时逐条语句的结果 |
| `mpp_view_identities(normalization, fingerprint)` | 该结构实际出现过的身份及记录数 |
| `mpp_view_timings(normalization, fingerprint, identity)` | 有记录的计时类别，按约定的默认顺序 |
| `mpp_view_versions(normalization, identity)` | 该集群已发布的版本，及能否选作参照 |
| `mpp_view_statistics(normalization, fingerprint, identity, build, [layer])` | 即 `mpp_query_statistics`；没有可用版本时返回零行而不是报错 |
| `mpp_view_sql_text(normalization, fingerprint, [sql_id])` | 所选原文或同一结构的一份示例 |
| `mpp_view_records(normalization, fingerprint, identity, timing, start, end, [sql_id])` | 一个身份、一类计时在时间范围内的记录 |
| `mpp_view_points(…, step_ms, [sql_id, min_ms, max_ms])` | 每格最慢和最快各一次的真实执行 |
| `mpp_view_counts(…, step_ms, [sql_id])` | 每格按状态的记录数 |
| `mpp_view_compare(normalization, fingerprint, identity, timing, build, start, end, [sql_id])` | 超过基线 P50／P95／P99 的次数和占比 |
| `mpp_view_texts(…, build, start, end, [order, hit_mode, hit_input, hit_sql_id, limit])` | 按原文拆开的表 |
| `mpp_view_executions(…, build, start, end, [sql_id, outcomes, min_ms, max_ms, order, limit])` | 明细列表，含与基线的比较和训练判定 |
| `mpp_view_ranking(normalization, start, end, [scope, database, user, timings, order, limit])` | 按时间范围现算的排行 |
| `mpp_view_baseline_ranking(normalization, [scope, database, user, timings, order, min_samples, limit])` | 按当前基线版本统计的排行 |
| `mpp_query_text_id(text)` | 按内容找一字不差的原文 |

口径上需要知道的几点：

- **失败、取消、超时的记录没有计时类别。** 库里这些记录的计时类别和耗时都为空。
  `mpp_view_records` 因此在每个计时类别下都带上它们；`unknown` 取全部没有计时类别的记录。
  排行里“未成功次数”按 SQL 身份统计；只有失败记录的身份单独成行，计时类别为空。
- **检索候选里的身份和时间来自命中的原文。** 按词和整段的每一行，记录数、身份数、
  记录最多的身份及其最近时间，都只算命中原文在筛选范围内的记录，这样进入详情后命中的原文一定在
  时间范围内。完整 SQL 按整个结构计算。
- **取值不同的那一段**是把所列原文的共同开头和共同结尾去掉后剩下的部分，是近似值。
- **训练判定**沿用 `mpp_query_training`：参与训练、被排除（附原因）、在训练窗口之外、
  未选入该版本的输入、该版本的判定规则不支持复算。

## 看板的标识、变量与跳转

三个看板都在文件夹 `MPP`（uid `mpp`）下，使用者的看板放在其子文件夹“用户自定义”（uid `mpp-custom`）。
随包看板由文件装入，界面里不能保存修改；要改就另存一份到“用户自定义”。升级只替换这三个文件。

| 看板 | uid | 地址 |
| --- | --- | --- |
| SQL 检索 | `mpp-search` | `/d/mpp-search/sql-search` |
| SQL 列表 | `mpp-list` | `/d/mpp-list/sql-list` |
| SQL 详情 | `mpp-detail` | `/d/mpp-detail/sql-detail` |

进入详情页的链接使用下列变量；这些名称保持稳定，变更须写入发布说明。

| 变量 | 含义 |
| --- | --- |
| `var-fp` | 结构指纹 |
| `var-identity` | 身份标记；不给或无效时取记录最多的身份 |
| `var-timing` | `request`、`execute_first`、`execute_fetch`、`parse`、`bind`、`unknown`；不给时取第一个有数据的 |
| `var-version` | 基线版本的标识；不给时取当前生效版本 |
| `var-sqlid` | 只看这一份原文；空表示全部原文 |
| `var-mode`、`var-q`、`var-hit` | 带入的检索方式、按词或整段的输入（标记）、完整 SQL 命中的原文标识，用于在原文表里标出命中 |
| `from`、`to` | 时间范围，毫秒时间戳 |

检索页自己的变量：`mode`、`q`（按词或整段的输入）、`fp`、`xstate`、`xreason`、`xsql`、`xhints`、`qd`
（完整 SQL 的结果）、`cluster`、`database`、`user`、`timefilter`、`order`。筛选下拉框的值是标记，
`*` 表示全部。

每条看板查询都以注释开头，写明来源，例如 `/* mpp-detail panel 7 A */`、
`/* mpp-detail variable identity */`，便于在数据库里辨认，也供端到端检查对应面板。

列表只显示前若干行时，“一共有多少行”不作为表里的一列（那样每行都是同一个值），而是在表的上方
单独显示一次：检索页的“命中的 SQL 结构总数”、列表页两个排行各自的总行数、详情页的原文份数和明细
条数。这个数字取自那张表自己的查询结果（Grafana 内置的 `-- Dashboard --` 数据源，按面板编号引用），
不另发查询；表的查询仍返回这一列（`total_structures`、`ranked_rows`、`range_texts`、`matching`），
只是在表里隐藏。生成脚本里用 `total(标题, 表, 列名)`，被引用的表先用 `layout.identify` 取得编号。

检索结果的“原文示例”只显示开头 160 个字符。这一格左上角有一个小三角，鼠标移上去弹出这份原文的开头一段，
点一下可以固定住。它用的是表格自带的“来自另一列的提示”：`example` 列设 `custom.tooltip.field`（值是隐藏列
`example_more`）和 `custom.tooltip.placement`；这两个要分别写成覆盖项，写成一个 `custom.tooltip` 对象不生效。
实现时要知道的三点：

- 只有小三角能触发，鼠标停在文字上不弹出；弹出框没有高度上限，也没有滚动条，超出窗口的部分看不到。
  所以查询把文本截到一屏以内：最多 1600 个字符，并按估算的显示行数最多 24 行（一行超过 75 个宽度单位
  就折行，非 ASCII 字符算两个），截断时末尾写明原文共多少字符。常量是生成脚本里的 `HOVER_*`。
- Grafana 对某一列给出的链接是加在全部列的链接之上，不是替换；给隐藏列写空的链接列表去不掉整行的链接。
  所以这张表不用默认链接，改为按列名的正则把链接给除 `example_more` 以外的列（`table()` 里的 `plain=True`），
  弹出的文字因此是普通文字，可以选中复制。
- 文本由面板查询从 `mpp_query_text` 取，没有改查询函数。
- 弹出框只在空白处换行，放不下的部分看不到。连续 80 个以上没有空白的字符（例如不带空格的取值列表）按 80 个一段断开显示，
  断开处标“↵”；这只影响弹出框里的显示。

详情页原文表的“取值不同的那一段”用同一机制弹出这一份原文（`hover_window()`）。各行是同一个结构，开头相同，所以放不下的
原文不从头显示，而是从“所列原文开始不同的地方”附近起显示一屏：往前两行的行首如果在 400 个字符之内就从那里开始，否则从
不同之处往前 200 个字符开始，开头写明略去了多少个相同的字符。开始不同的位置用 `mpp_view_common_prefix` 对所列原文里
按字节序最小和最大的两份求得，与 `mpp_view_texts` 算“取值不同的那一段”的做法相同。端到端检查用一组专门构造的文本经只读
数据源执行同一段查询，与独立实现逐项比对。

明细表上面的“明细图”画的就是明细表的那些行，不另外查询：它用 `-- Dashboard --` 数据源引用明细表的结果。明细表的查询
为此多返回几列（`bar` 是横轴的文字，`bar_p50`、`bar_over50`、`bar_over95`、`bar_over99`、`bar_plain`、`bar_none` 各是一种
颜色的柱子，定义在生成脚本的 `BARS`），每一行只有其中一列有值，这些列在表里隐藏。颜色取自 `mpp_view_executions` 给出的
“与基线的比较”，与耗时图里三条基线的颜色对应。没有耗时的执行不画成零高度的柱子，而是一根浅色的整格标记，用单独的、
不显示的纵轴（0 到 1）。图例里的条数是 Grafana 的 Count 计算，界面上显示为英文。

表格显示的两条约定，由 `table()` 统一设置：

- 列名单行显示，不截断也不换行：每一列至少和它的列名一样宽（`header_width()` 按 14 像素的字估算，偏宽取值，
  含格子的留白和按该列排序时列名旁的箭头），放不下的表格横向滚动。列名靠右对齐时被截掉的是开头，两个不同的列会
  显示成同一个名字，所以不让它被截；换行试过，用户认为难看。新增列时不用自己算宽度，给了固定宽度而不够时会被加宽。
- 耗时带单位显示（如“3.16 mins”“1.04 hours”）并靠右对齐，列太窄时被截掉的是最前面的数字。所以单位为毫秒的列最窄 88 像素
  （生成脚本里的 `DURATION`）；列数多到窗口放不下时表格横向滚动。分层明细有 24 列，合计约 2420 像素，在 1920 宽的窗口里
  约四分之一在右侧，要横向拖动，“分桶”一列固定（`frozenColumns`）。1920 宽下其余表格都不横向滚动；更窄的窗口里
  列多的表格会横向滚动。

检索页按 1920×1080 的屏幕排版（浏览器自身的栏占去一部分，按 1920×920 的窗口核对）：输入（宽 12、高 8）在左，
右边上下是总数（高 3）和“这次检索”（高 5），下面是结果列表（高 13），合计 21 格高，在一屏之内；逐条提示在
第二屏。Grafana 的格子宽度随窗口变（窗口宽度的二十四分之一），高度固定为每格 38 像素，所以窗口变矮时只是
少看到几行，变窄时列才会挤。结果列表各列的固定宽度合计不超过 1920 宽的窗口，“原文示例”取剩下的宽度并设了
最小值；窗口更窄（例如系统缩放 125%）时这张表横向滚动。三种方式的说明不占面板：开关上的文字是每种方式的
含义，“方式”旁边的圆圈图标和面板标题旁的圆圈图标给出完整规则（后者是表格）。没有用单选项自带的
`description`：它的提示弹在选项正下方，会盖住“检索”按钮。

## 文本传输：防止内容被悄悄改动

- **按词和整段**：表单脚本把输入编码成无填充的 URL 安全 base64，写进变量 `q`；面板的 SQL 用
  `mpp_view_decode` 解码。输入不以原样经过任何变量替换或引号处理。上限 256 KB。
- **完整 SQL**：表单脚本自己构造请求，经 Grafana 后端的 Infinity 数据源转发给指纹服务，
  内容以 base64 放在请求体里，不进地址栏。返回页面时由浏览器的会话存储填回输入框。
- **表单脚本不得含 `$` 和 `[[`**：插件会先对脚本做一次 Grafana 变量替换。
- **不用插件的 `patchFormValue` 回填输入框**：它会把代码编辑框里的每个换行改成反斜杠加 n；
  改用 `onChangeElements` 直接设置。
- **代码编辑框元素须显式 `isEscaping: false`**，面板须写明 `pluginVersion`，否则插件的迁移逻辑会打开转义
  或改写脚本。
- **换行只有一种写法**：浏览器里的编辑框对整段文本只保留一种换行，Windows 上是 CR LF，其他平台是 LF。
  表单一律按 LF 发送。因此粘贴内容里的 CR LF 到达时是 LF；按词、整段和结构指纹都不受影响
  （比较时本来就忽略空白），只有“一字不差的原文”受影响，所以指纹服务把两种换行写法视为相同。
- 使用者自己用 Grafana 自带的文本框变量做检索时，含双引号的输入会被加上反斜杠而查不到，
  应仿照随包看板用标记传递。

## 已知的行为与限制

- 筛选下拉框里的数据库和执行用户取自有基线的身份；只出现在无基线记录里的不在下拉框中。
- 只有失败记录、没有任何计时记录的 SQL，耗时图为空，失败标记只在下方的记录数图里看得到。
- 详情页的面板各自查询同一批执行记录；时间范围内记录越多，每个面板越慢。默认折叠的两行展开后才查询。
- 按时间范围现算的排行要扫描范围内的执行记录，耗时随范围和累计数据量增长；本版没有为它新增索引。
- 模糊检索的函数保留“每次重新规划”，并在函数上把并行的启动成本设为零，使它在长连接下使用并行；
  依据和数字见[实测报告](../reports/grafana-dashboards-2026-10-09.md)。

## 验证入口

```bash
.venv/bin/python scripts/db/verify_search.py           # 检索规则，命令行与函数一致
.venv/bin/python scripts/db/verify_views.py            # 只读账号、指纹服务、看板函数对独立计算
.venv/bin/python scripts/db/verify.py                  # 初始化、升级链和结构检查
.venv/bin/python scripts/tests/test_grafana.py         # 看板生成一致、安装程序拒绝摘要不符的文件
.venv/bin/python scripts/grafana/setup_dev.py synthetic --directory var/grafana-e2e
source BROWSER/env && BROWSER/venv/bin/python scripts/grafana/verify_e2e.py --directory var/grafana-e2e
.venv/bin/python scripts/db/verify_views_full.py upgrade|audit|timing --directory PRIVATE_COPY
```

修改看板时改 `build_dashboards.py` 并重新生成；提交的 JSON 与生成结果不一致会被 `test_grafana.py` 拒绝。
`verify_views_full.py` 是显式的全量核对，使用已停止的真实库副本，不加入日常检查。
