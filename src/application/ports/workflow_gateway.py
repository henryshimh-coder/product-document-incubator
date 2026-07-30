from __future__ import annotations

from typing import Any, Protocol


class WorkflowGateway(Protocol):
    def run(
        self,
        *,
        inputs: dict[str, Any],
        user: str,
        timeout_seconds: int,
    ) -> dict[str, Any]: ...
