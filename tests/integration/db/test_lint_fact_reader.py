from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.domain.enums import AuthorityLevel, CallResultMode, KnowledgeStatus
from src.domain.models import KnowledgeCard, ModelCallLog, Project
from src.infrastructure.db.connection import connect
from src.infrastructure.db.lint_fact_reader import SqliteLintFactReader
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
)
from src.infrastructure.observability.model_call_logger import ModelCallLogger

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _setup(tmp_path: Path) -> tuple[Path, SqliteLintFactReader]:
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="LLD",
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return db_path, SqliteLintFactReader(db_path)


def test_lint_fact_reader_excludes_unauthorized_security_preflight_without_outbound(
    tmp_path: Path,
) -> None:
    """Catches an outbound=0 security denial being treated as an actual model attempt."""
    db_path, reader = _setup(tmp_path)
    ModelCallLogger(db_path).record(
        ModelCallLog(
            id="CALL-UNAUTHORIZED",
            project_id="LLD",
            task_type="lint",
            workflow_run_id=None,
            correlation_id="CORR-UNAUTHORIZED",
            source_ids=["SRC-RISK"],
            baseline_version="LLD-724_1",
            model_label="dify",
            prompt_version="v1",
            schema_version="1.0",
            authorized=False,
            redacted=True,
            outbound_chars=0,
            outbound_coverage=0,
            result_mode=CallResultMode.REALTIME,
            status="failed",
            started_at=NOW,
            finished_at=NOW,
            elapsed_ms=0,
            error_code="EXTERNAL_CALL_DENIED",
        )
    )

    matched = reader.for_card(
        project_id="LLD",
        baseline_version="LLD-724_1",
        card_id="RULE-001",
        source_ids=("SRC-RISK",),
    )
    unrelated = reader.for_card(
        project_id="LLD",
        baseline_version="LLD-724_1",
        card_id="RULE-002",
        source_ids=("SRC-OTHER",),
    )

    assert matched.unauthorized_model_call is False
    assert unrelated.unauthorized_model_call is False


def test_lint_fact_reader_accepts_actual_unauthorized_external_attempt_by_json_membership(
    tmp_path: Path,
) -> None:
    """Catches positive outbound attempts being ignored or source IDs matched by substring."""
    db_path, reader = _setup(tmp_path)
    ModelCallLogger(db_path).record(
        ModelCallLog(
            id="CALL-ACTUAL-ATTEMPT",
            project_id="LLD",
            task_type="lint",
            workflow_run_id=None,
            correlation_id="CORR-ACTUAL-ATTEMPT",
            source_ids=["SRC-RISK-10"],
            baseline_version="LLD-724_1",
            model_label="dify",
            prompt_version="v1",
            schema_version="1.0",
            authorized=False,
            redacted=True,
            outbound_chars=128,
            outbound_coverage=0.1,
            result_mode=CallResultMode.REALTIME,
            status="failed",
            started_at=NOW,
            finished_at=NOW,
            elapsed_ms=5,
            error_code="DIFY_HTTP_ERROR",
        )
    )

    exact = reader.for_card(
        project_id="LLD",
        baseline_version="LLD-724_1",
        card_id="RULE-001",
        source_ids=("SRC-RISK-10",),
    )
    substring = reader.for_card(
        project_id="LLD",
        baseline_version="LLD-724_1",
        card_id="RULE-001",
        source_ids=("SRC-RISK-1",),
    )

    assert exact.unauthorized_model_call is True
    assert substring.unauthorized_model_call is False


