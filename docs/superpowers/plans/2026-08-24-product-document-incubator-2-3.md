# Product Document Incubator 2.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Owner 已授权的 L1/L2 材料在保留硬性个人标识自动遮盖、L3/L4 零外发和 Raw 不变的前提下，不再因业务词典或固定契约占比而被误拦截；同时将已归档材料改造为可搜索、按版本归组且可受控清理的 Owner 管理视图。

**Architecture:** 保留现有 Wiki Ingest 管线和 Dify 2.2 Schema，在本地增加显式的 `OWNER_CONFIRMED` 脱敏模式。出站安全证明同时签名载荷摘要、覆盖计算模式和覆盖字符数；Gateway 调用前从载荷重算这些值，防止伪造。材料管理层直接复用 `material_series_id`、现有中央 Source 仓库和项目 `source-index.json`；可删版本通过“文件移入项目回收区→原子更新活动索引→删除数据库记录”完成，任一步失败必须回滚。

**Tech Stack:** Python 3.12、Pydantic 2、SQLite、Streamlit 1.60、httpx/Dify Gateway、pytest 9、Ruff。

**Spec:** `产品文档孵化器_2.3迭代产品方案_v1.1.md`

## Global Constraints

- 仅 Wiki Ingest 显式使用 2.3 Owner 授权模式；Query、Lint、`ImportSource`、产品文档孵化和其他调用方默认严格模式不变。
- Owner 授权必须同时满足：项目允许外调、材料为 L1/L2、`source.is_redacted is True`、`source.allow_external_model is True`、`requested_by == "Owner"`。
- L3/L4 以及未授权材料的外部 Gateway 调用次数必须为零。
- 手机号、身份证号、银行卡号和邮箱必须在本地自动替换；明文不得进入 Gateway、日志或异常。
- 客户名称、策略词、财务词、领导名称和未发布决策只在 `OWNER_CONFIRMED` 模式下不阻断。
- Raw 永久只读；Ingest 前后字节、文件大小和 SHA-256 必须不变。
- 删除只允许 `pending_ingest` 和 `ingest_failed`；`ingesting` 和 `ingested` 在 UI 与用例层均禁止删除。
- 删除不改写 Raw 字节，只将完整 Source 目录移入 `.incubator/trash/sources/`，并保留含 SHA-256 的恢复清单。
- 外发来源分段最多 3 个，分段文本合计不得超过当前原文字符数的 25%。
- 规范化完整外发载荷不得超过 20,000 字符。
- 外发比例超限使用 `OUTBOUND_COVERAGE_EXCEEDED`；硬标识残留使用 `REDACTION_REQUIRED`；授权不足使用 `WIKI_EXTERNAL_CALL_DENIED`。
- Dify `WikiIngestWorkflowInput` 和 `WikiIngestWorkflowOutput` 继续使用 Schema `2.2`，不要求 Owner 修改已发布工作流。
- 不新增数据库字段、一级导航、权限中心、后台队列或 Raw 上传 API。
- 每个 Task 完成并提交后必须停止，向 Owner 汇报完成内容、验证证据和下一 Task，等待确认。
- 当前工作树已有 Owner 的未提交修改；每次提交只暂存本 Task 明确列出的文件。

---

## 一、文件结构与责职锁定

### 新增文件

| 文件 | 单一职责 |
| --- | --- |
| `tests/security/test_owner_confirmed_wiki_outbound.py` | 覆盖 Owner 授权、硬标识遮盖、业务词放行、L3/L4 与未授权零外发 |
| `src/application/use_cases/delete_archived_source.py` | 校验可删状态并协调 Raw 回收、活动索引与中央数据库回滚 |
| `src/infrastructure/files/source_trash.py` | 将单个 Source 目录原子移入项目回收区并写入可恢复清单 |
| `tests/integration/use_cases/test_delete_archived_source.py` | 验证可删状态、已 Ingest 禁删、索引/数据库/文件一致性与失败回滚 |
| `docs/qa/product-document-incubator-2.3-acceptance.md` | 记录 AC-01～AC-20 的命令、结果和人工页面证据 |

### 修改文件

