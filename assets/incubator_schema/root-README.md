# 产品文档孵化项目

## 项目身份

此目录是一项独立的产品文档孵化项目。项目 ID、名称和创建时间以
`.incubator/project.json` 为准；项目位置由中央控制面登记，不写入 Wiki 页面。

## 工作流

`归档 → Ingest → 孵化 → 发布`

1. 归档服务把原始材料写入 `raw/`，并记录完整性与来源元数据。
2. Ingest 将一份已归档材料转换为可追溯的 Wiki 来源页和主题页。
3. 孵化只使用已 Ingest 的 Wiki 证据生成候选文档。
4. 发布服务经 Owner 批准后原子更新当前版本与历史版本。

## 目录职责

- `raw/`：不可变原始材料；归档后仅供读取和完整性核验。
- `wiki/`：可浏览的来源、主题、索引、日志与待确认草稿。
- `schema/`：项目使用的可信页面模板与 Ingest 契约。
- `exports/`：Owner 导出的交付物。
- `.incubator/`：项目元数据、锁与可恢复事务记录；不手动编辑。

## Wiki 导航

- [Wiki 索引](wiki/index.md)
- [Wiki 日志](wiki/log.md)
- [来源页](wiki/sources/)
- [主题页](wiki/topics/)
- [本地 Ingest 草稿](wiki/drafts/local-ingest/)
- [Ingest Contract](schema/ingest-contract.md)

## 安全边界

Raw 不可修改，所有结论必须保留来源 ID 与可定位引用。不得复制其他项目正文、
业务数值或敏感内容。L3/L4 材料只能在本地处理，外部模型调用次数必须为零。
`wiki/current/` 与 `wiki/versions/` 仅由发布服务写入。

## Obsidian

可将此项目根目录作为 Obsidian vault 打开，阅读和编辑 `wiki/` 中的允许编辑页面。
编辑前先阅读 [项目规则](AGENTS.md) 与 [Ingest Contract](schema/ingest-contract.md)；
不要在 Obsidian 中改动 `raw/`、`.incubator/`、`wiki/current/` 或 `wiki/versions/`。
