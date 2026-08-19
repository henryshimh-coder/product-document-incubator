# 产品文档孵化器项目规则

本项目采用本地优先的 Raw / Wiki / Schema 三层结构。对 2.2 Wiki 页面，先阅读
`ingest-contract.md`；来源、主题、索引和日志必须由同一来源 Ingest 事务保持一致。

- `raw/` 是不可变来源，只允许系统追加，AI 只读。
- `wiki/drafts/` 保存候选产品文档，未经 Owner 批准不得成为当前版本。
- `wiki/current/` 只允许发布服务原子写入。
- `wiki/versions/` 只追加已生效历史版本。
- `wiki/sources/` 保存可追溯的单来源页面；`wiki/topics/` 保存带引用的主题聚合页面。
- `wiki/drafts/local-ingest/` 只保存 L3/L4 待确认本地草稿，未确认前不能参与孵化。
- 任何总结、建议或修改都必须保留来源引用；证据不足时明确标注缺口。
- 不自动消解冲突；保留相互矛盾的来源、定位引用和 Owner 待决项。
- 不得把其他项目的正文、业务数值或敏感内容复制到本项目。
- L3/L4 或未授权内容不得外发、不得构造外部 Gateway，模型调用次数必须为零。
- 禁止写入 `raw/`、`schema/`、`wiki/current/`、`wiki/versions/` 和发布 Manifest；只能通过对应的归档、可信资产或发布服务更新。