| 文件 | 修改目的 |
| --- | --- |
| `src/infrastructure/files/redactor.py` | 增加默认严格、显式 Owner 授权两种脱敏模式 |
| `src/infrastructure/files/wiki_outbound_context.py` | 按同一模式生成和重建可授权的已遮盖分段与 Wiki 投影 |
| `src/infrastructure/gateways/_common.py` | 签名并重算覆盖模式、覆盖字符数和真实覆盖率 |
| `src/application/use_cases/ingest_archived_source.py` | 强制 Owner 授权，调用 Owner 模式，区分安全错误并记录真实覆盖率 |
| `src/application/dto/materials.py` | 增加受控删除命令和结果 DTO |
| `src/ui/pages/materials.py` | 告知 Owner 授权含义、最低脱敏边界和当前材料的外发状态 |
| `src/application/container.py` | 组装受控删除用例所需的 Source 仓库、索引和回收区适配器 |
| `src/infrastructure/files/source_index_store.py` | 增加按 `source_id` 原子移除活动索引条目的能力 |
| `src/domain/errors.py` | 调整覆盖率超限文案，增加“已 Ingest/处理中不可删除”的稳定错误码 |
| `tests/unit/domain/test_redactor.py` | 验证两种脱敏模式的边界 |
| `tests/security/test_wiki_outbound_projection.py` | 验证 Owner 模式下的 Wiki 投影与跨项目、未授权边界 |
| `tests/integration/gateways/test_workflow_schemas.py` | 验证安全证明覆盖模式的防篡改和严格默认兼容 |
| `tests/integration/use_cases/test_wiki_ingest.py` | 验证 Wiki Ingest 成功、错误区分、真实审计值和 Raw 不变 |
| `tests/e2e/test_materials_page.py` | 验证 Owner 告知文案、授权状态和按钮行为 |

### 明确不修改语义

- `src/application/use_cases/import_source.py`
- `src/application/use_cases/run_query.py`
- `src/application/use_cases/run_lint.py`
- `src/application/use_cases/incubate_document.py`
- `src/infrastructure/gateways/schemas.py`
- `src/application/use_cases/prepare_local_wiki_ingest.py`
- `src/application/use_cases/confirm_local_wiki_ingest.py`
- Raw 归档目录和已发布产品文档

---

## 二、任务与投入

| Task | 节点 | 人日 |
| ---: | --- | ---: |
| 1 | 严格/最低脱敏双模式 | 0.50 |
| 2 | Owner 授权 Wiki 分段与安全投影 | 0.75 |
| 3 | 来源分段覆盖率证明与错误区分 | 0.75 |
| 4 | Owner 页面告知、审计与 E2E | 0.50 |
| 5 | 已归档材料分组管理与受控删除 | 1.00 |
| **合计** |  | **3.50** |

### 规格覆盖自检

| 产品要求 | 实施 Task |
| --- | --- |
| 业务词在 Owner 授权后可外发 | 1、2 |
| 手机、身份证、银行卡和邮箱本地遮盖 | 1、2 |
| 五项 Owner/项目/材料授权条件 | 2 |
| 分段 25% 和完整载荷 20,000 字符 | 3 |
| 证明不可伪造且 Gateway 前重算 | 3 |
| 错误码区分、页面告知与审计 | 3、4 |
| L3/L4、未授权、跨项目零外发 | 2、4 |
| Dify 2.2 Schema 和严格默认兼容 | 3、4 |
| 材料按系列归组、搜索、筛选与技术详情折叠 | 5 |
| 待 Ingest/失败材料可删，处理中/已 Ingest 服务端禁删 | 5 |

---

### Task 1: 严格/最低脱敏双模式

**Files:**
- Modify: `src/infrastructure/files/redactor.py`
- Test: `tests/unit/domain/test_redactor.py`

**Interfaces:**
- Consumes: 现有 `SecurityLevel`、`REDACTION_PATTERNS` 和五类业务词典。
- Produces: `RedactionMode.STRICT`、`RedactionMode.OWNER_CONFIRMED`；`redact_text(..., mode: RedactionMode = RedactionMode.STRICT) -> RedactionResult`。

- [ ] **Step 1: 写入默认严格模式兼容测试**

```python
def test_strict_mode_keeps_business_dictionary_redaction() -> None:
    result = redact_text(
        "某银行采用灰度策略",
        security_level=SecurityLevel.L2_INTERNAL,
        customer_names=("某银行",),
        strategy_terms=("灰度策略",),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
    )
    assert "某银行" not in result.redacted_text
    assert "灰度策略" not in result.redacted_text
```

- [ ] **Step 2: 运行单元测试并确认当前严格行为通过**

Run: `.venv/bin/pytest -q tests/unit/domain/test_redactor.py::test_strict_mode_keeps_business_dictionary_redaction`

Expected: PASS，证明默认行为基线未丢失。

- [ ] **Step 3: 写入 Owner 模式失败测试**

```python
def test_owner_confirmed_mode_allows_business_terms_but_masks_hard_identifiers() -> None:
    result = redact_text(
        "某银行采用灰度策略，联系 13812345678，邮箱 owner@example.com",
        mode=RedactionMode.OWNER_CONFIRMED,
        security_level=SecurityLevel.L2_INTERNAL,
        customer_names=("某银行",),
        strategy_terms=("灰度策略",),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
    )
    assert "某银行" in result.redacted_text
    assert "灰度策略" in result.redacted_text
    assert "13812345678" not in result.redacted_text
    assert "owner@example.com" not in result.redacted_text
    assert "[已脱敏:phone]" in result.redacted_text
    assert "[已脱敏:email]" in result.redacted_text
    assert result.safe_for_external_model is True
```

- [ ] **Step 4: 运行失败测试**

Run: `.venv/bin/pytest -q tests/unit/domain/test_redactor.py::test_owner_confirmed_mode_allows_business_terms_but_masks_hard_identifiers`

