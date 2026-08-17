from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.domain.enums import ProjectRootStatus
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteProjectRepository


def insert_legacy_project(db_path: Path, project_id: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, name, product_line, stage, current_baseline_id,
                allow_external_model, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "历史项目",
                "历史产品线",
                "待初始化",
                None,
                0,
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )


def write_project_json(project_root: Path, *, project_id: str, schema_version: str) -> None:
    (project_root / ".incubator" / "project.json").write_text(
        json.dumps(
            {"project_id": project_id, "schema_version": schema_version}, ensure_ascii=False
        ),
        encoding="utf-8",
    )


def test_migrate_adds_2_2_project_location_and_backfills_existing_project(
    tmp_path: Path,
) -> None:
    """Catches 2.2 migration omitting a discoverable legacy project root."""
    db_path = tmp_path / ".incubator/product_incubator.db"
    migrate(db_path)
    insert_legacy_project(db_path, "PROJECT_A")
    (tmp_path / "PROJECT_A/.incubator").mkdir(parents=True)
    write_project_json(tmp_path / "PROJECT_A", project_id="PROJECT_A", schema_version="2.1")

    migrate(db_path)

    project = SqliteProjectRepository(db_path).get("PROJECT_A")
    assert project.project_root_path == str((tmp_path / "PROJECT_A").resolve())
    assert project.root_status is ProjectRootStatus.AVAILABLE


def test_migrate_creates_documented_tables_and_enables_required_pragmas(
    tmp_path: Path,
) -> None:
    """Protects the runtime schema and connection durability configuration."""
    db_path = tmp_path / "product_intelligence.db"

    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert {
        "schema_migrations",
        "projects",
        "source_records",
        "baselines",
        "knowledge_cards",
        "relations",
        "issue_cards",
        "decisions",
        "change_requests",
        "model_call_logs",
        "cache_entries",
        "event_logs",
    } <= table_names
    assert journal_mode == "wal"


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    """Protects repeatable bootstrap and application startup."""
    db_path = tmp_path / "product_intelligence.db"

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert versions == [("1.0",), ("1.1",), ("1.2",), ("2.1",), ("2.2",)]


def test_migrate_creates_event_level_with_safe_info_default(tmp_path: Path) -> None:
    """Protects fresh audit rows from missing or accepting an invalid level."""
    db_path = tmp_path / "product_intelligence.db"

    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO event_logs (
                id, project_id, event_type, entity_type, entity_id,
                actor, correlation_id, payload_json, created_at
            ) VALUES (
                'EVENT-DEFAULT', 'LLD', 'legacy_event', 'source', 'SRC-001',
                'system', 'CORR-DEFAULT', '{}', '2026-07-29T00:00:00+00:00'
            )
            """
        )
        level = connection.execute(
            "SELECT level FROM event_logs WHERE id = 'EVENT-DEFAULT'"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO event_logs (
                    id, project_id, event_type, entity_type, entity_id,
                    actor, correlation_id, payload_json, created_at, level
                ) VALUES (
                    'EVENT-INVALID', 'LLD', 'legacy_event', 'source', 'SRC-001',
                    'system', 'CORR-INVALID', '{}', '2026-07-29T00:00:00+00:00', 'WARN'
                )
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO event_logs (
                    id, project_id, event_type, entity_type, entity_id,
                    actor, correlation_id, payload_json, created_at
                ) VALUES (
                    'EVENT-NULL-CORR', 'LLD', 'legacy_event', 'source', 'SRC-001',
                    'system', NULL, '{}', '2026-07-29T00:00:00+00:00'
                )
                """
            )
    assert level == "INFO"


