# v1.0.0 生产部署准备

来源：[Issue #59](https://github.com/shenxg13/sql-apm/issues/59) 的用户确认和先完成离线准备的实施授权。

- [运行环境与组件](../decisions/runtime-and-components.md#首个生产版本与二进制部署2026-10-12)维护原生部署、一次编译成品、固定路径、自启、资源参考和三轮验收要求。
- [命令行与配置](../features/operator-cli.md#生产安装账号与交互环境2026-10-12)维护操作者密码、pgpass 转义和登录 psql 默认环境。
- [生产手册](../../docs/runbooks/production-installation.md)与演练手册分离；[准备报告](../../docs/reports/v100-preparation-2026-10-12.md)区分开发机验证、上游限制与未执行的目标机验收。

没有修改业务规则、结构或 Harness 工作流。Grafana 上游密码限制的提议以在线确认推进，不把提议视为既成决定。