Expected: FAIL，因 `RedactionMode` 或 `mode` 尚未实现。

- [ ] **Step 5: 实现最小双模式**

```python
from enum import StrEnum


class RedactionMode(StrEnum):
    STRICT = "strict"
    OWNER_CONFIRMED = "owner_confirmed"
```

为 `redact_text` 增加 `mode` 默认参数。`STRICT` 继续将五类字典模式加入 `patterns`；`OWNER_CONFIRMED` 只使用 `REDACTION_PATTERNS` 中的四类硬标识正则。两种模式对 L3/L4 都必须返回 `safe_for_external_model=False`。

- [ ] **Step 6: 运行红线与全量脱敏单测**

Run: `.venv/bin/pytest -q tests/unit/domain/test_redactor.py`

Expected: PASS。

- [ ] **Step 7: 检查格式并提交 Task 1**

```bash
.venv/bin/ruff check src/infrastructure/files/redactor.py tests/unit/domain/test_redactor.py
.venv/bin/ruff format --check src/infrastructure/files/redactor.py tests/unit/domain/test_redactor.py
git add src/infrastructure/files/redactor.py tests/unit/domain/test_redactor.py
git commit -m "feat: add owner confirmed redaction mode"
```

**Owner checkpoint:** 停止，展示“业务词保留、硬标识遮盖、L3/L4 仍不安全”的单测证据。

---

### Task 2: Owner 授权 Wiki 分段与安全投影

**Files:**
- Modify: `src/application/use_cases/ingest_archived_source.py:159-210, 457-504`
- Modify: `src/infrastructure/files/wiki_outbound_context.py:123-521`
- Test: `tests/integration/use_cases/test_wiki_ingest.py`
- Test: `tests/security/test_wiki_outbound_projection.py`
- Create: `tests/security/test_owner_confirmed_wiki_outbound.py`

**Interfaces:**
- Consumes: Task 1 的 `RedactionMode.OWNER_CONFIRMED` 和 `redact_text` 结果。
- Produces: `WikiOutboundContextBuilder(..., redaction_mode: RedactionMode = RedactionMode.STRICT)`；`IngestArchivedSource._authorize_external(source, requested_by)`；Owner 模式下可重建、可签名的遮盖分段。

- [ ] **Step 1: 先扩展现有测试 fixture，再写入 Owner 授权成功的安全测试**

仅在 `tests/integration/use_cases/test_wiki_ingest.py` 的测试工厂增加参数，不增加生产代码接口：

```python
def make_ingest_fixture(
    tmp_path: Path,
    *,
    raw_text: str | None = None,
    security_level: SecurityLevel = SecurityLevel.L2_INTERNAL,
    is_redacted: bool = True,
    allow_external_model: bool = True,
    customer_names: tuple[str, ...] = (),
    strategy_terms: tuple[str, ...] = (),
) -> IngestFixture:
    # 在归档和 SHA-256 入库前使用 raw_text，不得在 Ingest 过程中改 Raw。
    ...

def execute(self, *, requested_by: str = "Owner"):
    return self.service.execute(
        IngestArchivedSourceInput(
            project_id="PROJECT_A",
            source_id=self.source_id,
            requested_by=requested_by,
        )
    )
```

```python
def test_owner_confirmed_l2_business_terms_reach_gateway_with_hard_ids_masked(
    tmp_path: Path,
) -> None:
    fixture = make_ingest_fixture(
        tmp_path,
        raw_text=("某银行采用灰度策略。联系人手机 13812345678，"
                  "邮箱 owner@example.com。") * 200,
        customer_names=("某银行",),
        strategy_terms=("灰度策略",),
    )
    before_raw = fixture.raw_path.read_bytes()
    before_sha = hashlib.sha256(before_raw).hexdigest()

    result = fixture.execute(requested_by="Owner")

    assert result.status.value == "ingested"
    sent = fixture.gateway.calls[0]["inputs"]["source_chunks"][0]["text"]
    assert "某银行" in sent
    assert "灰度策略" in sent
    assert "13812345678" not in sent
    assert "owner@example.com" not in sent
    assert "[已脱敏:phone]" in sent
    assert fixture.raw_path.read_bytes() == before_raw
    assert hashlib.sha256(fixture.raw_path.read_bytes()).hexdigest() == before_sha
```

实际写入测试时复用 `test_wiki_ingest.py` 现有 fixture 的归档构造方式，不引入修改 Raw 的生产代码。Raw 不变断言使用 Ingest 执行紧前保存的字节和 SHA-256。

- [ ] **Step 2: 写入未授权和 L3/L4 零外发测试**