def test_migrate_adds_audit_columns_to_existing_database_without_losing_rows(
    tmp_path: Path,
) -> None:
    """Protects upgrades from the T03 schema while retaining legacy audit rows."""
    db_path = tmp_path / "product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (version TEXT PRIMARY KEY);
            INSERT INTO schema_migrations(version) VALUES ('1.0');
            CREATE TABLE model_call_logs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL, task_type TEXT NOT NULL,
                source_ids_json TEXT NOT NULL, baseline_version TEXT NOT NULL,
                model_label TEXT NOT NULL, prompt_version TEXT NOT NULL,
                schema_version TEXT NOT NULL, authorized INTEGER NOT NULL,
                redacted INTEGER NOT NULL, outbound_chars INTEGER NOT NULL,
                outbound_coverage REAL NOT NULL, result_mode TEXT NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, elapsed_ms INTEGER, error_code TEXT
            );
            CREATE TABLE event_logs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL, event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                actor TEXT NOT NULL, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO model_call_logs VALUES (
                'CALL-LEGACY', 'LLD', 'query', '[]', 'LLD-724_1',
                'legacy', 'v1', '1.0', 1, 1, 0, 0, 'realtime',
                'succeeded', '2026-07-29T00:00:00+00:00',
                '2026-07-29T00:00:00+00:00', 0, NULL
            );
            INSERT INTO event_logs VALUES (
                'EVENT-LEGACY', 'LLD', 'legacy_event', 'source',
                'SRC-001', 'system', '{}', '2026-07-29T00:00:00+00:00'
            );
            """
        )

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        model_columns = {row[1] for row in connection.execute("PRAGMA table_info(model_call_logs)")}
        event_columns = {row[1] for row in connection.execute("PRAGMA table_info(event_logs)")}
        model_row = connection.execute(
            "SELECT id, correlation_id, workflow_run_id FROM model_call_logs"
        ).fetchone()
        event_row = connection.execute(
            "SELECT id, correlation_id, level FROM event_logs"
        ).fetchone()
    assert {"correlation_id", "workflow_run_id"} <= model_columns
    assert {"correlation_id", "level"} <= event_columns
    assert model_row == ("CALL-LEGACY", None, None)
    assert event_row == ("EVENT-LEGACY", "LEGACY-EVENT-LEGACY", "INFO")


def test_migrate_repairs_null_and_blank_event_correlations_despite_1_2_marker(
    tmp_path: Path,
) -> None:
    """Catches version-gated migration leaving invalid legacy audit data behind."""
    db_path = tmp_path / "product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (version TEXT PRIMARY KEY);
            INSERT INTO schema_migrations(version) VALUES ('1.0'), ('1.1'), ('1.2');
            CREATE TABLE event_logs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL, event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                actor TEXT NOT NULL, correlation_id TEXT,
                level TEXT NOT NULL DEFAULT 'INFO',
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            INSERT INTO event_logs VALUES (
                'EVENT-NULL', 'LLD', 'legacy_event', 'source', 'SRC-NULL',
                'system', NULL, 'INFO', '{}', '2026-07-29T00:00:00+00:00'
            );
            INSERT INTO event_logs VALUES (
                'EVENT-BLANK', 'LLD', 'legacy_event', 'source', 'SRC-BLANK',
                'system', '   ', 'INFO', '{}', '2026-07-29T00:00:01+00:00'
            );
            INSERT INTO event_logs VALUES (
                'EVENT-VALID', 'LLD', 'legacy_event', 'source', 'SRC-VALID',
                'system', 'CORR-VALID', 'INFO', '{}', '2026-07-29T00:00:02+00:00'
            );
            """
        )

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id, correlation_id FROM event_logs ORDER BY id"
        ).fetchall()
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert rows == [
        ("EVENT-BLANK", "LEGACY-EVENT-BLANK"),
        ("EVENT-NULL", "LEGACY-EVENT-NULL"),
        ("EVENT-VALID", "CORR-VALID"),
    ]
    assert versions == [("1.0",), ("1.1",), ("1.2",), ("2.1",), ("2.2",)]


def test_migrate_upgrades_legacy_issue_table_before_creating_fingerprint_index(
    tmp_path: Path,
) -> None:
    """Catches an index referencing a column that has not yet been added to an old DB."""
    db_path = tmp_path / "product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE issue_cards (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
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
                owner TEXT,
                due_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO issue_cards VALUES (
                'ISSUE-LEGACY', 'LLD', 'conflict', 'blocking', 'open',
                '历史冲突', '保留的历史问题', '[]', '["产品"]', '[]',
                NULL, NULL, NULL, NULL, NULL,
                '2026-07-29T00:00:00+00:00', '2026-07-29T00:00:00+00:00'
            );
            """
        )

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(issue_cards)")}
        row = connection.execute(
            "SELECT id, title, description FROM issue_cards WHERE id = 'ISSUE-LEGACY'"
        ).fetchone()
        index_columns = [
            item[2]
            for item in connection.execute(
                "PRAGMA index_info(idx_issue_project_fingerprint)"
            ).fetchall()
        ]
        legacy_index = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_issue_fingerprint",),
        ).fetchone()

    assert {
        "validation_note",
        "fingerprint",
        "target_rule_id",
        "raw_severity",
        "deterministic_rule_id",
    } <= columns
    assert row == ("ISSUE-LEGACY", "历史冲突", "保留的历史问题")
    assert index_columns == ["project_id", "fingerprint"]
    assert legacy_index is None


def test_migrate_adds_2_1_material_columns_and_default_draft_generation_mode(
    tmp_path: Path,
) -> None:
    """Catches a repeatable upgrade leaving legacy records unreadable by 2.1 services."""
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        source_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_records)")}
        draft_columns = {row[1] for row in connection.execute("PRAGMA table_info(document_drafts)")}
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert {"material_name", "material_series_id", "previous_source_id"} <= source_columns
    assert "generation_mode" in draft_columns
    assert versions == [("1.0",), ("1.1",), ("1.2",), ("2.1",), ("2.2",)]


def test_migrate_upgrades_pre_2_1_source_table_before_creating_series_indexes(
    tmp_path: Path,
) -> None:
    """Catches migration creating a 2.1 index before its legacy table has the needed columns."""
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE source_records (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                authority_level TEXT NOT NULL,
                source_department TEXT NOT NULL,
                provider TEXT,
                document_date TEXT NOT NULL,
                document_version TEXT NOT NULL,
                applicable_baseline_version TEXT NOT NULL,
                security_level TEXT NOT NULL,
                is_redacted INTEGER NOT NULL,
                allow_external_model INTEGER NOT NULL,
                is_sandbox INTEGER NOT NULL,
                ingest_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        index = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_source_series_version",),
        ).fetchone()
    assert index == ("idx_source_series_version",)
