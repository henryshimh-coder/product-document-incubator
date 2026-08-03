from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.infrastructure.db.migrations import migrate


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
    assert versions == [("1.0",), ("1.1",), ("1.2",)]


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
    assert event_row == ("EVENT-LEGACY", None, "INFO")
