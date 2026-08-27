# Document Incubation Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将候选产品文档生成改造成流式、可观察、可恢复且项目级防重复的长任务流程。

**Architecture:** Dify 通信层解析 SSE 并上报运行标识；中央 SQLite 持久化孵化任务状态；单进程协调器用一个后台线程执行现有候选文档生成用例，并在页面刷新或进程重启后查询 Dify 恢复结果。Streamlit 页面只负责启动任务和渲染持久化状态，不再同步等待完整模型响应。

**Tech Stack:** Python 3.12、httpx SSE 行流、SQLite、Pydantic、Streamlit、pytest、filelock/SQLite 事务。

**Spec:** `docs/superpowers/specs/2026-08-26-document-incubation-streaming-design.md`

## Global Constraints

- 同一项目同一时间最多一个 `pending` 或 `running` 文档孵化任务。
- SQLite 是任务事实来源；内存注册表只能防止当前进程重复启动线程。
- 不引入 Redis、Celery、消息队列、WebSocket 或新服务。
- 继续使用 `config/app.yaml` 中 `document_seconds: 300`。
- 不修改 Wiki Ingest、Raw 只读、L3/L4 本地处理和候选文档发布规则。
- Dify API Key、原始异常正文和完整材料内容不得进入任务表、页面错误或日志。
- 保留当前工作区所有 2.3 未提交改动，不进行重置或覆盖。

---

### Task 1: Dify 流式通信与运行详情查询

**Files:**
- Modify: `src/application/ports/workflow_gateway.py`
- Modify: `src/infrastructure/gateways/dify_client.py`
- Modify: `src/infrastructure/gateways/_common.py`
- Modify: `src/infrastructure/gateways/document_gateway.py`
- Test: `tests/integration/gateways/test_dify_client.py`
- Test: `tests/integration/gateways/test_document_gateway.py`

**Interfaces:**
- Produces: `DifyClient.run(..., on_started: Callable[[str, str], None] | None = None) -> dict[str, Any]`
- Produces: `DifyClient.get_run(*, workflow_run_id: str, user: str, timeout_seconds: int) -> dict[str, Any]`
- Produces: `DocumentWorkflowGateway.generate_draft(inputs, *, on_started=None) -> dict[str, Any]`

- [x] **Step 1: 编写流式响应失败测试**

新增测试，使用 `httpx.MockTransport` 返回由 `workflow_started`、`ping`、`workflow_finished` 组成的 SSE 字节流，断言请求体为 `response_mode=streaming`、回调收到 `TASK-001/WF-001`、最终返回现有结果契约。

- [x] **Step 2: 运行测试并确认因仍使用 blocking 而失败**

Run: `../../.venv/bin/pytest -q tests/integration/gateways/test_dify_client.py`

- [x] **Step 3: 实现最小 SSE 解析器**

逐行解析 `event:` 与 `data:`，忽略空行和 `ping`，遇到 `workflow_started` 调用回调；遇到 `workflow_finished` 提取 `data.outputs.result`；`workflow_failed/error` 映射为不包含原始正文的安全错误。

- [x] **Step 4: 增加运行详情查询测试与实现**

测试 `GET /workflows/run/WF-001` 的 `running/succeeded/failed` 三种状态；实现返回 `{workflow_run_id, status, result?}`，只在成功且存在输出时解析结果。

- [x] **Step 5: 贯通文档 Gateway 回调并验证输出规则不变**

调整共享 `invoke` 和 `DocumentWorkflowGateway.generate_draft` 的可选回调；保留 H1、来源 ID 和逐条引用校验。

- [x] **Step 6: 运行网关测试**

Run: `../../.venv/bin/pytest -q tests/integration/gateways/test_dify_client.py tests/integration/gateways/test_document_gateway.py`

Expected: PASS。

### Task 2: 孵化任务领域模型、数据库迁移与仓储

