# PostgreSQL 物理结构验证（2026-09-26）

关联 [Issue #7](https://github.com/shenxg13/sql-apm/issues/7)，
实施基线 main 为 `bbd1899b2684d448e941e324f4778eb35329fd0e`。
[物理映射](../design/postgresql-storage.md)与[操作恢复说明](../runbooks/database-initialization.md)
定义本次交付边界；本报告仅记录实际执行的存储及仓库检查。

## 环境与输入

- AlmaLinux／WSL 开发环境，实际 `psql (PostgreSQL) 17.10`，
  initdb、pg_ctl、psql 来自 /usr/pgsql-17/bin；产品回归 Python 3.9.5。
- 默认沙箱已知在执行前因 WSL 挂载错误失败；本次仓库写入和 disposable
  实例验证按会话批准的沙箱外调用执行。没有更改系统现有 5432 服务。
- 结构版本 1.0.0，41 张表，10 个额外索引（不含主键／唯一约束自动索引）。
- 合成输入来自 Issue #3 的
  [examples.json](../design/offline-data-contract/examples.json) base 及人工边界变体。
  输入 SHA-256：`f5598cc0bffbe495f5849b7e5b19d7d21c158bf95484546a0338b4db99356bed`。
  [fixture 映射](../../tests/database/fixture.py)只为该静态基础样例生成 INSERT，
  不是生产导入器，不执行样例中的业务 SQL。
- 本次验证 DDL SHA-256：
  `df6b4cec6abac9742c56afc3c238d2f16fadd2895da1d61c04fb6255336c3c28`。
  初始化也将相同摘要保存在 schema_version，部署后不能以同版本静默替换脚本。

## 实际执行与观察

```bash
.venv/bin/python scripts/db/verify.py
.venv/bin/python -m unittest discover -s tests -v
PATH="$PWD/var/harness-tools/bin:$PATH" scripts/quality/check.sh
```

数据库验证实际通过 **58 项存储检查**，另通过 **1 项独立异常清理检查**；
现有 Python 产品回归 **31 项通过**。完整 Harness 通过：Shell 语法／ShellCheck／shfmt、
78 个 Markdown 文件、模板完整性（138 个跟踪文件、18 个知识实体）及全部离线工作流回归。

| 检查组 | 实际观察 |
| --- | --- |
| 初始化与权限 | 默认项目从空实例创建；项目账号实际连接，角色无超级用户等实例级能力，拥有表及索引，可建／改／读／写／删项目对象。 |
| 合成契约回放 | 基础样例的来源、文件、尝试、证据、解释、原文、执行、规则、分组、输入／配置、决策、五层统计、发布和任务关联成功保存。 |
| 原文与执行身份 | 同一 Q1 对应两个不同事件；原文一行、执行两行。错误摘要、重复文件逻辑记录和悬空原文引用被拒绝。 |
| 未知、零和精度 | NULL 加原因、真实 0、0.001 ms 与微秒时间分别往返；负值、NaN、正负 Infinity 和缺失原因被拒绝。 |
| 统计 | 17 列指标、五层结果、67 桶覆盖可存；非法小时、重复整体桶、分位数逆序、缺原因、虚假门槛及零分母原因不符被拒绝。 |
| 空样本 | 零有效且有排除的显式统计保留，全部指标 no_samples；真实零样本值保留，三个零分母指标用 NULL/zero_denominator。 |
| 版本引用 | 规则被引用时删除失败；跨集群、跨归一化决策失败；当前指针不能引用失败发布或错误成功时间，失败后原指针保留。 |
| 扩展边界 | 合成非 SQL profile 可以引用通用输入／配置／Build，normalization_id 为 NULL，无需伪造 SQL；不代表其他来源适配器已实现。 |
| 完整重跑 | 使用真实 SCRAM 项目账号重跑，数据数量、原 schema_version 时间和原密码摘要均保持。 |
| 同名冲突 | 角色高权限、数据库／schema／表所有权、索引定义、额外约束、列漂移和版本摘要冲突均报错；错误数据库 owner 未被接管。 |
| 部分结构 | 兼容 scope 表预置一行后初始化，缺失表补齐且该行保留。 |
| 失败恢复 | 在项目 DDL 提交前注入除零错误；库内 schema 回滚，管理员已创建的角色／库保留；恢复脚本后可重跑完成。 |
| 持久化与清理 | PG 重启后统计行保留；预期 schema 无残留。正常和人为异常路径均停止私有实例并清理精确临时目录。 |

最后一次验证正常目录为 /tmp/sql-apm-pg-lc7k6ygp，
异常路径目录为 /tmp/sql-apm-pg-7okyg1_x；两者均由脚本创建、停止后删除。
PG 仅监听本次私有 Unix socket，TCP 禁用；测试生成的随机 SCRAM 密码及密码文件
只存在于该临时目录和本次进程内，输出及仓库不包含真实凭据。

## 修正及证据限制

初次验证发现 PG17 的 convert_to 表达式不能用于 immutable 生成列。
现实现显式保存 SHA-256 并用 CHECK 验证与完整原文一致，相关回归已通过。
该修正不改变原文复用语义。

- measured：以上有界 SQL 行为、实际连接、约束、重跑、事务恢复和实例清理。
- qualitative：索引、numeric、text ID、复合引用、显式 Decision 存储和普通表的资源取舍。
- 未测量：真实日志入库量、吞吐、内存／磁盘增长、30 天完整构建耗时和长期留存容量。
- 未交付／未验证：生产解析、配对、SQL 归一化、训练判断、统计公式计算、
  业务快照不可变、任务互斥、完整发布事务和 Grafana 查询页面。
  fixture 中可靠／成功状态为人工预设，数据库引用约束不能证明事实正确。
- 临时实例异常验证覆盖可捕获异常；SIGKILL、宿主机断电、停止工具本身故障
  不属于自动清理通过结论。停止失败时保留目录便于恢复，不删除运行中实例。
- 原始需求快照 blob SHA 仍为
  `d5b8c6e4ef8d317110aec7737d49088d4a9308e4`；未扫描或修改生产日志。

在线 PR 契约检查、CI 和独立评审证据保存在对应 PR／Issue，
不把实施者自检当作独立评审或已合并。
