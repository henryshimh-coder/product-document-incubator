from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteRelationRepository

NOW = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO projects (id, name, product_line, stage, current_baseline_id,"
            " allow_external_model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("LLD", "产品智策", "线", "demo", "BASE-1", 1, NOW.isoformat(), NOW.isoformat()),
        )
    return db_path


def _rel(
    db_path: Path,
    rel_id: str,
    source_id: str,
    target_id: str,
    *,
    relation_type: str = "supports",
    project_id: str = "LLD",
    created_at: datetime = NOW,
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO relations (id, project_id, source_id, relation_type, target_id,"
            " source_ref, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (rel_id, project_id, source_id, relation_type, target_id, created_at.isoformat()),
        )


def test_load_connected_returns_full_chain_in_stable_order(tmp_path):
    db_path = _db(tmp_path)
    later = datetime(2026, 8, 3, 7, 1, tzinfo=UTC)
    _rel(db_path, "REL-2", "B", "C", created_at=later)
    _rel(db_path, "REL-1", "A", "B")

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "A")

    assert [relation.id for relation in loaded] == ["REL-1", "REL-2"]


def test_load_connected_respects_max_depth(tmp_path):
    db_path = _db(tmp_path)
    for index in range(8):
        _rel(
            db_path,
            f"REL-{index}",
            f"N{index}",
            f"N{index + 1}",
            created_at=datetime(2026, 8, 3, 7, index, tzinfo=UTC),
        )

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "N0", max_depth=3)

    assert [relation.id for relation in loaded] == ["REL-0", "REL-1", "REL-2"]


def test_load_connected_depth_defaults_to_six_and_caps(tmp_path):
    db_path = _db(tmp_path)
    for index in range(9):
        _rel(
            db_path,
            f"REL-{index}",
            f"N{index}",
            f"N{index + 1}",
            created_at=datetime(2026, 8, 3, 7, index, tzinfo=UTC),
        )

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "N0")

    assert len(loaded) == 6
    assert loaded[-1].id == "REL-5"


def test_load_connected_terminates_on_cycles_without_duplicates(tmp_path):
    db_path = _db(tmp_path)
    _rel(db_path, "REL-A", "A", "B")
    _rel(db_path, "REL-B", "B", "C")
    _rel(db_path, "REL-C", "C", "A")

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "A")

    assert sorted(relation.id for relation in loaded) == ["REL-A", "REL-B", "REL-C"]


def test_load_connected_isolates_other_projects(tmp_path):
    db_path = _db(tmp_path)
    _rel(db_path, "REL-MINE", "A", "B")
    _rel(db_path, "REL-FOREIGN", "A", "X", project_id="OTHER")
    _rel(db_path, "REL-FOREIGN-2", "X", "Y", project_id="OTHER")

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "A")

    assert [relation.id for relation in loaded] == ["REL-MINE"]


def test_load_connected_excludes_unrelated_edges(tmp_path):
    db_path = _db(tmp_path)
    _rel(db_path, "REL-NEAR", "A", "B")
    _rel(db_path, "REL-FAR", "P", "Q")

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "A")

    assert [relation.id for relation in loaded] == ["REL-NEAR"]


def test_load_connected_unknown_anchor_returns_empty(tmp_path):
    db_path = _db(tmp_path)
    _rel(db_path, "REL-1", "A", "B")

    assert SqliteRelationRepository(db_path).load_connected("LLD", "MISSING") == []


def test_load_connected_ties_break_by_id(tmp_path):
    db_path = _db(tmp_path)
    _rel(db_path, "REL-Z", "A", "Z")
    _rel(db_path, "REL-A", "A", "Y")

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "A")

    assert [relation.id for relation in loaded] == ["REL-A", "REL-Z"]


@pytest.mark.parametrize("depth", [0, -1])
def test_load_connected_zero_or_negative_depth_returns_empty(tmp_path, depth):
    db_path = _db(tmp_path)
    _rel(db_path, "REL-1", "A", "B")

    assert SqliteRelationRepository(db_path).load_connected("LLD", "A", max_depth=depth) == []
