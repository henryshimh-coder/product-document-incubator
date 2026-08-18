from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import GatewayError, OutputValidationError
from src.domain.models import SourceRecord
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.wiki_outbound_context import WikiOutboundContextBuilder
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
            "applicable_scope": "BASE-1",
            "authority_level": "formal_effective",
            "security_level": "L2",
        },
        "source_chunks": [
            {
                "chunk_id": "SRC-A-0001",
                "locator": "line:1",
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


class SourceRepository:
    def __init__(self, sources: list[SourceRecord]) -> None:
        self.sources = {source.id: source for source in sources}

    def get(self, source_id: str) -> SourceRecord:
        return self.sources[source_id]


def source_record(
    source_id: str,
    *,
    project_id: str = "PROJECT_A",
    security_level: SecurityLevel = SecurityLevel.L2_INTERNAL,
    is_redacted: bool = True,
    allow_external_model: bool = True,
) -> SourceRecord:
    return SourceRecord(
        id=source_id,
        project_id=project_id,
        original_filename=f"{source_id}.md",
        archive_path=f"raw/{source_id}/{source_id}.md",
        sha256="a" * 64,
        mime_type="text/markdown",
        size_bytes=100,
        source_type="formal_document",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="Product",
        provider=None,
        document_date=date(2026, 8, 17),
        document_version="1.0",
        applicable_baseline_version="BASE-1",
        security_level=security_level,
        is_redacted=is_redacted,
        allow_external_model=allow_external_model,
        is_sandbox=False,
        ingest_status="pending_ingest",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        material_name="Pricing policy" if source_id == "SRC-A" else source_id,
    )


def authorized_builder(
    tmp_path: Path,
    *,
    incoming_source: SourceRecord | None = None,
) -> tuple[WikiOutboundContextBuilder, dict[str, Any], list[str]]:
    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.project_root.mkdir(parents=True)
    paths.system_root.mkdir(parents=True)
    (paths.system_root / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "2.2",
                "project_id": "PROJECT_A",
                "allow_external_model": True,
            }
        ),
        encoding="utf-8",
    )
    topic_path = paths.wiki_root / "topics" / "channels.md"
    topic_path.parent.mkdir(parents=True)
    topic_path.write_text(
        "---\npage_type: topic\ntopic_id: channels\nproject_id: PROJECT_A\n---\n"
        "\n- Safe channel 【SRC-L1：section】\n",
        encoding="utf-8",
    )
    raw_bytes = b"Approved redacted source statement."
    trusted_incoming = (incoming_source or source_record("SRC-A")).model_copy(
        update={
            "original_filename": "SRC-A.md",
            "archive_path": "raw/SRC-A/SRC-A.md",
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size_bytes": len(raw_bytes),
        }
    )
    raw_path = paths.project_root / trusted_incoming.archive_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(raw_bytes)
    paths.schema_root.mkdir(parents=True)
    (paths.schema_root / "ingest-contract.md").write_text(
        valid_input()["ingest_contract"],
        encoding="utf-8",
    )
    repository = SourceRepository(
        [
            trusted_incoming,
            source_record(
                "SRC-L1",
                security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
            ),
        ]
    )
    builder = WikiOutboundContextBuilder(paths, repository)
    inputs = valid_input()
    related_paths = ["wiki/topics/channels.md"]
    projection = builder.build("PROJECT_A", related_paths)
    inputs["safe_index_projection"] = projection.safe_index_projection
    inputs["safe_related_topics"] = [
        topic.model_dump(mode="json") for topic in projection.safe_related_topics
    ]
    return builder, inputs, related_paths


def test_wiki_gateway_sends_only_builder_authorized_projection_with_explicit_timeout(
    tmp_path: Path,
) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    client = FakeDifyClient(valid_output())
    authorization = builder.authorize(inputs, related_topic_paths=related_paths)

    output = WikiIngestGateway(client, timeout_seconds=60).generate(
        inputs,
        safety_proof=safety_proof(inputs),
        wiki_authorization=authorization,
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
    tmp_path: Path, output: dict[str, Any], detail: str
) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    inputs["task_id"] = "TASK-1"
    client = FakeDifyClient(output)

    with pytest.raises(OutputValidationError, match=detail):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=builder.authorize(
                inputs, related_topic_paths=related_paths
            ),
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
    tmp_path: Path, field: str, value: Any
) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    proof = safety_proof(inputs)
    authorization = builder.authorize(inputs, related_topic_paths=related_paths)
    inputs[field] = value
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_INGEST_INPUT_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=proof,
            wiki_authorization=authorization,
        )

    assert client.calls == []


def test_wiki_gateway_rejects_model_selected_target_path_in_output(tmp_path: Path) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    output = valid_output()
    output["topic_changes"][0]["target_path"] = "wiki/topics/model-selected.md"
    client = FakeDifyClient(output)

    with pytest.raises(OutputValidationError, match="WIKI_INGEST_OUTPUT_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=builder.authorize(
                inputs, related_topic_paths=related_paths
            ),
        )


