# 全量解析发现的问题修复（2026-09-27）

用户在[全量覆盖核查](mpp-full-scan-2026-09-27.md)后要求“开始修复”，本轮将确认的修复范围
同步至 [Issue #9](https://github.com/shenxg13/sql-apm/issues/9)，原型升级为 `mpp-adapter-probe/5`。
本轮修复深层结构处理、ROOTPARTITION、分区TRUNCATE、范围分区建表和旧式OIDS选项。
归一化、函数字典接入及最终产品指纹仍未交付。

## 实现与结构保留

### 深层结构

[结构工具](../../sql_apm/sql/structure.py)保留标准JSON编解码的普通路径，遇到递归深度限制时
改用显式栈；[AST清理](../../sql_apm/sql/pg_ast.py)同样使用显式栈复制结构，只删除原有的
位置字段。诊断摘要及子进程完整结构传输也使用同一编解码工具。

不提高进程全局递归限制、不截断AST、不把复杂表达式降级为原文。标准深度下的规范JSON字节
保持与原先 `sort_keys=True, ensure_ascii=True, separators=(',', ':')` 一致。
1,024项连续加法与UNION的完整结构、2,000层JSON往返及位置清理均有回归；进程的递归限制不变。
这些实测深度不是产品容量上限，也不承诺任意长度输入均可解析。

### ROOTPARTITION 与分区TRUNCATE

- `ANALYZE`／`ANALYSE [VERBOSE] ROOTPARTITION 表 [(列列表)]` 和 `ROOTPARTITION ALL`：
  普通目标、列列表、VERBOSE交给PG解析；扩展节点保存ROOTPARTITION与ALL标志，错误位置、
  多目标列表或多余内容整批拒绝。
- `ALTER TABLE … TRUNCATE PARTITION 名称`、`DEFAULT PARTITION`、`FOR (边界值)`、
  `FOR (rank(数值))`：保存目标种类、具体值及CASCADE／RESTRICT。与普通ALTER动作和MPP SET
  混合时，复用既有有序动作模型，保持所有动作的先后顺序及同一目标。

### 范围分区与OIDS

- 适配 `CREATE TABLE … [DISTRIBUTED …] PARTITION BY RANGE(键列表) (分区定义列表)`。
  节点保存键顺序、分区顺序／名称、默认分区、START／END值和包含关系、EVERY、显式类型转换、
  每个分区的WITH选项及TABLESPACE。未显式指定的包含关系沿用源语法的START包含、END不包含。
- 普通列定义与表级选项仍由PG生成完整AST；分区边界只接受源语法允许的常量、类型转换和
  负号形式。任意函数或算术、错误边界顺序、缺分隔符、尾随内容等不会被静默删除。
- `CREATE TABLE`／CTAS查询前的 `WITH OIDS`、`WITHOUT OIDS` 映射为明确的 `oids=true/false`
  选项，保持临时表、ON COMMIT、分布等结构。PG原生解析会忽略旧式WITHOUT OIDS的显式关闭标志，
  本次一并补齐；合成用例验证关闭与省略可区分。旧全量语料没有使用该形式的成功输入。
  查询中的字符串或表达式不做此替换。

适配依据沿用固定提交的
[Greenplum 6 grammar](https://github.com/greenplum-db/gpdb-archive/blob/9a08259bd1836f0cf5ba935e7e0030a5a9c0a54b/src/backend/parser/gram.y)。
语法有效性不等于对象存在、权限正确或已验证实际执行。当前仍不支持LIST分区定义、子分区模板、
分区ADD／EXCHANGE／SPLIT、其余COPY扩展、U&字符串等既有缺口；不将本轮范围分区支持扩大为全部MPP语法。

## 回归方法

[全量修复重放工具](../../sql_apm/diagnostics/mpp_full_repair.py)以只读方式使用版本4的完整原文索引，
启动与结束均核对旧库完整SHA-256，结果保存在独立的本地忽略索引。原46个日志的EOF和来源摘要
验证由前述全量报告提供，本轮复用其固定原文，不重新采样。

先重放全部旧失败，再重放全部旧成功。比较旧成功时，只在诊断摘要计算中将新树的版本字段
临时设回版本4，其余字段完全保留，计算相同规范编码的完整结构SHA-256；产品输出仍是版本5。
由此把“规则版本变化”与“树结构变化”分开核对。摘要比较及合成完整树断言共同提供证据，
不把摘要当作已实现的业务指纹，也不声称已人工验证每一个生产结构的语义。

MPP新节点有完整结构对照、字段变更区分、普通格式／Hint和错误批次测试。旧矩阵中三项已修复
缺口转为完整结构正例，其余失败边界保留；旧JSON报告不重写。

## 全量修复结果

[机器证据](data/mpp-full-repair-2026-09-27.json)记录全部1,156,621个不同原文的对照汇总、
384项修复的逐条来源定位及旧／新诊断、前后源码上下文和旧库摘要。
[合成验证证据](data/mpp-full-repair-validation-2026-09-27.json)保存矩阵、测试数量和源码摘要。

| 对照结果 | 不同原文 |
| --- | ---: |
| 旧成功的完整规范结构摘要不变，仅版本号升级 | 1,147,328 |
| 旧失败／异常转为成功 | 384 |
| 维持原拒绝原因 | 8,909 |
| 成功转失败／结构意外变化 | 0 |
| 合计 | 1,156,621 |

| 已修复类别 | 不同原文 | 日志出现次数 |
| --- | ---: | ---: |
| 深层COPY表达式导致的递归异常 | 191 | 608 |
| ANALYZE／ANALYSE ROOTPARTITION | 113 | 1,362 |
| ALTER TABLE TRUNCATE PARTITION | 77 | 202 |
| 带完整范围分区列表的建表 | 2 | 6 |
| WITH OIDS临时建表 | 1 | 12 |
| 合计 | 384 | 2,190 |

版本5总计返回1,147,712个结构、拒绝8,909个输入；异常、大小限制、超时和进程失败均为0。
按日志出现次数计，结构返回为11,211,696次，拒绝157,793次，分母仍为11,369,489次输入。
这些是固定日志和解析原型的状态，不是独立执行数或归一化通过率。

71项解析／诊断专项测试、31项既有业务测试通过。81项扩展案例及其字段／关系检查通过；
578项整体矩阵中，460项完整结构正例、102项非法批次、10项剩余缺口和6项保守边界全部符合预期。
新增JSON工具、新语法结构和重放状态比较另有专项测试。完整重放实测耗时433.385秒，
不构成产品性能验收承诺。完整离线Harness、工作区／索引差异检查均通过；
原始需求、函数字典、历史JSON和版本4原文索引摘要不变，在线Issue契约与所有者已复核。

## 验证命令

固定依赖仍在 `tests/parser_probe/requirements.txt`。从仓库根目录执行：

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe -v
.venv/bin/python -m unittest discover -s tests -v

PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_full_repair \
  --database var/parser-probe/full-repair-rerun.sqlite \
  --output var/parser-probe/full-repair-rerun.json

PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

重放命令默认读取本次版本4原文索引，并通过已保存的失败审计报告校验旧库摘要；要求新的结果库
路径，避免覆盖现有证据。新库只保存诊断和比较结果，生产原文继续留在旧的本地忽略索引。
八个隔离工作进程沿用512 KiB输入、5秒请求和512 MiB进程地址空间限制。
