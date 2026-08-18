from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from src.domain.wiki import WikiChangeSet, WikiIngestRun, WikiTransactionResult

if TYPE_CHECKING:
    from src.infrastructure.files.wiki_outbound_context import WikiOutboundAuthorization
    from src.infrastructure.gateways._common import OutboundSafetyProof
    from src.infrastructure.gateways.schemas import WikiIngestWorkflowOutput


class WikiIngestGenerating(Protocol):
    def generate(
        self,
        inputs: Mapping[str, Any],
        *,
        safety_proof: OutboundSafetyProof,
        wiki_authorization: WikiOutboundAuthorization,
        user: str | None = None,
        timeout_seconds: int | None = None,
    ) -> WikiIngestWorkflowOutput: ...


class WikiChangeSetValidating(Protocol):
    def validate_change_set(self, change_set: WikiChangeSet) -> None: ...


class WikiIngestRunRepository(Protocol):
    def add(self, run: WikiIngestRun) -> None: ...

    def get_by_transaction(self, transaction_id: str) -> WikiIngestRun | None: ...

    def get_succeeded_by_idempotency(self, key: str) -> WikiIngestRun | None: ...

    def update(self, run: WikiIngestRun) -> None: ...

    def list_interrupted(self, project_id: str, older_than: datetime) -> list[WikiIngestRun]: ...


class WikiTransactionCommitting(Protocol):
    def commit(self, change_set: WikiChangeSet) -> WikiTransactionResult: ...

    def recover(self) -> WikiTransactionResult | None: ...