def _knowledge_card(
    card_id: str,
    *,
    card_type: str,
    version: str = "LLD-724_1",
    status: KnowledgeStatus = KnowledgeStatus.EFFECTIVE,
) -> KnowledgeCard:
    return KnowledgeCard(
        id=card_id,
        project_id="LLD",
        card_type=card_type,
        title=card_id,
        content=f"{card_id} 的结构化内容。",
        status=status,
        product_version=version,
        applicable_scope="演示",
        source_refs=[f"SRC-BASE:{card_id}"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品",
        created_at=NOW,
        updated_at=NOW,
    )


def test_lint_fact_reader_rejects_dangling_change_and_recalculation_relations(
    tmp_path: Path,
) -> None:
    """Catches a relation row suppressing findings when its target object does not exist."""
    db_path, reader = _setup(tmp_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO relations (
                id, project_id, source_id, relation_type, target_id, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "REL-CHANGE",
                "LLD",
                "DECISION-CARD-001",
                "proposes_change_to",
                "CHANGE-001",
                None,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO relations (
                id, project_id, source_id, relation_type, target_id, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "REL-COST",
                "LLD",
                "COST-CARD-001",
                "recalculated_by",
                "COST-RESULT-001",
                None,
                NOW.isoformat(),
            ),
        )

    decision = reader.for_card(
        project_id="LLD",
        baseline_version="LLD-724_1",
        card_id="DECISION-CARD-001",
        source_ids=(),
    )
    cost = reader.for_card(
        project_id="LLD",
        baseline_version="LLD-724_1",
        card_id="COST-CARD-001",
        source_ids=(),
    )
    absent = reader.for_card(
        project_id="LLD",
        baseline_version="LLD-724_1",
        card_id="CARD-ABSENT",
        source_ids=(),
    )

    assert decision.change_mapping_exists is False
    assert cost.cost_recalculation_exists is False
    assert absent.change_mapping_exists is False
    assert absent.cost_recalculation_exists is False


def test_lint_fact_reader_requires_current_effective_typed_relation_targets(
    tmp_path: Path,
) -> None:
    """Catches old, candidate, or wrong-type targets being accepted as current facts."""
    db_path, reader = _setup(tmp_path)
    SqliteKnowledgeRepository(db_path).upsert_cards(
        [
            _knowledge_card("RULE-CURRENT", card_type="product_rule"),
            _knowledge_card(
                "RULE-CANDIDATE",
                card_type="product_rule",
                status=KnowledgeStatus.CANDIDATE,
            ),
            _knowledge_card(
                "COST-RESULT-OLD",
                card_type="cost_recalculation_result",
                version="LLD-700_1",
            ),
            _knowledge_card("COST-RESULT-WRONG", card_type="product_rule"),
            _knowledge_card(
                "COST-RESULT-CURRENT",
                card_type="cost_recalculation_result",
            ),
        ]
    )
    relations = [
        ("REL-GOV-VALID", "DECISION-VALID", "proposes_change_to", "RULE-CURRENT"),
        (
            "REL-GOV-CANDIDATE",
            "DECISION-CANDIDATE",
            "proposes_change_to",
            "RULE-CANDIDATE",
        ),
        ("REL-COST-OLD", "COST-OLD", "recalculated_by", "COST-RESULT-OLD"),
        (
            "REL-COST-WRONG",
            "COST-WRONG",
            "recalculated_by",
            "COST-RESULT-WRONG",
        ),
        (
            "REL-COST-VALID",
            "COST-VALID",
            "recalculated_by",
            "COST-RESULT-CURRENT",
        ),
    ]
    with connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO relations (
                id, project_id, source_id, relation_type, target_id, source_ref, created_at
            ) VALUES (?, 'LLD', ?, ?, ?, NULL, ?)
            """,
            [(*relation, NOW.isoformat()) for relation in relations],
        )

    facts = {
        card_id: reader.for_card(
            project_id="LLD",
            baseline_version="LLD-724_1",
            card_id=card_id,
            source_ids=(),
        )
        for card_id in (
            "DECISION-VALID",
            "DECISION-CANDIDATE",
            "COST-OLD",
            "COST-WRONG",
            "COST-VALID",
        )
    }

    assert facts["DECISION-VALID"].change_mapping_exists is True
    assert facts["DECISION-CANDIDATE"].change_mapping_exists is False
    assert facts["COST-OLD"].cost_recalculation_exists is False
    assert facts["COST-WRONG"].cost_recalculation_exists is False
    assert facts["COST-VALID"].cost_recalculation_exists is True
