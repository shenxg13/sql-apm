# 解析源码目录调整与等价验证

日期：2026-09-27（Asia/Hong_Kong）。用户指出解析源码和用例JSON不应集中在 `scripts/`，
并要求“开始优化调整”。本轮按已有[源码布局约定](../../.project-wiki/architecture/source-layout.md)
整理职责、依赖和入口，属于Issue #9已有实现范围，不改变SQL支持范围。

## 调整结果

| 内容 | 原位置 | 当前位置 |
| --- | --- | --- |
| MPP解析核心 | `scripts/diagnostics/mpp_parser_prototype.py` | [sql_apm/sql/mpp_parser.py](../../sql_apm/sql/mpp_parser.py) |
| 共用词法检查与类别标签 | `sql_apm/diagnostics/statement_census.py`内部 | [sql_apm/sql/lexical.py](../../sql_apm/sql/lexical.py) |
| PG语法树位置字段清理 | `scripts/diagnostics/parser_fidelity.py`内部 | [sql_apm/sql/pg_ast.py](../../sql_apm/sql/pg_ast.py) |
| 六个探测／矩阵／重放实现 | `scripts/diagnostics/*.py` | `sql_apm/diagnostics/`下的同名模块 |
| 两份合成输入JSON | `scripts/diagnostics/*-cases.json` | [tests/parser_probe/fixtures/](../../tests/parser_probe/fixtures/) |
| 实验依赖锁定 | `scripts/diagnostics/parser-probe-requirements.txt` | [tests/parser_probe/requirements.txt](../../tests/parser_probe/requirements.txt) |

六个诊断模块为 `parser_fidelity`、`mpp_adapter_probe`、`mpp_expansion_probe`、
`mpp_expansion_matrix`、`mpp_broad_matrix` 和 `mpp_broad_replay`。
该轮曾保留调用 `main()` 的薄脚本；用户随后要求清理，现已移除这些包装入口及类别调查包装脚本。
原纯解析实现脚本路径也已移除，当前统一使用包模块命令。
测试改为直接导入 `sql_apm`。当前命令清单见[维护入口](../../scripts/README.md#解析器结构保真探测)。

依赖方向为：模块命令进入诊断实现，诊断模块调用SQL模块。核心只依赖SQL包内共用逻辑与解析库，
不再导入脚本或日志调查模块；日志类别调查也复用同一份SQL词法逻辑。模块导入不修改 `sys.path`，
不执行诊断命令。诊断命令从仓库根目录运行；子进程使用包模块入口，并明确工作目录，
从其他目录调用探针仍能找到正确实现。

## 保持不变的内容

原型版本仍为 `mpp-adapter-probe/4`。解析函数与两个抽出的共用实现共25个函数／类节点，
Python AST逐项一致；没有借目录调整改变解析规则、词法拒绝、Hint处理或JSON编码。
两份夹具及依赖锁文件按字节迁移。

历史报告的源码链接及依赖安装路径更新为当前位置，增加了目录迁移提示；当时的测试分母、
结论和限制没有重写。已有 `docs/reports/data/` 证据JSON保持原始字节，其旧路径和摘要描述
当轮源码，由本轮迁移映射衔接。新运行的证据记录新模块及抽出依赖的源码摘要。
业务规则JSON和原始需求快照的摘要也保持不变。

报告及结果JSON继续放在 `docs/reports/` 和 `docs/reports/data/`；生产SQL和缓存继续只保留
在忽略区域。目录迁移没有把实验依赖升级为正式产品依赖，也没有添加安装打包或统一CLI功能。

## 验证

迁移前保存完整结果快照，迁移后对同一输入逐字段比较，**包含原型版本、所有语句、Hint及拒绝原因**，
没有仅比较是否返回AST。结果为：

| 固定输入 | 数量 | 迁移后结果 |
| --- | ---: | --- |
| 三组真实输入缓存 | 914 | 895个完整结构、19个拒绝，全部与迁移前相同 |
| 两份固定JSON夹具 | 148 | 122个完整结构、26个拒绝，全部相同 |
| 整体生成矩阵 | 578 | 457个完整结构、121个拒绝，全部相同 |
| 合计 | 1,640 | 完整输出逐字段一致 |

上述集合存在语法重叠，本轮用于验证迁移等价，没有新增生产抽样或扩大语法覆盖。
[机器证据](data/parser-layout-2026-09-27.json)保存文件映射、前后摘要、函数体比较、
逐输入结果摘要、历史证据校验以及命令验证结果；生产原文和完整结果快照均不进入报告区。

迁移时新增的[入口测试](../../tests/parser_probe/test_entrypoints.py)验证核心不依赖诊断／脚本、
包导入没有命令副作用或路径修改、不同工作目录下子进程可运行，以及入口输出一致；清理包装脚本后，入口比较改为包模块命令与可调用探针比较。
既有CI中的业务测试和字典命令没有失效路径；可选解析实验仍按固定依赖在本地执行，未新增在线安装流程。

最终42项解析专项测试、31项既有业务测试、函数字典1.0.1校验及覆盖检查、完整离线Harness、
工作区和索引的 `git diff --check` 均通过。六个诊断命令均已实际运行：914个真实输入的
子进程重放结果与迁移前一致，895个成功输入的格式检查通过；578／81两个矩阵通过，
67个候选解析器案例与原历史结果一致。相关文档链接和知识正文已同步。

Issue #9在线正文与评论没有变化，仍为进行中；本地修改尚未提交，未进行PR交接。

## 后续清理重复入口

同日用户继续要求清理 `scripts/`。已删除六个解析诊断包装脚本及
`statement_census.py` 包装脚本，共七个；原目录中的对应本地字节码缓存也已清理。
数据库、GitHub、质量检查及规则维护工具有独立职责，予以保留。

当前所有实际命令改为 `python -m sql_apm.diagnostics.<模块>`，测试改为验证包模块命令
与可调用探针的一致性。此次清理未修改任何 `sql_apm/` 源码、夹具或历史证据JSON；上面的
1,640项完整结果等价及机器证据仍描述迁移当轮状态，其旧包装脚本路径不是当前调用要求。

清理后七个包模块入口检查、42项解析专项测试、31项既有业务测试、578例整体结构矩阵、
完整离线Harness及工作区／索引格式检查均通过。在线Issue契约及状态没有变化，修改尚未提交。

## 当前调用方式

在仓库根目录使用已准备好的可选实验依赖：

```bash
# Python可复用接口；仍为解析原型，不是正式归一化／指纹接口
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -c \
  'from sql_apm.sql.mpp_parser import parse; print(parse("SELECT 1"))'

# 当前统一使用包模块入口
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_broad_matrix --output var/parser-probe/layout-matrix.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m sql_apm.diagnostics.mpp_expansion_matrix --output var/parser-probe/layout-expansion.json
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python \
  -m unittest discover -s tests/parser_probe -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m sql_apm.sql.function_dictionary validate rules/functions/v1.0.1.json
.venv/bin/python scripts/functions/coverage.py
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

新环境的实验依赖安装应使用 `tests/parser_probe/requirements.txt`，仍遵循既有版本和wheel摘要约束。
已知的分区、ANALYZE ROOTPARTITION等缺口继续按[整体验证报告](mpp-broad-validation-2026-09-27.md)记录，
本次目录归位未宣称完成Issue #9整体验收。
