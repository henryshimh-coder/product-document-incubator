from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.enums import IssueSeverity, IssueStatus
from src.domain.errors import DomainError
from src.domain.models import IssueCard, Relation
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteLintUnitOfWork,
    SqliteRelationRepository,
)

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


def _issue(issue_id: str) -> IssueCard:
    return IssueCard(
        id=issue_id,
        project_id="LLD",
        issue_type="conflict",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title=f"问题 {issue_id}",
        description="演示问题。",
        evidence=[],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="需要补充信息",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _relation(rel_id: str, source_id: str, target_id: str) -> Relation:
    return Relation(
        id=rel_id,
        project_id="LLD",
        source_id=source_id,
        relation_type="conflicts_with",
        target_id=target_id,
        source_ref=None,
        created_at=NOW,
    )


def test_lint_uow_writes_issues_and_relations_atomically(tmp_path):
    db_path = _db(tmp_path)
    uow = SqliteLintUnitOfWork(db_path)

    uow.apply(
        issues=[_issue("ISSUE-1")],
        relations=[_relation("REL-1", "RULE-001", "ISSUE-1")],
    )

    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "RULE-001")
    assert [relation.id for relation in loaded] == ["REL-1"]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 1


def test_lint_uow_repeat_apply_is_idempotent(tmp_path):
    db_path = _db(tmp_path)
    uow = SqliteLintUnitOfWork(db_path)
    issue = _issue("ISSUE-1")
    relation = _relation("REL-1", "RULE-001", "ISSUE-1")

    uow.apply(issues=[issue], relations=[relation])
    uow.apply(issues=[issue], relations=[relation])

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1


def test_lint_uow_conflicting_relation_fails_and_rolls_back_issue(tmp_path):
    db_path = _db(tmp_path)
    uow = SqliteLintUnitOfWork(db_path)
    uow.apply(issues=[_issue("ISSUE-1")], relations=[_relation("REL-1", "RULE-001", "ISSUE-1")])

    divergent = _relation("REL-1", "RULE-001", "ISSUE-2")
    with pytest.raises(DomainError, match="RELATION_CONFLICT:REL-1"):
        uow.apply(issues=[_issue("ISSUE-2")], relations=[divergent])

    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM issue_cards WHERE id = 'ISSUE-2'").fetchone()[
                0
            ]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1
