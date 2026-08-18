from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from src.domain.errors import OutputValidationError


class FakeDocumentClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.last_inputs: dict[str, Any] | None = None

    def run(self, *, inputs: dict[str, Any], user: str, timeout_seconds: int) -> dict[str, Any]:
        self.last_inputs = deepcopy(inputs)
        return {"workflow_run_id": "WF-DOCUMENT-001", "result": deepcopy(self.result)}


def _draft_input() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "task_type": "document_draft",
        "project_id": "PROJECT_A",
        "project_name": "项目 A",
        "project_description": "面向真实场景的产品文档",
        "schema_headings": ["产品概述", "业务流程"],
        "current_document_markdown": None,
        "source_fragments": [
            {
                "source_id": "SRC-001",
                "chunk_id": "SRC-001-0001",
                "locator": "heading:产品目标; line:1",
                "excerpt": "产品应支持 Owner 建立并维护独立项目。",
                "source_type": "product_requirement",
                "authority_level": "formal_effective",
            }
        ],
    }


def _wiki_draft_input(*, safe_for_external: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "task_type": "document_draft",
        "project_id": "PROJECT_A",
        "project_name": "项目 A",
        "project_description": "面向真实场景的产品文档",
        "schema_headings": ["产品概述", "业务流程"],
        "current_document_markdown": None,
        "wiki_pages": [
            {
                "source_id": "SRC-001",
                "page_path": "wiki/sources/SRC-001.md",
                "page_type": "source",
                "chunk_id": "SRC-001-SOURCE-0001",
                "locator": "wiki_page:wiki/sources/SRC-001.md; chunk:1",
                "excerpt": "已 Ingest 的安全 Wiki 内容。",
                "safe_for_external": safe_for_external,
            }
        ],
    }


def _draft_output(
    *,
    markdown: str = "# 项目 A 产品方案\n\n## 产品概述\n\n支持独立项目。",
) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "task_type": "document_draft",
        "document_markdown": markdown,
        "summary": "形成首版产品方案草稿。",
        "missing_sections": [],
        "evidence_gaps": [],
        "source_ids": ["SRC-001"],
        "section_citations": [
            {
                "heading": "产品概述",
                "source_id": "SRC-001",
                "chunk_id": "SRC-001-0001",
                "locator": "heading:产品目标; line:1",
                "excerpt": "产品应支持 Owner 建立并维护独立项目。",
            }
        ],
    }


def _suggestion_input() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "task_type": "structure_suggestion",
        "project_id": "PROJECT_A",
        "current_headings": ["产品概述"],
        "reference_projects": [{"project_id": "PROJECT_B", "headings": ["产品概述", "业务流程"]}],
    }


def test_document_gateway_rejects_markdown_without_h1() -> None:
    """Catches a model draft that cannot become a valid product-document candidate."""
    from src.infrastructure.gateways.document_gateway import DocumentWorkflowGateway

    client = FakeDocumentClient(_draft_output(markdown="## 缺少主标题\n\n内容"))
    gateway = DocumentWorkflowGateway(client, timeout_seconds=90)

    with pytest.raises(OutputValidationError, match="DOCUMENT_OUTPUT_INVALID"):
        gateway.generate_draft(_draft_input())


def test_document_gateway_rejects_unknown_citation_chunk() -> None:
    """Catches a generated citation that does not point to this request's source fragment."""
    from src.infrastructure.gateways.document_gateway import DocumentWorkflowGateway

    output = _draft_output()
    output["section_citations"][0]["chunk_id"] = "UNKNOWN-CHUNK"
    client = FakeDocumentClient(output)
    gateway = DocumentWorkflowGateway(client, timeout_seconds=90)

    with pytest.raises(OutputValidationError, match="DOCUMENT_OUTPUT_INVALID"):
        gateway.generate_draft(_draft_input())


def test_document_gateway_rejects_unsafe_wiki_pages_before_workflow_invocation() -> None:
    from src.infrastructure.gateways.document_gateway import DocumentWorkflowGateway

    client = FakeDocumentClient(_draft_output())
    gateway = DocumentWorkflowGateway(client, timeout_seconds=90)

    with pytest.raises(OutputValidationError, match="DOCUMENT_INPUT_INVALID"):
        gateway.generate_draft(_wiki_draft_input(safe_for_external=False))

    assert client.last_inputs is None


def test_suggestion_input_contains_only_outlines() -> None:
    """Catches full document text crossing the project-reference boundary for suggestions."""
    from src.infrastructure.gateways.document_gateway import DocumentWorkflowGateway

    client = FakeDocumentClient(
        {
            "schema_version": "2.0",
            "task_type": "structure_suggestion",
            "suggestions": [
                {
                    "title": "风险边界",
                    "reason": "参考项目已覆盖该关键章节。",
                    "reference_project_ids": ["PROJECT_B"],
                    "confidence": 0.83,
                }
            ],
        }
    )
    gateway = DocumentWorkflowGateway(client, timeout_seconds=90)

    gateway.generate_suggestions(_suggestion_input())

    assert client.last_inputs is not None
    assert "document_markdown" not in client.last_inputs
    assert client.last_inputs["reference_projects"] == [
        {"project_id": "PROJECT_B", "headings": ["产品概述", "业务流程"]}
    ]
