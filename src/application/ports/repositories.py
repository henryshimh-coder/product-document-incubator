from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.enums import ChangeReviewAction, ChangeStatus, IssueStatus
from src.domain.models import (
    Baseline,
    ChangeRequest,
    Decision,
    DecisionResult,
    EventLog,
    IngestReport,
    IssueCard,
    KnowledgeCard,
    ModelCallLog,
    Project,
    Relation,
    SourceRecord,
)


class ProjectRepository(Protocol):
    def add(self, project: Project) -> None: ...

    def get(self, project_id: str) -> Project: ...

    def list_all(self) -> list[Project]: ...

    def update_current_baseline(self, project_id: str, baseline_id: str) -> None: ...


class SourceRepository(Protocol):
    def add(self, source: SourceRecord) -> None: ...

    def delete(self, source_id: str, project_id: str) -> None: ...

    def get(self, source_id: str) -> SourceRecord: ...

    def find_by_sha256(self, project_id: str, sha256: str) -> SourceRecord | None: ...

    def list_for_project(self, project_id: str) -> list[SourceRecord]: ...

    def list_for_series(self, project_id: str, series_id: str) -> list[SourceRecord]: ...

    def find_latest_for_series(self, project_id: str, series_id: str) -> SourceRecord | None: ...

    def find_by_series_version(
        self, project_id: str, series_id: str, document_version: str
    ) -> SourceRecord | None: ...

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

    def list_all(self, project_id: str) -> list[IssueCard]: ...

    def update_status(self, issue_id: str, status: IssueStatus, updated_at: datetime) -> None: ...

    def upsert_all(self, issues: list[IssueCard]) -> None: ...


class DecisionRepository(Protocol):
    def add(self, decision: Decision, idempotency_key: str) -> None: ...

    def get(self, decision_id: str) -> Decision: ...

    def find_by_idempotency_key(self, idempotency_key: str) -> Decision | None: ...

    def list_for_project(self, project_id: str) -> list[Decision]: ...


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

    def list_release_candidates(self, project_id: str) -> list[ChangeRequest]: ...

    def find_by_decision_id(self, decision_id: str) -> ChangeRequest | None: ...

    def list_for_project(self, project_id: str) -> list[ChangeRequest]: ...


class DecisionUnitOfWork(Protocol):
    def record(
        self,
        *,
        decision: Decision,
        idempotency_key: str,
        command_fingerprint: str,
        issue_status: IssueStatus,
        issue_updated_at: datetime,
        change_request: ChangeRequest | None,
        relations: list[Relation],
    ) -> DecisionResult: ...


class ReviewUnitOfWork(Protocol):
    def record_review(
        self,
        *,
        change_id: str,
        action: ChangeReviewAction,
        reviewed_by: str,
        comment: str,
        idempotency_key: str,
        reviewed_at: datetime,
        expected_status: ChangeStatus,
        target_status: ChangeStatus,
        event: EventLog,
    ) -> ChangeRequest: ...


class ReleaseUnitOfWork(Protocol):
    def publish(
        self,
        *,
        superseded_baseline_id: str,
        new_baseline: Baseline,
        change_id: str,
        change_updated_at: datetime,
        project_id: str,
        event: EventLog,
        new_cards: list[KnowledgeCard],
        relations: list[Relation],
        parent_full_document_sha256: str,
        parent_card_snapshot_sha256: str,
    ) -> bool: ...


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


class ModelCallLogRepository(Protocol):
    def list_for_project(self, project_id: str, *, limit: int) -> list[ModelCallLog]: ...


class RelationRepository(Protocol):
    """Read the persisted relation graph; lifecycle relations are written by UoWs."""

    def load_connected(
        self,
        project_id: str,
        entity_id: str,
        *,
        max_depth: int = 6,
    ) -> list[Relation]: ...


class LintUnitOfWork(Protocol):
    """Atomically persist lint issue upserts and their knowledge->issue relations."""

    def apply(
        self,
        *,
        issues: list[IssueCard],
        relations: list[Relation],
    ) -> None: ...
