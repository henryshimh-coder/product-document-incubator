from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.infrastructure.gateways._common import invoke
from src.infrastructure.gateways.schemas import (
    DocumentDraftWorkflowInput,
    DocumentDraftWorkflowOutput,
    StructureSuggestionWorkflowInput,
    StructureSuggestionWorkflowOutput,
)

_H1 = re.compile(r"^#\s+\S", re.MULTILINE)


class DocumentWorkflowGateway:
    """A fail-closed gateway for 2.0 document draft and outline-only suggestions."""

    def __init__(self, client: WorkflowGateway, *, timeout_seconds: int) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    def generate_draft(
        self,
        inputs: Mapping[str, Any],
        *,
        on_started: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        try:
            validated = DocumentDraftWorkflowInput.model_validate(inputs)
        except ValidationError as error:
            raise OutputValidationError("DOCUMENT_INPUT_INVALID") from error
        serialized = validated.model_dump(mode="json")
        if serialized["wiki_pages"] is not None and any(
            page["safe_for_external"] is not True for page in serialized["wiki_pages"]
        ):
            raise OutputValidationError("DOCUMENT_INPUT_INVALID")
        workflow_run_id, raw_output = invoke(
            self.client,
            serialized,
            serialized["project_id"],
            self.timeout_seconds,
            on_started=on_started or (lambda _task_id, _run_id: None),
        )
        try:
            output = DocumentDraftWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("DOCUMENT_OUTPUT_INVALID") from error
        if not _H1.search(output.document_markdown):
            raise OutputValidationError("DOCUMENT_OUTPUT_INVALID")
        contexts = serialized["wiki_pages"] or serialized["source_fragments"]
        assert contexts is not None
        fragments = {
            (fragment["source_id"], fragment["chunk_id"]): fragment for fragment in contexts
        }
        source_ids = {item["source_id"] for item in contexts}
        if not set(output.source_ids) <= source_ids:
            raise OutputValidationError("DOCUMENT_OUTPUT_INVALID")
        for citation in output.section_citations:
            fragment = fragments.get((citation.source_id, citation.chunk_id))
            if fragment is None:
                raise OutputValidationError("DOCUMENT_OUTPUT_INVALID")
            if citation.locator != fragment["locator"] or citation.excerpt != fragment["excerpt"]:
                raise OutputValidationError("DOCUMENT_OUTPUT_INVALID")
        return {"workflow_run_id": workflow_run_id, "result": output.model_dump(mode="json")}

    def get_run(self, *, workflow_run_id: str, user: str) -> dict[str, Any]:
        return self.client.get_run(
            workflow_run_id=workflow_run_id,
            user=user,
            timeout_seconds=self.timeout_seconds,
        )

    def generate_suggestions(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        if "document_markdown" in inputs:
            raise OutputValidationError("STRUCTURE_INPUT_INVALID")
        try:
            validated = StructureSuggestionWorkflowInput.model_validate(inputs)
        except ValidationError as error:
            raise OutputValidationError("STRUCTURE_INPUT_INVALID") from error
        serialized = validated.model_dump(mode="json")
        workflow_run_id, raw_output = invoke(
            self.client,
            serialized,
            serialized["project_id"],
            self.timeout_seconds,
        )
        try:
            output = StructureSuggestionWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("STRUCTURE_OUTPUT_INVALID") from error
        allowed_projects = {item["project_id"] for item in serialized["reference_projects"]}
        for suggestion in output.suggestions:
            if not set(suggestion.reference_project_ids) <= allowed_projects:
                raise OutputValidationError("STRUCTURE_OUTPUT_INVALID")
        return {"workflow_run_id": workflow_run_id, "result": output.model_dump(mode="json")}
