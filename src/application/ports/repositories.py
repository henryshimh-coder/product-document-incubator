from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.enums import ChangeReviewAction, ChangeStatus, IssueStatus
from src.domain.models import (
    Baseline,
    ChangeRequest,
    Decision,
    EventLog,
    IngestReport,
    IssueCard,
    KnowledgeCard,
    Project,
    Relation,
    SourceRecord,
)


class ProjectRepository(Protocol):
    def get(self, project_id: str) -> Project: ...

    def update_current_baseline(self, project_id: str, baseline_id: str) -> None: ...


class SourceRepository(Protocol):
    def add(self, source: SourceRecord) -> None: ...

    def get(self, source_id: str) -> SourceRecord: ...

    def find_by_sha256(self, project_id: str, sha256: str) -> SourceRecord | None: ...

    def list_for_project(self, project_id: str) -> list[SourceRecord]: ...

    def update(self, source: SourceRecord) -> None: ...

    def update_ingest_status(self, source_id: str, ingest_status: str) -> None: ...


class KnowledgeRepository(Protocol):
    def upsert_cards(self, cards: list[KnowledgeCard]) -> None: ...

    def list_effective(self, project_id: str, version: str) -> list[KnowledgeCard]: ...

    def list_notices(self, project_id: str, version: str) -> list[KnowledgeCard]: ...

    def get_card(self, card_id: str) -> KnowledgeCard: ...


class IssueRepository(Protocol):
    def add_many(self, issues: list[IssueCard]) -> None: ...

    def get(self, issue_id: str) -> IssueCard: ...

    def list_open(self, project_id: str) -> list[IssueCard]: ...

    def update_status(self, issue_id: str, status: IssueStatus, updated_at: datetime) -> None: ...


class DecisionRepository(Protocol):
    def add(self, decision: Decision, idempotency_key: str) -> None: ...

    def get(self, decision_id: str) -> Decision: ...


class ChangeRepository(Protocol):
    def add(self, change: ChangeRequest) -> None: ...

    def get(self, change_id: str) -> ChangeRequest: ...

    def find_by_review_idempotency_key(self, idempotency_key: str) -> ChangeRequest | None: ...

    def record_review(
        self,
        change_id: str,
        action: ChangeReviewAction,
        reviewed_by: str,
        comment: str,
        idempotency_key: str,
        reviewed_at: datetime,
        target_status: ChangeStatus,
    ) -> ChangeRequest: ...

    def update_status(self, change_id: str, status: ChangeStatus, updated_at: datetime) -> None: ...

    def list_pending(self, project_id: str) -> list[ChangeRequest]: ...


class BaselineRepository(Protocol):
    def add(self, baseline: Baseline) -> None: ...

    def get(self, baseline_id: str) -> Baseline: ...

    def get_by_version(self, project_id: str, version: str) -> Baseline: ...

    def list_for_project(self, project_id: str) -> list[Baseline]: ...

    def mark_superseded(self, baseline_id: str) -> None: ...


class EventRepository(Protocol):
    def latest(self, project_id: str, *, limit: int) -> list[EventLog]: ...


class IngestUnitOfWork(Protocol):
    def complete(
        self,
        source: SourceRecord,
        cards: list[KnowledgeCard],
        relations: list[Relation],
        issues: list[IssueCard],
        event: EventLog,
    ) -> bool: ...

    def duplicate_report(
        self,
        source: SourceRecord,
        command_fingerprint: str,
    ) -> IngestReport: ...
