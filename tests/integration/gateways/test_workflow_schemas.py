from __future__ import annotations

import importlib
import json
from copy import deepcopy
from typing import Any

import pytest

from src.domain.enums import SecurityLevel
from src.domain.errors import GatewayError, OutputValidationError
from src.infrastructure.files.redactor import redact_text


def _gateway(name: str, client: Any):
    module = importlib.import_module(f"src.infrastructure.gateways.{name}_gateway")
    gateway = getattr(module, f"{name.title()}Gateway")
    return gateway(client)


class FakeDifyClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls = 0
        self.inputs: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.inputs.append(deepcopy(kwargs["inputs"]))
        return {"workflow_run_id": "WF-001", "result": deepcopy(self.result)}


def _canonical_payload(name: str, inputs: dict[str, Any]) -> str:
    schemas = importlib.import_module("src.infrastructure.gateways.schemas")
    schema = getattr(schemas, f"{name.title()}WorkflowInput")
    serialized = schema.model_validate(inputs).model_dump(mode="json")
    return json.dumps(
        serialized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _outbound_proof(
    name: str,
    inputs: dict[str, Any],
    *,
    outbound_coverage: float = 0.25,
    security_level: SecurityLevel = SecurityLevel.L2_INTERNAL,
    complete_dictionary_profile: bool = True,
    dictionary_terms: dict[str, list[str]] | None = None,
):
    canonical = _canonical_payload(name, inputs)
    redaction_kwargs: dict[str, Any] = {}
    if complete_dictionary_profile:
        terms = dictionary_terms or {}
        redaction_kwargs = {
            "customer_names": terms.get("customer_names", []),
            "strategy_terms": terms.get("strategy_terms", []),
            "financial_terms": terms.get("financial_terms", []),
            "leader_names": terms.get("leader_names", []),
            "unpublished_decisions": terms.get("unpublished_decisions", []),
        }
    redaction_result = redact_text(
        canonical,
        security_level=security_level,
        **redaction_kwargs,
    )
    common = importlib.import_module("src.infrastructure.gateways._common")
    return common.OutboundSafetyProof(
        redaction_result=redaction_result,
        outbound_coverage=outbound_coverage,
    )


def _run_gateway(
    name: str,
    client: FakeDifyClient,
    inputs: dict[str, Any],
    *,
    safety_proof=None,
):
    proof = safety_proof or _outbound_proof(name, inputs)
    return _gateway(name, client).run(inputs, safety_proof=proof)


def _citation() -> dict[str, Any]:
    return {
        "id": "CIT-BASE-001",
        "source_id": "SRC-BASE",
        "filename": "当前产品方案.md",
        "document_version": "LLD-724_1",
        "section": "目标客群",
        "excerpt": "当前目标客群为符合准入要求的存量客户。",
        "authority_level": "formal_effective",
    }


def _query_input() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-001",
        "language": "zh-CN",
        "scope": "effective_with_notices",
        "question": "当前目标客群是什么？",
        "effective_cards": [
            {
                "id": "RULE-001",
                "title": "目标客群",
                "content": "当前目标客群为符合准入要求的存量客户。",
                "source_citations": ["CIT-BASE-001"],
            }
        ],
        "notices": [
            {
                "type": "candidate",
                "id": "ITEM-CANDIDATE-001",
                "summary": "候选意见尚未生效。",
            },
            {
                "type": "conflict",
                "id": "ITEM-CONFLICT-001",
                "summary": "存在待裁决冲突。",
            },
        ],
        "citations": [_citation()],
    }


def _query_output() -> dict[str, Any]:
    return {
        "answer": "当前目标客群为符合准入要求的存量客户。",
        "effective_rules": ["RULE-001"],
        "citations": [_citation()],
        "candidate_notice": None,
        "conflict_notice": None,
        "baseline_version": "LLD-724_1",
        "evidence_sufficiency": "sufficient",
        "result_mode": "realtime",
        "model_call_id": None,
    }


