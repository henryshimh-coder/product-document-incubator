from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import Field

from src.domain.models import DomainModel, NonEmptyStr
from src.domain.wiki import WikiChangeSet, WikiIngestRun, WikiTransactionResult


class WikiSourceView(DomainModel):
    id: NonEmptyStr
    label: NonEmptyStr
    wiki_page_count: int = Field(ge=1)
    conflict_count: int = Field(ge=0)
    evidence_gap_count: int = Field(ge=0)


class WikiContextPage(DomainModel):
    source_id: NonEmptyStr
    page_path: NonEmptyStr
    page_type: Literal["source", "topic"]
    chunk_id: NonEmptyStr
    locator: NonEmptyStr
    excerpt: NonEmptyStr
    safe_for_external: bool = True


class WikiIncubationContext(DomainModel):
    source_ids: list[NonEmptyStr]
    pages: list[WikiContextPage] = Field(min_length=1)
    conflicts: list[NonEmptyStr] = Field(default_factory=list)
    evidence_gaps: list[NonEmptyStr] = Field(default_factory=list)


class WikiContextReading(Protocol):
    def list_ingested_sources(self, project_id: str) -> list[WikiSourceView]: ...

    def read_context(self, project_id: str, source_ids: list[str]) -> WikiIncubationContext: ...


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
        on_external_invoke: Callable[[], None] | None = None,
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
