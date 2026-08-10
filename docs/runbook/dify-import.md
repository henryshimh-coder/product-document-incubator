# Dify Workflow 导入与联通性手册

应用通过 Dify Workflow API 调用三个**独立治理**的工作流：导入（Ingest）、查询（Query）、自检（Lint）。调用方式为 `POST {DIFY_BASE_URL}/workflows/run`，请求体 `{"inputs": <下方输入契约>, "response_mode": "blocking", "user": <操作员标识>}`；三个工作流的 API Key 必须互不相同（启动时强校验，重复 Key 会被拒绝且 Key 不进入异常文本）。

> 本手册所有输入输出契约以 `src/infrastructure/gateways/schemas.py` 的 Pydantic 模型为唯一权威（`extra="forbid"`，多字段即拒绝）。第六节示例同时落为版本化 fixture（`docs/runbook/fixtures/dify/*.json`），由 `tests/unit/test_dify_runbook_fixtures.py` 逐份校验并做枚举变异反证，手册与代码不会漂移。

## 一、创建三个 Workflow 并取 Key

1. 在 Dify 控制台分别创建三个 **Workflow** 类型应用，建议命名：`产品智策-导入`、`产品智策-查询`、`产品智策-自检`。
2. 为工作区配置任一有效的模型供应商（OpenAI / Anthropic / Gemini / 通义 / 硅基流动等均可，需有可用额度；Dify 托管试用额度耗尽后必须自行配置凭据，否则所有模型调用报 400 "Model is not configured"）。2026-08-09 真实联通演练使用硅基流动 `Pro/deepseek-ai/DeepSeek-V3.2`（temperature 0.1，max_tokens 4096）。
3. 按第四节的最小节点映射编排各工作流，**发布**（只有已发布版本才能被 API 调用）。
4. 每个应用进入「访问 API / API Access」页，生成**各自独立**的 API Key（`app-` 前缀）。
5. 将三个 Key 填入项目根 `.env`（模板 `.env.example`；Key 只属于本地环境，绝不提交仓库、截图或日志）：

```text
DIFY_BASE_URL=https://api.dify.ai/v1
DIFY_INGEST_API_KEY=app-...
DIFY_QUERY_API_KEY=app-...
DIFY_LINT_API_KEY=app-...
```

应用在容器构建时自动加载项目根 `.env`（已存在的进程环境变量优先）；空 Key 时应用以本地治理模式启动（导入 / 查询 / 自检不可用，其余页面正常）。

## 二、输入契约（开始节点变量）

三个工作流公共字段：`schema_version`（固定 `"1.0"`）、`project_id`、`baseline_version`、`task_id`、`language`（固定 `"zh-CN"`）。

> **传输编码（硬约束，2026-08-09 实测确认）**：Dify 开始节点变量不支持数组值——`json_object` 类型只接受对象（数组被 API 层拒绝："must be a dict"），`paragraph` 只接受字符串。因此应用在网关层把所有**数组值编码为 JSON 字符串**（`src/infrastructure/gateways/dify_client.py` 的 `encode_for_dify_transport()`，UTF-8 不转义），对象与标量原样传输。对应地：
>
> 1. 下表中所有 `[]` 结尾的数组变量，在开始节点必须声明为 **paragraph（长文本）类型**；
> 2. 每个工作流开始节点之后必须紧跟一个**「解析输入」代码节点**，对数组变量执行 `json.loads(raw or "[]")` 还原为数组，后续节点一律引用解析节点的输出（见第四节）。

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

三个工作流统一拓扑（2026-08-09 真实联通演练实测通过的结构）：

```text
开始（声明第二节全部变量，数组变量声明为 paragraph）
  → 解析输入（代码节点：json.loads 还原数组）
  → 模型（LLM，temperature 0.1，max_tokens ≥ 4096）
  → JSON 提取（代码节点：剥离 ``` 围栏、截取首个 { 到末个 }，json.loads）
  → 结束（输出变量名为 result）