def test_wiki_gateway_requires_generic_and_wiki_specific_proofs(tmp_path: Path) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    authorization = builder.authorize(inputs, related_topic_paths=related_paths)
    client = FakeDifyClient(valid_output())

    with pytest.raises(TypeError, match="safety_proof"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs, wiki_authorization=authorization
        )
    with pytest.raises(TypeError, match="wiki_authorization"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs, safety_proof=safety_proof(inputs)
        )

    assert client.calls == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("safe_index_projection", "# Full private index\n\nOWNER-ONLY-INDEX"),
        (
            "safe_related_topics",
            [
                {
                    "title": "channels",
                    "markdown": "---\nproject_id: PROJECT_A\n---\n# Whole private topic",
                    "source_ids": ["SRC-L1"],
                }
            ],
        ),
        (
            "source_chunks",
            [
                {
                    "chunk_id": "SRC-A-0001",
                    "locator": "Whole file",
                    "text": "RELABELLED-FULL-SOURCE-CONTENT",
                }
            ],
        ),
    ],
)
def test_wiki_gateway_rejects_relabelled_content_even_with_valid_generic_proof(
    tmp_path: Path,
    field: str,
    replacement: Any,
) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    authorization = builder.authorize(inputs, related_topic_paths=related_paths)
    inputs[field] = replacement
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_OUTBOUND_AUTHORIZATION_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=authorization,
        )

    assert client.calls == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_chunks.text", "PRIVATE-CONTENT-BEFORE-AUTHORIZATION"),
        ("source_chunks.locator", "private:whole-file"),
        ("ingest_contract", "PRIVATE-DOCUMENT-RELABELLED-AS-CONTRACT"),
    ],
)
def test_builder_refuses_private_content_relabelled_before_authorization_and_no_invoke(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    if field.startswith("source_chunks."):
        inputs["source_chunks"][0][field.partition(".")[2]] = replacement
    else:
        inputs[field] = replacement
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_OUTBOUND_AUTHORIZATION_INVALID"):
        authorization = builder.authorize(inputs, related_topic_paths=related_paths)
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=authorization,
        )

    assert client.calls == []


@pytest.mark.parametrize(
    "incoming_source",
    [
        source_record("SRC-A", allow_external_model=False),
        source_record("SRC-A", is_redacted=False),
        source_record("SRC-A", project_id="PROJECT_B"),
    ],
    ids=["denied", "unredacted", "cross-project"],
)
def test_builder_cannot_authorize_untrusted_incoming_source_or_invoke_external_client(
    tmp_path: Path,
    incoming_source: SourceRecord,
) -> None:
    builder, inputs, related_paths = authorized_builder(
        tmp_path,
        incoming_source=incoming_source,
    )
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_OUTBOUND_AUTHORIZATION_INVALID"):
        authorization = builder.authorize(inputs, related_topic_paths=related_paths)
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=authorization,
        )

    assert client.calls == []


def test_wiki_gateway_rejects_forged_projection_authorization_before_invoke(
    tmp_path: Path,
) -> None:
    _, inputs, _ = authorized_builder(tmp_path)
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_OUTBOUND_AUTHORIZATION_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=object(),
        )

    assert client.calls == []


def test_wiki_gateway_rechecks_current_source_permission_before_invoke(tmp_path: Path) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    authorization = builder.authorize(inputs, related_topic_paths=related_paths)
    trusted_source = builder.sources.get("SRC-A")
    builder.sources.sources["SRC-A"] = trusted_source.model_copy(
        update={"allow_external_model": False}
    )
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_OUTBOUND_AUTHORIZATION_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=authorization,
        )

    assert client.calls == []


def test_wiki_gateway_rechecks_current_project_permission_before_invoke(tmp_path: Path) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    authorization = builder.authorize(inputs, related_topic_paths=related_paths)
    (builder.paths.system_root / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "2.2",
                "project_id": "PROJECT_A",
                "allow_external_model": False,
            }
        ),
        encoding="utf-8",
    )
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_OUTBOUND_AUTHORIZATION_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=authorization,
        )

    assert client.calls == []


def test_wiki_gateway_rejects_authorization_with_tampered_live_revalidator(
    tmp_path: Path,
) -> None:
    builder, inputs, related_paths = authorized_builder(tmp_path)
    authorization = builder.authorize(inputs, related_topic_paths=related_paths)
    tampered = object.__new__(type(authorization))
    object.__setattr__(tampered, "_payload_digest", authorization._payload_digest)
    object.__setattr__(tampered, "_signature", authorization._signature)
    object.__setattr__(tampered, "_revalidate", lambda _: None)
    client = FakeDifyClient(valid_output())

    with pytest.raises(GatewayError, match="WIKI_OUTBOUND_AUTHORIZATION_INVALID"):
        WikiIngestGateway(client, timeout_seconds=60).generate(
            inputs,
            safety_proof=safety_proof(inputs),
            wiki_authorization=tampered,
        )

    assert client.calls == []
