from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.domain.enums import DecisionAction, IssueSeverity, IssueStatus
from src.domain.models import Decision, IssueCard, Relation
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteDecisionUnitOfWork,
    SqliteIssueRepository,
    SqliteRelationRepository,
)

NOW = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)


def _env(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO projects (id, name, product_line, stage, current_baseline_id,"
            " allow_external_model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("LLD", "产品智策", "线", "demo", "BASE-1", 1, NOW.isoformat(), NOW.isoformat()),
        )
    SqliteIssueRepository(db_path).add_many(
        [
            IssueCard(
                id="ISSUE-1",
                project_id="LLD",
                issue_type="conflict",
                severity=IssueSeverity.PENDING_INFO,
                status=IssueStatus.OPEN,
                title="客群规则待收紧",
                description="风险意见要求收紧客群。",
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
        ]
    )
    return db_path


def _decision(decision_id: str = "DECISION-1") -> Decision:
    return Decision(
        id=decision_id,
        project_id="LLD",
        issue_id="ISSUE-1",
        action=DecisionAction.KEEP_CURRENT,
        conclusion="会议确认维持现状。",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        created_at=NOW,
    )


def _resolved_by(decision_id: str = "DECISION-1") -> Relation:
    return Relation(
        id=f"REL-ISSUE-1-RESOLVED-BY-{decision_id}",
        project_id="LLD",
        source_id="ISSUE-1",
        relation_type="resolved_by",
        target_id=decision_id,
        source_ref=None,
        created_at=NOW,
    )


def test_decision_uow_persists_lifecycle_relations_in_same_transaction(tmp_path):
    db_path = _env(tmp_path)

    result = SqliteDecisionUnitOfWork(db_path).record(
        decision=_decision(),
        idempotency_key="KEY-1",
        command_fingerprint="FP-1",
        issue_status=IssueStatus.CLOSED,
        issue_updated_at=NOW,
        change_request=None,
        relations=[_resolved_by()],
    )

    assert result.decision.id == "DECISION-1"
    loaded = SqliteRelationRepository(db_path).load_connected("LLD", "ISSUE-1")
    assert [(r.relation_type, r.target_id) for r in loaded] == [("resolved_by", "DECISION-1")]


def test_decision_uow_idempotent_replay_does_not_duplicate_relations(tmp_path):
    db_path = _env(tmp_path)
    uow = SqliteDecisionUnitOfWork(db_path)
    kwargs = {
        "decision": _decision(),
        "idempotency_key": "KEY-1",
        "command_fingerprint": "FP-1",
        "issue_status": IssueStatus.CLOSED,
        "issue_updated_at": NOW,
        "change_request": None,
        "relations": [_resolved_by()],
    }

    uow.record(**kwargs)
    replayed = uow.record(**kwargs)

    assert replayed.decision.id == "DECISION-1"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
