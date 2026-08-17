# Wiki Ingest Contract 2.2

## 目的与范围

Ingest 将**一份已经归档且完整性已校验的来源**转换为可追溯 Wiki 变更集。它不发布产品文档，
不改写 Raw，不取代现有 ImportSource、Query 或 Lint 链路。

## 输入

- 当前项目 ID、来源 ID、请求人；来源必须属于当前项目。
- 已归档 Raw 的相对路径、SHA-256、材料元数据、安全等级、脱敏与外发授权。
- 当前 Ingest Schema 版本、允许外发的索引/相关主题安全投影，以及本契约。

开始前必须重新核验 Raw 路径在当前项目 `raw/` 内且 SHA-256 匹配。L1/L2 仅在已脱敏、
已授权且项目允许外发时可使用外部 Ingest；L3/L4 仅走本地草稿与 Owner 确认，外部调用数为零。

## 输出

成功提交的 `WikiChangeSet` 必须只写入：

- `wiki/sources/` 中恰好一份来源页；
- `wiki/topics/` 中零份或多份主题页；
- `wiki/index.md`、`wiki/log.md` 与 `.incubator/source-index.json`。

每项变更包含项目内规范化相对路径、create/replace 操作、前后 SHA-256。来源页、主题页、
索引、日志、冲突数和证据缺口数由同一事务提交。不得写入 Raw、Schema、`wiki/current/`、
`wiki/versions/`、候选文档或发布 Manifest。

## 状态

| 状态 | 含义 |
| --- | --- |
| `pending_ingest` | 已归档，等待 Ingest。 |
| `ingesting` | 已取得项目 Ingest 锁，正在构建或提交。 |
| `ingested` | Wiki 变更已提交，来源页和主题页可供后续本地流程使用。 |
| `ingest_failed` | 失败且 Wiki 正式页保持不变；记录安全错误码。 |
| `reingest_recommended` | Schema 或来源改变，旧 Wiki 仍可读，等待 Owner 明确重新 Ingest。 |
| `local_review_required` | L3/L4 本地草稿已创建，等待 Owner 在本地确认。 |

历史项目的 `archived` 状态保持可读；新 2.2 项目归档后使用 `pending_ingest`。

## 幂等性

幂等键为 `SHA-256(project_id + source_id + raw_sha256 + ingest_schema_version)`。
同一幂等键已有成功运行时，返回已提交结果，不再次调用模型、不新增日志或重复段落。Schema
版本升级会产生新键，并将来源标为 `reingest_recommended`，直到 Owner 重新 Ingest。

## 失败与恢复

任何预检、授权、抽取、模型、校验或提交失败都不得修改正式 Wiki 页面；来源标为
`ingest_failed` 并只记录安全错误码。事务日志依次记录 `building`、`prepared`、
`files_committed`、`database_committed`、`committed`；失败走 `rolling_back` 到 `rolled_back`。
无法确定的磁盘/数据库状态必须标为 `recovery_required`，不得覆盖 Owner 的未知编辑。