def _ingest_input() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-001",
        "language": "zh-CN",
        "source": {
            "id": "SRC-RISK-001",
            "type": "risk_opinion",
            "authority_level": "professional_opinion",
            "document_version": "v1.0",
            "document_date": "2026-07-29",
            "applicable_scope": "一期",
        },
        "baseline_rules": [
            {
                "id": "RULE-001",
                "title": "目标客群",
                "content": "当前目标客群规则",
                "status": "effective",
            }
        ],
        "source_chunks": [
            {
                "chunk_id": "CHUNK-001",
                "locator": "第2页/客群要求",
                "text": "风险意见要求收紧目标客群。",
            }
        ],
    }


def _ingest_output() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_id": "TASK-001",
        "summary": "识别到一条待讨论冲突。",
        "items": [
            {
                "item_id": "ITEM-001",
                "item_type": "professional_opinion",
                "title": "客群限制意见",
                "content": "建议收紧目标客群。",
                "target_card_id": "RULE-001",
                "result_type": "conflict_discussion",
                "status": "conflict",
                "source_citations": [
                    {
                        "source_id": "SRC-RISK-001",
                        "chunk_id": "CHUNK-001",
                        "locator": "第2页/客群要求",
                        "excerpt": "风险意见要求收紧目标客群。",
                    }
                ],
                "confidence": 0.86,
                "uncertainty": "尚未形成正式决定",
            }
        ],
        "relations": [
            {
                "source_id": "ITEM-001",
                "relation_type": "conflicts_with",
                "target_id": "RULE-001",
            }
        ],
    }


def _lint_input() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-001",
        "language": "zh-CN",
        "baseline_rules": [
            {
                "id": "RULE-001",
                "source_id": "SRC-BASE",
                "citation_id": "CIT-BASE-001",
                "document_version": "LLD-724_1",
                "page_or_section": "目标客群",
                "excerpt": "当前目标客群规则。",
            }
        ],
        "comparison_items": [
            {
                "id": "ITEM-001",
                "source_id": "SRC-RISK",
                "citation_id": "CIT-RISK-001",
                "document_version": "v1.0",
                "page_or_section": "客群限制",
                "excerpt": "风险意见要求收紧客群。",
            }
        ],
        "deterministic_findings": [],
        "allowed_issue_types": [
            "conflict",
            "omission",
            "stale",
            "not_synchronized",
            "insufficient_evidence",
        ],
    }


def _lint_output() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": "conflict",
                "severity": "pending_decision",
                "title": "客群边界不一致",
                "description": "需要会议确认当前执行口径",
                "evidence": [
                    {
                        "source_id": "SRC-BASE",
                        "citation_id": "CIT-BASE-001",
                        "excerpt": "当前目标客群规则。",
                        "document_version": "LLD-724_1",
                        "page_or_section": "目标客群",
                        "side": "current_baseline",
                    },
                    {
                        "source_id": "SRC-RISK",
                        "citation_id": "CIT-RISK-001",
                        "excerpt": "风险意见要求收紧客群。",
                        "document_version": "v1.0",
                        "page_or_section": "客群限制",
                        "side": "challenging_source",
                    },
                ],
                "impacted_domains": ["产品", "风险"],
                "options": [{"code": "A", "label": "收紧", "impact": "调整产品规则"}],
                "ai_recommendation": "A",
                "ai_confidence": 0.78,
                "uncertainty": "专业意见尚未形成正式决定",
            }
        ],
    }


def test_query_gateway_returns_validated_output_and_workflow_run_id():
    """Catches valid Dify output losing its audit run ID or trusted structure."""
    inputs = _query_input()
    client = FakeDifyClient(_query_output())

    result = _run_gateway("query", client, inputs)

    assert client.inputs == [inputs]
    assert result["workflow_run_id"] == "WF-001"
    assert result["result"]["answer"] == "当前目标客群为符合准入要求的存量客户。"
    assert result["result"]["evidence_sufficiency"] == "sufficient"


