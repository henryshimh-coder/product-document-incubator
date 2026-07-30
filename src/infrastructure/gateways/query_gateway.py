from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.domain.services.citation_validator import CitationValidator
from src.infrastructure.gateways._common import invoke
from src.infrastructure.gateways.schemas import QueryWorkflowOutput


class QueryGateway:
    def __init__(self, client: WorkflowGateway) -> None:
        self.client = client

    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        user: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        if inputs.get("schema_version") != "1.0":
            raise OutputValidationError("SCHEMA_VERSION_MISMATCH")
        workflow_run_id, raw_output = invoke(self.client, inputs, user, timeout_seconds)
        try:
            output = QueryWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("QUERY_OUTPUT_INVALID") from error
        if output.baseline_version != inputs.get("baseline_version"):
            raise OutputValidationError("BASELINE_VERSION_MISMATCH")
        trusted_citations = inputs.get("citations", [])
        if not isinstance(trusted_citations, list):
            raise OutputValidationError("QUERY_INPUT_INVALID")
        validator = CitationValidator(trusted_citations)
        for citation in output.citations:
            validator.validate(citation.model_dump(mode="json"))
        if output.evidence_sufficiency != "insufficient" and not any(
            validator.has_direct_support(output.answer, citation.model_dump(mode="json"))
            for citation in output.citations
        ):
            output = output.model_copy(update={"evidence_sufficiency": "insufficient"})
        return {
            "workflow_run_id": workflow_run_id,
            "result": output.model_dump(mode="json"),
        }
