"""Release UoW payload ownership, relation idempotency and conflict boundaries."""

from __future__ import annotations

import sqlite3

import pytest

from src.domain.enums import AuthorityLevel, BaselineStatus, ChangeStatus, KnowledgeStatus
from src.domain.errors import DomainError
from src.domain.models import Baseline, EventLog, KnowledgeCard, Relation
from src.infrastructure.db.repositories import SqliteReleaseUnitOfWork
from tests.integration.release_env import (
    CURRENT_BASELINE_ID,
    NOW,
    PROJECT_ID,
    TARGET_BASELINE_ID,
    TARGET_VERSION,
    build_release_environment,
    make_change,
)


def _new_baseline() -> Baseline:
    return Baseline(
        id=TARGET_BASELINE_ID,
        project_id=PROJECT_ID,
        version=TARGET_VERSION,
        parent_baseline_id=CURRENT_BASELINE_ID,
        status=BaselineStatus.EFFECTIVE,
        full_document_path=f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/full.md",
        card_snapshot_path=f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/cards.json",
        manifest_sha256="d" * 64,
        full_document_sha256="1" * 64,
        card_snapshot_sha256="2" * 64,
        change_request_id="CHANGE-001",
        approved_by="产品经理",
        effective_at=NOW,
        created_at=NOW,
    )