```python
@pytest.mark.parametrize(
    ("requested_by", "is_redacted", "allow_external", "level"),
    [
        ("Agent", True, True, SecurityLevel.L2_INTERNAL),
        ("Owner", False, True, SecurityLevel.L2_INTERNAL),
        ("Owner", True, False, SecurityLevel.L2_INTERNAL),
        ("Owner", True, True, SecurityLevel.L3_CONFIDENTIAL),
        ("Owner", True, True, SecurityLevel.L4_RESTRICTED),
    ],
)
def test_missing_owner_authorization_never_reaches_gateway(
    tmp_path: Path, requested_by, is_redacted, allow_external, level
) -> None:
    fixture = make_ingest_fixture(
        tmp_path,
        is_redacted=is_redacted,
        allow_external_model=allow_external,
        security_level=level,
    )
    with pytest.raises(DomainError, match="WIKI_EXTERNAL_CALL_DENIED"):
        fixture.execute(requested_by=requested_by)
    assert fixture.gateway.calls == []
```

- [ ] **Step 3: 运行新测试并确认失败**

Run: `.venv/bin/pytest -q tests/security/test_owner_confirmed_wiki_outbound.py`

Expected: FAIL，当前分段遇到字典词或硬标识会拒绝，且 `requested_by` 未进入授权判断。

- [ ] **Step 4: 实现应用层 Owner 授权**

将 `_authorize_external(source)` 改为 `_authorize_external(source, requested_by)`，在现有五项条件中加入 `requested_by == "Owner"`。授权在文本抽取、安全投影和证明生成之前执行。

- [ ] **Step 5: 实现可重建的 Owner 遮盖分段**

`IngestArchivedSource` 创建 `WikiOutboundContextBuilder` 时显式传入 `RedactionMode.OWNER_CONFIRMED`。`_redacted_chunks` 保留 `safe_for_external_model` 失败关闭，但不再以 `redacted_text != chunk.text` 为失败条件；它将遮盖后的文本写入 `source_chunks`。

`WikiOutboundContextBuilder._trusted_source_chunks` 必须使用相同模式从经 SHA-256 校验的 Raw 重建预期分段，使授权重验可以确认 Gateway 收到的确实是本地生成的遮盖版。

- [ ] **Step 6: 实现 Owner 模式 Wiki 投影**

`_safe_topic` 和 `safe_source_page` 在 Owner 模式下对硬标识执行占位符替换，对业务词保留原文；严格默认模式仍使用原来的整段拒绝规则。授权重验使用相同 `redaction_mode`，不允许调用方传入不一致的投影。

- [ ] **Step 7: 运行聚焦测试**

Run: `.venv/bin/pytest -q tests/security/test_owner_confirmed_wiki_outbound.py tests/security/test_wiki_outbound_projection.py tests/integration/use_cases/test_wiki_ingest.py`

Expected: PASS；既有跨项目、未授权和 L3/L4 测试仍通过。

- [ ] **Step 8: 检查格式并提交 Task 2**

```bash
.venv/bin/ruff check src/application/use_cases/ingest_archived_source.py src/infrastructure/files/wiki_outbound_context.py tests/integration/use_cases/test_wiki_ingest.py tests/security/test_wiki_outbound_projection.py tests/security/test_owner_confirmed_wiki_outbound.py
.venv/bin/ruff format --check src/application/use_cases/ingest_archived_source.py src/infrastructure/files/wiki_outbound_context.py tests/integration/use_cases/test_wiki_ingest.py tests/security/test_wiki_outbound_projection.py tests/security/test_owner_confirmed_wiki_outbound.py
git add src/application/use_cases/ingest_archived_source.py src/infrastructure/files/wiki_outbound_context.py tests/integration/use_cases/test_wiki_ingest.py tests/security/test_wiki_outbound_projection.py tests/security/test_owner_confirmed_wiki_outbound.py
git commit -m "feat: honor owner wiki outbound authorization"
```

**Owner checkpoint:** 停止，展示 Owner 已授权 L2 成功、硬标识仅以占位符进入 Fake Gateway、L3/L4 与未授权调用数为零。

---

### Task 3: 来源分段覆盖率证明与错误区分

**Files:**
- Modify: `src/infrastructure/gateways/_common.py:17-206`
- Modify: `src/application/use_cases/ingest_archived_source.py:180-209, 972-1024`
- Modify: `src/domain/errors.py:90-128`
- Test: `tests/integration/gateways/test_workflow_schemas.py`
- Test: `tests/integration/use_cases/test_wiki_ingest.py`

**Interfaces:**
- Consumes: Task 1 的 `RedactionMode`；Task 2 生成的已遮盖 `source_chunks`。
- Produces: `OutboundCoverageMode.CANONICAL_PAYLOAD`、`OutboundCoverageMode.WIKI_SOURCE_CHUNKS`；`create_outbound_safety_proof(..., redaction_mode=..., coverage_mode=...)`；可重算的 `OutboundSafetyProof`。

- [ ] **Step 1: 写入固定契约不占材料覆盖率的失败测试**

```python
def test_wiki_coverage_counts_only_source_chunk_text() -> None:
    inputs = _wiki_ingest_schema_input()
    inputs["source_chunks"] = [
        {"chunk_id": "CHK-A", "locator": "第1节", "text": "A" * 200}
    ]
    inputs["ingest_contract"] = "契约" * 1500

    proof = create_outbound_safety_proof(
        WikiIngestWorkflowInput,
        inputs,
        security_level=SecurityLevel.L2_INTERNAL,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        source_total_chars=1000,
        redaction_mode=RedactionMode.OWNER_CONFIRMED,
        coverage_mode=OutboundCoverageMode.WIKI_SOURCE_CHUNKS,
    )

    assert proof is not None
```