def test_gateway_requires_local_outbound_safety_proof_before_external_call():
    """Catches callers omitting the local proof while sending an otherwise valid payload."""
    client = FakeDifyClient(_query_output())

    with pytest.raises(TypeError, match="safety_proof"):
        _gateway("query", client).run(_query_input())

    assert client.calls == 0


@pytest.mark.parametrize(
    "security_level",
    [SecurityLevel.L3_CONFIDENTIAL, SecurityLevel.L4_RESTRICTED],
)
def test_gateway_rejects_non_exportable_security_level_before_external_call(
    security_level: SecurityLevel,
):
    """Catches an L3/L4 payload crossing the external-model boundary."""
    inputs = _query_input()
    client = FakeDifyClient(_query_output())
    proof = _outbound_proof("query", inputs, security_level=security_level)

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        _gateway("query", client).run(inputs, safety_proof=proof)

    assert client.calls == 0


def test_gateway_rejects_incomplete_redaction_dictionary_profile_before_external_call():
    """Catches a proof that skipped T04's five required local dictionaries."""
    inputs = _query_input()
    client = FakeDifyClient(_query_output())
    proof = _outbound_proof(
        "query",
        inputs,
        complete_dictionary_profile=False,
    )

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        _gateway("query", client).run(inputs, safety_proof=proof)

    assert client.calls == 0


@pytest.mark.parametrize(
    ("sensitive_term", "dictionary_name"),
    [
        ("张三", "customer_names"),
        ("北极星计划", "strategy_terms"),
        ("预算利润", "financial_terms"),
        ("王总", "leader_names"),
        ("尚未发布的董事会决定", "unpublished_decisions"),
    ],
)
def test_gateway_rejects_payload_still_requiring_dictionary_redaction(
    sensitive_term: str,
    dictionary_name: str,
):
    """Catches locally known names or business terms remaining in the final payload."""
    inputs = _query_input()
    inputs["question"] = f"请解释{sensitive_term}相关规则"
    client = FakeDifyClient(_query_output())
    proof = _outbound_proof(
        "query",
        inputs,
        dictionary_terms={dictionary_name: [sensitive_term]},
    )

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        _gateway("query", client).run(inputs, safety_proof=proof)

    assert client.calls == 0


def test_gateway_rejects_proof_bound_to_an_earlier_payload_before_external_call():
    """Catches payload mutation after local redaction proof generation."""
    inputs = _query_input()
    proof = _outbound_proof("query", inputs)
    inputs["question"] = "变更后的查询问题"
    client = FakeDifyClient(_query_output())

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        _gateway("query", client).run(inputs, safety_proof=proof)

    assert client.calls == 0


@pytest.mark.parametrize("outbound_coverage", [-0.01, 0.2501])
def test_gateway_rejects_outbound_coverage_outside_minimum_necessary_boundary(
    outbound_coverage: float,
):
    """Catches a full or invalid document share escaping the 25% cumulative boundary."""
    inputs = _query_input()
    client = FakeDifyClient(_query_output())
    proof = _outbound_proof(
        "query",
        inputs,
        outbound_coverage=outbound_coverage,
    )

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        _gateway("query", client).run(inputs, safety_proof=proof)

    assert client.calls == 0


@pytest.mark.parametrize("length_field", ["original_chars", "redacted_chars"])
def test_gateway_rejects_tampered_redaction_length_binding(
    length_field: str,
):
    """Catches a real T04 proof whose payload-length binding was modified afterward."""
    inputs = _query_input()
    client = FakeDifyClient(_query_output())
    proof = _outbound_proof("query", inputs)
    redaction_result = proof.redaction_result.model_copy(
        update={
            length_field: getattr(proof.redaction_result, length_field) + 1,
        }
    )
    tampered_proof = proof.model_copy(update={"redaction_result": redaction_result})

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        _gateway("query", client).run(inputs, safety_proof=tampered_proof)

    assert client.calls == 0


