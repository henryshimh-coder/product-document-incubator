from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import GatewayError, OutputValidationError
from src.infrastructure.files.redactor import REDACTION_PATTERNS

InputModel = TypeVar("InputModel", bound=BaseModel)


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