- [ ] **Step 2: 写入覆盖模式防篡改测试**

```python
def test_preinvoke_validation_recomputes_signed_wiki_source_coverage() -> None:
    inputs = _wiki_ingest_schema_input()
    proof = create_outbound_safety_proof(
        WikiIngestWorkflowInput,
        inputs,
        security_level=SecurityLevel.L2_INTERNAL,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        source_total_chars=4000,
        redaction_mode=RedactionMode.OWNER_CONFIRMED,
        coverage_mode=OutboundCoverageMode.WIKI_SOURCE_CHUNKS,
    )
    inputs["source_chunks"][0]["text"] += "X" * 1200
    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        validate_input(
            WikiIngestWorkflowInput,
            inputs,
            invalid_detail="WIKI_INGEST_INPUT_INVALID",
            safety_proof=proof,
        )
```

- [ ] **Step 3: 写入真正覆盖超限的用例测试**

```python
def test_wiki_ingest_reports_coverage_error_before_gateway(tmp_path: Path) -> None:
    fixture = make_ingest_fixture(tmp_path, raw_text="A" * 1000)
    with pytest.raises(DomainError) as caught:
        fixture.execute(requested_by="Owner")
    assert caught.value.code == ErrorCode.OUTBOUND_COVERAGE_EXCEEDED.value
    assert fixture.gateway.calls == []
```

若现有抽取器对 1,000 字符文本产生的分段不超过 25%，不要为测试在生产代码中加“指定分段长度”后门；改为直接单测 `_coverage_chars` 与用例的覆盖率预检函数。

- [ ] **Step 4: 运行新测试并确认失败**

Run: `.venv/bin/pytest -q tests/integration/gateways/test_workflow_schemas.py -k 'wiki_coverage or signed_wiki_source' tests/integration/use_cases/test_wiki_ingest.py -k coverage_error`

Expected: FAIL，当前安全证明只支持整个规范化载荷覆盖率。

- [ ] **Step 5: 实现覆盖模式和签名字段**

```python
class OutboundCoverageMode(StrEnum):
    CANONICAL_PAYLOAD = "canonical_payload"
    WIKI_SOURCE_CHUNKS = "wiki_source_chunks"
```

新增 `_coverage_chars(serialized, canonical_payload, mode) -> int`：

- `CANONICAL_PAYLOAD` 返回 `len(canonical_payload)`；
- `WIKI_SOURCE_CHUNKS` 验证 `source_chunks` 是非空列表，然后返回每个分段 `text` 的字符数合计；
- 结果不是正整数时拒绝生成证明。

`OutboundSafetyProof` 增加不可变的 `_coverage_mode` 和 `_coverage_chars`。`_proof_message` 和 `_sign_proof` 必须将两个值纳入 HMAC。`validate_input` 从当前规范化载荷重算覆盖字符数和比例，不信任调用方传入的数值。

- [ ] **Step 6: 保留总载荷上限和硬标识检查**

`outbound_chars = len(canonical_payload)` 仍用于 20,000 字符上限和审计。证明生成使用 Task 1 的 `redaction_mode`，Gateway 调用前继续执行 `_contains_sensitive_residue(serialized)`，保证四类硬标识明文不能绕过。

- [ ] **Step 7: Wiki Ingest 显式选择来源分段覆盖模式**

```python
safety_proof = create_outbound_safety_proof(
    WikiIngestWorkflowInput,
    workflow_inputs,
    security_level=source.security_level,
    customer_names=self.customer_names,
    strategy_terms=self.strategy_terms,
    financial_terms=self.financial_terms,
    leader_names=self.leader_names,
    unpublished_decisions=self.unpublished_decisions,
    source_total_chars=len(extracted.text),
    redaction_mode=RedactionMode.OWNER_CONFIRMED,
    coverage_mode=OutboundCoverageMode.WIKI_SOURCE_CHUNKS,
)
```

在证明生成前以相同公式预检覆盖率；超过 25% 时抛出 `DomainError(ErrorCode.OUTBOUND_COVERAGE_EXCEEDED)`。证明内部校验失败仍使用 `REDACTION_REQUIRED`。

- [ ] **Step 8: 写入真实审计覆盖率**

`_record_external_model_call` 增加 `outbound_coverage: float` 参数，使用预检得到的来源分段覆盖率，不再使用当前的 `1.0 if outbound_chars else 0.0`。日志不保存分段正文。

- [ ] **Step 9: 运行 Gateway、Wiki Ingest 和其他严格调用方回归**

Run: `.venv/bin/pytest -q tests/integration/gateways/test_workflow_schemas.py tests/integration/gateways/test_wiki_ingest_gateway.py tests/integration/use_cases/test_wiki_ingest.py tests/unit/application/test_run_query.py tests/integration/use_cases/test_import_source.py`

