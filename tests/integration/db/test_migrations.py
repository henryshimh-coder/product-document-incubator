from __future__ import annotations

import sqlite3
from pathlib import Path

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
    assert versions == [("1.0",)]
