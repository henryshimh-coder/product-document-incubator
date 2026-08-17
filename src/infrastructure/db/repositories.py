from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import suppress
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
    ProjectRootStatus,
    StructureSuggestionStatus,
)
from src.domain.errors import DomainError, ErrorCode
from src.domain.incubator import DocumentDraft, StructureSuggestion
from src.domain.models import (
    Baseline,
    ChangeRequest,
    Decision,
    DecisionResult,
    EventLog,
    IngestReport,
    IngestResultView,
    IssueCard,
    KnowledgeCard,
    ModelCallLog,
    Project,
    Relation,
    SourceRecord,
)
from src.domain.policies.state_transition import ensure_change_transition
from src.infrastructure.db.connection import connect
from src.infrastructure.observability.event_logger import (
    AuditDurabilityUncertainError,
    EventLogger,
)

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
        project_root_path = (
            str(Path(project.project_root_path).expanduser().resolve())
            if project.project_root_path is not None
            else None
        )
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, product_line, stage, current_baseline_id,
                    allow_external_model, created_at, updated_at, project_root_path,
                    root_status, root_last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    project_root_path,
                    project.root_status.value,
                    (
                        project.root_last_verified_at.isoformat()
                        if project.root_last_verified_at is not None
                        else None
                    ),
                ),
            )

    def get(self, project_id: str) -> Project:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone(),
                "project",
                project_id,
            )
        return self._to_model(row)

    def list_all(self) -> list[Project]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC, id"
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def update_current_baseline(self, project_id: str, baseline_id: str) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                "UPDATE projects SET current_baseline_id = ? WHERE id = ?",
                (baseline_id, project_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"project not found: {project_id}")

    def update_root_location(
        self,
        project_id: str,
        project_root: Path,
        status: ProjectRootStatus,
        verified_at: datetime | None,
    ) -> None:
        resolved_root = project_root.expanduser().resolve()
        with connect(self.db_path) as connection:
            result = connection.execute(
                """
                UPDATE projects
                SET project_root_path = ?, root_status = ?, root_last_verified_at = ?
                WHERE id = ?
                """,
                (
                    str(resolved_root),
                    status.value,
                    verified_at.isoformat() if verified_at is not None else None,
                    project_id,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(f"project not found: {project_id}")

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Project:
        data = _row_data(row)
        data["allow_external_model"] = bool(data["allow_external_model"])
        return Project.model_validate(data)


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
                    ingest_status, created_at, material_name, material_series_id,
                    previous_source_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source.material_name,
                    source.material_series_id,
                    source.previous_source_id,
                ),
            )

    def delete(self, source_id: str, project_id: str) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                "DELETE FROM source_records WHERE id = ? AND project_id = ?",
                (source_id, project_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"source not found: {source_id}")

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

    def list_for_series(self, project_id: str, series_id: str) -> list[SourceRecord]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM source_records
                WHERE project_id = ? AND material_series_id = ?
                ORDER BY created_at, id
                """,
                (project_id, series_id),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_latest_for_series(self, project_id: str, series_id: str) -> SourceRecord | None:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT current.* FROM source_records AS current
                WHERE current.project_id = ? AND current.material_series_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM source_records AS successor
                      WHERE successor.project_id = current.project_id
                        AND successor.previous_source_id = current.id
                  )
                ORDER BY current.created_at, current.id
                """,
                (project_id, series_id),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("MATERIAL_SERIES_FORKED")
        return self._to_model(rows[0])

    def find_by_series_version(
        self, project_id: str, series_id: str, document_version: str
    ) -> SourceRecord | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT * FROM source_records
                WHERE project_id = ? AND material_series_id = ? AND document_version = ?
                """,
                (project_id, series_id, document_version),
            ).fetchone()
        return None if row is None else self._to_model(row)

    def update(self, source: SourceRecord) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                """
                UPDATE source_records
                SET original_filename = ?, archive_path = ?, mime_type = ?, size_bytes = ?,
                    source_type = ?, authority_level = ?, source_department = ?, provider = ?,
                    document_date = ?, document_version = ?, applicable_baseline_version = ?,
                    security_level = ?, is_redacted = ?, allow_external_model = ?,
                    is_sandbox = ?, ingest_status = ?, material_name = ?,
                    material_series_id = ?, previous_source_id = ?
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
                    source.material_name,
                    source.material_series_id,
                    source.previous_source_id,
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


class SqliteDocumentDraftRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, draft: DocumentDraft) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO document_drafts (
                    id, project_id, version_id, display_version, parent_version_id, status,
                    markdown_path, markdown_sha256, source_ids_json, section_citations_json,
                    summary, missing_sections_json, evidence_gaps_json, created_at, updated_at,
                    generation_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.project_id,
                    draft.version_id,
                    draft.display_version,
                    draft.parent_version_id,
                    draft.status.value,
                    draft.markdown_path,
                    draft.markdown_sha256,
                    _json_dumps(draft.source_ids),
                    _json_dumps([item.model_dump(mode="json") for item in draft.section_citations]),
                    draft.summary,
                    _json_dumps(draft.missing_sections),
                    _json_dumps(draft.evidence_gaps),
                    draft.created_at.isoformat(),
                    draft.updated_at.isoformat(),
                    draft.generation_mode.value,
                ),
            )

    def get(self, draft_id: str) -> DocumentDraft:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM document_drafts WHERE id = ?", (draft_id,)
                ).fetchone(),
                "document draft",
                draft_id,
            )
        return self._to_model(row)

    def update(self, draft: DocumentDraft) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                """
                UPDATE document_drafts
                SET status = ?, markdown_sha256 = ?, summary = ?, missing_sections_json = ?,
                    evidence_gaps_json = ?, updated_at = ?
                WHERE id = ? AND project_id = ?
                """,
                (
                    draft.status.value,
                    draft.markdown_sha256,
                    draft.summary,
                    _json_dumps(draft.missing_sections),
                    _json_dumps(draft.evidence_gaps),
                    draft.updated_at.isoformat(),
                    draft.id,
                    draft.project_id,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(f"document draft not found: {draft.id}")

    def list_for_project(self, project_id: str) -> list[DocumentDraft]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM document_drafts
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> DocumentDraft:
        data = _row_data(row)
        data["source_ids"] = _json_loads(data.pop("source_ids_json"))
        data["section_citations"] = _json_loads(data.pop("section_citations_json"))
        data["missing_sections"] = _json_loads(data.pop("missing_sections_json"))
        data["evidence_gaps"] = _json_loads(data.pop("evidence_gaps_json"))
        return DocumentDraft.model_validate(data)


class SqliteStructureSuggestionRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, suggestion: StructureSuggestion) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO structure_suggestions (
                    id, project_id, title, reason, reference_project_ids_json,
                    confidence, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion.id,
                    suggestion.project_id,
                    suggestion.title,
                    suggestion.reason,
                    _json_dumps(suggestion.reference_project_ids),
                    suggestion.confidence,
                    suggestion.status.value,
                    suggestion.created_at.isoformat(),
                    suggestion.updated_at.isoformat(),
                ),
            )

    def get(self, suggestion_id: str) -> StructureSuggestion:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM structure_suggestions WHERE id = ?", (suggestion_id,)
                ).fetchone(),
                "structure suggestion",
                suggestion_id,
            )
        return self._to_model(row)

    def list_for_project(self, project_id: str) -> list[StructureSuggestion]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM structure_suggestions
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def update(self, suggestion: StructureSuggestion) -> None:
        with connect(self.db_path) as connection:
            result = connection.execute(
                """
                UPDATE structure_suggestions SET status = ?, updated_at = ?
                WHERE id = ? AND project_id = ?
                """,
                (
                    suggestion.status.value,
                    suggestion.updated_at.isoformat(),
                    suggestion.id,
                    suggestion.project_id,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(f"structure suggestion not found: {suggestion.id}")

    def accepted_titles(self, project_id: str) -> list[str]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT title FROM structure_suggestions
                WHERE project_id = ? AND status = ?
                ORDER BY created_at, id
                """,
                (project_id, StructureSuggestionStatus.ACCEPTED.value),
            ).fetchall()
        return [str(row["title"]) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> StructureSuggestion:
        data = _row_data(row)
        data["reference_project_ids"] = _json_loads(data.pop("reference_project_ids_json"))
        return StructureSuggestion.model_validate(data)


class SqliteBaselineRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, baseline: Baseline) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO baselines (
                    id, project_id, version, display_version, parent_baseline_id, status,
                    full_document_path, card_snapshot_path, manifest_sha256,
                    full_document_sha256, card_snapshot_sha256,
                    change_request_id, approved_by, effective_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    baseline.id,
                    baseline.project_id,
                    baseline.version,
                    baseline.display_version,
                    baseline.parent_baseline_id,
                    baseline.status.value,
                    baseline.full_document_path,
                    baseline.card_snapshot_path,
                    baseline.manifest_sha256,
                    baseline.full_document_sha256,
                    baseline.card_snapshot_sha256,
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

    def get_by_version(self, project_id: str, version: str) -> Baseline:
        with connect(self.db_path) as connection:
            row = _require(
                connection.execute(
                    "SELECT * FROM baselines WHERE project_id = ? AND version = ?",
                    (project_id, version),
                ).fetchone(),
                "baseline version",
                f"{project_id}:{version}",
            )
        return Baseline.model_validate(_row_data(row))

    def list_for_project(self, project_id: str) -> list[Baseline]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM baselines WHERE project_id = ? ORDER BY created_at DESC, id",
                (project_id,),
            ).fetchall()
        return [Baseline.model_validate(_row_data(row)) for row in rows]

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

    def list_notices(self, project_id: str, version: str) -> list[KnowledgeCard]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_cards
                WHERE project_id = ? AND product_version = ? AND status IN (?, ?)
                ORDER BY CASE status WHEN ? THEN 0 ELSE 1 END, id
                """,
                (
                    project_id,
                    version,
                    KnowledgeStatus.CANDIDATE.value,
                    KnowledgeStatus.CONFLICT.value,
                    KnowledgeStatus.CANDIDATE.value,
                ),
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


def _upsert_issue(connection: sqlite3.Connection, issue: IssueCard) -> None:
    """Insert or refresh one issue row inside an existing transaction."""

    existing = None
    if issue.fingerprint is not None:
        existing = connection.execute(
            "SELECT id, created_at FROM issue_cards WHERE project_id = ? AND fingerprint = ?",
            (issue.project_id, issue.fingerprint),
        ).fetchone()
    legacy_title = _LEGACY_DETERMINISTIC_TITLES.get(issue.deterministic_rule_id or "")
    if existing is None and issue.target_rule_id is not None and legacy_title == issue.title:
        candidates = connection.execute(
            """
            SELECT id, created_at, fingerprint, issue_type,
                   evidence_json, impacted_domains_json, target_rule_id,
                   deterministic_rule_id
            FROM issue_cards
            WHERE project_id = ? AND issue_type = ?
              AND target_rule_id = ? AND title = ?
              AND fingerprint IS NOT NULL
            """,
            (
                issue.project_id,
                issue.issue_type,
                issue.target_rule_id,
                issue.title,
            ),
        ).fetchall()
        legacy_candidates = [
            row
            for row in candidates
            if row["fingerprint"] == _legacy_issue_fingerprint(row)
            and row["deterministic_rule_id"] in {None, issue.deterministic_rule_id}
        ]
        if len(legacy_candidates) == 1:
            existing = legacy_candidates[0]
    stored = issue
    if existing is not None:
        stored = issue.model_copy(
            update={
                "id": existing["id"],
                "created_at": datetime.fromisoformat(existing["created_at"]),
            }
        )
    connection.execute(
        """
        INSERT INTO issue_cards (
            id, project_id, issue_type, severity, status, title, description,
            evidence_json, impacted_domains_json, options_json, ai_recommendation,
            ai_confidence, uncertainty, validation_note, raw_severity,
            deterministic_rule_id, fingerprint, target_rule_id, owner, due_at,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            issue_type = excluded.issue_type,
            severity = excluded.severity,
            title = excluded.title,
            description = excluded.description,
            evidence_json = excluded.evidence_json,
            impacted_domains_json = excluded.impacted_domains_json,
            options_json = excluded.options_json,
            ai_recommendation = excluded.ai_recommendation,
            ai_confidence = excluded.ai_confidence,
            uncertainty = excluded.uncertainty,
            validation_note = excluded.validation_note,
            raw_severity = excluded.raw_severity,
            deterministic_rule_id = excluded.deterministic_rule_id,
            fingerprint = excluded.fingerprint,
            target_rule_id = excluded.target_rule_id,
            updated_at = excluded.updated_at
        """,
        SqliteIssueRepository._values(stored),
    )


def _insert_relation_guarded(
    connection: sqlite3.Connection,
    relation: Relation,
) -> None:
    """Insert one relation inside an existing transaction.

    Same id with identical facts is an idempotent skip; same id with divergent
    facts fails closed instead of being silently swallowed.
    """

    existing = connection.execute(
        """
        SELECT project_id, source_id, relation_type, target_id, source_ref
        FROM relations
        WHERE id = ?
        """,
        (relation.id,),
    ).fetchone()
    if existing is not None:
        identical = (
            existing["project_id"] == relation.project_id
            and existing["source_id"] == relation.source_id
            and existing["relation_type"] == relation.relation_type
            and existing["target_id"] == relation.target_id
            and existing["source_ref"] == relation.source_ref
        )
        if not identical:
            raise DomainError(
                ErrorCode.RELATION_CONFLICT,
                f"RELATION_CONFLICT:{relation.id}",
            )
        return
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
                        ai_confidence, uncertainty, validation_note, raw_severity,
                        deterministic_rule_id, fingerprint, target_rule_id, owner, due_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        issue.validation_note,
                        None if issue.raw_severity is None else issue.raw_severity.value,
                        issue.deterministic_rule_id,
                        issue.fingerprint,
                        issue.target_rule_id,
                        issue.owner,
                        _iso_or_none(issue.due_at),
                        issue.created_at.isoformat(),
                        issue.updated_at.isoformat(),
                    ),
                )

    def upsert_all(self, issues: list[IssueCard]) -> None:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for issue in issues:
                _upsert_issue(connection, issue)

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

    def list_all(self, project_id: str) -> list[IssueCard]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM issue_cards WHERE project_id = ? ORDER BY updated_at DESC, id",
                (project_id,),
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
    def _values(issue: IssueCard) -> tuple[Any, ...]:
        return (
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
            issue.validation_note,
            None if issue.raw_severity is None else issue.raw_severity.value,
            issue.deterministic_rule_id,
            issue.fingerprint,
            issue.target_rule_id,
            issue.owner,
            _iso_or_none(issue.due_at),
            issue.created_at.isoformat(),
            issue.updated_at.isoformat(),
        )

    @staticmethod
    def _to_model(row: sqlite3.Row) -> IssueCard:
        data = _row_data(row)
        data["evidence"] = _json_loads(data.pop("evidence_json"))
        data["impacted_domains"] = _json_loads(data.pop("impacted_domains_json"))
        data["options"] = _json_loads(data.pop("options_json"))
        return IssueCard.model_validate(data)