**Files:**
- Modify: `src/domain/enums.py`
- Modify: `src/domain/incubator.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Test: `tests/integration/db/test_migrations.py`
- Create: `tests/integration/db/test_document_incubation_job_repository.py`

**Interfaces:**
- Produces: `DocumentIncubationJobStatus(PENDING, RUNNING, SUCCEEDED, FAILED)`
- Produces: `DocumentIncubationJob`
- Produces: `SqliteDocumentIncubationJobRepository.create(job)`, `get(id)`, `get_active(project_id)`, `get_latest(project_id)`, `mark_started(...)`, `mark_succeeded(...)`, `mark_failed(...)`

- [x] **Step 1: 编写迁移和模型失败测试**

断言新表包含设计文档列，状态约束只接受四种状态；断言部分唯一索引拒绝同一项目的第二条活动任务但允许历史终态共存。

- [x] **Step 2: 运行测试并确认表不存在**

Run: `../../.venv/bin/pytest -q tests/integration/db/test_migrations.py tests/integration/db/test_document_incubation_job_repository.py`

- [x] **Step 3: 添加模型与迁移**

新增任务模型和 `document_incubation_jobs` 表；使用 `WHERE status IN ('pending','running')` 的唯一索引保证项目级单任务。

- [x] **Step 4: 实现条件状态更新仓储**

`mark_started` 仅允许 `pending -> running`；`mark_succeeded/mark_failed` 仅允许活动态转终态；终态重复收口不得创建第二份结果。

- [x] **Step 5: 覆盖竞争和幂等测试**

两个数据库连接并发创建同一项目任务时只允许一个成功；重复成功更新保持同一个 `draft_id`；失败错误只存安全错误码。

- [x] **Step 6: 运行数据库测试**

Run: `../../.venv/bin/pytest -q tests/integration/db/test_migrations.py tests/integration/db/test_document_incubation_job_repository.py`

Expected: PASS。

### Task 3: 现有孵化用例暴露流式标识与幂等完成入口

**Files:**
- Modify: `src/application/use_cases/incubate_document.py`
- Modify: `src/application/dto/documents.py`
- Test: `tests/integration/use_cases/test_incubate_document.py`

**Interfaces:**
- Consumes: Task 1 的 `on_started` 回调。
- Produces: `IncubateDocument.execute(command, *, on_started=None) -> IncubationView`
- Produces: `IncubateDocument.complete_from_workflow(command, workflow_response) -> IncubationView`

- [x] **Step 1: 编写回调和恢复完成失败测试**

断言首次流式启动时回调收到标识；断言传入已完成 Dify 响应时无需再次调用外部网关即可生成候选文档。

- [x] **Step 2: 运行测试确认接口尚不存在**

Run: `../../.venv/bin/pytest -q tests/integration/use_cases/test_incubate_document.py`

- [x] **Step 3: 拆分“构建输入、调用模型、完成落盘”三个内部阶段**

保持现有版本号、引用标题校验、Markdown 写入回滚、模型调用审计和日志内容；正常执行与恢复执行复用同一个完成落盘函数。

- [x] **Step 4: 保证恢复完成幂等**

恢复路径由上层任务仓储持有 `draft_id`；已完成任务不得再次进入落盘。用例自身继续依赖 `UNIQUE(project_id, version_id)` 作为最后防线。

- [x] **Step 5: 运行孵化用例测试**

Run: `../../.venv/bin/pytest -q tests/integration/use_cases/test_incubate_document.py`

Expected: PASS。

### Task 4: 后台协调器、项目级防重复与 Dify 恢复

**Files:**
- Create: `src/application/use_cases/manage_document_incubation_job.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/application/container.py`
- Test: `tests/integration/use_cases/test_manage_document_incubation_job.py`

**Interfaces:**
- Consumes: Task 2 的任务仓储、Task 3 的孵化用例、Task 1 的运行详情查询。
- Produces: `DocumentIncubationCoordinator.start(command) -> DocumentIncubationJob`
- Produces: `DocumentIncubationCoordinator.get_current(project_id) -> DocumentIncubationJob | None`
- Produces: `DocumentIncubationCoordinator.get_result(job_id) -> IncubationView | None`

- [x] **Step 1: 编写立即返回与单任务失败测试**

使用阻塞事件控制假网关，断言 `start()` 在模型完成前返回 `pending/running`；连续调用两次只启动一个线程并返回同一活动任务。

- [x] **Step 2: 运行测试确认协调器不存在**

Run: `../../.venv/bin/pytest -q tests/integration/use_cases/test_manage_document_incubation_job.py`

- [x] **Step 3: 实现进程内线程注册表和后台执行**

模块级注册表以 `db_path + project_id` 为键并受 `threading.Lock` 保护；线程启动前先由 SQLite 原子创建任务。后台线程保存 Dify 标识，成功后关联 `draft_id`，异常后仅保存安全错误码。

- [x] **Step 4: 实现刷新/重启恢复**

`get_current()` 发现活动任务且当前进程无对应线程时：有 `workflow_run_id` 就查询 Dify；成功则调用 `complete_from_workflow`，失败或停止则收口失败，仍运行则原样返回；无标识且超过启动宽限期则失败。

- [x] **Step 5: 覆盖重启、恢复成功和失败测试**

清空内存注册表模拟进程重启，验证不会发起第二次 Dify 运行；恢复成功只生成一个候选文档；Dify 失败后按钮可再次创建新任务。

- [x] **Step 6: 注入 AppContainer**

保留现有 `incubate_document` 供候选编辑功能使用，新增 `document_incubation_jobs` 协调服务供页面启动和查询。

- [x] **Step 7: 运行协调器测试**

Run: `../../.venv/bin/pytest -q tests/integration/use_cases/test_manage_document_incubation_job.py`

Expected: PASS。

### Task 5: Streamlit 处理中状态、按钮禁用与刷新恢复

**Files:**
- Modify: `src/ui/pages/incubate.py`
- Modify: `tests/e2e/test_incubate_page.py`

**Interfaces:**
- Consumes: `container.document_incubation_jobs.start/get_current/get_result`。
- Produces: 页面任务状态卡、禁用的 `incubate_generate` 按钮和终态反馈。

- [x] **Step 1: 编写页面立即反馈失败测试**

用可控假协调器让任务保持 `running`，断言点击后页面出现“候选产品文档生成中”，生成按钮 `disabled=True`，且重复 rerun 不增加 `start()` 次数。

- [x] **Step 2: 编写刷新恢复失败测试**

首次渲染直接返回持久化运行中任务，断言无需点击即可显示处理中；返回成功任务时显示现有候选编辑区；失败任务显示安全错误码且按钮恢复。

- [x] **Step 3: 运行页面测试确认当前同步行为失败**

Run: `../../.venv/bin/pytest -q tests/e2e/test_incubate_page.py`

- [x] **Step 4: 改造页面交互**

页面顶部先查询当前任务；活动态渲染 `st.status`/信息卡并禁用按钮；点击只调用 `start()` 后 `st.rerun()`；成功后读取已有候选文档；失败只显示安全错误码和可重试说明。

- [x] **Step 5: 运行页面测试**

Run: `../../.venv/bin/pytest -q tests/e2e/test_incubate_page.py`

Expected: PASS。

### Task 6: 文档、回归和人工验收

**Files:**
- Modify: `docs/runbook/dify-document-workflow.md`
- Modify: `docs/qa/product-document-incubator-2.2-acceptance.md`（若存在对应运行说明段落）
- Modify: `docs/superpowers/plans/2026-08-26-document-incubation-streaming.md`

**Interfaces:**
- Consumes: Task 1-5 的最终实现。
- Produces: 可复现的配置、故障恢复和人工验收步骤。

- [x] **Step 1: 更新运维说明**

记录流式模式、300 秒总等待、任务状态表、应用重启后的恢复规则，以及如何用 Dify 日志核对 `workflow_run_id`。

- [x] **Step 2: 运行聚焦测试**

Run: `../../.venv/bin/pytest -q tests/integration/gateways/test_dify_client.py tests/integration/gateways/test_document_gateway.py tests/integration/db/test_document_incubation_job_repository.py tests/integration/use_cases/test_incubate_document.py tests/integration/use_cases/test_manage_document_incubation_job.py tests/e2e/test_incubate_page.py`

Expected: PASS。

- [x] **Step 3: 运行静态检查和格式检查**

Run: `../../.venv/bin/ruff check src/application/ports/workflow_gateway.py src/infrastructure/gateways/dify_client.py src/infrastructure/gateways/_common.py src/infrastructure/gateways/document_gateway.py src/domain/enums.py src/domain/incubator.py src/application/ports/incubator.py src/infrastructure/db/migrations.py src/infrastructure/db/repositories.py src/application/use_cases/incubate_document.py src/application/use_cases/manage_document_incubation_job.py src/application/container.py src/ui/pages/incubate.py tests/integration/gateways/test_dify_client.py tests/integration/gateways/test_document_gateway.py tests/integration/db/test_document_incubation_job_repository.py tests/integration/use_cases/test_incubate_document.py tests/integration/use_cases/test_manage_document_incubation_job.py tests/e2e/test_incubate_page.py`

Run: `../../.venv/bin/ruff format --check` on the same file set.

- [x] **Step 4: 运行全量测试与 Git 空白检查**

Run: `../../.venv/bin/pytest -q`

Run: `git diff --check`

Expected: 全量测试通过且无新增空白错误。

- [x] **Step 5: 本地人工验收**

在 Owner 项目中选择已 Ingest Wiki，点击生成后 1 秒内看见处理中；刷新页面仍显示同一任务；再次点击不可用；Dify 完成后刷新得到唯一候选文档；模拟应用重启后能根据 `workflow_run_id` 恢复。

复核记录：本地 8512 端口页面烟测通过；为避免重复外发项目 Wiki，本轮未再次触发真实
Dify 调用。处理中、刷新恢复、重启恢复、唯一候选草稿由集成/E2E 回归与 Owner 此前提供的
Dify 成功运行记录共同覆盖。

- [x] **Step 6: 更新本计划勾选状态并提交验收摘要**

记录最终测试数量、最终 SHA、已知质量债务和是否允许合并。
