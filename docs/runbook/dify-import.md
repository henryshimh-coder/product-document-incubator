# Dify Workflow 导入与联通性手册

应用通过 Dify Workflow API 调用三个**独立治理**的工作流：导入（Ingest）、查询（Query）、自检（Lint）。调用方式为 `POST {DIFY_BASE_URL}/workflows/run`，请求体 `{"inputs": <下方输入契约>, "response_mode": "blocking", "user": <操作员标识>}`；三个工作流的 API Key 必须互不相同（启动时强校验，重复 Key 会被拒绝且 Key 不进入异常文本）。

> 本手册所有输入输出契约以 `src/infrastructure/gateways/schemas.py` 的 Pydantic 模型为唯一权威（`extra="forbid"`，多字段即拒绝）。第六节示例同时落为版本化 fixture（`docs/runbook/fixtures/dify/*.json`），由 `tests/unit/test_dify_runbook_fixtures.py` 逐份校验并做枚举变异反证，手册与代码不会漂移。

## 一、创建三个 Workflow 并取 Key

1. 在 Dify 控制台分别创建三个 **Workflow** 类型应用，建议命名：`产品智策-导入`、`产品智策-查询`、`产品智策-自检`。
2. 按第四节的最小节点映射编排各工作流，**发布**（只有已发布版本才能被 API 调用）。
3. 每个应用进入「访问 API / API Access」页，生成**各自独立**的 API Key（`app-` 前缀）。
4. 将三个 Key 填入项目根 `.env`（模板 `.env.example`；Key 只属于本地环境，绝不提交仓库、截图或日志）：

```text
DIFY_BASE_URL=https://api.dify.ai/v1
DIFY_INGEST_API_KEY=app-...
DIFY_QUERY_API_KEY=app-...
DIFY_LINT_API_KEY=app-...
```

应用在容器构建时自动加载项目根 `.env`（已存在的进程环境变量优先）；空 Key 时应用以本地治理模式启动（导入 / 查询 / 自检不可用，其余页面正常）。

## 二、输入契约（开始节点变量）

三个工作流公共字段：`schema_version`（固定 `"1.0"`）、`project_id`、`baseline_version`、`task_id`、`language`（固定 `"zh-CN"`）。

### 导入（Ingest）

| 字段 | 类型与约束 |
| --- | --- |
| `source` | 对象：`id`、`type`、`authority_level`、`document_version`、`document_date`（`YYYY-MM-DD`）、`applicable_scope` |
| `baseline_rules[]` | ≤20 条：`id`、`title`、`content`、`status`（固定 `"effective"`） |
| `source_chunks[]` | 1–20 段：`chunk_id`、`locator`、`text`（单段 ≤2000 字符） |

`authority_level` 枚举（**不是** L1/L2/L3，那是安全级别，不进入此契约）：

| 值 | 含义 |
| --- | --- |
| `formal_effective` | 正式生效文件 |
| `formal_decision` | 正式会议决定 |
| `professional_opinion` | 专业意见 |
| `discussion_reference` | 讨论参考 |

### 查询（Query）

| 字段 | 类型与约束 |
| --- | --- |
| `scope` | `effective` / `effective_with_notices` / `historical` |
| `question` | ≤500 字符 |
| `effective_cards[]` | ≤20 张：`id`、`title`、`content`、`source_citations[]`（该卡的来源引用 ID 列表） |
| `notices[]` | ≤20 条：`type`（`candidate` / `conflict`）、`id`、`summary` |
| `citations[]` | ≤50 条：`id`、`source_id`、`filename`、`document_version`、`section`、`excerpt`、`authority_level`（同上枚举） |

### 自检（Lint）

