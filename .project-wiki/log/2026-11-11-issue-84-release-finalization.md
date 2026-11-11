# v1.3.1 发布文档定稿

按 [Issue #84](https://github.com/shenxg13/sql-apm/issues/84) 范围第 8、9、10、17 项，
实现 PR #85 经 [R2 批准](https://github.com/shenxg13/sql-apm/pull/85#issuecomment-6796839201)
合并为 `d73e6fd`，该提交的 main CI 通过。

- [v1.3.1 发布说明](../../docs/releases/v1.3.1.md)补齐已完成的用户验收、独立评审及实现合并事实，
  保留开发机／验证机证据与生产未验证边界。
- [v1.3.0 发布说明](../../docs/releases/v1.3.0.md#勘误2026-11-11)追加日期勘误，区分空库开发测量与生产观察；
  正式发布时再追加 GitHub 正文勘误，旧标签、附件和包内文档不变。
- [存储主题](../architecture/postgresql-storage.md)、[物理设计](../../docs/design/postgresql-storage.md)及
  [结构说明](../../docs/design/database-structure.md)按程序版本固定结构映射，补齐 v1.3.0／1.14.0 和 v1.3.1／1.15.0，
  历史升级说明指向相应标签的手册。

本次只改文档；正式制包从本次文档 PR 独立评审合并且 main CI 通过的提交生成。
生产升级和效果仍按 V10–V11 验收，不以文档定稿宣告完成；Harness 流程未变。
