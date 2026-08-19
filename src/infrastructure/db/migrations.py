from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.infrastructure.db.connection import connect

INITIAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    product_line TEXT NOT NULL,
    stage TEXT NOT NULL,
    current_baseline_id TEXT,
    allow_external_model INTEGER NOT NULL DEFAULT 0 CHECK (allow_external_model IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    project_root_path TEXT,
    root_status TEXT NOT NULL DEFAULT 'unavailable',
    root_last_verified_at TEXT
);

CREATE TABLE IF NOT EXISTS source_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    original_filename TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    source_type TEXT NOT NULL,
    authority_level TEXT NOT NULL,
    source_department TEXT NOT NULL,
    provider TEXT,
    document_date TEXT NOT NULL,
    document_version TEXT NOT NULL,
    applicable_baseline_version TEXT NOT NULL,
    security_level TEXT NOT NULL,
    is_redacted INTEGER NOT NULL CHECK (is_redacted IN (0, 1)),
    allow_external_model INTEGER NOT NULL CHECK (allow_external_model IN (0, 1)),
    is_sandbox INTEGER NOT NULL CHECK (is_sandbox IN (0, 1)),
    ingest_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    material_name TEXT,
    material_series_id TEXT,
    previous_source_id TEXT,
    UNIQUE(project_id, sha256)
);

CREATE TABLE IF NOT EXISTS baselines (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    version TEXT NOT NULL,
    parent_baseline_id TEXT REFERENCES baselines(id),
    status TEXT NOT NULL,
    full_document_path TEXT NOT NULL,
    card_snapshot_path TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    change_request_id TEXT,
    approved_by TEXT NOT NULL,
    effective_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE IF NOT EXISTS knowledge_cards (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    card_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    product_version TEXT NOT NULL,
    applicable_scope TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    authority_level TEXT NOT NULL,
    owner TEXT NOT NULL,
    confidence REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    source_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    source_ref TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, source_id, relation_type, target_id)
);

CREATE TABLE IF NOT EXISTS issue_cards (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    impacted_domains_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    ai_recommendation TEXT,
    ai_confidence REAL,
    uncertainty TEXT,
    validation_note TEXT,
    raw_severity TEXT,
    deterministic_rule_id TEXT,
    fingerprint TEXT,
    target_rule_id TEXT,
    owner TEXT,
    due_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    issue_id TEXT NOT NULL REFERENCES issue_cards(id),
    action TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    responsible_party TEXT,
    due_at TEXT,
    verification_condition TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    command_fingerprint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    issue_id TEXT NOT NULL REFERENCES issue_cards(id),
    decision_id TEXT NOT NULL REFERENCES decisions(id),
    target_card_id TEXT NOT NULL,
    before_content TEXT NOT NULL,
    after_content TEXT NOT NULL,
    rationale TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    impacted_objects_json TEXT NOT NULL,
    responsible_domain TEXT NOT NULL,
    required_approver_role TEXT NOT NULL,
    demo_confirmer TEXT NOT NULL,
    status TEXT NOT NULL,
    review_action TEXT,
    reviewed_by TEXT,
    review_comment TEXT,
    review_idempotency_key TEXT UNIQUE,
    reviewed_at TEXT,
    target_version TEXT NOT NULL,
    effective_condition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_call_logs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_type TEXT NOT NULL,
    workflow_run_id TEXT,
    correlation_id TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    baseline_version TEXT NOT NULL,
    model_label TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    authorized INTEGER NOT NULL CHECK (authorized IN (0, 1)),
    redacted INTEGER NOT NULL CHECK (redacted IN (0, 1)),
    outbound_chars INTEGER NOT NULL DEFAULT 0,
    outbound_coverage REAL NOT NULL DEFAULT 0,
    result_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    elapsed_ms INTEGER,
    error_code TEXT
);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'LLD',
    task_type TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    baseline_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_label TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    response_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_drafts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    version_id TEXT NOT NULL,
    display_version TEXT,
    parent_version_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('candidate_draft','pending_owner','published')),
    markdown_path TEXT NOT NULL,
    markdown_sha256 TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    section_citations_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    missing_sections_json TEXT NOT NULL,
    evidence_gaps_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    generation_mode TEXT NOT NULL DEFAULT 'external_ai',
    UNIQUE(project_id, version_id)
);

