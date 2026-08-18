from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.infrastructure.files.wiki_outbound_context import (
    WikiOutboundAuthorization,
    validate_wiki_outbound_authorization,
)
from src.infrastructure.gateways._common import OutboundSafetyProof, invoke, validate_input
from src.infrastructure.gateways.schemas import (
    WikiIngestWorkflowInput,
    WikiIngestWorkflowOutput,
)


class WikiIngestGateway:
    """Fail-closed adapter for the dedicated 2.2 Wiki proposal workflow."""

    def __init__(self, client: WorkflowGateway, *, timeout_seconds: int) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        inputs: Mapping[str, Any],
        *,
        safety_proof: OutboundSafetyProof,
        wiki_authorization: WikiOutboundAuthorization,
        user: str | None = None,
        timeout_seconds: int | None = None,
    ) -> WikiIngestWorkflowOutput:
        validated_inputs = validate_input(
            WikiIngestWorkflowInput,
            inputs,
            invalid_detail="WIKI_INGEST_INPUT_INVALID",
            safety_proof=safety_proof,
        )
        validate_wiki_outbound_authorization(validated_inputs, wiki_authorization)
        effective_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        _, raw_output = invoke(
            self.client,
            validated_inputs,
            user,
            effective_timeout,
        )
        raw_schema_version = raw_output.get("schema_version")
        if isinstance(raw_schema_version, str) and (
            raw_schema_version != validated_inputs["schema_version"]
        ):
            raise OutputValidationError("SCHEMA_VERSION_MISMATCH")
        try:
            output = WikiIngestWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("WIKI_INGEST_OUTPUT_INVALID") from error
        if output.task_id != validated_inputs["task_id"]:
            raise OutputValidationError("TASK_ID_MISMATCH")
        return output
