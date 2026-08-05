"""Real publish-then-query flow: current, historical and tamper boundaries (T10-2)."""

from __future__ import annotations

import sqlite3

import pytest

from src.application.dto.query import RunQueryInput
from src.application.dto.release import PublishBaselineInput
from src.application.use_cases.publish_baseline import PublishBaseline
from src.application.use_cases.run_query import RunQuery
from src.domain.enums import ChangeStatus
from src.domain.errors import DomainError
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteReleaseUnitOfWork,
    SqliteSourceRepository,
)
from src.infrastructure.files.baseline_card_reader import LocalBaselineCardReader
from src.infrastructure.files.manifest_integrity import ManifestIntegrityChecker
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader
from src.infrastructure.recovery.reconciliation_service import ReconciliationService
from src.infrastructure.recovery.release_guard import ReleaseGuard
from tests.integration.release_env import (
    AFTER_CONTENT,
    BEFORE_CONTENT,
    CURRENT_VERSION,
    NOW,
    PROJECT_ID,
    RELEASE_NOTE,
    REVIEWER,
    TARGET_BASELINE_ID,
    TARGET_VERSION,
    build_release_environment,
    make_change,
)


class StubQueryGateway:
    """Echo the trusted local cards like the real workflow contract would."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_inputs = None

    def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
        self.calls += 1
        self.last_inputs = inputs
        cards = inputs["effective_cards"]
        return {
            "workflow_run_id": "WF-QUERY-IT",
            "result": {
                "answer": cards[0]["content"],
                "effective_rules": [card["id"] for card in cards],
                "citations": inputs["citations"],
                "candidate_notice": None,
                "conflict_notice": None,
                "baseline_version": inputs["baseline_version"],
                "evidence_sufficiency": "sufficient",
                "result_mode": "realtime",
                "model_call_id": "CALL-QUERY-IT",
            },
        }


def _publish_use_case(env) -> PublishBaseline:
    return PublishBaseline(
        manifest_store=env.manifest_store,
        markdown_store=env.markdown_store,
        changes=env.changes,
        baselines=env.baselines,
        sources=env.sources,
        issues=SqliteIssueRepository(env.db_path),
        integrity=ManifestIntegrityChecker(
            project_root=env.project_root,
            db_path=env.db_path,
            manifest_path=env.manifest_path,
        ),
        release_uow=SqliteReleaseUnitOfWork(env.db_path, event_logger=env.event_logger),
        reconciliation=ReconciliationService(
            manifest_store=env.manifest_store,
            db_path=env.db_path,
            project_root=env.project_root,
        ),
        guard=ReleaseGuard(),
        lock_path=env.project_root / "data/local_state/locks" / f"{PROJECT_ID}.release.lock",
        now=lambda: NOW,
    )


def _query_use_case(env, gateway: StubQueryGateway) -> RunQuery:
    return RunQuery(
        manifest=env.manifest_store,
        baselines=SqliteBaselineRepository(env.db_path),
        projects=SqliteProjectRepository(env.db_path),
        knowledge=SqliteKnowledgeRepository(env.db_path),
        sources=SqliteSourceRepository(env.db_path),
        baseline_cards=LocalBaselineCardReader(env.project_root),
        material_reader=LocalQueryMaterialReader(env.project_root),
        gateway=gateway,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        task_id_factory=lambda: "TASK-QUERY-IT",
    )


def _query(question: str, *, scope="effective", historical_version=None) -> RunQueryInput:
    return RunQueryInput(
        project_id=PROJECT_ID,
        question=question,
        scope=scope,
        historical_version=historical_version,
    )


def _publish_command() -> PublishBaselineInput:
    return PublishBaselineInput(
        project_id=PROJECT_ID,
        change_request_id="CHANGE-001",
        approved_by=REVIEWER,
        impact_reviewed=True,
        release_note=RELEASE_NOTE,
    )


def _env(tmp_path):
    return build_release_environment(tmp_path, change=make_change(ChangeStatus.APPROVED))


def test_publish_then_current_and_historical_queries_stay_version_consistent(tmp_path) -> None:
    """Catches post-publish queries reading stale SQLite cards or foreign sources."""
    env = _env(tmp_path)
    gateway = StubQueryGateway()
    query = _query_use_case(env, gateway)

    before = query.execute(_query("当前目标客群是什么？"))
    assert before.baseline_version == CURRENT_VERSION
    assert before.answer == BEFORE_CONTENT
    assert {citation.source_id for citation in before.citations} == {"SRC-BASE"}

    baseline = _publish_use_case(env).execute(_publish_command())
    assert baseline.version == TARGET_VERSION

    after = query.execute(_query("收紧后的目标客群是什么？"))
    assert after.baseline_version == TARGET_VERSION
    assert after.answer == AFTER_CONTENT
    assert set(after.effective_rules) == {"RULE-001", "API-CUSTOMER"}
    citations_by_rule = {
        card["id"]: card["source_citations"] for card in gateway.last_inputs["effective_cards"]
    }
    citation_index = {citation.id: citation for citation in after.citations}
    rule_citation = citation_index[sorted(citations_by_rule["RULE-001"])[0]]
    assert rule_citation.source_id == TARGET_BASELINE_ID
    assert rule_citation.document_version == TARGET_VERSION
    api_citation = citation_index[sorted(citations_by_rule["API-CUSTOMER"])[0]]
    assert api_citation.source_id == "SRC-BASE"

    history = query.execute(
        _query("历史目标客群是什么？", scope="historical", historical_version=CURRENT_VERSION)
    )
    assert history.baseline_version == CURRENT_VERSION
    assert history.answer == BEFORE_CONTENT
    assert {citation.source_id for citation in history.citations} == {"SRC-BASE"}

    again = query.execute(_query("收紧后的目标客群是什么？"))
    assert again.baseline_version == TARGET_VERSION
    assert again.answer == AFTER_CONTENT
    assert gateway.calls == 4


def test_tampered_historical_snapshot_fails_closed_without_model_call(tmp_path) -> None:
    """Catches a forged historical cards.json reaching the model boundary."""
    env = _env(tmp_path)
    gateway = StubQueryGateway()
    query = _query_use_case(env, gateway)
    _publish_use_case(env).execute(_publish_command())
    historical_cards = (
        env.project_root
        / "data/obsidian_vault/02_Current_Baseline"
        / CURRENT_VERSION
        / "cards.json"
    )
    historical_cards.write_text(
        historical_cards.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )

    with pytest.raises(DomainError, match="BASELINE_INTEGRITY_FAILED"):
        query.execute(
            _query("历史目标客群是什么？", scope="historical", historical_version=CURRENT_VERSION)
        )

    assert gateway.calls == 0


def test_tampered_current_snapshot_fails_closed_without_model_call(tmp_path) -> None:
    """Catches a forged current cards.json reaching the model boundary."""
    env = _env(tmp_path)
    gateway = StubQueryGateway()
    query = _query_use_case(env, gateway)
    _publish_use_case(env).execute(_publish_command())
    current_cards = (
        env.project_root / "data/obsidian_vault/02_Current_Baseline" / TARGET_VERSION / "cards.json"
    )
    current_cards.write_text(
        current_cards.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )

    with pytest.raises(DomainError, match="BASELINE_INTEGRITY_FAILED"):
        query.execute(_query("收紧后的目标客群是什么？"))

    assert gateway.calls == 0


def test_historical_query_never_inherits_future_child_sources(tmp_path) -> None:
    """Catches a future version's source authorizing a historical answer."""
    env = _env(tmp_path)
    gateway = StubQueryGateway()
    query = _query_use_case(env, gateway)
    _publish_use_case(env).execute(_publish_command())
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE source_records SET applicable_baseline_version = ? WHERE id = ?",
            (TARGET_VERSION, "SRC-BASE"),
        )

    current = query.execute(_query("收紧后的目标客群是什么？"))
    assert current.baseline_version == TARGET_VERSION

    with pytest.raises(DomainError, match="EXTERNAL_CALL_DENIED"):
        query.execute(
            _query("历史目标客群是什么？", scope="historical", historical_version=CURRENT_VERSION)
        )

    assert gateway.calls == 1