| 字段 | 类型与约束 |
| --- | --- |
| `input_contract_version` | 固定 `"2.0"`，必须与 `config/schema.yaml` 的 `lint_input_contract_version` 一致 |
| `baseline_rules[]` | ≤50 条基线侧引用：`id`、`source_id`、`citation_id`、`document_version`、`page_or_section`、`excerpt` |
| `comparison_items[]` | ≤50 条比对侧引用，结构同上 |
| `deterministic_findings[]` | 本地确定性发现：`id`、`rule_id`、`issue_type`、`severity`、`title`、`description`、`target_identity`、`locally_validated`（固定 `true`） |
| `allowed_issue_types[]` | ≤5 种：`conflict` / `omission` / `stale` / `not_synchronized` / `insufficient_evidence` |

## 三、输出契约（结束节点）

应用只读取响应 `data.outputs.result`（对象或 JSON 字符串）。**结束节点必须输出名为 `result` 的变量**，结构如下。

### 导入输出

| 字段 | 约束 |
| --- | --- |
| `schema_version` | 固定 `"1.0"` |
| `task_id` | 必须与输入 `task_id` 一致 |
| `summary` | 非空 |
| `items[]` | 每条：`item_id`、`item_type`（`professional_opinion` / `discussion_reference`）、`title`、`content`、`target_card_id`（可空）、`result_type`（`candidate` / `conflict_discussion` / `information_gap`）、`status`（`ai_inferred` / `candidate` / `conflict`）、`source_citations[]`（**至少 1 条**：`source_id`、`chunk_id`、`locator`、`excerpt`）、`confidence`（0–1）、`uncertainty`（可空） |
| `relations[]` | 每条：`source_id`、`relation_type`（`derived_from` / `supports` / `conflicts_with` / `proposes_change_to` / `approved_as` / `supersedes` / `impacts` / `to_be_verified_by`）、`target_id` |

### 查询输出

| 字段 | 约束 |
| --- | --- |
| `answer` | ≤500 字符；不得复述 notices 内容 |
| `effective_rules[]` | **生效卡片 ID 列表**（不是规则文本）：每个 ID 必须属于输入 `effective_cards[].id`，否则应用报 `UNKNOWN_EFFECTIVE_RULE`；且返回的 `citations` 必须与该卡 `source_citations` 有交集，否则报 `EFFECTIVE_RULE_CITATION_MISSING` |
| `citations[]` | 结构与输入 `citations` 相同；只允许返回输入中出现过的可信引用 |
| `candidate_notice` / `conflict_notice` | 可空；非空时必须逐字等于输入 `notices` 中对应 `summary` |
| `baseline_version` | 必须与输入一致 |
| `evidence_sufficiency` | `sufficient` / `partial` / `insufficient` |
| `result_mode` | `realtime` / `cache` / `local_only` |
| `model_call_id` | 可空 |

### 自检输出

| 字段 | 约束 |
| --- | --- |
| `schema_version` | 固定 `"1.0"` |
| `issues[]` | 每条：`issue_type`（须在 `allowed_issue_types` 内）、`severity`、`title`、`description`、`evidence[]`、`impacted_domains[]`（≥1）、`options[]`（`code` / `label` / `impact`）、`ai_recommendation`（可空）、`ai_confidence`（0–1，可空）、`uncertainty`（可空） |

`severity` 枚举（**不是** critical/major/minor）：

| 值 | 含义 |
| --- | --- |
| `blocking` | 阻断发布，必须人工决定 |
| `pending_decision` | 待会议决定 |
| `pending_info` | 待补充信息 |

`evidence[]` 每条：`source_id`、`citation_id`、`excerpt`、`document_version`、`page_or_section`、`side`。`side` 枚举（**不是** baseline/comparison）：`current_baseline`（基线侧）/ `challenging_source`（挑战来源侧）。重大问题要求同时包含两侧证据且来自至少两个不同来源。

## 四、最小节点映射

### 导入工作流