@pytest.mark.parametrize(
    ("name", "input_factory", "field_path", "length"),
    [
        ("ingest", _ingest_input, ("source_chunks", 0, "text"), 2001),
        ("ingest", _ingest_input, ("baseline_rules", 0, "content"), 2001),
        ("query", _query_input, ("effective_cards", 0, "content"), 2001),
        ("query", _query_input, ("citations", 0, "excerpt"), 2001),
        ("lint", _lint_input, ("baseline_rules", 0, "excerpt"), 2001),
        ("query", _query_input, ("question",), 501),
    ],
)
def test_gateway_rejects_oversized_free_text_before_external_call(
    name: str,
    input_factory,
    field_path: tuple[Any, ...],
    length: int,
):
    """Catches a document-sized fragment bypassing the per-field minimum boundary."""
    inputs = input_factory()
    proof = _outbound_proof(name, inputs)
    target: Any = inputs
    for part in field_path[:-1]:
        target = target[part]
    target[field_path[-1]] = "文" * length
    client = FakeDifyClient(
        {"ingest": _ingest_output, "query": _query_output, "lint": _lint_output}[name]()
    )

    with pytest.raises(GatewayError, match=f"{name.upper()}_INPUT_INVALID"):
        _gateway(name, client).run(inputs, safety_proof=proof)

    assert client.calls == 0


def test_query_gateway_rejects_effective_rule_text_that_is_not_a_trusted_card_id():
    """Catches candidate or invented text being presented as a trusted effective rule."""
    output = _query_output()
    output["effective_rules"] = ["当前目标客群为符合准入要求的存量客户。"]

    with pytest.raises(OutputValidationError, match="UNKNOWN_EFFECTIVE_RULE"):
        _run_gateway("query", FakeDifyClient(output), _query_input())


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("candidate_notice", "模型虚构的候选通知。", "UNKNOWN_CANDIDATE_NOTICE"),
        ("conflict_notice", "模型虚构的冲突通知。", "UNKNOWN_CONFLICT_NOTICE"),
        ("candidate_notice", "存在待裁决冲突。", "UNKNOWN_CANDIDATE_NOTICE"),
        ("conflict_notice", "候选意见尚未生效。", "UNKNOWN_CONFLICT_NOTICE"),
    ],
)
def test_query_gateway_rejects_untrusted_or_cross_typed_notices(
    field: str, value: str, detail: str
):
    """Catches Query inventing notices or relabeling a trusted notice across types."""
    output = _query_output()
    output[field] = value

    with pytest.raises(OutputValidationError, match=detail):
        _run_gateway("query", FakeDifyClient(output), _query_input())


def test_query_gateway_rejects_unknown_citation():
    """Catches Query accepting a citation outside the supplied universe."""
    output = _query_output()
    output["citations"][0]["id"] = "CIT-INVENTED"

    with pytest.raises(OutputValidationError, match="UNKNOWN_CITATION"):
        _run_gateway("query", FakeDifyClient(output), _query_input())


@pytest.mark.parametrize("reported_sufficiency", ["sufficient", "insufficient"])
def test_query_gateway_replaces_unsupported_answer_with_insufficient_evidence_notice(
    reported_sufficiency: str,
):
    """Catches an unsupported definite company fact surviving evidence degradation."""
    output = _query_output()
    output["answer"] = "当前目标客群包括所有从未合作的新客户。"
    output["evidence_sufficiency"] = reported_sufficiency

    result = _run_gateway("query", FakeDifyClient(output), _query_input())

    assert result["result"]["evidence_sufficiency"] == "insufficient"
    assert result["result"]["answer"] == "现有证据不足，无法给出确定性结论。"


def test_query_gateway_rejects_unsupported_input_schema_version():
    """Catches Query calling the external workflow with an unsupported input contract."""
    inputs = _query_input()
    proof = _outbound_proof("query", inputs)
    inputs["schema_version"] = "2.0"
    client = FakeDifyClient(_query_output())

    with pytest.raises(GatewayError, match="QUERY_INPUT_INVALID"):
        _gateway("query", client).run(inputs, safety_proof=proof)

    assert client.calls == 0


