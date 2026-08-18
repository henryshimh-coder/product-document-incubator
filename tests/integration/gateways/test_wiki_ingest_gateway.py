from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from src.domain.enums import SecurityLevel
from src.domain.errors import GatewayError, OutputValidationError
from src.infrastructure.gateways._common import create_outbound_safety_proof
from src.infrastructure.gateways.schemas import WikiIngestWorkflowInput
from src.infrastructure.gateways.wiki_ingest_gateway import WikiIngestGateway


class FakeDifyClient:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(deepcopy(kwargs))
        return {"workflow_run_id": "WF-WIKI-001", "result": deepcopy(self.output)}


def valid_input(*, task_id: str = "TASK-1") -> dict[str, Any]:
    return {
        "schema_version": "2.2",
        "task_id": task_id,
        "project_id": "PROJECT_A",
        "source": {
            "id": "SRC-A",
            "source_type": "formal_document",
            "material_name": "Pricing policy",
            "document_version": "1.0",
            "document_date": "2026-08-17",
            "applicable_scope": "Project A",
            "authority_level": "formal_effective",
            "security_level": "L2",
        },
        "source_chunks": [
            {
                "chunk_id": "SRC-A-0001",
                "locator": "Section 1",
                "text": "Approved redacted source statement.",
            }
        ],
        "safe_index_projection": "- channels [SRC-L1]",
        "safe_related_topics": [
            {
                "title": "channels",
                "markdown": "Safe channel",
                "source_ids": ["SRC-L1"],
            }
        ],
        "ingest_contract": "Generate traceable Wiki statements with source citations.",
    }


def valid_output(
    *, task_id: str = "TASK-1", schema_version: str = "2.2"
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "task_id": task_id,
        "source_page_markdown": "# Source SRC-A\n\nApproved statement.",
        "topic_changes": [
            {
                "topic_id": "channels",
                "title": "Channels",
                "change_type": "update",
                "markdown": "Safe channel updated from SRC-A.",
                "source_ids": ["SRC-A", "SRC-L1"],
            }
        ],
        "conflicts": [
            {
                "summary": "New source differs from the current channel statement.",
                "source_ids": ["SRC-A", "SRC-L1"],
            }
        ],
        "evidence_gaps": ["Launch date has no supporting citation."],
    }


def safety_proof(inputs: dict[str, Any]):
    return create_outbound_safety_proof(
        WikiIngestWorkflowInput,
        inputs,
        security_level=SecurityLevel.L2_INTERNAL,
        customer_names=[],
        strategy_terms=[],
        financial_terms=[],
        leader_names=[],
        unpublished_decisions=[],
        source_total_chars=100_000,
    )


def test_wiki_gateway_sends_only_validated_projection_with_explicit_timeout() -> None:
    inputs = valid_input()
    client = FakeDifyClient(valid_output())

    output = WikiIngestGateway(client, timeout_seconds=60).generate(
        inputs, safety_proof=safety_proof(inputs)
    )

    assert output.task_id == "TASK-1"
    assert output.topic_changes[0].topic_id == "channels"
    assert client.calls == [
        {
            "inputs": WikiIngestWorkflowInput.model_validate(inputs).model_dump(mode="json"),
            "user": "PROJECT_A",
            "timeout_seconds": 60,
        }
    ]
    serialized_payload = str(client.calls[0]["inputs"])
    assert "raw_path" not in serialized_payload
    assert "current_index" not in serialized_payload
    assert "wiki/topics/" not in serialized_payload


@pytest.mark.parametrize(
    ("output", "detail"),
    [
        (valid_output(task_id="OTHER"), "TASK_ID_MISMATCH"),
        (valid_output(schema_version="2.1"), "SCHEMA_VERSION_MISMATCH"),
    ],
)
def test_wiki_gateway_rejects_task_and_schema_mismatch_before_return(
    output: dict[str, Any], detail: str
) -> None:
    inputs = valid_input(task_id="TASK-1")
    client = FakeDifyClient(output)

    with pytest.raises(OutputValidationError, match=detail):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs, safety_proof=safety_proof(inputs)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_document", "UNREDACTED RAW"),
        ("raw_path", "raw/SRC-A/source.md"),
        ("current_index", "# Full current index"),
        ("related_topic_paths", ["wiki/topics/channels.md"]),
        ("target_path", "wiki/topics/model-selected.md"),
    ],
)
def test_wiki_gateway_rejects_forbidden_context_before_external_call(
    field: str, value: Any
) -> None:
    inputs = valid_input()
    proof = safety_proof(inputs)
    inputs[field] = value
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_INGEST_INPUT_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(inputs, safety_proof=proof)

    assert client.calls == []


def test_wiki_gateway_rejects_model_selected_target_path_in_output() -> None:
    inputs = valid_input()
    output = valid_output()
    output["topic_changes"][0]["target_path"] = "wiki/topics/model-selected.md"
    client = FakeDifyClient(output)

    with pytest.raises(OutputValidationError, match="WIKI_INGEST_OUTPUT_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs, safety_proof=safety_proof(inputs)
        )


def test_wiki_gateway_requires_bound_local_safety_proof() -> None:
    client = FakeDifyClient(valid_output())

    with pytest.raises(TypeError, match="safety_proof"):
        WikiIngestGateway(client, timeout_seconds=60).generate(valid_input())

    assert client.calls == []
