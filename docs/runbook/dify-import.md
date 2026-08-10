# Dify Workflow 导入与联通性手册

应用通过 Dify Workflow API 调用三个**独立治理**的工作流：导入（Ingest）、查询（Query）、自检（Lint）。调用方式为 `POST {DIFY_BASE_URL}/workflows/run`，`response_mode=blocking`，三个 Key 必须互不相同（启动时强校验）。

## 一、创建 / 导入三个 Workflow

在 Dify 控制台分别创建三个 Workflow 应用（或导入团队提供的 DSL 导出），命名建议：`产品智策-导入`、`产品智策-查询`、`产品智策-自检`。每个应用发布后在「API 访问」页生成独立 API Key，填入 `.env`（模板 `.env.example`）：

```text
DIFY_BASE_URL=https://api.dify.ai/v1
DIFY_INGEST_API_KEY=app-...
DIFY_QUERY_API_KEY=app-...
DIFY_LINT_API_KEY=app-...
```

## 二、输入契约（开始节点变量）

三个 Workflow 公共字段：`schema_version="1.0"`、`project_id`、`baseline_version`、`task_id`、`language="zh-CN"`。

### 导入（Ingest）

| 字段 | 说明 |
| --- | --- |
| `source` | 来源元数据：`id / type / authority_level(L1-L3) / document_version / document_date / applicable_scope` |
| `baseline_rules[]` | 当前生效规则卡：`id / title / content / status="effective"`（≤20 条） |
| `source_chunks[]` | 材料片段：`chunk_id / locator / text`（1–20 段，单段 ≤2000 字符） |

### 查询（Query）

| 字段 | 说明 |
| --- | --- |
| `scope` | `effective / effective_with_notices / historical` |
| `question` | 用户问题（≤500 字符） |
| `effective_cards[]` | 生效卡：`id / title / content / source_citations[]` |
| `notices[]` | 提示：`type(candidate/conflict) / id / summary` |
| `citations[]` | 引用材料：`id / source_id / filename / document_version / section / excerpt / authority_level` |

### 自检（Lint）

| 字段 | 说明 |
| --- | --- |
| `input_contract_version` | 固定 `"2.0"`（须与 `config/schema.yaml` 的 `lint_input_contract_version` 一致） |
| `baseline_rules[]` | 基线侧引用：`id / source_id / citation_id / document_version / page_or_section / excerpt` |
| `comparison_items[]` | 比对侧引用，结构同上 |
| `deterministic_findings[]` | 本地确定性发现（含 `locally_validated=true`） |
| `allowed_issue_types[]` | 允许的问题类型白名单（≤5 种） |

## 三、输出契约（结束节点，JSON）

### 导入输出

```json
{
  "schema_version": "1.0",
  "task_id": "与输入一致",
  "summary": "材料摘要",
  "items": [{"item_id": "...", "item_type": "professional_opinion|discussion_reference", "title": "...", "content": "...", "target_card_id": "可空", "result_type": "candidate|conflict_discussion|information_gap", "status": "ai_inferred|candidate|conflict", "source_citations": [{"source_id": "...", "chunk_id": "...", "locator": "...", "excerpt": "..."}], "confidence": 0.9, "uncertainty": "可空"}],
  "relations": [{"source_id": "...", "relation_type": "derived_from|supports|conflicts_with|proposes_change_to|approved_as|supersedes|impacts|to_be_verified_by", "target_id": "..."}]
}
```

每条 item 必须至少 1 条 `source_citations`，`confidence ∈ [0,1]`。

### 查询输出

```json
{
  "answer": "≤500 字符",
  "effective_rules": ["规则文本"],
  "citations": ["与输入 citations 同结构"],
  "candidate_notice": "可空",
  "conflict_notice": "可空",
  "baseline_version": "与输入一致",
  "evidence_sufficiency": "sufficient|partial|insufficient",
  "result_mode": "realtime|cache",
  "model_call_id": "可空"
}
```

### 自检输出

```json
{
  "schema_version": "1.0",
  "issues": [{"issue_type": "...", "severity": "critical|major|minor", "title": "...", "description": "...", "evidence": [{"source_id": "...", "citation_id": "...", "excerpt": "...", "document_version": "...", "page_or_section": "...", "side": "baseline|comparison"}], "impacted_domains": ["≥1 项"], "options": [{"code": "...", "label": "...", "impact": "..."}], "ai_recommendation": "可空", "ai_confidence": 0.9, "uncertainty": "可空"}]
}
```

完整字段与约束以 `src/infrastructure/gateways/schemas.py` 的 Pydantic 模型为准（额外字段一律拒绝，`extra="forbid"`）。

## 四、联通性验证（交付前 runbook 演练）

```bash
uv sync --frozen && cp .env.example .env   # 填入三个真实 Key
uv run python scripts/reset_demo.py --snapshot initial
uv run streamlit run streamlit_app.py --server.headless true
```

在应用内依次实操作证：

1. **导入**页上传一份演示材料，实时分析成功并产出带引用的条目；
2. **查询**页实时提问，回答附基线版本与引用；
3. **自检**页一键自检，问题列表正常返回；
4. 完成「决定 → 变更单 → 审批 → 发布」，基线演进且追溯主链完整。

如实说明：T13 联合验收的外部模型侧为本地 mock 网关（fixtures 与上述契约同构）；本手册第 4 节的真实联通性演练是交付前必做项。
