# 候选产品文档流式孵化与任务恢复设计

## 1. 目标

将候选产品文档生成从单次阻塞请求改造成可观察、可恢复、不可重复提交的长任务流程。Owner 点击“生成候选产品文档”后，页面立即显示处理中；刷新或重新打开页面后仍能看到同一个任务的真实状态，成功后自动恢复候选文档，失败后可安全重试。

## 2. 已确认范围

- Dify 文档工作流使用 `response_mode=streaming`。
- 收到 `workflow_started` 后立即保存 `task_id` 与 `workflow_run_id`。
- 同一项目同一时间最多一个 `pending` 或 `running` 孵化任务。
- 页面显示“准备提交”“生成中”“已完成”“失败”四类状态。
- 处理中禁用生成按钮，避免重复点击。
- 页面刷新或本地应用重启后，根据持久化任务恢复状态。
- 有 `workflow_run_id` 时，通过 Dify 查询运行详情并恢复最终输出。
- 无 `workflow_run_id` 且本地执行已中断的陈旧任务，标记失败并允许重试。
- 不引入 Redis、Celery、消息队列或新服务；仅使用现有进程内后台线程和中央 SQLite。

## 3. 不在本次范围

- 多机器任务接管。
- 多 Owner 协作、权限体系或任务分配。
- 通用后台任务平台。
- 在线取消 Dify 工作流。
- 对 Wiki Ingest 工作流进行相同改造。
- 修改候选文档业务 Schema、引用校验或发布规则。

## 4. 架构

### 4.1 分层职责

1. `DifyClient`
   - 发送流式请求。
   - 解析 SSE 的 `workflow_started`、`workflow_finished`、`workflow_failed`、`error` 与 `ping`。
   - 通过回调上报 `task_id`、`workflow_run_id`。
   - 提供 `get_run(workflow_run_id)` 查询最终状态与输出。
   - 保持现有脱敏、鉴权错误映射和异常信息不泄密约束。

2. `DocumentWorkflowGateway`
   - 继续承担文档输入、输出和引用的业务校验。
   - 允许向通信层传入运行标识回调。
   - 将流式结束后的输出转换为现有 `{workflow_run_id, result}` 契约。

3. `DocumentIncubationJobRepository`
   - 持久化本地任务、来源材料、状态、Dify 标识、候选文档标识与安全错误码。
   - 通过 SQLite 部分唯一索引保证一个项目只有一个活动任务。
   - 所有状态变化均通过条件更新完成，防止并发线程重复收口。

4. `DocumentIncubationCoordinator`
   - `start()` 原子创建任务并启动单个后台线程。
   - 后台线程调用现有 `IncubateDocument.execute()` 完成生成和落盘。
   - `get_current()` 读取任务并在必要时向 Dify 查询恢复。
   - 进程内注册表只负责避免重复启动线程，不作为事实来源；SQLite 才是事实来源。

5. Streamlit 页面
   - 点击后只创建任务，不等待模型完成。
   - 立即 rerun，展示任务状态卡与开始时间。
   - 活动任务存在时禁用生成按钮。
   - 每次页面运行时调用 `get_current()`，因此刷新后仍可恢复。

### 4.2 状态机

```text
pending -> running -> succeeded
                  \-> failed

pending --本地进程中断且无 workflow_run_id--> failed
running --Dify 查询 succeeded---------------> succeeded
running --Dify 查询 failed/stopped----------> failed
running --Dify 查询 running-----------------> running
```

终态不可回退。`succeeded` 必须关联一个已写入数据库的候选文档；`failed` 只记录安全错误码，不保存 Dify 原始错误正文。

## 5. 数据模型

新增 `document_incubation_jobs`：

| 字段 | 用途 |
|---|---|
| `id` | 本地任务 ID，格式 `INCUBATION-...` |
| `project_id` | 项目 ID |
| `source_ids_json` | 本次选中的 Wiki 来源 ID |
| `requested_by` | Owner 名称 |
| `status` | `pending/running/succeeded/failed` |
| `dify_task_id` | Dify `task_id`，可空 |
| `workflow_run_id` | Dify `workflow_run_id`，可空 |
| `draft_id` | 成功生成的候选文档 ID，成功时非空 |
| `error_code` | 安全错误码，失败时可用 |
| `created_at` | 创建时间 |
| `started_at` | 开始调用时间 |
| `updated_at` | 最近状态更新时间 |
| `finished_at` | 终止时间 |

建立部分唯一索引：项目存在 `pending` 或 `running` 任务时，不允许创建第二条活动任务。

## 6. 恢复规则

- 页面刷新：直接读取 SQLite；如果任务仍由当前进程线程执行，仅展示状态。
- 本地进程重启：
  - 有 `workflow_run_id`：查询 Dify 运行详情。
  - Dify 已成功：重新使用返回输出完成现有校验和候选文档落盘。
  - Dify 已失败或停止：本地任务转为失败。
  - Dify 仍在运行：保持运行中，后续页面刷新继续查询。
  - 无 `workflow_run_id`：超过配置的启动宽限时间后转为失败，避免永久锁死。
- 同一 Dify 成功结果最多落盘一次；任务条件更新和 `draft_id` 用于幂等保护。

## 7. 超时和连接策略

- 文档工作流总等待时间继续使用已确认的 `document_seconds=300`。
- SSE 读取超时允许覆盖 Dify 心跳间隔；`ping` 不改变业务状态。
- HTTP 连接建立失败、超时、认证失败与输出非法继续映射到既有安全错误码。
- 不把 API Key、Dify 原始错误正文或材料正文写入任务表和页面。

## 8. 页面交互

- 无活动任务：显示可用的“生成候选产品文档”。
- 点击成功创建任务：显示“候选产品文档生成中，请勿重复提交”，按钮禁用。
- 运行中：显示开始时间和当前阶段；刷新页面不会新增任务。
- 成功：显示“已生成候选版本 …”，并继续使用现有候选文档编辑区。
- 失败：显示安全错误码和“可重新生成”的提示，按钮恢复可用。

## 9. 验收标准

1. Dify 请求体为 `response_mode=streaming`，能够解析分块 SSE。
2. 首个 `workflow_started` 事件后，数据库能查到两个 Dify 标识。
3. 一个项目并发点击两次，只产生一条活动任务和一次外部调用。
4. 点击后页面在模型完成前显示处理中且按钮禁用。
5. 刷新页面不会丢失状态，也不会重复调用 Dify。
6. 模拟应用重启后，可通过 `workflow_run_id` 恢复成功或失败状态。
7. 成功结果只生成一个候选文档，引用与 Markdown 校验规则保持不变。
8. 失败后能够重试，且页面和日志不泄露密钥或 Dify 原始敏感错误。
9. 现有全量测试无回归。
