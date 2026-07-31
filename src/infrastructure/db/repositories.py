from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from src.domain.enums import (
    BaselineStatus,
    CallResultMode,
    ChangeReviewAction,
    ChangeStatus,
    IssueStatus,
    KnowledgeStatus,
)
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
from src.infrastructure.db.connection import connect

Model = TypeVar("Model", bound=BaseModel)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str) -> Any:
    return json.loads(value)


def _row_data(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _require(row: sqlite3.Row | None, entity: str, entity_id: str) -> sqlite3.Row:
    if row is None:
        raise KeyError(f"{entity} not found: {entity_id}")
    return row


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("updated_at must be an aware UTC datetime")
    return value


class SqliteProjectRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, project: Project) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, product_line, stage, current_baseline_id,
                    allow_external_model, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.id,
                    project.name,
                    project.product_line,
                    project.stage,
                    project.current_baseline_id,
                    int(project.allow_external_model),
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )

    def get(self, project_id: str) -> Project:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone(),
                "project",
                project_id,
            )
        data = _row_data(row)
        data["allow_external_model"] = bool(data["allow_external_model"])
        return Project.model_validate(data)

    def update_current_baseline(self, project_id: str, baseline_id: str) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                "UPDATE projects SET current_baseline_id = ? WHERE id = ?",
                (baseline_id, project_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"project not found: {project_id}")


class SqliteSourceRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, source: SourceRecord) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO source_records (
                    id, project_id, original_filename, archive_path, sha256, mime_type,
                    size_bytes, source_type, authority_level, source_department, provider,
                    document_date, document_version, applicable_baseline_version,
                    security_level, is_redacted, allow_external_model, is_sandbox,
                    ingest_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.id,
                    source.project_id,
                    source.original_filename,
                    source.archive_path,
                    source.sha256,
                    source.mime_type,
                    source.size_bytes,
                    source.source_type,
                    source.authority_level.value,
                    source.source_department,
                    source.provider,
                    source.document_date.isoformat(),
                    source.document_version,
                    source.applicable_baseline_version,
                    source.security_level.value,
                    int(source.is_redacted),
                    int(source.allow_external_model),
                    int(source.is_sandbox),
                    source.ingest_status,
                    source.created_at.isoformat(),
                ),
            )

    def get(self, source_id: str) -> SourceRecord:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM source_records WHERE id = ?", (source_id,)
                ).fetchone(),
                "source",
                source_id,
            )
        return self._to_model(row)

    def find_by_sha256(self, project_id: str, sha256: str) -> SourceRecord | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM source_records WHERE project_id = ? AND sha256 = ?",
                (project_id, sha256),
            ).fetchone()
        return None if row is None else self._to_model(row)

    def list_for_project(self, project_id: str) -> list[SourceRecord]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM source_records WHERE project_id = ? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def update(self, source: SourceRecord) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                """
                UPDATE source_records
                SET original_filename = ?, archive_path = ?, mime_type = ?, size_bytes = ?,
                    source_type = ?, authority_level = ?, source_department = ?, provider = ?,
                    document_date = ?, document_version = ?, applicable_baseline_version = ?,
                    security_level = ?, is_redacted = ?, allow_external_model = ?,
                    is_sandbox = ?, ingest_status = ?
                WHERE id = ? AND project_id = ? AND sha256 = ?
                """,
                (
                    source.original_filename,
                    source.archive_path,
                    source.mime_type,
                    source.size_bytes,
                    source.source_type,
                    source.authority_level.value,
                    source.source_department,
                    source.provider,
                    source.document_date.isoformat(),
                    source.document_version,
                    source.applicable_baseline_version,
                    source.security_level.value,
                    int(source.is_redacted),
                    int(source.allow_external_model),
                    int(source.is_sandbox),
                    source.ingest_status,
                    source.id,
                    source.project_id,
                    source.sha256,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(f"source not found: {source.id}")

    def update_ingest_status(self, source_id: str, ingest_status: str) -> None:
        if not ingest_status.strip():
            raise ValueError("ingest_status cannot be empty")
        with connect(self.db_path) as connection:
            result = connection.execute(
                "UPDATE source_records SET ingest_status = ? WHERE id = ?",
                (ingest_status, source_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"source not found: {source_id}")

    @staticmethod
    def _to_model(row: sqlite3.Row) -> SourceRecord:
        data = _row_data(row)
        for field in ("is_redacted", "allow_external_model", "is_sandbox"):
            data[field] = bool(data[field])
        return SourceRecord.model_validate(data)


class SqliteBaselineRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, baseline: Baseline) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO baselines (
                    id, project_id, version, parent_baseline_id, status,
                    full_document_path, card_snapshot_path, manifest_sha256,
                    change_request_id, approved_by, effective_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    baseline.id,
                    baseline.project_id,
                    baseline.version,
                    baseline.parent_baseline_id,
                    baseline.status.value,
                    baseline.full_document_path,
                    baseline.card_snapshot_path,
                    baseline.manifest_sha256,
                    baseline.change_request_id,
                    baseline.approved_by,
                    _iso_or_none(baseline.effective_at),
                    baseline.created_at.isoformat(),
                ),
            )

    def get(self, baseline_id: str) -> Baseline:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM baselines WHERE id = ?", (baseline_id,)
                ).fetchone(),
                "baseline",
                baseline_id,
            )
        return Baseline.model_validate(_row_data(row))

    def mark_superseded(self, baseline_id: str) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                "UPDATE baselines SET status = ? WHERE id = ?",
                (BaselineStatus.SUPERSEDED.value, baseline_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"baseline not found: {baseline_id}")


