# Dify 产品文档工作流运行手册

## 配置

在运行环境中设置 `DIFY_BASE_URL` 与 `DIFY_DOCUMENT_API_KEY`。文档工作流使用
独立 Key，不依赖既有 Ingest、Query、Lint 的三把 Key；未配置时，1.x 功能照常可用，
“文档孵化”入口应提示 Owner 配置该 Key 后再启用。

`config/app.yaml` 的 `timeouts.document_seconds` 固定配置为 300 秒。服务端、页面和
日志中不得输出 API Key 或其派生值。

这里的 300 秒是一次 Dify 流式 HTTP 请求允许等待的总时长，不是页面轮询间隔。页面点击
“生成候选产品文档”后会立即把任务交给本地后台线程，Owner 不需要停留在当前页面等待。

## 工作流 A：产品方案草稿

输入为 JSON 对象，必须含：项目 ID、项目名称/说明、目标章节、当前产品文档（首版可为
`null`）以及经本地归档和安全筛选后的来源片段。每个来源片段只包含 `source_id`、
`chunk_id`、定位信息、摘录、固定材料类型及权威级别。材料类型仅可取平台定义的 8 类；新归档材料的
权威级别仅可取“正式基线依据”或“参考材料”。

仅已完成脱敏确认、权限标记为允许外部调用且安全等级为 L1/L2 的材料可进入工作流 A。L3/L4
材料不得发送到 Dify 或其他外部模型；它们只能在本地完成正文提取、对照与本地候选草稿创建。

输出 JSON 必须符合 `DocumentDraftWorkflowOutput`：

- `document_markdown` 必须以唯一 H1 开头；
- 每个引用必须逐字匹配输入中的来源、chunk、定位和摘录；
- `source_ids` 只能引用本次输入中的来源；
- 返回摘要、缺失章节和证据缺口。

应用保存本次请求与响应的脱敏 JSON、工作流运行 ID、耗时和模型调用日志；候选草稿仅写入
`wiki/drafts/`，绝不直接改写 `wiki/current/当前产品方案.md`。

### 调用模式与任务生命周期

工作流 A 必须使用 Dify `streaming` 响应模式。收到 `workflow_started` 事件后，应用立即把
`task_id` 和 `workflow_run_id` 写入中央 SQLite；收到 `workflow_finished` 且输出通过契约
校验后，应用才保存候选草稿。页面刷新只读取任务状态，不会重复发起同一项目的生成请求。

| 状态 | 含义 | Owner 页面表现 | 恢复规则 |
| --- | --- | --- | --- |
| `PENDING` | 已登记，后台线程尚未取得 Dify 运行 ID | 显示“生成中”，按钮禁用 | 超过 30 秒仍未启动时转为 `DOCUMENT_INCUBATION_START_TIMEOUT` |
| `RUNNING` | 已取得 `workflow_run_id`，Dify 正在执行 | 显示“生成中”，按钮禁用 | 页面刷新或应用重启后按运行 ID向 Dify 查询 |
| `SUCCEEDED` | 输出已校验且候选草稿已持久化 | 展示唯一候选草稿 | 重复刷新返回同一个 `draft_id`，不重复保存 |
| `FAILED` | 调用、超时或输出校验失败 | 只展示安全错误码，可重新生成 | 新请求建立新任务，历史记录保留供排障 |

数据库使用项目级活动任务唯一约束，因此同一项目最多只有一个 `PENDING` 或 `RUNNING`
任务。连续点击、刷新或多个页面同时操作均不得创建重复候选草稿。

### 刷新与应用重启恢复

刷新页面时，应用从中央 SQLite 读取当前任务。若本地后台线程仍在运行，继续展示处理中；
若应用已经重启且任务处于 `RUNNING`，应用使用已保存的 `workflow_run_id` 调用 Dify 运行查询
接口：

- Dify 仍在运行：保持 `RUNNING`；
- Dify 已成功：校验输出并幂等保存候选草稿，然后转为 `SUCCEEDED`；
- Dify 已失败或停止：转为 `FAILED`，仅保存和展示安全错误码；
- 网络暂时不可用：本次页面查询不创建新任务，保留原运行 ID，稍后刷新可再次恢复。

应用重启不会依赖内存线程找回任务，恢复依据始终是 SQLite 中的 `workflow_run_id`。

### Owner 操作与排障

1. 在“文档孵化”中选择已 Ingest 的 Wiki 页面，只点击一次“生成候选产品文档”。
2. 页面应在 1 秒内显示“候选产品文档生成中，请稍候”，生成按钮处于禁用状态。
3. 可以直接刷新页面；同一任务应继续显示，且不会再次调用 Dify。
4. 在 Dify 的“产品文档草稿”应用日志中按项目 ID 查找运行记录，打开记录核对运行 ID。
5. 必要时用下列只读命令核对本地任务；把数据库路径替换为当前项目库实际路径：

   ```bash
   sqlite3 <中央数据库路径> "SELECT id,status,dify_task_id,workflow_run_id,draft_id,error_code,updated_at FROM document_incubation_jobs ORDER BY created_at DESC LIMIT 5;"
   ```

6. Dify 日志中的运行 ID 应与 `workflow_run_id` 一致。Dify 成功后刷新页面，应只出现一份
   候选草稿。

常见安全错误码包括 `DOCUMENT_INCUBATION_START_TIMEOUT`、`MODEL_TIMEOUT:DIFY_TIMEOUT`、
`MODEL_OUTPUT_INVALID:DIFY_RESPONSE_INVALID` 和 `EXTERNAL_CALL_DENIED:DIFY_TRANSPORT_FAILED`。
页面不得展示堆栈、响应正文、API Key 或其他敏感细节；详细原因在本地受控日志和 Dify
运行日志中核对。

## 工作流 B：结构完善建议

输入只允许当前文档的章节标题和其他项目的章节标题摘要，不得传入任何产品 Markdown、
原文材料或全文。输出最多 20 条结构建议，每条包含标题、原因、参考项目 ID 和置信度；
输出中不得携带产品 Markdown。

工作流返回的参考项目 ID 必须属于本次输入。契约校验失败、超时或 Dify 返回不合法数据时，
应用以失败处理，不保存候选草稿，也不覆盖当前生效版本。
