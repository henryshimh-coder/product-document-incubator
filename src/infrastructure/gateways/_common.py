from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError


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