@pytest.mark.parametrize(
    ("mutation", "detail"),
    [
        (lambda value: value.update(answer="答" * 501), "QUERY_OUTPUT_INVALID"),
        (lambda value: value.update(evidence_sufficiency="certain"), "QUERY_OUTPUT_INVALID"),
        (lambda value: value.update(baseline_version="LLD-OLD"), "BASELINE_VERSION_MISMATCH"),
        (lambda value: value.update(chain_of_thought="secret"), "QUERY_OUTPUT_INVALID"),
    ],
)
def test_query_gateway_rejects_invalid_schema_or_relationship(mutation, detail: str):
    """Catches Query accepting overlong, invalid-enum, stale-version, or hidden-reasoning output."""
    output = _query_output()
    mutation(output)

    with pytest.raises(OutputValidationError, match=detail):
        _run_gateway("query", FakeDifyClient(output), _query_input())


def test_ingest_gateway_rejects_effective_status():
    """Catches an external model directly creating effective knowledge."""
    output = _ingest_output()
    output["items"][0]["status"] = "effective"

    with pytest.raises(OutputValidationError, match="INGEST_OUTPUT_INVALID"):
        _run_gateway("ingest", FakeDifyClient(output), _ingest_input())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("item_type", "Decision"),
        ("item_type", "ChangeRequest"),
        ("item_type", "arbitrary"),
        ("result_type", "Decision"),
        ("result_type", "arbitrary"),
    ],
)
def test_ingest_gateway_rejects_ungoverned_item_and_result_types(field: str, value: str):
    """Catches model-created classifications outside the governed ingest taxonomy."""
    output = _ingest_output()
    output["items"][0][field] = value

    with pytest.raises(OutputValidationError, match="INGEST_OUTPUT_INVALID"):
        _run_gateway("ingest", FakeDifyClient(output), _ingest_input())


def test_ingest_gateway_rejects_mismatched_task_id():
    """Catches an output from another Dify task being attached to this request."""
    output = _ingest_output()
    output["task_id"] = "TASK-OTHER"

    with pytest.raises(OutputValidationError, match="TASK_ID_MISMATCH"):
        _run_gateway("ingest", FakeDifyClient(output), _ingest_input())


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("chunk_id", "CHUNK-INVENTED", "UNKNOWN_CITATION"),
        ("source_id", "SRC-INVENTED", "UNKNOWN_CITATION"),
        ("locator", "虚构页码", "CITATION_METADATA_MISMATCH"),
    ],
)
def test_ingest_gateway_rejects_invented_source_citation(field: str, value: str, detail: str):
    """Catches Ingest inventing chunks, sources, or page locators."""
    output = _ingest_output()
    output["items"][0]["source_citations"][0][field] = value

    with pytest.raises(OutputValidationError, match=detail):
        _run_gateway("ingest", FakeDifyClient(output), _ingest_input())


def test_lint_gateway_accepts_two_sided_issue_from_allowed_input_universe():
    """Catches valid governed Lint output being rejected at the adapter boundary."""
    result = _run_gateway("lint", FakeDifyClient(_lint_output()), _lint_input())

    assert result["workflow_run_id"] == "WF-001"
    assert result["result"]["issues"][0]["severity"] == "pending_decision"


def test_lint_gateway_rejects_major_issue_without_both_sides():
    """Catches a major Lint issue presented without current and challenging evidence."""
    output = _lint_output()
    output["issues"][0]["evidence"] = output["issues"][0]["evidence"][:1]

    with pytest.raises(OutputValidationError, match="LINT_OUTPUT_INVALID"):
        _run_gateway("lint", FakeDifyClient(output), _lint_input())


def test_lint_gateway_rejects_evidence_with_swapped_source_sides():
    """Catches baseline and comparison citations mislabeled as the opposite evidence side."""
    output = _lint_output()
    output["issues"][0]["evidence"][0]["side"] = "challenging_source"
    output["issues"][0]["evidence"][1]["side"] = "current_baseline"

    with pytest.raises(OutputValidationError, match="CITATION_SIDE_MISMATCH"):
        _run_gateway("lint", FakeDifyClient(output), _lint_input())


