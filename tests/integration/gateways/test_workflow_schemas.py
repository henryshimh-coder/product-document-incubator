from __future__ import annotations

import importlib
from copy import deepcopy
from typing import Any

import pytest

from src.domain.errors import OutputValidationError


def _gateway(name: str, client: Any):
    module = importlib.import_module(f"src.infrastructure.gateways.{name}_gateway")
    gateway = getattr(module, f"{name.title()}Gateway")
    return gateway(client)


class FakeDifyClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {"workflow_run_id": "WF-001", "result": deepcopy(self.result)}


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
        "notices": [],
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
    result = _gateway("query", FakeDifyClient(_query_output())).run(_query_input())

    assert result["workflow_run_id"] == "WF-001"
    assert result["result"]["answer"] == "当前目标客群为符合准入要求的存量客户。"
    assert result["result"]["evidence_sufficiency"] == "sufficient"


def test_query_gateway_accepts_effective_rule_text_from_trusted_card_content():
    """Catches valid QueryResponse rule strings being mistaken for card identifiers."""
    output = _query_output()
    output["effective_rules"] = ["当前目标客群为符合准入要求的存量客户。"]

    result = _gateway("query", FakeDifyClient(output)).run(_query_input())

    assert result["result"]["effective_rules"] == ["当前目标客群为符合准入要求的存量客户。"]


def test_query_gateway_rejects_unknown_citation():
    """Catches Query accepting a citation outside the supplied universe."""
    output = _query_output()
    output["citations"][0]["id"] = "CIT-INVENTED"

    with pytest.raises(OutputValidationError, match="UNKNOWN_CITATION"):
        _gateway("query", FakeDifyClient(output)).run(_query_input())


def test_query_gateway_rejects_unsupported_input_schema_version():
    """Catches Query validating output against a schema version it does not implement."""
    inputs = _query_input()
    inputs["schema_version"] = "2.0"

    with pytest.raises(OutputValidationError, match="SCHEMA_VERSION_MISMATCH"):
        _gateway("query", FakeDifyClient(_query_output())).run(inputs)


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
        _gateway("query", FakeDifyClient(output)).run(_query_input())


def test_ingest_gateway_rejects_effective_status():
    """Catches an external model directly creating effective knowledge."""
    output = _ingest_output()
    output["items"][0]["status"] = "effective"

    with pytest.raises(OutputValidationError, match="INGEST_OUTPUT_INVALID"):
        _gateway("ingest", FakeDifyClient(output)).run(_ingest_input())


def test_ingest_gateway_rejects_mismatched_task_id():
    """Catches an output from another Dify task being attached to this request."""
    output = _ingest_output()
    output["task_id"] = "TASK-OTHER"

    with pytest.raises(OutputValidationError, match="TASK_ID_MISMATCH"):
        _gateway("ingest", FakeDifyClient(output)).run(_ingest_input())


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
        _gateway("ingest", FakeDifyClient(output)).run(_ingest_input())


def test_lint_gateway_accepts_two_sided_issue_from_allowed_input_universe():
    """Catches valid governed Lint output being rejected at the adapter boundary."""
    result = _gateway("lint", FakeDifyClient(_lint_output())).run(_lint_input())

    assert result["workflow_run_id"] == "WF-001"
    assert result["result"]["issues"][0]["severity"] == "pending_decision"


def test_lint_gateway_rejects_major_issue_without_both_sides():
    """Catches a major Lint issue presented without current and challenging evidence."""
    output = _lint_output()
    output["issues"][0]["evidence"] = output["issues"][0]["evidence"][:1]

    with pytest.raises(OutputValidationError, match="LINT_OUTPUT_INVALID"):
        _gateway("lint", FakeDifyClient(output)).run(_lint_input())


def test_lint_gateway_rejects_disallowed_issue_type_and_decision_fields():
    """Catches Lint escaping the requested taxonomy or creating governance decisions."""
    output = _lint_output()
    output["issues"][0]["issue_type"] = "made_up"
    output["issues"][0]["decision"] = {"action": "accept_change"}

    with pytest.raises(OutputValidationError, match="LINT_OUTPUT_INVALID"):
        _gateway("lint", FakeDifyClient(output)).run(_lint_input())
