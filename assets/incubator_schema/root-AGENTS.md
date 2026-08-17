# 项目 Wiki 操作规则

在创建、编辑或确认任何 Wiki 页面前，先阅读
[`schema/ingest-contract.md`](schema/ingest-contract.md)。

- `raw/` 是归档后的只读证据：不得修改、重命名、删除或以摘要替换原件。
- 一次 Ingest 只处理一个来源；来源页、主题页和索引变更必须能追溯到该单一来源。
- 每项事实、结论、冲突和证据缺口都要使用来源 ID 与可定位章节引用；没有证据时明确标记为缺口。
- 不自动消解冲突。保留相互矛盾的陈述、来源和 Owner 待决项。
- 只读取和写入当前项目目录；禁止复制其他项目的正文、数值、索引或敏感内容。
- L3/L4 或未授权内容只允许本地 Markdown/Obsidian 流程；不得构造外部 Gateway、外发正文或写入模型调用日志。
- 不得写入 `wiki/current/`、`wiki/versions/`、发布 Manifest、`schema/` 或 `raw/`；这些位置分别只由发布服务、可信资产和归档服务管理。
- `.incubator/transactions/`、`.incubator/locks/` 由系统管理。不要删除、覆盖或手工“修复”事务记录。