def test_lint_gateway_rejects_disallowed_issue_type_and_decision_fields():
    """Catches Lint escaping the requested taxonomy or creating governance decisions."""
    output = _lint_output()
    output["issues"][0]["issue_type"] = "made_up"
    output["issues"][0]["decision"] = {"action": "accept_change"}

    with pytest.raises(OutputValidationError, match="LINT_OUTPUT_INVALID"):
        _run_gateway("lint", FakeDifyClient(output), _lint_input())


@pytest.mark.parametrize(
    ("name", "input_factory", "output_factory"),
    [
        ("ingest", _ingest_input, _ingest_output),
        ("query", _query_input, _query_output),
        ("lint", _lint_input, _lint_output),
    ],
)
def test_gateway_rejects_extra_input_before_external_call(
    name: str,
    input_factory,
    output_factory,
):
    """Catches raw documents or other non-minimum fields crossing the Dify boundary."""
    inputs = input_factory()
    proof = _outbound_proof(name, inputs)
    inputs["raw_document"] = "完整未脱敏文档"
    client = FakeDifyClient(output_factory())

    with pytest.raises(GatewayError, match=f"{name.upper()}_INPUT_INVALID"):
        _gateway(name, client).run(inputs, safety_proof=proof)

    assert client.calls == 0


@pytest.mark.parametrize(
    ("name", "input_factory", "output_factory"),
    [
        ("ingest", _ingest_input, _ingest_output),
        ("query", _query_input, _query_output),
        ("lint", _lint_input, _lint_output),
    ],
)
def test_gateway_requires_language_before_external_call(
    name: str,
    input_factory,
    output_factory,
):
    """Catches a task bypassing the required common workflow input contract."""
    inputs = input_factory()
    proof = _outbound_proof(name, inputs)
    inputs.pop("language")
    client = FakeDifyClient(output_factory())

    with pytest.raises(GatewayError, match=f"{name.upper()}_INPUT_INVALID"):
        _gateway(name, client).run(inputs, safety_proof=proof)

    assert client.calls == 0


@pytest.mark.parametrize(
    ("name", "input_factory", "output_factory", "nested_collection"),
    [
        ("ingest", _ingest_input, _ingest_output, "source_chunks"),
        ("query", _query_input, _query_output, "effective_cards"),
        ("lint", _lint_input, _lint_output, "comparison_items"),
    ],
)
def test_gateway_rejects_extra_nested_input_before_external_call(
    name: str,
    input_factory,
    output_factory,
    nested_collection: str,
):
    """Catches full text or customer fields hidden inside an allowed input collection."""
    inputs = input_factory()
    proof = _outbound_proof(name, inputs)
    inputs[nested_collection][0]["customer_data"] = "不属于工作流契约"
    client = FakeDifyClient(output_factory())

    with pytest.raises(GatewayError, match=f"{name.upper()}_INPUT_INVALID"):
        _gateway(name, client).run(inputs, safety_proof=proof)

    assert client.calls == 0


@pytest.mark.parametrize(
    "sensitive_question",
    [
        "请查询客户 13812345678 的规则",
        "请查询身份证 11010519491231002X",
        "请查询银行卡 6222021234567890123",
        "请查询 someone@example.com 的规则",
    ],
)
def test_gateway_rejects_known_sensitive_residue_before_external_call(
    sensitive_question: str,
):
    """Catches known T04 redaction residues leaving through an otherwise valid input."""
    inputs = _query_input()
    inputs["question"] = sensitive_question
    client = FakeDifyClient(_query_output())
    proof = _outbound_proof("query", inputs)

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID") as caught:
        _gateway("query", client).run(inputs, safety_proof=proof)

    assert caught.value.code == "REDACTION_REQUIRED"
    assert client.calls == 0
