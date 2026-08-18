from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.wiki import WikiChangeSet, WikiIngestRun, WikiTransactionResult


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