def _iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


class SqliteKnowledgeRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def upsert_cards(self, cards: list[KnowledgeCard]) -> None:
        with connect(self.db_path) as connection:
            for card in cards:
                connection.execute(
                    """
                    INSERT INTO knowledge_cards (
                        id, project_id, card_type, title, content, status, product_version,
                        applicable_scope, source_refs_json, authority_level, owner, confidence,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        card_type = excluded.card_type,
                        title = excluded.title,
                        content = excluded.content,
                        status = excluded.status,
                        product_version = excluded.product_version,
                        applicable_scope = excluded.applicable_scope,
                        source_refs_json = excluded.source_refs_json,
                        authority_level = excluded.authority_level,
                        owner = excluded.owner,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at
                    """,
                    (
                        card.id,
                        card.project_id,
                        card.card_type,
                        card.title,
                        card.content,
                        card.status.value,
                        card.product_version,
                        card.applicable_scope,
                        _json_dumps(card.source_refs),
                        card.authority_level.value,
                        card.owner,
                        card.confidence,
                        card.created_at.isoformat(),
                        card.updated_at.isoformat(),
                    ),
                )

    def list_effective(self, project_id: str, version: str) -> list[KnowledgeCard]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_cards
                WHERE project_id = ? AND product_version = ? AND status = ?
                ORDER BY id
                """,
                (project_id, version, KnowledgeStatus.EFFECTIVE.value),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def get_card(self, card_id: str) -> KnowledgeCard:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)
                ).fetchone(),
                "knowledge card",
                card_id,
            )
        return self._to_model(row)

    @staticmethod
    def _to_model(row: sqlite3.Row) -> KnowledgeCard:
        data = _row_data(row)
        data["source_refs"] = _json_loads(data.pop("source_refs_json"))
        return KnowledgeCard.model_validate(data)


class SqliteIssueRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add_many(self, issues: list[IssueCard]) -> None:
        with connect(self.db_path) as connection:
            for issue in issues:
                connection.execute(
                    """
                    INSERT INTO issue_cards (
                        id, project_id, issue_type, severity, status, title, description,
                        evidence_json, impacted_domains_json, options_json, ai_recommendation,
                        ai_confidence, uncertainty, owner, due_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue.id,
                        issue.project_id,
                        issue.issue_type,
                        issue.severity.value,
                        issue.status.value,
                        issue.title,
                        issue.description,
                        _json_dumps([item.model_dump(mode="json") for item in issue.evidence]),
                        _json_dumps(issue.impacted_domains),
                        _json_dumps(issue.options),
                        issue.ai_recommendation,
                        issue.ai_confidence,
                        issue.uncertainty,
                        issue.owner,
                        _iso_or_none(issue.due_at),
                        issue.created_at.isoformat(),
                        issue.updated_at.isoformat(),
                    ),
                )

    def get(self, issue_id: str) -> IssueCard:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM issue_cards WHERE id = ?", (issue_id,)
                ).fetchone(),
                "issue",
                issue_id,
            )
        return self._to_model(row)

    def list_open(self, project_id: str) -> list[IssueCard]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM issue_cards WHERE project_id = ? AND status = ? ORDER BY id",
                (project_id, IssueStatus.OPEN.value),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def update_status(self, issue_id: str, status: IssueStatus, updated_at: datetime) -> None:
        updated_at = _require_utc(updated_at)
        with connect(self.db_path) as connection:
            result = connection.execute(
                "UPDATE issue_cards SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, updated_at.isoformat(), issue_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"issue not found: {issue_id}")

    @staticmethod
    def _to_model(row: sqlite3.Row) -> IssueCard:
        data = _row_data(row)
        data["evidence"] = _json_loads(data.pop("evidence_json"))
        data["impacted_domains"] = _json_loads(data.pop("impacted_domains_json"))
        data["options"] = _json_loads(data.pop("options_json"))
        return IssueCard.model_validate(data)


class SqliteDecisionRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, decision: Decision, idempotency_key: str) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO decisions (
                    id, project_id, issue_id, action, conclusion, confirmed_by,
                    responsible_party, due_at, verification_condition, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.project_id,
                    decision.issue_id,
                    decision.action.value,
                    decision.conclusion,
                    decision.confirmed_by,
                    decision.responsible_party,
                    _iso_or_none(decision.due_at),
                    decision.verification_condition,
                    idempotency_key,
                    decision.created_at.isoformat(),
                ),
            )

    def get(self, decision_id: str) -> Decision:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM decisions WHERE id = ?", (decision_id,)
                ).fetchone(),
                "decision",
                decision_id,
            )
        data = _row_data(row)
        data.pop("idempotency_key")
        return Decision.model_validate(data)


class SqliteChangeRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, change: ChangeRequest) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO change_requests (
                    id, project_id, issue_id, decision_id, target_card_id, before_content,
                    after_content, rationale, evidence_refs_json, impacted_objects_json,
                    responsible_domain, required_approver_role, demo_confirmer, status,
                    review_action, reviewed_by, review_comment, review_idempotency_key,
                    reviewed_at, target_version, effective_condition, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._values(change),
            )

    def get(self, change_id: str) -> ChangeRequest:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM change_requests WHERE id = ?", (change_id,)
                ).fetchone(),
                "change request",
                change_id,
            )
        return self._to_model(row)

    def find_by_review_idempotency_key(self, idempotency_key: str) -> ChangeRequest | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM change_requests WHERE review_idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else self._to_model(row)

    def record_review(
        self,
        change_id: str,
        action: ChangeReviewAction,
        reviewed_by: str,
        comment: str,
        idempotency_key: str,
        reviewed_at: datetime,
        target_status: ChangeStatus,
    ) -> ChangeRequest:
        with connect(self.db_path) as connection:
            result = connection.execute(
                """
                UPDATE change_requests
                SET status = ?, review_action = ?, reviewed_by = ?, review_comment = ?,
                    review_idempotency_key = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    target_status.value,
                    action.value,
                    reviewed_by,
                    comment,
                    idempotency_key,
                    reviewed_at.isoformat(),
                    reviewed_at.isoformat(),
                    change_id,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(f"change request not found: {change_id}")
        return self.get(change_id)

    def update_status(self, change_id: str, status: ChangeStatus, updated_at: datetime) -> None:
        updated_at = _require_utc(updated_at)
        with connect(self.db_path) as connection:
            result = connection.execute(
                "UPDATE change_requests SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, updated_at.isoformat(), change_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"change request not found: {change_id}")

    def list_pending(self, project_id: str) -> list[ChangeRequest]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM change_requests
                WHERE project_id = ? AND status = ?
                ORDER BY created_at, id
                """,
                (project_id, ChangeStatus.PENDING_APPROVAL.value),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    @staticmethod
    def _values(change: ChangeRequest) -> tuple[Any, ...]:
        return (
            change.id,
            change.project_id,
            change.issue_id,
            change.decision_id,
            change.target_card_id,
            change.before_content,
            change.after_content,
            change.rationale,
            _json_dumps(change.evidence_refs),
            _json_dumps(change.impacted_objects),
            change.responsible_domain,
            change.required_approver_role,
            change.demo_confirmer,
            change.status.value,
            None if change.review_action is None else change.review_action.value,
            change.reviewed_by,
            change.review_comment,
            change.review_idempotency_key,
            _iso_or_none(change.reviewed_at),
            change.target_version,
            change.effective_condition,
            change.created_at.isoformat(),
            change.updated_at.isoformat(),
        )

    @staticmethod
    def _to_model(row: sqlite3.Row) -> ChangeRequest:
        data = _row_data(row)
        data["evidence_refs"] = _json_loads(data.pop("evidence_refs_json"))
        data["impacted_objects"] = _json_loads(data.pop("impacted_objects_json"))
        return ChangeRequest.model_validate(data)


class SqliteIngestUnitOfWork:
    """Own one SQLite transaction for every authoritative ingest result write."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def complete(
        self,
        source: SourceRecord,
        cards: list[KnowledgeCard],
        relations: list[Relation],
        issues: list[IssueCard],
        event: EventLog,
    ) -> None:
        with connect(self.db_path) as connection:
            for card in cards:
                connection.execute(
                    """
                    INSERT INTO knowledge_cards (
                        id, project_id, card_type, title, content, status, product_version,
                        applicable_scope, source_refs_json, authority_level, owner, confidence,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        card_type = excluded.card_type,
                        title = excluded.title,
                        content = excluded.content,
                        status = excluded.status,
                        product_version = excluded.product_version,
                        applicable_scope = excluded.applicable_scope,
                        source_refs_json = excluded.source_refs_json,
                        authority_level = excluded.authority_level,
                        owner = excluded.owner,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at
                    """,
                    (
                        card.id,
                        card.project_id,
                        card.card_type,
                        card.title,
                        card.content,
                        card.status.value,
                        card.product_version,
                        card.applicable_scope,
                        _json_dumps(card.source_refs),
                        card.authority_level.value,
                        card.owner,
                        card.confidence,
                        card.created_at.isoformat(),
                        card.updated_at.isoformat(),
                    ),
                )
            for relation in relations:
                connection.execute(
                    """
                    INSERT INTO relations (
                        id, project_id, source_id, relation_type, target_id, source_ref, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation.id,
                        relation.project_id,
                        relation.source_id,
                        relation.relation_type,
                        relation.target_id,
                        relation.source_ref,
                        relation.created_at.isoformat(),
                    ),
                )
            for issue in issues:
                connection.execute(
                    """
                    INSERT INTO issue_cards (
                        id, project_id, issue_type, severity, status, title, description,
                        evidence_json, impacted_domains_json, options_json, ai_recommendation,
                        ai_confidence, uncertainty, owner, due_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue.id,
                        issue.project_id,
                        issue.issue_type,
                        issue.severity.value,
                        issue.status.value,
                        issue.title,
                        issue.description,
                        _json_dumps([item.model_dump(mode="json") for item in issue.evidence]),
                        _json_dumps(issue.impacted_domains),
                        _json_dumps(issue.options),
                        issue.ai_recommendation,
                        issue.ai_confidence,
                        issue.uncertainty,
                        issue.owner,
                        _iso_or_none(issue.due_at),
                        issue.created_at.isoformat(),
                        issue.updated_at.isoformat(),
                    ),
                )
            result = connection.execute(
                "UPDATE source_records SET ingest_status = 'completed' WHERE id = ?",
                (source.id,),
            )
            if result.rowcount != 1:
                raise KeyError(f"source not found: {source.id}")
            connection.execute(
                """
                INSERT INTO event_logs (
                    id, project_id, event_type, entity_type, entity_id,
                    actor, correlation_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.project_id,
                    event.event_type,
                    event.entity_type,
                    event.entity_id,
                    event.actor,
                    event.correlation_id,
                    _json_dumps(event.payload),
                    event.created_at.isoformat(),
                ),
            )

    def duplicate_report(self, source: SourceRecord) -> IngestReport:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM event_logs
                WHERE project_id = ? AND entity_type = 'source' AND entity_id = ?
                  AND event_type = 'source_ingest_completed'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (source.project_id, source.id),
            ).fetchone()
        if row is None:
            raise KeyError(f"completed ingest event not found: {source.id}")
        payload = _json_loads(row["payload_json"])
        return IngestReport(
            source_id=source.id,
            duplicate=True,
            summary="该材料已完成导入，未重复写入。",
            created_card_ids=payload["created_card_ids"],
            created_relation_ids=payload["created_relation_ids"],
            created_issue_ids=payload["created_issue_ids"],
            candidate_count=payload["candidate_count"],
            conflict_count=payload["conflict_count"],
            result_mode=CallResultMode(payload["result_mode"]),
            model_call_id=payload.get("model_call_id"),
        )
