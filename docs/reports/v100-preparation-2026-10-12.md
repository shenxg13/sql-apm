# v1.0.0 连接验证环境前的准备

对应 [Issue #59](https://github.com/shenxg13/sql-apm/issues/59)，从 #57 合并后的
`5494075f13c7030c3f0d1d084a4589a439745b62` 开始。用户明确本轮先完成连接验证环境之前的工作。
本文是实施与证据边界记录，不是 Issue 正文或执行状态镜像。目标机未连接，未合并、未正式发布。
以上是离线准备阶段的截止状态；环境恢复后的第一轮实测见[成品编译验证](v100-runtime-build-2026-10-12.md)。

## 实现

- v1.0.0 程序和验收包；完整包支持复用唯一 Kylin 成品，无成品时只能生成明确标识的编译候选。
- 成品导出保存系统版本、编译前后 RPM、命令和源码／wheel 摘要；清单记录文件内容、权限、链接及固定前缀引用。
  安装拒绝其他目录、已有目标、文件不符、归档越界及依赖锁不符；不调用 yum 或编译器。
- 自检依次检查 Python 系统库模块和全部已装依赖、PG 三个可执行文件、临时实例查询、程序包和十个随包入口。
  任一步失败停止，保留具体检查结果和输出，指向手工编译附录。临时实例确认停止后才清除。
- PostgreSQL systemd、两个账号的登录 psql 环境、操作者密码输入和 pgpass 转义；业务代码、结构、规则和传输脚本未改。
- 生产手册、演练手册、配置／使用／结构说明和同源发布说明；六份 HTML 分别提供生产与演练入口。

## 本地证据（measured，开发机）

记录保存在 `var/issue59/checks/`，制品在 `var/issue59/deliveries/`；这些目录被 Git 忽略。
原始 SQL、身份、连接细节和凭据不进入公开报告。

已执行：

- 新增安装工具有界回归 12 项通过，含短密码输入、转义、账号轮换保留、符号链接拒绝、文件／权限篡改、
  越界与重复归档成员、错误目录和模拟缺库停止，以及完整导出／安装回放和重复归档字节一致。模拟缺库只证明停止逻辑，不是目标机缺库验收。
- PostgreSQL 17.10 私有临时实例：两个角色各测试单字符、冒号、反斜杠、组合与保留空格，10 次 SCRAM 登录通过；
  错误密码被拒绝、只读角色 5 次写入被拒绝；实例停止清除。另有三个生成 profile 的实际 psql 用法通过：项目默认、只读覆盖和管理员默认；未改动真实账号的 home。测试没有访问已有库。
- 普通单元测试 104 项通过；Grafana 工具测试 5 项通过。
- 文档自动检查核对配置、62 张表、55 个查询函数及随包路径，另校验生产手册 Bash／内嵌 Python 语法和三份真实配置示例。
- 制包与 HTML 回归 37 项通过；重复制包和隔离安装结果见下节。

首次 Harness 检查在新增文件尚未加入索引时失败，原因是引用完整性只接收受版本管理的目标；
没有删除引用或放宽检查；新文件加入索引并修正新增脚本的 shfmt 格式和 Markdown 排版后，完整离线 Harness 通过。

## 固定候选与重复构建（measured）

首个检查候选为 `cec28b3acf351ae0e842a8f74fb7c8fdd04a113e`。
两次从干净固定提交构建，使用同一组源、391 个 RPM、wheel 和固定 Grafana／插件，三个归档分别字节一致：

| 制品 | 大小（字节） | SHA-256 |
| --- | --- | --- |
| 程序包 | 506670 | `04694ccbe6382540260ef532d427758b29d569a51cd345bf55319981c7b9d7c7` |
| 验收包 | 341906 | `a3e9a19d2141b13e58e51f94763fd6df915ec6b4e506cda5c4466af4a995e68b` |
| 编译候选包 | 972454320 | `501b1cb9be62d39e183dfd8fa04e363fcbe56ddd624302226e097612127dcdad` |

机器记录为 `var/issue59/checks/repeat-build.json`，制品位于
`var/issue59/deliveries/cec28b3acf351ae0e842a8f74fb7c8fdd04a113e/`。
它尚不含 Kylin 成品，manifest 中 `bundle_type=compilation-bootstrap`、`production_use=false`。
归档包含六份离线 HTML，固定第三方组件摘要按原清单逐项核对。

首次组装因旧 downloads 目录已清理而停止，原错误保存在 bundle-1／bundle-2 日志。
随后核对保留的 v0.3.0 最终完整包摘要，从中只恢复源码、wheel 和来源清单到
`var/issue59/inputs/offline-bundle/`，使用新输出目录构建成功；没有改动旧包或重新下载替代版本。
制包操作说明已更新为可重复的恢复步骤。

独立安装目录 `var/issue59/isolated-cec28b3/` 从两个归档解压，重新创建 venv、离线装入锁定 wheel，
程序校验为 127 个文件；十个入口 unit、database、publication、smoke、cleanup、search、views、grafana、daily、daily-recovery 全部退出 0。
发布检查 33 项、清理 23 项、daily 26 项、daily-recovery 16 项；每份输出摘要保存在
`var/issue59/checks/isolated-verification.json`，临时数据库均停止清理。项目检查入口也通过。
本候选之后的修改仅为文档与开发机验证脚本，最终包须通过 `--previous-program` 验证所有产品文件仍相同。

## Grafana 上游限制（measured + 官方资料）

对清单固定的 Grafana 13.2.3 用独立目录和回环接口检查：初始 admin 单字符可登录，
创建 viewer 时三个 1–3 字符合成用例返回 HTTP 400、`password.password-policy-too-short`，4 字符成功。
原始记录在 `var/issue59/password-probe/results.json`，诊断进程已经停止。
随后两个账号的四字符、含冒号、含反斜杠三组密码均经真实 `/login` 登录通过，
结果在同目录 `roundtrip-results.json`；这不改变 1–3 字符创建 viewer 被拒绝的事实。
[官方文档](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/grafana/)
也说明默认最低 4 字符。用户于 2026-10-12 明确选择“接受 Grafana 上游限制”，
[确认记录](https://github.com/shenxg13/sql-apm/issues/59#issuecomment-6117961397)与 Issue 正文已同步：
两个数据库账号接受任意非空单行密码；Grafana 的 admin、viewer 保留上游至少 4 字符的限制，
本项目不叠加其他长度或字符规则，也不修改固定组件绕过上游限制。手册与验收用例据此更新。

## 资源、兼容与恢复（qualitative）

这些改动不进入导入、归一化或统计热路径。导出、安装和校验按成品字节线性读写，
归档不带数据与日志；自检临时数据库和十个入口串行，不做真实数据回放。
失败保留命名的输出目录且不自动重试或编译，避免把当前主机悄悄升级为编译环境。
安装不覆盖已有运行时；发生部分安装时，操作者先核对目录，不盲目重跑覆盖。
文件原子替换只作用于明确的密码文件；数据库和文件不具备跨资源事务，失败诊断区分数据库未改与文件更新未完成。

生产二进制能否兼容初始系统库尚未实测。固定前缀扫描的实际路径清单只能在 Kylin 成品导出后提供；
当前工具实现不能冒充成品核实。32 GB 参数仍为未经验证建议，未新增性能门槛。

## 接续边界

目标机可用后按[演练手册](../runbooks/kylin-offline-deployment.md)执行一次编译与十个入口、导出固定成品、
恢复初始系统后二进制完整验证，再由用户恢复初始系统亲自按[生产手册](../runbooks/production-installation.md)验收。
还须记录系统包完全不变、真实系统缺库反例、九任务、每日／定时／传输、账号、检索与四看板、真实重启和补跑。
二进制成品、完整包重复构建、目标机 measured 结果和用户确认均未被本地结果替代。
正式版本合并与发布保持待完成，旧版本标签／附件未变。
