# MPP 解析原型整体扩展验证

> 目录调整说明：本文源码链接已更新为[当前目录布局](parser-layout-2026-09-27.md)。历史机器证据中的路径和摘要保持原样，对应当轮源码。

日期：2026-09-27（Asia/Hong_Kong）。用户在混合 ALTER 修复后要求“整体扩大范围验证”。
本轮覆盖整个解析原型，承接[混合 ALTER 验证](mixed-alter-2026-09-27.md)，仍属于
[Issue #9](https://github.com/shenxg13/sql-apm/issues/9) 的解析阶段，未交付业务归一化与正式指纹。

## 结论

- 新增564个真实输入，561个返回结构、3个拒绝；561个完整树均通过格式扰动比较。
  其中423个无适配扩展的输入还与直接调用 PG 解析器的完整树一致。
- 保留原350个输入的回归：334个结构排除原型版本号后逐字段一致，16个拒绝原因不变。
  累计914个定位，895个返回结构、19个拒绝；这是有偏抽样结果，不能外推生产覆盖率。
- 新增578个合成案例：457个完整结构对照、102个非法批次、13个未适配案例、6个保护边界，
  结果均符合记录预期。“符合预期”包含明确拒绝，不能解读为578个语法全部支持。
- 发现并修复 COPY `ON SEGMENT` 在错误位置仍被接受的问题，原型升至 `mpp-adapter-probe/4`。
  新发现真实 MPP `ANALYZE ROOTPARTITION` 适配缺口，已保留失败证据和最小重现，未在本轮补齐。

[机器证据](data/mpp-broad-validation-2026-09-27.json)保存源码摘要、语法依据摘要、逐案例结果、
真实定位、拒绝核对及 COPY 修复前后结构。生产 SQL 仅保留在本地忽略缓存，报告中的 SQL 均为合成重现。

## 整体合成覆盖

[矩阵运行器](../../sql_apm/diagnostics/mpp_broad_matrix.py)为每个正向案例构造完整预期结构：
原生部分独立调用固定版本 PG 解析器，MPP 部分显式指定类型、标志、值和顺序，再逐字段比较。
不以“能返回 AST”或仅存在某个节点作为完整结构验证。

| 正向类别 | 案例数 | 主要覆盖 |
| --- | ---: | --- |
| 原生 SQL | 60 | 查询、CTE、窗口、集合、数组、JSON／XML、写入、DDL、事务、配置、权限、游标、维护命令 |
| 建表／物化视图分布及存储选项 | 26 | 三类分布、键序、opclass、CTAS、临时表、ROW 兼容 |
| 外部表创建 | 64 | 读写／WEB 标志、LOCATION 顺序、执行位置、编码、格式参数的具体类型与值 |
| 外部表删除 | 16 | WEB、IF EXISTS、限定对象列表、CASCADE |
| COPY ON SEGMENT | 36 | 表／查询、方向、文件／PROGRAM／标准流及合法选项位置 |
| 混合 ALTER | 192 | 六种目标写法、普通动作、四种分布形式及动作前后顺序 |
| 异类多语句批次 | 49 | 七种代表语句的有序两两组合 |
| Hint | 14 | 块／行 Hint 原文、位置及格式变化 |
| 合计 | 457 | 全部完整预期结构一致；每例另有两种格式扰动，共914项 |

另有12组成对差异检查，覆盖对象、运算、值、类型、配置、数量、顺序、Hint 与扩展参数。
11个类别各选一个案例执行两次新进程比较；128语句批次、128列建表、64层表达式及64个 Hint
均有有界结构与稳定性检查，另核对探针512 KiB输入上限。这些数值是实验规模，不是产品容量或吞吐承诺。

上一轮81个案例、65项字段断言及25组关系也全部通过。新增矩阵与旧矩阵可能有语法重叠，
不把两个案例数相加宣称独立语法覆盖数量。

## COPY 位置校验修复

版本3只在抽取 `ON SEGMENT` 后交给 PG 解析剩余文本，导致下列错误写法被误接受：

```sql
COPY t TO PROGRAM ON SEGMENT 'cat';
COPY t TO '/tmp/a' WITH (format csv) ON SEGMENT;
COPY t TO '/tmp/a' ON SEGMENT WITH CSV HEADER;
```

固定提交的
[Greenplum 6 grammar](https://github.com/greenplum-db/gpdb-archive/blob/9a08259bd1836f0cf5ba935e7e0030a5a9c0a54b/src/backend/parser/gram.y)
在3862、3947、3957及4022行附近明确规定：`ON SEGMENT` 属于旧式 `copy_opt_item`，
必须出现在文件／命令字符串之后的选项列表中；不能与括号式选项列表拼接，`WITH` 也不能移到选项中间。

版本4先用一个合法旧式选项验证原位置，再构造原有扩展表示；验证用的临时选项不进入输出，
用户真实写出的 FREEZE、CSV、HEADER 等仍完整保留。七种错误排列分别放在批次首、中、尾，
形成21个修复前误接受、修复后整批拒绝的对照。合法位置及真实 FREEZE 选项另有正向回归。

旧测试中有一例 `ON SEGMENT WITH CSV HEADER`，按上述语法应拒绝；本轮将正向例纠正为
`WITH ON SEGMENT CSV HEADER`，并把原写法纳入明确负例。没有把旧测试的错误预期当成兼容要求。

## 真实输入抽样与结果

[抽样与重放工具](../../sql_apm/diagnostics/mpp_broad_replay.py)遍历119／120现有清单中的46个文件。
每文件最多读取16 MiB前缀、处理10,000条完整 CSV 记录，最多选16个新形态；排除原350个输入已选形态，
优先增加类别、MPP特征及第5,000条以后的样本。形态摘要只用于抽样去重，不是业务指纹或语义等价证明。
SQL字段、不同的内联SQL及内部SQL字段均参与候选检查，本轮最终选出的564个定位全部来自SQL字段。

| 抽样项 | 实测 |
| --- | ---: |
| 文件／读取前缀字节 | 46／760,106,532 |
| 完整 CSV 记录／输入检查 | 443,858／408,971 |
| 各文件新增候选形态累计 | 5,511 |
| 选中119／120输入 | 308／256 |
| 位于第5,000条之后的选中输入 | 369 |
| 因前缀边界丢弃的不完整末条记录 | 4 |
| 新增重放／结构／拒绝 | 564／561／3 |
| 成功输入完整树格式比较 | 561／561 |
| 原生完整树与直接 PG 对照 | 423／423 |
| 本次重放耗时 | 68.793秒 |

前缀截断只在 CSV 明确到达所读边界且提示记录未结束时丢弃末条，其他 CSV 错误不会静默跳过。
抽样保存原清单文件摘要、实际前缀摘要、读取前后大小和修改时间；只有1个完整读入文件重新核对了
全文件摘要，其余45个文件没有声称重算全文件哈希。输入缓存再按逐条 SQL 摘要核验。

新样本实际包含分布扩展89个、外部表创建37个、COPY ON SEGMENT 12个、MPP ALTER 1个、外部表删除1个；
这些是语句／节点数，不能与批次输入分母混用。抽样中的 `partition` 特征会命中窗口的 `PARTITION BY`，
不能据此声称分区建表已支持。本轮仍没有真实混合 ALTER 正向样本，该部分结构证据来自合成用例。

## 三个新增拒绝的核对

| 定位 | 观察与分类 | 最小合成重现 |
| --- | --- | --- |
| 119，`gpdb-2026-07-18_000000.csv`，记录3410 | INSERT的SELECT列表以逗号结束，缺少后继表达式，属于不完整SQL；未推断产生原因或执行结果 | `INSERT INTO t(a) SELECT 1,` |
| 119，同文件，记录111 | 只有分号，没有可解析语句，预期拒绝 | `;` |
| 120，`gpdb-2026-09-16_000000.csv`，记录389 | `ANALYZE ROOTPARTITION` 是固定GP语法中的合法分支，当前适配器不支持 | `ANALYZE ROOTPARTITION s.t` |

ROOTPARTITION分支可在上述grammar第11395行附近核对。本轮新增真实拒绝中，前两项为输入边界，
第三项为实际支持缺口；不能把三项都归为脏数据。基于已有抽样只确定观察到这种缺口，没有推算其生产占比。

## 尚未完成的范围与复现

本轮矩阵保留13个合法语法缺口案例：ANALYZE ROOTPARTITION、外部 OPTIONS 的保留字标签、
两种MPP分区建表、四种分区ALTER、四种COPY特有选项和U&字符串。六个保护边界包括普通字符串
反斜杠歧义、不能可靠保留的Hint以及空语句；与102个非法批次单列。

结构验证没有执行 SQL、连接业务数据库或验证现场对象／扩展版本兼容性。PG树对照验证的是适配层没有
丢失原生节点，不构成与PG实现独立的第二份语义证明。完整归一化、函数字典接入、正式指纹及Issue #9
整体验收仍未完成。

```bash
# 首次抽样（生产原文只写入忽略缓存）；后续省略 --root 使用固定缓存
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_broad_replay --root raw/inbox/hashdata \
  --output var/parser-probe/broad-replay.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_broad_matrix --output var/parser-probe/broad-matrix-after.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe -v
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
git diff --check
```

运行环境为CPython 3.9.5、pglast 7.18；需保留前两轮忽略缓存及原来源清单。矩阵按固定组合生成，
无随机种子；重放定位、结构摘要及比较结果可复现，比较时排除耗时字段。

最终检查：38项解析专项测试、31项既有业务测试、完整离线Harness及工作区／索引的
`git diff --check`均通过；机器证据的源码摘要与当前文件一致。Issue正文与评论没有变化，
仍为 `status:in-progress`；本地修改尚未提交，未进行PR交接或Issue整体验收。