def _snapshot_card(**updates) -> KnowledgeCard:
    card = KnowledgeCard(
        id="RULE-001",
        project_id=PROJECT_ID,
        card_type="rule",
        title="目标客群",
        content="收紧后的目标客群仅覆盖高净值存量客户。",
        status=KnowledgeStatus.EFFECTIVE,
        product_version=TARGET_VERSION,
        applicable_scope="演示",
        source_refs=["SRC-BASE"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品",
        created_at=NOW,
        updated_at=NOW,
    )
    return card.model_copy(update=updates)


def _approved_relation() -> Relation:
    return Relation(
        id=f"REL-CHANGE-001-APPROVED-AS-{TARGET_BASELINE_ID}",
        project_id=PROJECT_ID,
        source_id="CHANGE-001",
        relation_type="approved_as",
        target_id=TARGET_BASELINE_ID,
        source_ref=None,
        created_at=NOW,
    )


def _event() -> EventLog:
    return EventLog(
        id="EVENT-UOW-001",
        project_id=PROJECT_ID,
        event_type="baseline_published",
        entity_type="baseline",
        entity_id=TARGET_BASELINE_ID,
        actor="产品经理",
        correlation_id="CHANGE-001",
        payload={"target_version": TARGET_VERSION},
        created_at=NOW,
    )


def _publish_kwargs(*, cards=None, relations=None) -> dict:
    return {
        "superseded_baseline_id": CURRENT_BASELINE_ID,
        "new_baseline": _new_baseline(),
        "change_id": "CHANGE-001",
        "change_updated_at": NOW,
        "project_id": PROJECT_ID,
        "event": _event(),
        "new_cards": cards if cards is not None else [_snapshot_card()],
        "relations": relations if relations is not None else [_approved_relation()],
        "parent_full_document_sha256": "a" * 64,
        "parent_card_snapshot_sha256": "b" * 64,
    }


def _approved_env(tmp_path):
    return build_release_environment(tmp_path, change=make_change(ChangeStatus.APPROVED))


def _change_status(env) -> str:
    with sqlite3.connect(env.db_path) as connection:
        return connection.execute(
            "SELECT status FROM change_requests WHERE id = 'CHANGE-001'"
        ).fetchone()[0]


def test_uow_mirrors_cards_relations_and_parent_hashes_atomically(tmp_path) -> None:
    """Catches the mirror transaction dropping cards, relations or parent hashes."""
    env = _approved_env(tmp_path)
    uow = SqliteReleaseUnitOfWork(env.db_path, event_logger=env.event_logger)

    uncertain = uow.publish(**_publish_kwargs())

    assert uncertain is False
    parent = env.baselines.get(CURRENT_BASELINE_ID)
    assert parent.status == BaselineStatus.SUPERSEDED
    assert parent.full_document_sha256 == "a" * 64
    assert parent.card_snapshot_sha256 == "b" * 64
    with sqlite3.connect(env.db_path) as connection:
        cards = connection.execute(
            """
            SELECT product_version FROM knowledge_cards
            WHERE project_id = ? AND status = 'effective'
            """,
            (PROJECT_ID,),
        ).fetchall()
        relations = connection.execute(
            "SELECT id FROM relations WHERE project_id = ?", (PROJECT_ID,)
        ).fetchall()
    assert {row[0] for row in cards} == {TARGET_VERSION}
    assert [row[0] for row in relations] == [_approved_relation().id]
    assert _change_status(env) == ChangeStatus.PUBLISHED.value


def test_uow_rejects_cards_outside_project_or_version_before_sql(tmp_path) -> None:
    """Catches a foreign or wrong-version card entering the mirror transaction."""
    env = _approved_env(tmp_path)
    uow = SqliteReleaseUnitOfWork(env.db_path, event_logger=env.event_logger)

    with pytest.raises(DomainError, match="RELEASE_MIRROR_CARD_MISMATCH:RULE-001"):
        uow.publish(**_publish_kwargs(cards=[_snapshot_card(project_id="OTHER")]))
    with pytest.raises(DomainError, match="RELEASE_MIRROR_CARD_MISMATCH:RULE-001"):
        uow.publish(
            **_publish_kwargs(cards=[_snapshot_card(product_version="LLD-999_9")]),
        )

    assert _change_status(env) == ChangeStatus.APPROVED.value
    assert env.baselines.get(CURRENT_BASELINE_ID).status == BaselineStatus.EFFECTIVE


def test_uow_rejects_relations_outside_the_publish_context(tmp_path) -> None:
    """Catches a relation endpoint unrelated to this publish entering the mirror."""
    env = _approved_env(tmp_path)
    relation = _approved_relation().model_copy(update={"target_id": "BASE-UNRELATED"})
    uow = SqliteReleaseUnitOfWork(env.db_path, event_logger=env.event_logger)

    with pytest.raises(DomainError, match="RELEASE_MIRROR_RELATION_MISMATCH"):
        uow.publish(**_publish_kwargs(relations=[relation]))

    assert _change_status(env) == ChangeStatus.APPROVED.value


def test_uow_skips_identical_existing_relation_for_retry(tmp_path) -> None:
    """Catches a stable relation retry being treated as a conflict."""
    env = _approved_env(tmp_path)
    relation = _approved_relation()
    with sqlite3.connect(env.db_path) as connection:
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
    uow = SqliteReleaseUnitOfWork(env.db_path, event_logger=env.event_logger)

    uncertain = uow.publish(**_publish_kwargs())

    assert uncertain is False
    with sqlite3.connect(env.db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM relations WHERE id = ?", (relation.id,)
        ).fetchone()[0]
    assert count == 1


def test_uow_fails_on_same_id_relation_with_different_facts(tmp_path) -> None:
    """Catches INSERT OR IGNORE silently swallowing a conflicting relation fact."""
    env = _approved_env(tmp_path)
    relation = _approved_relation()
    with sqlite3.connect(env.db_path) as connection:
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
                CURRENT_BASELINE_ID,
                relation.source_ref,
                relation.created_at.isoformat(),
            ),
        )
    uow = SqliteReleaseUnitOfWork(env.db_path, event_logger=env.event_logger)

    with pytest.raises(DomainError, match="RELEASE_MIRROR_RELATION_CONFLICT"):
        uow.publish(**_publish_kwargs())

    assert _change_status(env) == ChangeStatus.APPROVED.value
    assert env.baselines.get(CURRENT_BASELINE_ID).status == BaselineStatus.EFFECTIVE