Expected: PASS；不显式传入 `coverage_mode` 的调用方仍按整个规范化载荷计算。

- [ ] **Step 10: 检查格式并提交 Task 3**

```bash
.venv/bin/ruff check src/infrastructure/gateways/_common.py src/application/use_cases/ingest_archived_source.py src/domain/errors.py tests/integration/gateways/test_workflow_schemas.py tests/integration/use_cases/test_wiki_ingest.py
.venv/bin/ruff format --check src/infrastructure/gateways/_common.py src/application/use_cases/ingest_archived_source.py src/domain/errors.py tests/integration/gateways/test_workflow_schemas.py tests/integration/use_cases/test_wiki_ingest.py
git add src/infrastructure/gateways/_common.py src/application/use_cases/ingest_archived_source.py src/domain/errors.py tests/integration/gateways/test_workflow_schemas.py tests/integration/use_cases/test_wiki_ingest.py
git commit -m "fix: calculate wiki outbound coverage from source chunks"
```

**Owner checkpoint:** 停止，展示固定契约不再占用 25% 材料预算、真正超限使用准确错误码、证明篡改时外调为零。

---

### Task 4: Owner 页面告知、审计与 E2E

**Files:**
- Modify: `src/ui/pages/materials.py:29-104, 132-297`
- Modify: `tests/e2e/test_materials_page.py`
- Modify: `tests/integration/use_cases/test_wiki_ingest.py`

**Interfaces:**
- Consumes: Task 2 的 Owner 授权状态和 Task 3 的真实审计值。
- Produces: Owner 可理解的授权告知、已归档材料外发状态和真实审计证据。

- [ ] **Step 1: 写入页面文案失败测试**

```python
def test_materials_page_explains_owner_outbound_authorization(tmp_path) -> None:
    page = AppTest.from_function(
        _render_materials_page,
        args=(str(tmp_path / "library"), str(tmp_path / "需求.md")),
    ).run()
    rendered = "\n".join(item.value for item in (*page.caption, *page.info, *page.markdown))
    assert "手机号、身份证号、银行卡号和邮箱会在本地自动遮盖" in rendered
    assert "业务名称和策略术语在 Owner 授权后可外发" in rendered
```

```python
def test_authorized_archived_material_shows_owner_outbound_notice(tmp_path) -> None:
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / "library"), "pending_ingest", "L2"),
    ).run()
    rendered = "\n".join(item.value for item in (*page.caption, *page.info, *page.markdown))
    assert "Owner 已确认并授权必要内容外发" in rendered
    assert page.button(key="material_ingest_SRC-PROJECT-A-001")
```

- [ ] **Step 2: 运行页面测试并确认失败**

Run: `.venv/bin/pytest -q tests/e2e/test_materials_page.py -k 'owner_outbound or authorized_archived'`

Expected: FAIL，当前页面没有 2.3 告知文案。

- [ ] **Step 3: 实现归档和 Ingest 前告知**

将原页面标签“已确认脱敏”改为“已确认内容可外发”，但仍写入现有 `is_redacted` 字段，不做数据库迁移。在两个勾选项后增加固定 caption，文案与产品方案 4.1 完全一致。对 `security_level in {"L1", "L2"}` 且 `is_redacted` 与 `allow_external_model` 均为真的已归档材料，在 Ingest 按钮前显示“Owner 已确认并授权必要内容外发”。L3/L4 继续显示本地流程。

- [ ] **Step 4: 验证审计真实性**

在 `test_wiki_ingest.py` 成功用例中读取最新 `model_call_logs`，断言：

```python
assert audit["authorized"] == 1
assert audit["redacted"] == 1
assert audit["result_mode"] == "realtime"
assert audit["outbound_chars"] == len(canonical_payload)
assert audit["outbound_coverage"] == pytest.approx(source_chunk_chars / source_total_chars)
```

失败用例继续断言日志不包含正文、手机号、身份证号、银行卡号或邮箱。

- [ ] **Step 5: 运行 Task 4 聚焦测试**

Run: `.venv/bin/pytest -q tests/integration/use_cases/test_wiki_ingest.py tests/e2e/test_materials_page.py -k 'owner or authorization or audit or redaction'`

Expected: PASS。

- [ ] **Step 6: 检查格式并提交 Task 4**

```bash
.venv/bin/ruff check src/ui/pages/materials.py tests/e2e/test_materials_page.py tests/integration/use_cases/test_wiki_ingest.py
.venv/bin/ruff format --check src/ui/pages/materials.py tests/e2e/test_materials_page.py tests/integration/use_cases/test_wiki_ingest.py
git diff --check
git add src/ui/pages/materials.py tests/e2e/test_materials_page.py tests/integration/use_cases/test_wiki_ingest.py
git commit -m "feat: explain owner controlled wiki outbound"
```

Expected: 全部通过。