CREATE TABLE IF NOT EXISTS structure_suggestions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    reason TEXT NOT NULL,
    reference_project_ids_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL CHECK (status IN ('open','accepted','ignored')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_logs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO'
        CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_project_created
    ON source_records(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_card_project_status
    ON knowledge_cards(project_id, status);
CREATE INDEX IF NOT EXISTS idx_issue_project_status
    ON issue_cards(project_id, status, severity);
CREATE INDEX IF NOT EXISTS idx_change_project_status
    ON change_requests(project_id, status);
CREATE INDEX IF NOT EXISTS idx_event_entity
    ON event_logs(project_id, entity_type, entity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_document_drafts_project_created
    ON document_drafts(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_structure_suggestions_project_status
    ON structure_suggestions(project_id, status, created_at DESC);
"""


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _root_is_available(project_root: Path, project_id: str) -> bool:
    project_json = project_root / ".incubator" / "project.json"
    if not project_root.is_dir() or not project_json.is_file():
        return False
    try:
        payload = json.loads(project_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("project_id") == project_id


def migrate(db_path: Path) -> None:
    """Create or update the local SQLite schema safely and repeatedly."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.executescript(INITIAL_SCHEMA_SQL)
        _add_column_if_missing(connection, "model_call_logs", "workflow_run_id", "TEXT")
        _add_column_if_missing(
            connection,
            "cache_entries",
            "project_id",
            "TEXT NOT NULL DEFAULT 'LLD'",
        )
        _add_column_if_missing(connection, "model_call_logs", "correlation_id", "TEXT")
        _add_column_if_missing(connection, "event_logs", "correlation_id", "TEXT")
        _add_column_if_missing(connection, "issue_cards", "validation_note", "TEXT")
        _add_column_if_missing(connection, "issue_cards", "raw_severity", "TEXT")
        _add_column_if_missing(connection, "issue_cards", "deterministic_rule_id", "TEXT")
        _add_column_if_missing(connection, "issue_cards", "fingerprint", "TEXT")
        _add_column_if_missing(connection, "issue_cards", "target_rule_id", "TEXT")
        _add_column_if_missing(connection, "baselines", "full_document_sha256", "TEXT")
        _add_column_if_missing(connection, "baselines", "card_snapshot_sha256", "TEXT")
        _add_column_if_missing(connection, "baselines", "display_version", "TEXT")
        _add_column_if_missing(connection, "source_records", "material_name", "TEXT")
        _add_column_if_missing(connection, "source_records", "material_series_id", "TEXT")
        _add_column_if_missing(connection, "source_records", "previous_source_id", "TEXT")
        _add_column_if_missing(connection, "source_records", "ingest_schema_version", "TEXT")
        _add_column_if_missing(connection, "source_records", "ingested_at", "TEXT")
        _add_column_if_missing(connection, "source_records", "source_page_path", "TEXT")
        _add_column_if_missing(
            connection,
            "source_records",
            "topic_page_paths_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        _add_column_if_missing(connection, "source_records", "ingest_result_digest", "TEXT")
        _add_column_if_missing(connection, "source_records", "ingest_error_code", "TEXT")
        _add_column_if_missing(connection, "source_records", "generation_mode", "TEXT")
        _add_column_if_missing(connection, "projects", "project_root_path", "TEXT")
        _add_column_if_missing(
            connection,
            "projects",
            "root_status",
            "TEXT NOT NULL DEFAULT 'unavailable'",
        )
        _add_column_if_missing(connection, "projects", "root_last_verified_at", "TEXT")
        _add_column_if_missing(
            connection,
            "document_drafts",
            "generation_mode",
            "TEXT NOT NULL DEFAULT 'external_ai'",
        )
        _add_column_if_missing(
            connection,
            "decisions",
            "command_fingerprint",
            "TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            connection,
            "event_logs",
            "level",
            "TEXT NOT NULL DEFAULT 'INFO' CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR'))",
        )
        connection.execute(
            "UPDATE event_logs SET correlation_id = 'LEGACY-' || id "
            "WHERE correlation_id IS NULL OR TRIM(correlation_id) = ''"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_call_correlation "
            "ON model_call_logs(project_id, correlation_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_correlation "
            "ON event_logs(project_id, correlation_id, created_at)"
        )
        connection.execute("DROP INDEX IF EXISTS idx_issue_fingerprint")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_issue_project_fingerprint "
            "ON issue_cards(project_id, fingerprint) WHERE fingerprint IS NOT NULL"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_project_series "
            "ON source_records(project_id, material_series_id, created_at)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_source_series_version "
            "ON source_records(project_id, material_series_id, document_version) "
            "WHERE material_series_id IS NOT NULL"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wiki_ingest_runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id),
                source_id TEXT NOT NULL REFERENCES source_records(id),
                transaction_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                schema_version TEXT NOT NULL,
                generation_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                source_page_path TEXT,
                topic_page_paths_json TEXT NOT NULL DEFAULT '[]',
                result_digest TEXT,
                error_code TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wiki_transaction_bindings (
                transaction_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                binding_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        control_root = db_path.parent.parent
        legacy_projects = connection.execute(
            "SELECT id FROM projects WHERE project_root_path IS NULL"
        ).fetchall()
        for project in legacy_projects:
            project_id = str(project["id"])
            project_root = (control_root / project_id).resolve()
            root_status = (
                "available" if _root_is_available(project_root, project_id) else "unavailable"
            )
            connection.execute(
                "UPDATE projects SET project_root_path = ?, root_status = ? "
                "WHERE id = ? AND project_root_path IS NULL",
                (str(project_root), root_status, project_id),
            )
        connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", ("1.0",))
        connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", ("1.1",))
        connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", ("1.2",))
        connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", ("2.1",))
        connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", ("2.2",))
