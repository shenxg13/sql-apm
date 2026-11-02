# v1.2.0 生产验收与文档收尾

来源：[#74 关闭前交接](https://github.com/shenxg13/sql-apm/issues/74#issuecomment-6574148359)、
[N11 范围变更及 N12 用户决定](https://github.com/shenxg13/sql-apm/issues/74#issuecomment-6573185120)和
[观察项最终处置更正](https://github.com/shenxg13/sql-apm/issues/74#issuecomment-6574427464)。

- [运行环境](../decisions/runtime-and-components.md#v120-带数据升级2026-10-27)更新生产升级判定依据：
  不带出升级记录，依结果包与用户确认完成验收；迁移模型仍未在生产实测。
  升级和制包手册同步，保留已有命令及不可回退边界。
- [存储主题](../architecture/postgresql-storage.md#查询读取结构-11302026-10-26)及
  [数据库结构说明](../../docs/design/database-structure.md)同步已发布 v1.2.0／结构 1.13.0 和带数据升级入口，落实 O-74-006。
- [检索主题](../features/sql-search-and-views.md#v120-生产复测与后续边界2026-11-02)记录已完成的生产复测，
  区分达到、未达到及未验证：首次排行超限由用户接受；统计读取缺陷已转 #73，尚未修复；
  工具包冲突和原最高频身份验证交后续 v1.3。sqlfind 手册链接本次实测和处置。
- O-74-002／003／004 经 #73 交后续发版，不由 #73 实施；不改 v1.2.0 已发布内容。
  本次只同步文档和知识，不实施性能修复或重新制包。
