from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import GatewayError, OutputValidationError
from src.infrastructure.files.redactor import REDACTION_PATTERNS, RedactionResult

InputModel = TypeVar("InputModel", bound=BaseModel)
MAX_OUTBOUND_COVERAGE = 0.25


class OutboundSafetyProof(BaseModel):
    """Local-only evidence binding T04 redaction to the exact outbound payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    redaction_result: RedactionResult
    outbound_coverage: float = Field(allow_inf_nan=False)


def _contains_sensitive_residue(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in REDACTION_PATTERNS.values())
    if isinstance(value, Mapping):
        return any(_contains_sensitive_residue(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_residue(item) for item in value)
    return False


def validate_input(
    schema: type[InputModel],
    inputs: Mapping[str, Any],
    *,
    invalid_detail: str,
    safety_proof: OutboundSafetyProof,
) -> dict[str, Any]:
    validation_failed = False
    validated: InputModel | None = None
    try:
        validated = schema.model_validate(inputs)
    except ValidationError:
        validation_failed = True
    if validation_failed or validated is None:
        raise GatewayError.workflow_input_invalid(invalid_detail)
    serialized = validated.model_dump(mode="json")
    canonical_payload = json.dumps(
        serialized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if not isinstance(safety_proof, OutboundSafetyProof):
        raise GatewayError.outbound_safety_proof_invalid()
    redaction = safety_proof.redaction_result
    if (
        not redaction.safe_for_external_model
        or redaction.redacted_text != canonical_payload
        or redaction.original_chars != len(canonical_payload)
        or redaction.redacted_chars != len(canonical_payload)
        or not 0 <= safety_proof.outbound_coverage <= MAX_OUTBOUND_COVERAGE
    ):
        raise GatewayError.outbound_safety_proof_invalid()
    if _contains_sensitive_residue(serialized):
        raise GatewayError.sensitive_input_detected()
    return serialized


def invoke(
    client: WorkflowGateway,
    inputs: Mapping[str, Any],
    user: str | None,
    timeout_seconds: int,
) -> tuple[str, dict[str, Any]]:
    response = client.run(
        inputs=dict(inputs),
        user=user or str(inputs.get("project_id", "workflow")),
        timeout_seconds=timeout_seconds,
    )
    workflow_run_id = response.get("workflow_run_id")
    result = response.get("result")
    if not isinstance(workflow_run_id, str) or not workflow_run_id.strip():
        raise OutputValidationError("DIFY_RESPONSE_INVALID")
    if not isinstance(result, Mapping):
        raise OutputValidationError("DIFY_RESPONSE_INVALID")
    return workflow_run_id, dict(result)