**Owner checkpoint:** 停止，展示页面授权告知、错误区分和真实审计值，等待 Owner 确认再进入 Task 5。

---

### Task 5: 已归档材料分组管理与受控删除

**Files:**
- Create: `src/application/use_cases/delete_archived_source.py`
- Create: `src/infrastructure/files/source_trash.py`
- Create: `tests/integration/use_cases/test_delete_archived_source.py`
- Modify: `src/application/dto/materials.py`
- Modify: `src/application/container.py`
- Modify: `src/infrastructure/files/source_index_store.py`
- Modify: `src/domain/errors.py`
- Modify: `src/ui/pages/materials.py`
- Modify: `tests/e2e/test_materials_page.py`
- Create: `docs/qa/product-document-incubator-2.3-acceptance.md`

**Interfaces:**
- Consumes: `SourceRepository`、`SourceIndexStore`、`ProjectPaths`、`material_series_id`、`ingest_status`。
- Produces: 按材料系列归组的管理视图；`DeleteArchivedSource.execute(command)` 的状态门禁、回收清单和失败回滚。

- [ ] **Step 1: 写入分组、筛选和信息层级失败测试**

在 `tests/e2e/test_materials_page.py` 构造同一 `material_series_id` 的 3 个版本和另一系列，断言：

1. 主列表只有 2 个材料组；
2. 同系列最新归档记录显示在组外，历史版本在 expander 内；
3. 搜索名称、状态和材料类型可缩小结果集；
4. 完整路径、Source ID 和 SHA-256 仅出现在“技术详情”内；
5. 错误只出现在所属版本行内，不渲染全局大面积警告。

- [ ] **Step 2: 写入删除状态门禁失败测试**

`tests/integration/use_cases/test_delete_archived_source.py` 必须覆盖：

```python
@pytest.mark.parametrize("status", ["pending_ingest", "ingest_failed"])
def test_deletes_only_not_ingested_source_versions(status): ...

@pytest.mark.parametrize("status", ["ingesting", "ingested"])
def test_refuses_to_delete_running_or_ingested_sources(status): ...
```

禁删用例必须断言：中央数据库记录、活动索引、Raw 字节和 Wiki 文件全部不变。可删用例必须断言 Raw 字节与 SHA-256 在回收区中原样保留。

- [ ] **Step 3: 运行失败测试**

Run: `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py tests/e2e/test_materials_page.py -k 'group or filter or detail or delete'`

Expected: FAIL，当前尚无分组视图、删除用例和回收区。

- [ ] **Step 4: 实现可回滚的受控删除**

1. `DeleteArchivedSourceInput` 只接收 `project_id`、`source_id`、`requested_by="Owner"` 和显式确认标志；
2. 用例重新从仓库读取当前状态，不信任 UI 传入的状态；
3. 仅 `pending_ingest` 和 `ingest_failed` 通过，其他状态返回 `MATERIAL_DELETE_NOT_ALLOWED`；
4. 验证 `archive_path` 位于当前项目 `raw/<year>/<source_id>/` 且不经过软链接；
5. 顺序执行：完整 Source 目录移入 `.incubator/trash/sources/<UTC>-<source_id>/`，写入包含来源元数据和 SHA-256 的 `manifest.json`，`SourceIndexStore.remove(source_id)` 原子更新活动索引，最后删除中央数据库记录；
6. 数据库删除是最后一个可见状态变更。在此之前任一步失败，必须将 Source 目录移回原位并用 `index.upsert(source)` 恢复活动索引。

- [ ] **Step 5: 实现紧凑材料管理视图**

1. 使用“归档新材料 / 已归档材料”两个页签分离录入与管理；
2. 从中央 Source 仓库读取当前项目材料，按 `material_series_id or source.id` 归组，组内按 `created_at` 倒序；
3. 组外展示最新归档版本的名称、版本、类型、中文状态、文档日期和操作；
4. 历史版本与技术字段使用独立 expander，不在主视图显示完整绝对路径；
5. 失败信息使用行内提示，并附“查看技术错误码”；
6. `ingested` 显示“查看 Wiki 结果”和“已生成 Wiki，不可删除”；`ingesting` 不显示删除；只有两个可删状态显示“删除此版本”。

- [ ] **Step 6: 实现二次确认与页面局部刷新**

删除入口点击后显示当前材料名称、版本、“仅删除此版本”和明确确认控件。用例成功后只刷新当前页状态；删除最新版本后，组内下一条记录自动成为组外版本。

- [ ] **Step 7: 验证回滚、已 Ingest 禁删和页面行为**

Run: `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py tests/integration/files/test_project_source_archive.py tests/e2e/test_materials_page.py`

Expected: PASS；注入文件移动、索引写入和数据库删除失败时都能恢复原状。

- [ ] **Step 8: 运行 2.3 聚焦验收、全量回归和静态门禁**

