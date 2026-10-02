# Kylin V10 SP2 离线部署验证（2026-10-02）

[Issue #31](https://github.com/shenxg13/sql-apm/issues/31) 的实施证据。
[操作手册](../runbooks/kylin-offline-deployment.md)、
[用户记录模板](../runbooks/kylin-validation-record.md)与
[机器记录](data/kylin-offline-deployment-2026-10-02.json)分别保存步骤、人工结果和脱敏实测。
本报告不代表生产部署，也不代替用户恢复快照后的独立执行。

## 输入与适用性

程序包固定为包含 #29 的 main `6451d140d44f4e06cc34862c3e5aff7593d7afeb`。
产品代码和规则未改；唯一已有验证代码改动为 verify_publication.py 的 --pg-bin，
默认路径保持开发机原值。部署辅助文件与手册作为有独立摘要的 support 交付。

[Alma 对照](data/kylin-alma-baseline-2026-10-02.json)来自
[#29 R2 最终 head 重放](https://github.com/shenxg13/sql-apm/pull/30#issuecomment-5939592275)。
其产品文件摘要与 main 合并树逐项相同；保存原机器记录摘要及九任务全部可比较计数。
没有拿早期含合成排除时段的统计报告作等值基准。

固定 55 个日志共 15,816,193,813 字节，仅传到已授权演练机的 /data/sql-apm/logs；
目标机逐项摘要与源端清单一致。程序包、RPM、日志、数据库和密码文件均未进入 Git。

## 观察与部署验证

以下为 observed：演练系统 Kylin V10 SP2 Build09、x86_64、glibc 2.28，
8 vCPU、约 14 GiB 内存，/data 为约 190 GiB XFS；免密 sudo 可用。
虚拟机快照已创建由用户提供，SSH 检查不证明快照可恢复。

以下为 measured／verified：

| 项目 | 实际结果 |
| --- | --- |
| RPM 收集 | 391 个；初始主机全部根包与依赖，全部签名通过；收集前后已安装包清单字节一致。 |
| 离线依赖安装 | 全部网络 yum 源禁用，仅启用 file:// 本地仓库且保留 gpgcheck；18 包安装、28 包升级，66 秒。 |
| 基础工具 | 用户在收集结束后安装 tar、createrepo_c；不把它们列为额外离线根包。 |
| Python | 官方源码构建 3.9.5，项目目录解释器，120 秒；不使用系统 Python 运行项目。 |
| Python 依赖 | ensurepip 离线引导；两个固定 wheel 以 no-index／require-hashes 安装；pip check 无冲突。 |
| 标准库 | SSL／哈希、压缩、SQLite、ctypes、readline、Decimal、UUID、时区、spawn 多进程均通过；无互联网探测。 |
| PostgreSQL | 官方源码构建 17.10，保留 ICU／readline／zlib／OpenSSL，147 秒。 |
| Kylin 自检 | unittest 66 项；verify.py 266 个 PASS；verify_publication.py 31 项，均退出 0。 |
| 开发机回归 | verify_publication.py 不带参数时 31 项通过。沙箱内私有实例启动失败，获准外部运行后通过，不归因于产品。 |
| 项目初始化 | bootstrap、schema、check 通过，结构 1.6.0；独立 postgres 系统用户，pg_ctl 管理。 |
| 认证 | 本机项目账号 SCRAM 成功，密码仅存程序目录外 0600 文件；演练实例没有 trust。 |
| 远程限制 | 只监听指定 LAN 地址，HBA 限定交接网段；跨机错误密码被拒绝。 |

Kylin 两项数据库自检使用私有临时实例，结束后停止清理；这些合成用例的局部 trust
不是演练实例的配置。未运行 Kylin 上的 *_full、parser_probe 或 Harness。

## 九任务与资源

串行执行 119 首批、三个日批、一次重建，再执行 120 首批及三个日批。
每步通过实际 CLI，立即查询 status/history，核对窗口、结果及前驱链，并比较 Alma 计数。
阶段耗时、数据库字节和文件系统占用由机器记录保存；总耗时包含 CLI 与后续核对。
这些为 measured，不设性能门槛；磁盘占用包含同一数据盘上的其他演练材料，不等同数据库大小。

九任务执行结果将在本次试跑结束后填入；目前不声明 K8／K9 已通过。

## 尚未完成与失败边界

- 跨机正确密码登录尚未验证：自动审批拒绝将真实密码文件复制到开发机临时目录，
  理由是用户尚未明确授权凭据导出。没有改为通过其他途径导出凭据。
- 自动审批曾拒绝递归取回整个 records 目录；随后仅导出已核对字段的版本、模块、
  测试项数、权限与耗时摘要。原始运行日志留在目标机，未把目录整体复制到仓库。
- 用户恢复快照后的 yum 源安装及两项编译、独立完整执行、DBA 工具验证尚未完成；
  对应 K3a、K6a、K11 不能标记全部通过。
- 生产内网源、不同初始系统上的 RPM 完整性、120 三十天首批成本和 aarch64 均未验证。
- 九任务之后的并行重建为可选，本次尚未执行；不据此增加已验证场景。

## 交付物与检查

离线包及清单留在开发机忽略目录 var/issue31，具体文件名、SHA-256 及用户验收交接写入 PR。
完整 Harness 已覆盖新增脚本与手册并通过；最终源码和报告提交前再核对变更及在线 PR 契约。
长期决定由[运行环境主题](../../.project-wiki/decisions/runtime-and-components.md)维护，
本报告区分用户提供事实、实际测量与仍待完成的验证。
