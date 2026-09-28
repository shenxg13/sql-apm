# 七天日志门槛诊断 R1 整改验证（2026-09-28）

本报告记录 [Issue #13](https://github.com/shenxg13/sql-apm/issues/13)、
[PR #14](https://github.com/shenxg13/sql-apm/pull/14) 的实施方整改证据，对应
[R1 统一账本](https://github.com/shenxg13/sql-apm/pull/14#issuecomment-5872964099)
中唯一的 P2 阻断 I13-R1-F001。R1 全面发现已完成，已消耗一轮；独立 R2 验证退出条件。
本文不代替独立评审结论，历史报告及六份补充 JSON 保持原样。

## 修复与退出条件

R1 固定目标及 merge-base 为 `a305eebe130f786a818b6a043042c1c547559a47`，
initial head 为 `6cc9f63349c95df60aa24d2c4db0111dfc771183`。
已按[明确交接](https://github.com/shenxg13/sql-apm/issues/13#issuecomment-5872983203)
恢复整改，没有重置轮次。

旧 `lexical_digest` 将共享扫描器保留的美元引号体替换成常量标记，未先拒绝体内非法字节。
实施前复现确认：无 tag 的非法 UTF-8、带 tag 的 NUL 和合法美元字符串得到同一成功指纹。
新增回归在旧实现下有 33 个失败子场景，修复后全部通过。

[修复](../../sql_apm/diagnostics/threshold_coverage.py)在扫描及替换前调用已有的完整文本检查，
拒绝任何 NUL 或 surrogate 字符；隔离进程用 `surrogateescape` 解码，非法 UTF-8 字节因此
也被拒绝。输出固定为 `lexical_refused`、`invalid_encoding_or_nul`、空指纹。
该记录仍留在全部输入的出现次数分母，不参与成功组、分子或活跃日期。
正式归一化、共享近似规则、字典和训练规则均未改；诊断输出格式及合法输入的哈希域仍为
`threshold-coverage/1`，运行源码摘要区分修复前后。

| 退出条件 | 实施验证 |
| --- | --- |
| 统一非法输入边界 | 5 种非法字节序列／NUL × 16 个上下文，共 80 场景：带／不带 tag 美元引号，普通、E／B／X／N／U& 字符串，普通／Unicode 引号标识符、裸标识符，行／块／Hint／嵌套注释及未闭合字符串 |
| 合法对照 | 两种美元分隔符 × 空串、中文／emoji、引号／注释状文本、字面反斜线，共 8 场景仍归一；对象名变化仍区分 |
| 七天／35 次聚合 | 两种美元分隔符 × 非法 UTF-8／NUL × 全非法／混合法，共 8 组隔离 capture；五方案总分母均为 35，所有门槛组及分子为 0 |
| 不借用拒绝日期 | 混合组中合法输入只有六天、30 次；第七天的 5 次非法输入不补足活跃日，成功出现分母仅 30 |
| 真实语料及摘要 | 全量字节核查、全部非法输入和有界合法对照回放，详见下节及[新证据](data/log-supplement-r1-encoding-2026-09-28.json) |

用例位于[门槛专项测试](../../tests/parser_probe/test_threshold_coverage.py)。
另有审计器回归：人为恢复旧误成功，审计必须检出；注入无关结构指纹漂移，审计必须失败且不发布结果。

## 真实语料影响

新增[编码审计](../../sql_apm/diagnostics/threshold_encoding_audit.py)只读扫描原完整索引，
以严格 UTF-8 解码和原始 NUL 字节检查独立选择问题输入，并逐条核对原文字节摘要。
回放选择为全部非法输入、128 个均匀序号、按 ID 顺序前 8 个合法美元标记输入及最长输入；
有 tag 标记的合法真实输入未找到，由合成测试覆盖。选择 ID、集合摘要及运行源码摘要写入新 JSON，
不导出 SQL、AST、参数值或任意异常文本。

| 观察 | 结果 |
| --- | --- |
| 原完整输入数 | 1,497,418 |
| 非法 UTF-8／含 NUL 原文 | 224／0 |
| 非法输入出现次数 | 119：14；120：994，共 1,008 |
| 上述输入旧词法成功数 | 0；原结果均无指纹 |
| 修复后隔离回放 | 361 个不同输入 × 五方案 = 1,805 项结果比较 |
| 指纹、成功／拒绝状态变化 | 0；全部非法输入仍为空指纹 |
| 词法拒绝原因变化 | 60 个 `unclosed_bracket`、58 个 `unclosed_comment` 改为 `invalid_encoding_or_nul`；对应 120 集群 464／238 次出现；119 无原因变化 |
| 其他结果变化 | 0；正式上下文与旧完整证据相同 |

**推断及依据**：完整语料的新增检查只命中上述 224 个已拒绝输入；逐条回放没有指纹变化，
其余合法输入进入的扫描和哈希逻辑未改。因此本次修复不会改变该语料的成功组、达标组、
分子、两种分母及门槛比例，既有报告的覆盖数字仍有效。原因优先级变化如上，不能把旧 JSON
的拒绝原因分布称为新代码重跑结果。旧完整覆盖附件继续绑定旧源码；新审计绑定修复源码，
没有把旧全量附件改成“修复后全量运行”证据。

输入索引、旧缓存、旧覆盖附件的前后完整 SHA-256 相同。原索引摘要为
`b50c2e2660dab8c4d22f9c1e90a1640555c4caadcc0ed96589dcc97f46db59b2`；
旧缓存摘要为 `70cf0a33a982fc07785b66f3d706d4c134bc52ea8d679a4610a281a890762111`。
全部依赖源码在运行前后保持一致，新 JSON 保存准确摘要与正式上下文。

## 成本、复现与限制

本次风险来自诊断常量替换前的漏检。完整字节核查加相关输入回放可确定这一变更在固定语料中的
影响；无需重新读取 55 份 CSV 或对全部原文重做五方案解析。实测字节扫描及选集准备前检查为
4.065 秒，361 条隔离回放为 1.138 秒，审计总计 6.468 秒；总计包含结束摘要核对，不含启动前
三个输入文件的摘要。计时是本机观察，不是吞吐承诺。使用两个隔离进程，单输入 512 KiB、
5 秒、每进程地址空间 512 MiB，复用原 capture 的失败与回收边界。

以下为本轮实际命令；复跑需选择新的输出和工作目录，保留已有证据。原始索引、选择子集和
缓存都留在本地忽略的 `var/`；没有这些原始材料时，仍可运行全部合成回归。

```bash
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m sql_apm.diagnostics.threshold_encoding_audit \
  --source var/parser-probe/issue13/full-scan.sqlite \
  --baseline var/parser-probe/issue13/coverage.sqlite \
  --evidence docs/reports/data/log-supplement-coverage-2026-09-28.json \
  --output docs/reports/data/log-supplement-r1-encoding-2026-09-28.json \
  --work-dir var/parser-probe/issue13/r1/audit-final

.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=var/parser-probe/site-packages .venv/bin/python -m unittest discover -s tests/parser_probe -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh --pr 14 --repo shenxg13/sql-apm
```

普通 49 项、解析专项 161 项通过；完整 Harness 与在线 PR 契约结果见
[检查摘要](data/log-supplement-r1-checks-2026-09-28.json)。
本轮没有重新采集 CSV、重做全部解析／词法普查或重新聚合完整门槛结果；原有全量证据和
R1 独立复核继续保留。候选语义安全性、执行还原、训练资格与生产部署均不在本次整改范围。
