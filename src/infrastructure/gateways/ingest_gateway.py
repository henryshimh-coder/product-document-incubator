from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.domain.services.citation_validator import CitationValidator
from src.infrastructure.gateways._common import invoke
from src.infrastructure.gateways.schemas import IngestWorkflowOutput


class IngestGateway:
    def __init__(self, client: WorkflowGateway) -> None:
        self.client = client

    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        user: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        workflow_run_id, raw_output = invoke(self.client, inputs, user, timeout_seconds)
        try:
            output = IngestWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("INGEST_OUTPUT_INVALID") from error
        if output.schema_version != inputs.get("schema_version"):
            raise OutputValidationError("SCHEMA_VERSION_MISMATCH")
        if inputs.get("task_id") is not None and output.task_id != inputs.get("task_id"):
            raise OutputValidationError("TASK_ID_MISMATCH")

        source = inputs.get("source")
        if not isinstance(source, Mapping):
            raise OutputValidationError("INGEST_INPUT_INVALID")
        source_id = source.get("id")
        chunks = {
            chunk.get("chunk_id"): chunk
            for chunk in inputs.get("source_chunks", [])
            if isinstance(chunk, Mapping)
        }
        baseline_ids = {
            rule.get("id") for rule in inputs.get("baseline_rules", []) if isinstance(rule, Mapping)
        }
        item_ids = {item.item_id for item in output.items}
        for item in output.items:
            if item.target_card_id is not None and item.target_card_id not in baseline_ids:
                raise OutputValidationError("UNKNOWN_TARGET_CARD")
            for citation in item.source_citations:
                if citation.source_id != source_id or citation.chunk_id not in chunks:
                    raise OutputValidationError("UNKNOWN_CITATION")
                chunk = chunks[citation.chunk_id]
                if citation.locator != chunk.get("locator"):
                    raise OutputValidationError("CITATION_METADATA_MISMATCH")
                validator = CitationValidator(
                    [
                        {
                            "id": citation.chunk_id,
                            "source_id": source_id,
                            "excerpt": chunk.get("text"),
                        }
                    ]
                )
                if not validator.has_direct_support(
                    citation.excerpt, {"excerpt": chunk.get("text")}
                ):
                    raise OutputValidationError("CITATION_METADATA_MISMATCH")
        for relation in output.relations:
            if relation.source_id not in item_ids:
                raise OutputValidationError("UNKNOWN_RELATION_SOURCE")
            if relation.target_id not in baseline_ids | item_ids:
                raise OutputValidationError("UNKNOWN_RELATION_TARGET")
        return {
            "workflow_run_id": workflow_run_id,
            "result": output.model_dump(mode="json"),
        }