```text
开始节点：声明第二节导入输入的全部变量
  → 模型节点：阅读 source_chunks，对照 baseline_rules 产出候选/冲突条目；
     提示词必须要求：item 逐条附 source_citations（chunk_id 逐字取自输入）、
     confidence ∈ [0,1]、task_id 原样回传、不发明 chunk 中不存在的文本
  → 代码/模板节点（可选）：把模型输出整理为第三节导入输出结构
  → 结束节点：输出变量 result = 上述 JSON
```

### 查询工作流

```text
开始节点：声明第二节查询输入的全部变量
  → 模型节点：仅用 effective_cards 与 citations 回答 question；
     提示词必须要求：effective_rules 只填卡片 ID（来自输入 effective_cards[].id）、
     citations 只从输入 citations 原样选取、answer ≤500 字、
     证据不足时 evidence_sufficiency=insufficient
  → 结束节点：输出变量 result = 第三节查询输出结构
```

### 自检工作流

```text
开始节点：声明第二节自检输入的全部变量（含 input_contract_version="2.0"）
  → 模型节点：以 deterministic_findings 为锚，比较 baseline_rules 与
     comparison_items，发现冲突/缺失/过期等问题；
     提示词必须要求：severity 用 blocking/pending_decision/pending_info、
     每条 evidence 标注 side=current_baseline|challenging_source 且
     citation_id 逐字取自对应侧输入、重大问题双侧证据
  → 结束节点：输出变量 result = 第三节自检输出结构
```

## 五、blocking 响应结构

应用按 blocking 模式调用，HTTP 200 响应体：

```json
{
  "workflow_run_id": "运行 ID（应用记录为模型调用凭证）",
  "data": {
    "outputs": {
      "result": { "…第三节输出契约…" }
    }
  }
}
```

`result` 也接受 JSON 字符串（应用会自行解析）。非 200、超时（导入/自检 60s、查询 30s，`config/app.yaml`）或结构不符都会触发应用 fail-closed 错误码，不会写入半成品状态。

## 六、契约示例（版本化 fixture）

六份可直接对照的完整示例，已由自动测试逐份校验：

| 文件 | 内容 |
| --- | --- |
| `docs/runbook/fixtures/dify/ingest-input.json` | 导入输入（`authority_level=professional_opinion`） |
| `docs/runbook/fixtures/dify/ingest-output.json` | 导入输出（1 条 conflict 条目 + 回连关系） |
| `docs/runbook/fixtures/dify/query-input.json` | 查询输入（1 张生效卡 + 1 条引用） |
| `docs/runbook/fixtures/dify/query-output.json` | 查询输出（`effective_rules` 为卡片 ID，引用相交） |
| `docs/runbook/fixtures/dify/lint-input.json` | 自检输入（`input_contract_version=2.0`，双侧各 1 条引用） |
| `docs/runbook/fixtures/dify/lint-output.json` | 自检输出（blocking 冲突，双侧证据、两个来源） |

## 七、真实联通性验证（交付前必做）

```bash
uv sync --frozen && cp .env.example .env   # 填入三个真实互异 Key
uv run python scripts/reset_demo.py --snapshot initial
uv run streamlit run streamlit_app.py --server.headless true
```

在应用内依次实操，逐步保留非敏感证据（时间、workflow run ID、基线版本、错误码；**不得记录 API Key 或未脱敏材料**）：

1. **导入**页上传演示材料，实时分析成功并产出带引用的条目；
2. **查询**页实时提问，回答附基线版本与引用；
3. **自检**页一键自检，问题列表正常返回；
4. 完成「决定 → 变更单 → 审批 → 发布」，基线演进且发布后查询命中新版本、追溯主链完整；
5. 演练结束执行 `reset_demo.py` + `validate_data.py` + 全量测试，全部通过。

如实说明：T13 联合验收的外部模型侧为本地 mock 网关（fixtures 与本契约同构）；本节真实联通演练是交付前必做项，其证据与「干净设备可启动（HTTP 200）」是两类不同证据，交付清单分别记录。