```bash
.venv/bin/pytest -q tests/unit/domain/test_redactor.py tests/security/test_owner_confirmed_wiki_outbound.py tests/security/test_wiki_outbound_projection.py tests/integration/gateways/test_workflow_schemas.py tests/integration/gateways/test_wiki_ingest_gateway.py tests/integration/use_cases/test_wiki_ingest.py tests/integration/use_cases/test_delete_archived_source.py tests/e2e/test_materials_page.py
.venv/bin/pytest -q
.venv/bin/ruff check src/infrastructure/files/redactor.py src/infrastructure/files/wiki_outbound_context.py src/infrastructure/files/source_index_store.py src/infrastructure/files/source_trash.py src/infrastructure/gateways/_common.py src/application/dto/materials.py src/application/use_cases/ingest_archived_source.py src/application/use_cases/delete_archived_source.py src/application/container.py src/domain/errors.py src/ui/pages/materials.py tests/unit/domain/test_redactor.py tests/security/test_owner_confirmed_wiki_outbound.py tests/security/test_wiki_outbound_projection.py tests/integration/gateways/test_workflow_schemas.py tests/integration/use_cases/test_wiki_ingest.py tests/integration/use_cases/test_delete_archived_source.py tests/e2e/test_materials_page.py
.venv/bin/ruff format --check src/infrastructure/files/redactor.py src/infrastructure/files/wiki_outbound_context.py src/infrastructure/files/source_index_store.py src/infrastructure/files/source_trash.py src/infrastructure/gateways/_common.py src/application/dto/materials.py src/application/use_cases/ingest_archived_source.py src/application/use_cases/delete_archived_source.py src/application/container.py src/domain/errors.py src/ui/pages/materials.py tests/unit/domain/test_redactor.py tests/security/test_owner_confirmed_wiki_outbound.py tests/security/test_wiki_outbound_projection.py tests/integration/gateways/test_workflow_schemas.py tests/integration/use_cases/test_wiki_ingest.py tests/integration/use_cases/test_delete_archived_source.py tests/e2e/test_materials_page.py
git diff --check
```

Expected: 全部通过。如全仓历史门禁存在已知债务，验收报告必须单独列出，不得将其写成 2.3 通过。

- [ ] **Step 9: 编写 AC-01～AC-20 验收报告并提交 Task 5**

`docs/qa/product-document-incubator-2.3-acceptance.md` 必须包含：最终代码 SHA、测试命令与通过数、Fake Gateway 硬标识占位符证据、L3/L4 零调用证据、Raw SHA-256 前后一致证据、覆盖率重算证据、材料分组/筛选页面截图、已 Ingest 禁删证据、删除回滚证据以及未完成项。

```bash
git add src/application/dto/materials.py src/application/use_cases/delete_archived_source.py src/application/container.py src/infrastructure/files/source_index_store.py src/infrastructure/files/source_trash.py src/domain/errors.py src/ui/pages/materials.py tests/integration/use_cases/test_delete_archived_source.py tests/e2e/test_materials_page.py docs/qa/product-document-incubator-2.3-acceptance.md
git commit -m "feat: manage and safely remove archived sources"
```

**Owner checkpoint:** 停止，展示分组筛选页面、可删/禁删证据、AC-01～AC-20 结果、全量测试数、最终 SHA 和仍存债务，由 Owner 决定合并或发布。

---

## 三、执行顺序与停止规则

```text
Task 1 双模式脱敏
→ Owner 确认
→ Task 2 授权分段与投影
→ Owner 确认
→ Task 3 覆盖率证明与错误区分
→ Owner 确认
→ Task 4 页面告知与审计
→ Owner 确认
→ Task 5 材料分组管理与受控删除
→ Owner 决定合并/发布
```

任一 Task 出现以下情况时必须停止并报告：

- 需要改变 Dify 2.2 Schema；
- 需要数据库迁移；
- 任一 L3/L4 测试触发外部客户端；
- Raw 字节或 SHA-256 在 Ingest 前后不一致；
- 严格默认模式的 Query、Lint 或 `ImportSource` 回归失败；
- 预计投入超过 3.5 人日。

## 四、最终完成定义

2.3 只有同时满足以下条件才算完成：

1. AC-01～AC-20 全部有可复验证据；
2. Owner 已授权 L1/L2 业务词材料可成功完成 Wiki Ingest；
3. 四类硬标识只以占位符进入 Gateway；
4. L3/L4 和未授权测试的 Gateway 调用数为零；
5. 来源分段 25% 覆盖率与整体 20,000 字符上限同时生效；
6. 安全证明在载荷、模式、字符数或签名被篡改时于 Gateway 调用前拒绝；
7. Raw 完整性、Wiki 事务、项目隔离、Dify 2.2 Schema 与其他严格调用路径均通过回归；
8. `docs/qa/product-document-incubator-2.3-acceptance.md` 记录最终 SHA、测试数、页面证据和全部未完成债务。
9. 同一材料的版本已正确归组，搜索、筛选、技术详情和行内错误交互符合 AC-17～AC-18；
10. `pending_ingest`/`ingest_failed` 可受控移入回收区，`ingesting`/`ingested` 在 UI 和用例层均不可删除，符合 AC-19～AC-20。
