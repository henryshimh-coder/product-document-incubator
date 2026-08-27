from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class WorkflowGateway(Protocol):
    def run(
        self,
        *,
        inputs: dict[str, Any],
        user: str,
        timeout_seconds: int,
        on_started: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]: ...

    def get_run(
        self,
        *,
        workflow_run_id: str,
        user: str,
        timeout_seconds: int,
    ) -> dict[str, Any]: ...