_LEGACY_DETERMINISTIC_TITLES = {
    "STR-001": "知识卡缺少来源",
    "STR-002": "当前卡片引用不存在",
    "GOV-001": "非生效内容进入当前基线",
    "GOV-002": "未授权资料禁止外部模型调用",
    "GOV-003": "正式会议决定未映射到产品变更",
    "VER-001": "当前基线引用历史产品规则",
    "VER-002": "技术方案对应产品版本落后",
    "MKT-001": "市场判断没有证据或验证计划",
    "COST-001": "影响成本的产品参数变化后未重算",
}


def _legacy_issue_fingerprint(row: sqlite3.Row) -> str:
    evidence = _json_loads(row["evidence_json"])
    impacted_domains = _json_loads(row["impacted_domains_json"])
    normalized = "\n".join(
        (
            row["issue_type"].strip().casefold(),
            "|".join(sorted(item["citation_id"] for item in evidence)),
            "|".join(sorted(domain.strip().casefold() for domain in impacted_domains)),
            (row["target_rule_id"] or "").strip().casefold(),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SqliteDecisionRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def add(self, decision: Decision, idempotency_key: str) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO decisions (
                    id, project_id, issue_id, action, conclusion, confirmed_by,
                    responsible_party, due_at, verification_condition, idempotency_key,
                    command_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "",
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
        data.pop("command_fingerprint", None)
        return Decision.model_validate(data)

    def find_by_idempotency_key(self, idempotency_key: str) -> Decision | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        if row is None:
            return None
        data = _row_data(row)
        data.pop("idempotency_key")
        data.pop("command_fingerprint", None)
        return Decision.model_validate(data)

    def list_for_project(self, project_id: str) -> list[Decision]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        decisions = []
        for row in rows:
            data = _row_data(row)
            data.pop("idempotency_key")
            data.pop("command_fingerprint", None)
            decisions.append(Decision.model_validate(data))
        return decisions


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

    def list_release_candidates(self, project_id: str) -> list[ChangeRequest]:
        """List publish-retry approved changes first, then pending, then needs_info."""
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM change_requests
                WHERE project_id = ? AND status IN (?, ?, ?)
                ORDER BY CASE status WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END,
                         created_at, id
                """,
                (
                    project_id,
                    ChangeStatus.APPROVED.value,
                    ChangeStatus.PENDING_APPROVAL.value,
                    ChangeStatus.NEEDS_INFO.value,
                    ChangeStatus.APPROVED.value,
                    ChangeStatus.PENDING_APPROVAL.value,
                ),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_by_decision_id(self, decision_id: str) -> ChangeRequest | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM change_requests WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        return None if row is None else self._to_model(row)

    def list_for_project(self, project_id: str) -> list[ChangeRequest]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM change_requests WHERE project_id = ? ORDER BY created_at, id",
                (project_id,),
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


class SqliteDecisionUnitOfWork:
    """Atomically record a decision, issue transition, and optional change request."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

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
    ) -> DecisionResult:
        from src.domain.errors import DomainError, ErrorCode

        issue_updated_at = _require_utc(issue_updated_at)
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(self.db_path)
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM decisions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["command_fingerprint"] != command_fingerprint:
                    raise DomainError(ErrorCode.DECISION_IDEMPOTENCY_CONFLICT)
                stored_decision = self._decision(existing)
                change_row = connection.execute(
                    "SELECT * FROM change_requests WHERE decision_id = ?",
                    (stored_decision.id,),
                ).fetchone()
                connection.commit()
                return DecisionResult(
                    decision=stored_decision,
                    change_request=(
                        None if change_row is None else SqliteChangeRepository._to_model(change_row)
                    ),
                )

            issue_row = connection.execute(
                "SELECT project_id, status FROM issue_cards WHERE id = ?",
                (decision.issue_id,),
            ).fetchone()
            if issue_row is None or issue_row["project_id"] != decision.project_id:
                raise DomainError(ErrorCode.DECISION_INVALID, "ISSUE_NOT_FOUND")
            if issue_row["status"] != IssueStatus.OPEN.value:
                raise DomainError(ErrorCode.DECISION_INVALID, "ISSUE_NOT_OPEN")

            connection.execute(
                """
                INSERT INTO decisions (
                    id, project_id, issue_id, action, conclusion, confirmed_by,
                    responsible_party, due_at, verification_condition, idempotency_key,
                    command_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    command_fingerprint,
                    decision.created_at.isoformat(),
                ),
            )
            updated = connection.execute(
                "UPDATE issue_cards SET status = ?, updated_at = ? WHERE id = ?",
                (issue_status.value, issue_updated_at.isoformat(), decision.issue_id),
            )
            if updated.rowcount != 1:
                raise DomainError(ErrorCode.DECISION_INVALID, "ISSUE_UPDATE_FAILED")
            if change_request is not None:
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
                    SqliteChangeRepository._values(change_request),
                )
            for relation in relations:
                _insert_relation_guarded(connection, relation)
            connection.commit()
            return DecisionResult(decision=decision, change_request=change_request)
        except sqlite3.Error as error:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise DomainError(ErrorCode.DECISION_PERSISTENCE_FAILED) from error
        except BaseException:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()

    @staticmethod
    def _decision(row: sqlite3.Row) -> Decision:
        data = _row_data(row)
        data.pop("idempotency_key")
        data.pop("command_fingerprint", None)
        return Decision.model_validate(data)


class SqliteEventRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def latest(self, project_id: str, *, limit: int) -> list[EventLog]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, event_type, entity_type, entity_id, actor,
                       correlation_id, payload_json, created_at
                FROM event_logs
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        events: list[EventLog] = []
        for row in rows:
            data = _row_data(row)
            data["payload"] = _json_loads(data.pop("payload_json"))
            events.append(EventLog.model_validate(data))
        return events


class SqliteIngestUnitOfWork:
    """Own one SQLite transaction for every authoritative ingest result write."""

    def __init__(self, db_path: Path, event_logger: EventLogger | None = None) -> None:
        self.db_path = db_path
        self.event_logger = event_logger or EventLogger(db_path)

    def complete(
        self,
        source: SourceRecord,
        cards: list[KnowledgeCard],
        relations: list[Relation],
        issues: list[IssueCard],
        event: EventLog,
    ) -> bool:
        prepared_event = self.event_logger.prepare(event)
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
                _insert_relation_guarded(connection, relation)
            for issue in issues:
                connection.execute(
                    """
                    INSERT INTO issue_cards (
                        id, project_id, issue_type, severity, status, title, description,
                        evidence_json, impacted_domains_json, options_json, ai_recommendation,
                        ai_confidence, uncertainty, target_rule_id, owner, due_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        issue.target_rule_id,
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
            self.event_logger.insert_prepared(connection, prepared_event)
        try:
            self.event_logger.append_committed(prepared_event)
        except AuditDurabilityUncertainError:
            return True
        return False

    def duplicate_report(
        self,
        source: SourceRecord,
        command_fingerprint: str,
    ) -> IngestReport:
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
        if payload.get("command_fingerprint") != command_fingerprint:
            raise ValueError("SOURCE_METADATA_MISMATCH")
        try:
            result_items = [
                IngestResultView.model_validate(item) for item in payload["result_items"]
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("DUPLICATE_RESULT_ITEMS_INVALID") from error
        cache_generated_at = payload.get("cache_generated_at")
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
            source_hash8=source.sha256[:8],
            cache_generated_at=cache_generated_at,
            result_items=result_items,
        )


class SqliteReviewUnitOfWork:
    """Atomically record a human review and its audit event in one transaction."""

    def __init__(self, db_path: Path, event_logger: EventLogger | None = None) -> None:
        self.db_path = db_path
        self.event_logger = event_logger or EventLogger(db_path)

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
    ) -> ChangeRequest:
        prepared = self.event_logger.prepare(event)
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(self.db_path)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM change_requests WHERE id = ?", (change_id,)
            ).fetchone()
            if row is None:
                raise DomainError(ErrorCode.CHANGE_NOT_REVIEWABLE, "CHANGE_NOT_FOUND")
            if row["review_idempotency_key"] is not None:
                if row["review_idempotency_key"] == idempotency_key:
                    connection.commit()
                    return SqliteChangeRepository._to_model(row)
                raise DomainError(ErrorCode.CHANGE_NOT_REVIEWABLE, "ALREADY_REVIEWED")
            current_status = ChangeStatus(row["status"])
            if current_status != expected_status:
                raise DomainError(ErrorCode.CHANGE_NOT_REVIEWABLE)
            ensure_change_transition(current_status, target_status)
            conflict = connection.execute(
                "SELECT id FROM change_requests WHERE review_idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if conflict is not None:
                raise DomainError(ErrorCode.REVIEW_IDEMPOTENCY_CONFLICT)
            updated = connection.execute(
                """
                UPDATE change_requests
                SET status = ?, review_action = ?, reviewed_by = ?, review_comment = ?,
                    review_idempotency_key = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ? AND status = ?
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
                    expected_status.value,
                ),
            )
            if updated.rowcount != 1:
                raise DomainError(ErrorCode.CHANGE_NOT_REVIEWABLE)
            stored_row = connection.execute(
                "SELECT * FROM change_requests WHERE id = ?", (change_id,)
            ).fetchone()
            self.event_logger.insert_prepared(connection, prepared)
            connection.commit()
            reviewed = SqliteChangeRepository._to_model(stored_row)
        except sqlite3.Error as error:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise DomainError(ErrorCode.REVIEW_PERSISTENCE_FAILED) from error
        except BaseException:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()
        try:
            self.event_logger.append_committed(prepared)
        except AuditDurabilityUncertainError:
            pass
        return reviewed


class SqliteReleaseUnitOfWork:
    """Own the single SQLite transaction that mirrors a freshly effective manifest."""

    def __init__(self, db_path: Path, event_logger: EventLogger | None = None) -> None:
        self.db_path = db_path
        self.event_logger = event_logger or EventLogger(db_path)

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
    ) -> bool:
        self._validate_mirror_payload(
            project_id=project_id,
            new_baseline=new_baseline,
            change_id=change_id,
            superseded_baseline_id=superseded_baseline_id,
            new_cards=new_cards,
            relations=relations,
        )
        prepared = self.event_logger.prepare(event)
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(self.db_path)
            connection.execute("BEGIN IMMEDIATE")
            change_row = connection.execute(
                "SELECT status FROM change_requests WHERE id = ?", (change_id,)
            ).fetchone()
            if change_row is None or change_row["status"] != ChangeStatus.APPROVED.value:
                raise DomainError(ErrorCode.CHANGE_NOT_APPROVED, "MIRROR_PRECONDITION_FAILED")
            backfilled = connection.execute(
                """
                UPDATE baselines
                SET full_document_sha256 = ?, card_snapshot_sha256 = ?
                WHERE id = ? AND project_id = ?
                """,
                (
                    parent_full_document_sha256,
                    parent_card_snapshot_sha256,
                    superseded_baseline_id,
                    project_id,
                ),
            )
            if backfilled.rowcount != 1:
                raise DomainError(ErrorCode.RELEASE_FAILED, "PARENT_BASELINE_NOT_FOUND")
            superseded = connection.execute(
                "UPDATE baselines SET status = ? WHERE id = ? AND status = ?",
                (
                    BaselineStatus.SUPERSEDED.value,
                    superseded_baseline_id,
                    BaselineStatus.EFFECTIVE.value,
                ),
            )
            if superseded.rowcount != 1:
                raise DomainError(ErrorCode.RELEASE_FAILED, "SUPERSEDED_BASELINE_NOT_EFFECTIVE")
            connection.execute(
                """
                INSERT INTO baselines (
                    id, project_id, version, parent_baseline_id, status,
                    full_document_path, card_snapshot_path, manifest_sha256,
                    full_document_sha256, card_snapshot_sha256,
                    change_request_id, approved_by, effective_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_baseline.id,
                    new_baseline.project_id,
                    new_baseline.version,
                    new_baseline.parent_baseline_id,
                    new_baseline.status.value,
                    new_baseline.full_document_path,
                    new_baseline.card_snapshot_path,
                    new_baseline.manifest_sha256,
                    new_baseline.full_document_sha256,
                    new_baseline.card_snapshot_sha256,
                    new_baseline.change_request_id,
                    new_baseline.approved_by,
                    _iso_or_none(new_baseline.effective_at),
                    new_baseline.created_at.isoformat(),
                ),
            )
            updated_change = connection.execute(
                "UPDATE change_requests SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (
                    ChangeStatus.PUBLISHED.value,
                    change_updated_at.isoformat(),
                    change_id,
                    ChangeStatus.APPROVED.value,
                ),
            )
            if updated_change.rowcount != 1:
                raise DomainError(ErrorCode.CHANGE_NOT_APPROVED, "MIRROR_PRECONDITION_FAILED")
            updated_project = connection.execute(
                "UPDATE projects SET current_baseline_id = ?, updated_at = ? WHERE id = ?",
                (new_baseline.id, change_updated_at.isoformat(), project_id),
            )
            if updated_project.rowcount != 1:
                raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH, "PROJECT_NOT_FOUND")
            connection.execute(
                "DELETE FROM knowledge_cards WHERE project_id = ? AND status = ?",
                (project_id, KnowledgeStatus.EFFECTIVE.value),
            )
            for card in new_cards:
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
                existing = connection.execute(
                    """
                    SELECT project_id, source_id, relation_type, target_id, source_ref
                    FROM relations
                    WHERE id = ?
                    """,
                    (relation.id,),
                ).fetchone()
                if existing is not None:
                    identical = (
                        existing["project_id"] == relation.project_id
                        and existing["source_id"] == relation.source_id
                        and existing["relation_type"] == relation.relation_type
                        and existing["target_id"] == relation.target_id
                        and existing["source_ref"] == relation.source_ref
                    )
                    if not identical:
                        raise DomainError(
                            ErrorCode.RELEASE_FAILED,
                            f"RELEASE_MIRROR_RELATION_CONFLICT:{relation.id}",
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO relations (
                        id, project_id, source_id, relation_type, target_id,
                        source_ref, created_at
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
            self.event_logger.insert_prepared(connection, prepared)
            connection.commit()
        except sqlite3.Error as error:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise DomainError(ErrorCode.RELEASE_FAILED, "MIRROR_WRITE_FAILED") from error
        except BaseException:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()
        try:
            self.event_logger.append_committed(prepared)
        except AuditDurabilityUncertainError:
            return True
        return False

    @staticmethod
    def _validate_mirror_payload(
        *,
        project_id: str,
        new_baseline: Baseline,
        change_id: str,
        superseded_baseline_id: str,
        new_cards: list[KnowledgeCard],
        relations: list[Relation],
    ) -> None:
        """Fail closed before SQL when the mirrored payload leaves the publish context."""
        for card in new_cards:
            if card.project_id != project_id or card.product_version != new_baseline.version:
                raise DomainError(
                    ErrorCode.RELEASE_FAILED,
                    f"RELEASE_MIRROR_CARD_MISMATCH:{card.id}",
                )
        allowed_endpoints = {change_id, new_baseline.id, superseded_baseline_id}
        for relation in relations:
            endpoints = {relation.source_id, relation.target_id}
            if relation.project_id != project_id or not endpoints <= allowed_endpoints:
                raise DomainError(
                    ErrorCode.RELEASE_FAILED,
                    f"RELEASE_MIRROR_RELATION_MISMATCH:{relation.id}",
                )


class SqliteModelCallLogRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def list_for_project(self, project_id: str, *, limit: int) -> list[ModelCallLog]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_call_logs
                WHERE project_id = ?
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> ModelCallLog:
        data = _row_data(row)
        data["source_ids"] = _json_loads(data.pop("source_ids_json"))
        data["authorized"] = bool(data["authorized"])
        data["redacted"] = bool(data["redacted"])
        return ModelCallLog.model_validate(data)


class SqliteLintUnitOfWork:
    """Atomically persist lint issue upserts and their knowledge->issue relations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def apply(
        self,
        *,
        issues: list[IssueCard],
        relations: list[Relation],
    ) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(self.db_path)
            connection.execute("BEGIN IMMEDIATE")
            for issue in issues:
                _upsert_issue(connection, issue)
            for relation in relations:
                _insert_relation_guarded(connection, relation)
            connection.commit()
        except sqlite3.Error as error:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise DomainError(ErrorCode.LINT_PERSISTENCE_FAILED) from error
        except BaseException:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            raise
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()


class SqliteRelationRepository:
    """Read the persisted relation graph with project isolation and depth bounds."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def load_connected(
        self,
        project_id: str,
        entity_id: str,
        *,
        max_depth: int = 6,
    ) -> list[Relation]:
        depth = min(max(int(max_depth), 0), 6)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                WITH RECURSIVE frontier(entity_id, depth) AS (
                    SELECT ?, 0
                    UNION
                    SELECT
                        CASE
                            WHEN r.source_id = f.entity_id THEN r.target_id
                            ELSE r.source_id
                        END,
                        f.depth + 1
                    FROM relations r
                    JOIN frontier f
                        ON r.source_id = f.entity_id OR r.target_id = f.entity_id
                    WHERE r.project_id = ? AND f.depth < ?
                )
                SELECT DISTINCT
                    r.id, r.project_id, r.source_id, r.relation_type,
                    r.target_id, r.source_ref, r.created_at
                FROM relations r
                WHERE r.project_id = ?
                  AND r.source_id IN (SELECT entity_id FROM frontier)
                  AND r.target_id IN (SELECT entity_id FROM frontier)
                ORDER BY r.created_at, r.id
                """,
                (entity_id, project_id, depth, project_id),
            ).fetchall()
        return [
            Relation(
                id=row["id"],
                project_id=row["project_id"],
                source_id=row["source_id"],
                relation_type=row["relation_type"],
                target_id=row["target_id"],
                source_ref=row["source_ref"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
