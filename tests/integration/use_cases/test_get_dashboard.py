from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from scripts.bootstrap_demo import bootstrap
from src.application.dto.dashboard import GetDashboardInput
from src.application.use_cases.get_dashboard import GetDashboard
from src.domain.enums import (
    AuthorityLevel,
    ChangeStatus,
    DecisionAction,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)
from src.domain.models import (
    Baseline,
    ChangeRequest,
    Decision,
    EventLog,
    IssueCard,
    KnowledgeCard,
    Project,
    SourceRecord,
)
from src.infrastructure.db.repositories import (
    SqliteChangeRepository,
    SqliteDecisionRepository,
    SqliteEventRepository,
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.manifest_integrity import ManifestIntegrityChecker
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.observability.event_logger import EventLogger

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _dashboard(root: Path) -> GetDashboard:
    db_path = root / "data/local_state/product_intelligence.db"
    manifest_path = root / "data/local_state/current_baseline.json"
    return GetDashboard(
        manifest=ManifestStore(manifest_path),
        integrity=ManifestIntegrityChecker(
            project_root=root,
            db_path=db_path,
            manifest_path=manifest_path,
        ),
        projects=SqliteProjectRepository(db_path),
        issues=SqliteIssueRepository(db_path),
        changes=SqliteChangeRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        events=SqliteEventRepository(db_path),
    )


def _add_project(db_path: Path, project_id: str) -> None:
    SqliteProjectRepository(db_path).add(
        Project(
            id=project_id,
            name=f"{project_id} 项目",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _source(project_id: str, index: int) -> SourceRecord:
    return SourceRecord(
        id=f"SRC-{project_id}-{index}",
        project_id=project_id,
        original_filename=f"资料-{index}.md",
        archive_path=f"data/source_archive/{project_id}/SRC-{index}/source.md",
        sha256=f"{index + 1:064x}",
        mime_type="text/markdown",
        size_bytes=42,
        source_type="formal_document",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
        is_redacted=True,
        allow_external_model=False,
        is_sandbox=False,
        ingest_status="completed",
        created_at=NOW + timedelta(minutes=index),
    )


def _issue(project_id: str, index: int) -> IssueCard:
    return IssueCard(
        id=f"ISSUE-{project_id}-{index}",
        project_id=project_id,
        issue_type="information_gap",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title=f"待补充信息 {index}",
        description="当前资料不足。",
        evidence=[],
        impacted_domains=["产品"],
        options=[{"action": "补充资料"}],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="需要补充资料",
        owner=None,
        due_at=None,
        created_at=NOW + timedelta(minutes=index),
        updated_at=NOW + timedelta(minutes=index),
    )


def _add_pending_change(db_path: Path, project_id: str, index: int) -> None:
    issue = _issue(project_id, index + 20)
    SqliteIssueRepository(db_path).add_many([issue])
    card = KnowledgeCard(
        id=f"CARD-{project_id}-{index}",
        project_id=project_id,
        card_type="rule",
        title="目标客群",
        content="当前规则。",
        status=KnowledgeStatus.EFFECTIVE,
        product_version="LLD-724_1",
        applicable_scope="演示",
        source_refs=["SRC-BASE"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品经理",
        created_at=NOW,
        updated_at=NOW,
    )
    SqliteKnowledgeRepository(db_path).upsert_cards([card])
    decision = Decision(
        id=f"DECISION-{project_id}-{index}",
        project_id=project_id,
        issue_id=issue.id,
        action=DecisionAction.KEEP_CURRENT,
        conclusion="当前先保持原方案并进入变更审批。",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        created_at=NOW,
    )
    SqliteDecisionRepository(db_path).add(
        decision,
        idempotency_key=f"decision-{project_id}-{index}",
    )
    SqliteChangeRepository(db_path).add(
        ChangeRequest(
            id=f"CHANGE-{project_id}-{index}",
            project_id=project_id,
            issue_id=issue.id,
            decision_id=decision.id,
            target_card_id=card.id,
            before_content="旧规则",
            after_content="新规则",
            rationale="依据会议结论更新。",
            evidence_refs=["SRC-BASE"],
            impacted_objects=[card.id],
            responsible_domain="产品",
            required_approver_role="产品经理",
            demo_confirmer="产品经理",
            status=ChangeStatus.PENDING_APPROVAL,
            review_action=None,
            reviewed_by=None,
            review_comment=None,
            review_idempotency_key=None,
            reviewed_at=None,
            target_version="LLD-724_2",
            effective_condition="审批通过后发布。",
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _record_events(db_path: Path, project_id: str, count: int, log_path: Path) -> None:
    logger = EventLogger(db_path)
    logger.log_path = log_path
    for index in range(count):
        logger.record(
            EventLog(
                id=f"EVENT-{project_id}-{index:02d}",
                project_id=project_id,
                event_type="source_imported",
                entity_type="source",
                entity_id=f"SRC-{project_id}-{index}",
                actor=f"操作人-{index}",
                correlation_id=f"CORR-{project_id}-{index}",
                payload={"description": f"导入资料 {index}"},
                created_at=NOW + timedelta(minutes=index),
            )
        )


def test_dashboard_manifest_baseline_wins_over_stale_project_mirror_and_limits_events(
    tmp_path: Path,
) -> None:
    """Catches selecting projects.current_baseline_id or returning more than five events."""
    bootstrap(tmp_path)
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE projects SET current_baseline_id = ? WHERE id = ?",
            ("BASE-STALE", "LLD"),
        )
    _record_events(db_path, "LLD", 7, tmp_path / "events.jsonl")

    view = _dashboard(tmp_path).execute(GetDashboardInput(project_id="LLD"))

    assert view.current_baseline is not None
    assert view.current_baseline.id == "BASE-LLD-724_1"
    assert view.current_baseline.version == "LLD-724_1"
    assert view.integrity_ok is False
    assert [event["id"] for event in view.recent_events] == [
        "EVENT-LLD-06",
        "EVENT-LLD-05",
        "EVENT-LLD-04",
        "EVENT-LLD-03",
        "EVENT-LLD-02",
    ]


def test_dashboard_counts_and_recent_events_are_scoped_to_requested_project(
    tmp_path: Path,
) -> None:
    """Catches unscoped aggregate queries leaking another project's counts or activity."""
    bootstrap(tmp_path)
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    _add_project(db_path, "OTHER")
    sources = SqliteSourceRepository(db_path)
    sources.add(_source("LLD", 0))
    sources.add(_source("LLD", 1))
    sources.add(_source("OTHER", 2))
    SqliteIssueRepository(db_path).add_many([_issue("LLD", 0), _issue("OTHER", 1)])
    _add_pending_change(db_path, "LLD", 0)
    _add_pending_change(db_path, "OTHER", 1)
    _record_events(db_path, "LLD", 2, tmp_path / "lld-events.jsonl")
    _record_events(db_path, "OTHER", 3, tmp_path / "other-events.jsonl")

    view = _dashboard(tmp_path).execute(GetDashboardInput(project_id="LLD"))

    assert view.open_issue_count == 2
    assert view.candidate_change_count == 1
    # 两个新增来源 + bootstrap 归档的基座材料（SRC-LLD-BASE）；OTHER 项目不得计入。
    assert view.source_count == 3
    assert [event["id"] for event in view.recent_events] == [
        "EVENT-LLD-01",
        "EVENT-LLD-00",
    ]
    assert view.integrity_ok is True


@pytest.mark.parametrize(
    "corruption",
    ["asset_hash", "project_mirror", "baseline_mirror", "baseline_missing"],
)
def test_integrity_failure_is_reported_without_repairing_authoritative_or_mirror_state(
    tmp_path: Path,
    corruption: str,
) -> None:
    """Catches an integrity check that misses corruption or silently repairs local state."""
    manifest = bootstrap(tmp_path)
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    manifest_path = tmp_path / "data/local_state/current_baseline.json"
    if corruption == "asset_hash":
        (tmp_path / manifest.full_document_path).write_text("tampered", encoding="utf-8")
    elif corruption == "project_mirror":
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE projects SET current_baseline_id = 'BASE-STALE' WHERE id = 'LLD'"
            )
    elif corruption == "baseline_mirror":
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE baselines SET version = 'STALE-VERSION' WHERE id = 'BASE-LLD-724_1'"
            )
    else:
        with sqlite3.connect(db_path) as connection:
            connection.execute("DELETE FROM baselines WHERE id = 'BASE-LLD-724_1'")
    manifest_before = manifest_path.read_bytes()
    with sqlite3.connect(db_path) as connection:
        mirror_before = tuple(
            connection.execute(
                "SELECT current_baseline_id FROM projects WHERE id = 'LLD'"
            ).fetchone()
        )
        baseline_row = connection.execute(
            "SELECT version, full_document_path, card_snapshot_path FROM baselines "
            "WHERE id = 'BASE-LLD-724_1'"
        ).fetchone()
        baseline_before = None if baseline_row is None else tuple(baseline_row)

    view = _dashboard(tmp_path).execute(GetDashboardInput(project_id="LLD"))

    assert view.integrity_ok is False
    assert view.current_baseline is not None
    assert isinstance(view.current_baseline, Baseline)
    assert view.current_baseline.id == manifest.current_baseline_id
    assert view.current_baseline.version == manifest.current_version
    assert view.current_baseline.manifest_sha256 == hashlib.sha256(manifest_before).hexdigest()
    assert view.current_baseline.created_at == manifest.published_at
    assert manifest_path.read_bytes() == manifest_before
    with sqlite3.connect(db_path) as connection:
        assert (
            tuple(
                connection.execute(
                    "SELECT current_baseline_id FROM projects WHERE id = 'LLD'"
                ).fetchone()
            )
            == mirror_before
        )
        baseline_row = connection.execute(
            "SELECT version, full_document_path, card_snapshot_path FROM baselines "
            "WHERE id = 'BASE-LLD-724_1'"
        ).fetchone()
        assert (None if baseline_row is None else tuple(baseline_row)) == baseline_before


def test_dashboard_rejects_project_mismatch_before_combining_any_project_data(
    tmp_path: Path,
) -> None:
    """Catches mixing one project's Manifest baseline with another project's aggregates."""
    bootstrap(tmp_path)
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    _add_project(db_path, "OTHER")
    SqliteSourceRepository(db_path).add(_source("OTHER", 4))

    with pytest.raises(ValueError, match="project"):
        _dashboard(tmp_path).execute(GetDashboardInput(project_id="OTHER"))


def test_container_composes_dashboard_from_valid_local_data_without_dify_keys(
    tmp_path: Path,
) -> None:
    """Catches the local home page becoming unavailable when Dify credentials are absent."""
    from src.application.container import build_container

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app_yaml = config_dir / "app.yaml"
    schema_yaml = config_dir / "schema.yaml"
    app_yaml.write_text(
        """
app:
  name: 产品智策
  project_id: LLD
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [pdf, docx, txt, md]
  demo_mode: true

timeouts:
  ingest_seconds: 60
  query_seconds: 30
  lint_seconds: 60
""".strip(),
        encoding="utf-8",
    )
    schema_yaml.write_text("schema_version: '1.0'\n", encoding="utf-8")
    bootstrap(tmp_path)

    container = build_container(
        app_yaml,
        schema_yaml,
        environ={"INCUBATOR_LIBRARY_ROOT": str(tmp_path)},
    )

    assert container.dashboard is not None
    assert container.import_source is None
    assert (
        container.dashboard.execute(GetDashboardInput(project_id="LLD")).current_baseline.version
        == "LLD-724_1"
    )
