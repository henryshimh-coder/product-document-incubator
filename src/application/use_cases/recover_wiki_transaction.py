from __future__ import annotations

from src.application.dto.wiki_ingest import RecoverWikiTransactionInput
from src.application.ports.wiki_ingest import WikiTransactionCommitting
from src.domain.errors import DomainError, ErrorCode
from src.domain.wiki import WikiTransactionResult


class RecoverWikiTransaction:
    """Run project recovery immediately when the project service is constructed."""

    def __init__(
        self,
        *,
        project_id: str,
        coordinator: WikiTransactionCommitting,
    ) -> None:
        self.project_id = project_id
        self.coordinator = coordinator
        self.startup_result = coordinator.recover()

    def execute(
        self,
        command: RecoverWikiTransactionInput,
    ) -> WikiTransactionResult | None:
        if command.project_id != self.project_id:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "PROJECT_ID_MISMATCH")
        return self.coordinator.recover()