```

**平台陷阱（均为实测踩坑，必须遵守）**：

1. **节点 ID 只允许字母、数字、下划线，严禁连字符**。Dify 模板引用 `{{#节点ID.变量#}}` 的正则不匹配带连字符的节点 ID：不报错、不插值，模板原文透传给模型（实测 `{{#start-query.question#}}` 原样出现在模型输入中）。手工编排时不要改名，使用 Dify 自动生成的数字 ID 即可；DSL 导入时自定义 ID 必须去连字符。
2. **max_tokens 必须显式设置（建议 ≥4096）**。缺省时模型输出可能在 JSON 中途截断，`result` 成为非法 JSON 字符串，应用报 `DIFY_RESPONSE_INVALID`（fail-closed）。
3. 模型输出篇幅要设上限（提示词中约束 items/issues ≤3 条、摘要/描述字数上限），防止长输出截断与超时。

### 导入工作流提示词硬规则

- `task_id` 原样回传；`status` 与 `result_type` 固定映射：`candidate→candidate`、`conflict_discussion→conflict`、`information_gap→ai_inferred`；
- `relations` 硬约束：`conflict_discussion` 条目必须有且仅有 1 条 `conflicts_with` 指向其 `target_card_id`；`candidate` 且 `target_card_id` 非空的条目必须有且仅有 1 条 `proposes_change_to`；其余条目不得有任何关系；`relation_type` 只允许这两种（应用侧逐条校验，违反即 `CONFLICT_RELATION_REQUIRED` / `CANDIDATE_RELATION_REQUIRED` 等 fail-closed）；
- `source_citations`：`source_id` 等于输入 `source.id`，`chunk_id` / `locator` 逐字等于输入 chunk，`excerpt` 为 chunk 原文逐字连续片段；
- **`content` 必须是 chunk 原文的逐字连续片段**（下游自检要求条目内容能在来源原文中逐字命中，否则报 `LINT_COMPARISON_TEXT_MISMATCH`）；概括性描述写入 `title`/`summary`。

### 查询工作流提示词硬规则

- 仅用 `effective_cards` 与 `citations` 回答；`effective_rules` 只填卡片 ID；`citations` 只从输入原样选取且与所用卡片 `source_citations` 相交；`answer` ≤500 字且不复述 notices；
- **有相关生效卡片时 `answer` 必须逐字引用卡片 `content` 作答且 `evidence_sufficiency=sufficient`**，严禁输出"资料不足"类措辞；仅当输入中没有任何相关生效卡片时才允许回答资料不足。

### 自检工作流提示词硬规则

- 以 `deterministic_findings` 为锚比较 `baseline_rules` 与 `comparison_items`；
- `severity` 用 `blocking`/`pending_decision`/`pending_info`；每条 `evidence` 标注 `side=current_baseline|challenging_source`，`citation_id` 逐字取自对应侧输入；重大问题双侧证据且来自至少两个不同来源。

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

### 2026-08-09 真实联通演练实录（已完成）

环境：干净克隆（仅仓库跟踪文件 + `.env` 三个互异真实 Key）+ `uv sync --frozen`；Dify 云工作区三个已发布工作流；模型硅基流动 `Pro/deepseek-ai/DeepSeek-V3.2`。全链路证据（脱敏，无 Key）存档于 [../qa/dify-live-e2e-2026-08-09.json](../qa/dify-live-e2e-2026-08-09.json)，代码 SHA 见证据内 `git_sha` 字段。

| 步骤 | 结果 | workflow run ID | 耗时 |
| --- | --- | --- | --- |
| 真实导入（风险意见） | 产出 1 条 conflict 条目，引用锚定来源 chunk | `a9db4486-9713-4592-99a1-8abcc6bbd99b` | 16.8s |
| 发布前真实查询 | 命中 LLD-724_1，逐字答出现行规则，引用 `CIT-SRC-LLD-BASE-01` | `ef9f2db9-f96c-4ec7-98a3-210d482e3265` | 16.8s |
| 真实自检 | 1 条 blocking 冲突，双侧证据（current_baseline + challenging_source） | `7ce68fe5-676a-4e00-a338-ca7e897bf97c` | 28.1s |
| 决定→变更单→审批→发布 | accept_change → approved → 发布 **LLD-724_1 → LLD-724_2** | （本地治理，无模型调用） | — |
| 发布后真实查询 | 命中 **LLD-724_2**，逐字答出新规则，引用新版 `CIT-BASE-LLD-724_2-01` | `b6a1e4b6-c67e-471c-bf8e-ae36ad7c00c6` | 14.3s |
| 追溯主链 | 6 节点齐全（source→knowledge→issue→decision→change→baseline），`missing_links=[]` | — | — |

全链路无重试、无错误码；演练后 `reset_demo` + `validate_data` + 全量测试 + 静态门禁复跑全部通过（见交付清单 v0.1.1 增补节）。
