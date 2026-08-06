from __future__ import annotations

import json
import sqlite3
from unittest.mock import Mock
from uuid import uuid4

import pytest
from filelock import FileLock
from pydantic import ValidationError

from src.application.dto.release import PublishBaselineInput
from src.domain.enums import BaselineStatus, ChangeStatus
from src.domain.errors import DomainError, ErrorCode
from src.infrastructure.files.manifest_store import ManifestDurabilityUncertainError
from tests.integration.release_env import (
    BASE_RULE_CHUNK_ID,
    BEFORE_CONTENT,
    CURRENT_BASELINE_ID,
    CURRENT_VERSION,
    NOW,
    PROJECT_ID,
    RELEASE_NOTE,
    REVIEWER,
    RISK_CHUNK_ID,
    RISK_LOCATOR,
    RULE_CARD_REF,
    TARGET_BASELINE_ID,
    TARGET_VERSION,
    build_release_environment,
    make_change,
)


def _use_case(env, **overrides):
    from src.application.use_cases.publish_baseline import PublishBaseline
    from src.infrastructure.db.repositories import (
        SqliteIssueRepository,
        SqliteReleaseUnitOfWork,
    )
    from src.infrastructure.files.manifest_integrity import ManifestIntegrityChecker
    from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader
    from src.infrastructure.recovery.reconciliation_service import ReconciliationService
    from src.infrastructure.recovery.release_guard import ReleaseGuard

    kwargs = {
        "manifest_store": env.manifest_store,
        "markdown_store": env.markdown_store,
        "changes": env.changes,
        "baselines": env.baselines,
        "sources": env.sources,
        "issues": SqliteIssueRepository(env.db_path),
        "integrity": ManifestIntegrityChecker(
            project_root=env.project_root,
            db_path=env.db_path,
            manifest_path=env.manifest_path,
        ),
        "material_reader": LocalQueryMaterialReader(env.project_root),
        "release_uow": SqliteReleaseUnitOfWork(env.db_path, event_logger=env.event_logger),
        "reconciliation": ReconciliationService(
            manifest_store=env.manifest_store,
            db_path=env.db_path,
            project_root=env.project_root,
        ),
        "guard": ReleaseGuard(),
        "lock_path": env.project_root / "data/local_state/locks" / f"{PROJECT_ID}.release.lock",
        "now": lambda: NOW,
        "event_id_factory": lambda: f"EVENT-{uuid4().hex.upper()}",
    }
    kwargs.update(overrides)
    return PublishBaseline(**kwargs)


def _approved_env(tmp_path):
    return build_release_environment(tmp_path, change=make_change(ChangeStatus.APPROVED))


def _command(**updates) -> PublishBaselineInput:
    base = {
        "project_id": PROJECT_ID,
        "change_request_id": "CHANGE-001",
        "approved_by": REVIEWER,
        "impact_reviewed": True,
        "release_note": RELEASE_NOTE,
    }
    base.update(updates)
    return PublishBaselineInput(**base)


def test_publish_success_replaces_manifest_and_mirrors_atomically(tmp_path) -> None:
    """Catches the happy path leaving either the old manifest or an inconsistent mirror."""
    env = _approved_env(tmp_path)
    before_manifest = env.manifest_store.read_and_validate()
    use_case = _use_case(env)

    baseline = use_case.execute(_command())

    assert baseline.id == TARGET_BASELINE_ID
    assert baseline.status == BaselineStatus.EFFECTIVE
    assert baseline.parent_baseline_id == CURRENT_BASELINE_ID
    assert baseline.version == TARGET_VERSION
    assert baseline.approved_by == REVIEWER
    manifest = env.manifest_store.read_and_validate()
    assert baseline.full_document_sha256 == manifest.full_document_sha256
    assert baseline.card_snapshot_sha256 == manifest.card_snapshot_sha256
    parent = env.baselines.get(CURRENT_BASELINE_ID)
    assert parent.status == BaselineStatus.SUPERSEDED
    assert parent.full_document_sha256 == before_manifest.full_document_sha256
    assert parent.card_snapshot_sha256 == before_manifest.card_snapshot_sha256
    assert manifest.current_version == TARGET_VERSION
    assert manifest.parent_baseline_id == CURRENT_BASELINE_ID
    assert manifest.change_request_id == "CHANGE-001"
    version_dir = env.project_root / "data/obsidian_vault/02_Current_Baseline" / TARGET_VERSION
    assert (version_dir / "full.md").is_file()
    assert (version_dir / "cards.json").is_file()
    assert (version_dir / "diff.md").is_file()
    release_record = json.loads((version_dir / "release.json").read_text(encoding="utf-8"))
    assert release_record["parent_version"] == CURRENT_VERSION
    assert release_record["target_version"] == TARGET_VERSION
    assert release_record["change_request_id"] == "CHANGE-001"
    assert release_record["approved_by"] == REVIEWER
    assert release_record["release_note"] == RELEASE_NOTE
    assert release_record["card_count"] == 2
    full_text = (version_dir / "full.md").read_text(encoding="utf-8")
    assert "收紧后的目标客群仅覆盖高净值存量客户。" in full_text
    assert "当前目标客群是符合准入要求的存量客户。" not in full_text
    assert full_text.count(f"当前版本：{TARGET_VERSION}") == 1
    assert f"当前版本：{CURRENT_VERSION}" not in full_text
    cards = {card["id"]: card for card in json.loads((version_dir / "cards.json").read_text())}
    assert cards["RULE-001"]["product_version"] == TARGET_VERSION
    assert cards["RULE-001"]["content"] == "收紧后的目标客群仅覆盖高净值存量客户。"
    assert cards["API-CUSTOMER"]["product_version"] == TARGET_VERSION
    assert cards["API-CUSTOMER"]["content"] == "客群接口规则。"
    assert env.changes.get("CHANGE-001").status == ChangeStatus.PUBLISHED
    assert env.projects.get(PROJECT_ID).current_baseline_id == TARGET_BASELINE_ID
    with sqlite3.connect(env.db_path) as connection:
        events = connection.execute(
            "SELECT event_type, entity_id FROM event_logs WHERE event_type = 'baseline_published'"
        ).fetchall()
        mirrored = connection.execute(
            """
            SELECT id, product_version, content FROM knowledge_cards
            WHERE project_id = ? AND status = 'effective' ORDER BY id
            """,
            (PROJECT_ID,),
        ).fetchall()
        relations = connection.execute(
            "SELECT source_id, relation_type, target_id FROM relations ORDER BY id"
        ).fetchall()
    assert events == [("baseline_published", TARGET_BASELINE_ID)]
    assert [(row[0], row[1], row[2]) for row in mirrored] == [
        ("API-CUSTOMER", TARGET_VERSION, "客群接口规则。"),
        ("RULE-001", TARGET_VERSION, "收紧后的目标客群仅覆盖高净值存量客户。"),
    ]
    assert relations == [
        (TARGET_BASELINE_ID, "supersedes", CURRENT_BASELINE_ID),
        ("CHANGE-001", "approved_as", TARGET_BASELINE_ID),
    ]


def test_publish_backfills_asset_hashes_for_pre_upgrade_parent_row(tmp_path) -> None:
    """Catches a superseded pre-upgrade baseline staying unverifiable for history queries."""
    env = _approved_env(tmp_path)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            """
            UPDATE baselines
            SET full_document_sha256 = NULL, card_snapshot_sha256 = NULL
            WHERE id = ?
            """,
            (CURRENT_BASELINE_ID,),
        )
    before_manifest = env.manifest_store.read_and_validate()

    baseline = _use_case(env).execute(_command())

    parent = env.baselines.get(CURRENT_BASELINE_ID)
    assert parent.status == BaselineStatus.SUPERSEDED
    assert parent.full_document_sha256 == before_manifest.full_document_sha256
    assert parent.card_snapshot_sha256 == before_manifest.card_snapshot_sha256
    assert baseline.full_document_sha256 is not None
    assert baseline.card_snapshot_sha256 is not None


@pytest.mark.parametrize(
    "status",
    [
        ChangeStatus.PENDING_APPROVAL,
        ChangeStatus.REJECTED,
        ChangeStatus.DEFERRED,
        ChangeStatus.NEEDS_INFO,
    ],
)
def test_publish_rejects_unapproved_change(tmp_path, status) -> None:
    """Catches publishing a change that has no human approval."""
    env = build_release_environment(tmp_path, change=make_change(status))
    use_case = _use_case(env)

    with pytest.raises(DomainError, match="CHANGE_NOT_APPROVED"):
        use_case.execute(_command())


def test_publish_requires_impact_review(tmp_path) -> None:
    """Catches publishing without the human impact check."""
    env = _approved_env(tmp_path)
    use_case = _use_case(env)

    with pytest.raises(DomainError, match="IMPACT_REVIEW_REQUIRED"):
        use_case.execute(_command(impact_reviewed=False))


def test_publish_rejects_invalid_release_note() -> None:
    """Catches an out-of-range release note passing the input contract."""
    with pytest.raises(ValidationError):
        _command(release_note="太短")
    with pytest.raises(ValidationError):
        _command(release_note="长" * 201)


def test_publish_rejects_when_manifest_integrity_fails(tmp_path) -> None:
    """Catches publishing on top of a corrupted current baseline asset."""
    env = _approved_env(tmp_path)
    full_path = env.project_root / ("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md")
    full_path.write_text(full_path.read_text(encoding="utf-8") + "篡改", encoding="utf-8")
    use_case = _use_case(env)

    with pytest.raises(DomainError, match="BASELINE_INTEGRITY_FAILED"):
        use_case.execute(_command())


def test_publish_rejects_when_target_version_dir_exists(tmp_path) -> None:
    """Catches overwriting an already present target version directory."""
    env = _approved_env(tmp_path)
    env.markdown_store.write_baseline(TARGET_VERSION, "# 旧版本\n", [])
    use_case = _use_case(env)

    with pytest.raises(DomainError, match="TARGET_VERSION_ALREADY_EXISTS"):
        use_case.execute(_command())


def test_publish_rejects_when_change_missing(tmp_path) -> None:
    """Catches publishing a change request that does not exist."""
    env = _approved_env(tmp_path)
    use_case = _use_case(env)

    with pytest.raises(DomainError, match="RELEASE_CHANGE_MISMATCH"):
        use_case.execute(_command(change_request_id="CHANGE-MISSING"))


def test_publish_rejects_approver_mismatch(tmp_path) -> None:
    """Catches an approver identity that does not come from the human review record."""
    env = _approved_env(tmp_path)
    use_case = _use_case(env)

    with pytest.raises(DomainError) as raised:
        use_case.execute(_command(approved_by="其他操作员"))

    assert raised.value.code == ErrorCode.RELEASE_CHANGE_MISMATCH.value
    assert raised.value.detail == "APPROVER_MISMATCH"


def test_full_document_generation_failure_keeps_old_manifest(tmp_path) -> None:
    """Catches a file staging failure leaving the current manifest untouched."""
    env = _approved_env(tmp_path)
    before = env.manifest_store.read_and_validate()
    use_case = _use_case(env)
    use_case.markdown_store = Mock(wraps=env.markdown_store)
    use_case.markdown_store.build_release_full_document = Mock(side_effect=OSError("disk full"))

    with pytest.raises(OSError):
        use_case.execute(_command())

    assert env.manifest_store.read_and_validate() == before
    release_root = env.project_root / "data/obsidian_vault/02_Current_Baseline"
    assert [item.name for item in release_root.iterdir()] == [CURRENT_VERSION]


def test_candidate_validation_failure_keeps_old_manifest(tmp_path) -> None:
    """Catches a candidate manifest mismatch leaving the current manifest untouched."""
    env = _approved_env(tmp_path)
    before = env.manifest_store.read_and_validate()
    use_case = _use_case(env)
    use_case.manifest_store = Mock(wraps=env.manifest_store)
    use_case.manifest_store.validate_candidate = Mock(
        side_effect=DomainError(ErrorCode.RELEASE_FAILED, "CANDIDATE_FULL_HASH_MISMATCH")
    )

    with pytest.raises(DomainError, match="RELEASE_FAILED"):
        use_case.execute(_command())

    assert env.manifest_store.read_and_validate() == before
    release_root = env.project_root / "data/obsidian_vault/02_Current_Baseline"
    assert [item.name for item in release_root.iterdir()] == [CURRENT_VERSION]


def test_commit_failure_keeps_old_manifest(tmp_path) -> None:
    """Catches a final directory commit failure leaving the current manifest untouched."""
    env = _approved_env(tmp_path)
    before = env.manifest_store.read_and_validate()
    use_case = _use_case(env)
    use_case.markdown_store = Mock(wraps=env.markdown_store)
    use_case.markdown_store.commit_release_dir = Mock(side_effect=OSError("disk full"))

    with pytest.raises(OSError):
        use_case.execute(_command())

    assert env.manifest_store.read_and_validate() == before
    release_root = env.project_root / "data/obsidian_vault/02_Current_Baseline"
    assert [item.name for item in release_root.iterdir()] == [CURRENT_VERSION]


def test_manifest_replace_failure_quarantines_unreferenced_dir(tmp_path) -> None:
    """Catches a confirmed manifest replacement failure silently dropping audit evidence."""
    env = _approved_env(tmp_path)
    before = env.manifest_store.read_and_validate()
    use_case = _use_case(env)
    use_case.manifest_store = Mock(wraps=env.manifest_store)
    use_case.manifest_store.atomic_replace = Mock(side_effect=OSError("rename failed"))

    with pytest.raises(OSError):
        use_case.execute(_command())

    assert env.manifest_store.read_and_validate() == before
    release_root = env.project_root / "data/obsidian_vault/02_Current_Baseline"
    assert [item.name for item in release_root.iterdir()] == [CURRENT_VERSION]
    quarantine = env.project_root / "data/obsidian_vault/99_Quarantine"
    quarantined = list(quarantine.iterdir())
    assert len(quarantined) == 1
    assert quarantined[0].name.startswith(f"{TARGET_VERSION}-quarantined-")
    assert (quarantined[0] / "release.json").is_file()
    assert env.changes.get("CHANGE-001").status == ChangeStatus.APPROVED


def test_manifest_durability_uncertain_with_old_manifest_quarantines(tmp_path) -> None:
    """Catches guessing the manifest state from the durability exception type."""
    env = _approved_env(tmp_path)
    before = env.manifest_store.read_and_validate()
    use_case = _use_case(env)
    original = env.manifest_store.atomic_replace

    def uncertain_without_replace(manifest):
        raise ManifestDurabilityUncertainError("fsync unconfirmed")

    use_case.manifest_store = Mock(wraps=env.manifest_store)
    use_case.manifest_store.atomic_replace = Mock(side_effect=uncertain_without_replace)

    with pytest.raises(DomainError) as raised:
        use_case.execute(_command())

    assert raised.value.code == ErrorCode.RELEASE_FAILED.value
    assert raised.value.detail == "MANIFEST_REPLACE_UNCERTAIN"
    assert env.manifest_store.read_and_validate() == before
    release_root = env.project_root / "data/obsidian_vault/02_Current_Baseline"
    assert [item.name for item in release_root.iterdir()] == [CURRENT_VERSION]
    assert original is not None


def test_manifest_durability_uncertain_with_new_manifest_continues(tmp_path) -> None:
    """Catches treating a confirmed replacement as a failure after the uncertain signal."""
    env = _approved_env(tmp_path)
    use_case = _use_case(env)
    real_replace = env.manifest_store.atomic_replace

    def replace_then_uncertain(manifest):
        real_replace(manifest)
        raise ManifestDurabilityUncertainError("fsync unconfirmed")

    use_case.manifest_store = Mock(wraps=env.manifest_store)
    use_case.manifest_store.atomic_replace = Mock(side_effect=replace_then_uncertain)

    baseline = use_case.execute(_command())

    assert baseline.version == TARGET_VERSION
    assert env.manifest_store.read_and_validate().current_version == TARGET_VERSION
    assert env.projects.get(PROJECT_ID).current_baseline_id == TARGET_BASELINE_ID


def test_release_lock_conflict_returns_stable_error(tmp_path) -> None:
    """Catches a concurrent publish creating staging directories or partial state."""
    env = _approved_env(tmp_path)
    lock_path = env.project_root / "data/local_state/locks" / f"{PROJECT_ID}.release.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = FileLock(str(lock_path))
    held.acquire(timeout=0)
    try:
        use_case = _use_case(env)
        with pytest.raises(DomainError, match="RELEASE_LOCKED"):
            use_case.execute(_command())
    finally:
        held.release()
    release_root = env.project_root / "data/obsidian_vault/02_Current_Baseline"
    assert [item.name for item in release_root.iterdir()] == [CURRENT_VERSION]


def test_duplicate_publish_is_blocked_after_success(tmp_path) -> None:
    """Catches a repeated publish click creating a duplicate version or approval."""
    env = _approved_env(tmp_path)
    use_case = _use_case(env)
    use_case.execute(_command())

    with pytest.raises(DomainError, match="CHANGE_NOT_APPROVED"):
        use_case.execute(_command())

    assert env.manifest_store.read_and_validate().current_version == TARGET_VERSION


def test_ambiguous_full_document_target_fails_closed(tmp_path) -> None:
    """Catches a blind global replace when the before-content is not unique."""
    env = _approved_env(tmp_path)
    full_path = env.project_root / ("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md")
    full_path.write_text(
        full_path.read_text(encoding="utf-8") + "\n当前目标客群是符合准入要求的存量客户。\n",
        encoding="utf-8",
    )
    # Re-sign the manifest so asset hashes pass, and stub the SQLite mirror check
    # so the test isolates the ambiguous before-content guard.
    env.manifest_store.atomic_replace(
        env.manifest_store.read_and_validate().model_copy(
            update={
                "full_document_sha256": env.markdown_store.sha256_for(
                    "data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"
                )
            }
        )
    )
    use_case = _use_case(env, integrity=Mock(validate=Mock(return_value=True)))
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        use_case.execute(_command())

    assert raised.value.code == ErrorCode.RELEASE_FAILED.value
    assert raised.value.detail.startswith("FULL_DOCUMENT_TARGET_NOT_UNIQUE")
    assert env.manifest_store.read_and_validate() == before


def test_sqlite_mirror_failure_repaired_returns_success(tmp_path) -> None:
    """Catches a mirror failure being repaired from the effective manifest."""
    env = _approved_env(tmp_path)
    from src.infrastructure.recovery.reconciliation_service import ReconciliationService

    reconciliation = ReconciliationService(
        manifest_store=env.manifest_store,
        db_path=env.db_path,
        project_root=env.project_root,
    )
    use_case = _use_case(env, reconciliation=reconciliation)
    original_publish = use_case.release_uow.publish
    calls = {"count": 0}

    def fail_once(**kwargs):
        calls["count"] += 1
        raise sqlite3.OperationalError("locked")

    use_case.release_uow = Mock(wraps=use_case.release_uow)
    use_case.release_uow.publish = Mock(side_effect=fail_once)
    reconciliation.rebuild_current_from_manifest = Mock(
        wraps=reconciliation.rebuild_current_from_manifest
    )

    baseline = use_case.execute(_command())

    assert baseline.status == BaselineStatus.EFFECTIVE
    assert calls["count"] == 1
    reconciliation.rebuild_current_from_manifest.assert_called_once()
    assert env.projects.get(PROJECT_ID).current_baseline_id == TARGET_BASELINE_ID
    assert env.changes.get("CHANGE-001").status == ChangeStatus.PUBLISHED
    assert original_publish is not None


def test_sqlite_mirror_failure_unrepaired_blocks_and_reports(tmp_path) -> None:
    """Catches an unrepaired mirror mismatch allowing further publishes."""
    env = _approved_env(tmp_path)
    from src.domain.models import RepairResult
    from src.infrastructure.recovery.reconciliation_service import ReconciliationService
    from src.infrastructure.recovery.release_guard import ReleaseGuard

    guard = ReleaseGuard()
    reconciliation = ReconciliationService(
        manifest_store=env.manifest_store,
        db_path=env.db_path,
        project_root=env.project_root,
    )
    reconciliation.rebuild_current_from_manifest = Mock(
        return_value=RepairResult(
            success=False, repaired_entities=[], error_code="REPAIR_WRITE_FAILED"
        )
    )
    use_case = _use_case(env, reconciliation=reconciliation, guard=guard)
    use_case.release_uow = Mock(wraps=use_case.release_uow)
    use_case.release_uow.publish = Mock(side_effect=sqlite3.OperationalError("locked"))

    with pytest.raises(DomainError, match="RELEASE_MIRROR_REPAIR_REQUIRED"):
        use_case.execute(_command())

    assert env.manifest_store.read_and_validate().current_version == TARGET_VERSION
    assert guard.is_blocked
    with pytest.raises(DomainError, match="RELEASE_BLOCKED"):
        use_case.execute(_command())


def test_publish_failure_keeps_change_approved_for_retry(tmp_path) -> None:
    """Catches a retry creating a second approval record after a file failure."""
    env = _approved_env(tmp_path)
    use_case = _use_case(env)
    use_case.markdown_store = Mock(wraps=env.markdown_store)
    use_case.markdown_store.commit_release_dir = Mock(side_effect=OSError("disk full"))

    with pytest.raises(OSError):
        use_case.execute(_command())

    change = env.changes.get("CHANGE-001")
    assert change.status == ChangeStatus.APPROVED
    assert change.review_idempotency_key == "REVIEW-KEY-001"


def _assert_release_tree_and_state_unchanged(env, before) -> None:
    assert env.changes.get("CHANGE-001").status == ChangeStatus.APPROVED
    assert env.manifest_store.read_and_validate() == before
    assert env.projects.get(PROJECT_ID).current_baseline_id == CURRENT_BASELINE_ID
    assert env.baselines.get(CURRENT_BASELINE_ID).status == BaselineStatus.EFFECTIVE
    release_root = env.project_root / "data/obsidian_vault/02_Current_Baseline"
    assert [item.name for item in release_root.iterdir()] == [CURRENT_VERSION]


def _tamper_source_archive(env, source_id: str) -> bytes:
    """Append bytes to a source archive without touching its SourceRecord."""
    source = env.sources.get(source_id)
    archive_path = env.project_root / source.archive_path
    original = archive_path.read_bytes()
    archive_path.write_bytes(original + "\n被篡改的附加内容。\n".encode())
    return original


def _restore_source_archive(env, source_id: str, payload: bytes) -> None:
    source = env.sources.get(source_id)
    (env.project_root / source.archive_path).write_bytes(payload)


def test_publish_fails_when_card_source_archive_is_tampered(tmp_path) -> None:
    """V3-A06: 篡改基线卡来源归档后发布在创建临时发布目录前失败。"""
    env = _approved_env(tmp_path)
    _tamper_source_archive(env, "SRC-BASE")
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_SOURCE_INTEGRITY_FAILED.value
    assert raised.value.detail == "PUBLISH_SOURCE_INTEGRITY_FAILED:SRC-BASE"
    assert str(env.project_root) not in str(raised.value)
    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_fails_when_change_evidence_source_archive_is_tampered(tmp_path) -> None:
    """V3-A07: 篡改变更证据来源归档后发布失败，批准状态保留。"""
    env = _approved_env(tmp_path)
    _tamper_source_archive(env, "SRC-RISK")
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_SOURCE_INTEGRITY_FAILED.value
    assert raised.value.detail == "PUBLISH_SOURCE_INTEGRITY_FAILED:SRC-RISK"
    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_fails_when_card_citation_chunk_does_not_exist(tmp_path) -> None:
    """V3-A08: citation/chunk 不存在时发布失败，不回退到其他片段。"""
    env = _approved_env(tmp_path)
    _rewrite_rule_source_refs(env, ["SRC-BASE:SRC-BASE-9999"])
    use_case = _use_case(env, integrity=Mock(validate=Mock(return_value=True)))
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        use_case.execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_CITATION_UNVERIFIABLE.value
    assert raised.value.detail == "PUBLISH_CITATION_UNVERIFIABLE:RULE-001"
    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_fails_when_archive_path_escapes_controlled_root(tmp_path) -> None:
    """V3-A09: archive 路径越界时发布失败，不读取越界文件。"""
    env = _approved_env(tmp_path)
    escaped = env.project_root / "data/source_archive/LLD/SRC-RISK/当前产品方案.md"
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE source_records SET archive_path = ? WHERE id = 'SRC-BASE'",
            (str(escaped),),
        )
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_SOURCE_INTEGRITY_FAILED.value
    assert raised.value.detail == "PUBLISH_SOURCE_INTEGRITY_FAILED:SRC-BASE"
    assert str(env.project_root) not in str(raised.value)
    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_retry_succeeds_after_restoring_source_without_reapproval(tmp_path) -> None:
    """V3-A10: 恢复合法来源后可直接重试发布，不重复人工审批。"""
    env = _approved_env(tmp_path)
    original = _tamper_source_archive(env, "SRC-BASE")
    use_case = _use_case(env)

    with pytest.raises(DomainError) as raised:
        use_case.execute(_command())
    assert raised.value.code == ErrorCode.PUBLISH_SOURCE_INTEGRITY_FAILED.value
    change = env.changes.get("CHANGE-001")
    assert change.status == ChangeStatus.APPROVED
    assert change.review_idempotency_key == "REVIEW-KEY-001"

    _restore_source_archive(env, "SRC-BASE", original)
    baseline = use_case.execute(_command())

    assert baseline.version == TARGET_VERSION
    assert env.changes.get("CHANGE-001").status == ChangeStatus.PUBLISHED
    assert env.manifest_store.read_and_validate().current_version == TARGET_VERSION


def test_publish_allows_bare_source_id_alongside_locatable_citation(tmp_path) -> None:
    """V3-A11: 裸 source ID 只作补充关联，合法 citation 满足正式证据门槛。"""
    env = _approved_env(tmp_path)
    _rewrite_rule_source_refs(env, ["SRC-BASE", RULE_CARD_REF])
    use_case = _use_case(env, integrity=Mock(validate=Mock(return_value=True)))

    baseline = use_case.execute(_command())

    assert baseline.version == TARGET_VERSION
    assert env.manifest_store.read_and_validate().current_version == TARGET_VERSION


def test_publish_fails_when_card_has_only_bare_source_ids(tmp_path) -> None:
    """V3-A12: 只有裸 source ID 的 effective 卡不满足正式证据门槛。"""
    env = _approved_env(tmp_path)
    _rewrite_rule_source_refs(env, ["SRC-BASE"])
    use_case = _use_case(env, integrity=Mock(validate=Mock(return_value=True)))
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        use_case.execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_CITATION_UNVERIFIABLE.value
    assert raised.value.detail == "PUBLISH_CARD_CITATION_REQUIRED:RULE-001"
    _assert_release_tree_and_state_unchanged(env, before)


def _baseline_rule_fragment_locator(env) -> str:
    from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader

    manifest = env.manifest_store.read_and_validate()
    material = LocalQueryMaterialReader(env.project_root).read_baseline(
        project_id=PROJECT_ID,
        asset_id=manifest.current_baseline_id,
        version=manifest.current_version,
        relative_path=manifest.full_document_path,
        expected_sha256=manifest.full_document_sha256,
    )
    return next(item for item in material.fragments if BEFORE_CONTENT in item.text).locator


def _append_issue_evidence(env, evidence: dict) -> None:
    from src.infrastructure.db.repositories import SqliteIssueRepository

    issue = SqliteIssueRepository(env.db_path).get("ISSUE-001")
    payload = [item.model_dump(mode="json") for item in issue.evidence]
    payload.append(evidence)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE issue_cards SET evidence_json = ? WHERE id = 'ISSUE-001'",
            (json.dumps(payload, ensure_ascii=False),),
        )


def _add_change_evidence_ref(env, citation_id: str) -> None:
    change = env.changes.get("CHANGE-001")
    refs = [*change.evidence_refs, citation_id]
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE change_requests SET evidence_refs_json = ? WHERE id = 'CHANGE-001'",
            (json.dumps(refs, ensure_ascii=False),),
        )


def _update_issue_evidence(env, match_id: str, **updates) -> None:
    from src.infrastructure.db.repositories import SqliteIssueRepository

    issue = SqliteIssueRepository(env.db_path).get("ISSUE-001")
    payload = []
    for item in issue.evidence:
        data = item.model_dump(mode="json")
        if item.citation_id == match_id:
            data.update(updates)
        payload.append(data)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE issue_cards SET evidence_json = ? WHERE id = 'ISSUE-001'",
            (json.dumps(payload, ensure_ascii=False),),
        )


def _replace_change_evidence_ref(env, old: str, new: str) -> None:
    change = env.changes.get("CHANGE-001")
    refs = [new if item == old else item for item in change.evidence_refs]
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE change_requests SET evidence_refs_json = ? WHERE id = 'CHANGE-001'",
            (json.dumps(refs, ensure_ascii=False),),
        )


def test_publish_accepts_baseline_side_evidence_with_locatable_position(tmp_path) -> None:
    """V4-A07: 当前基线侧证据 citation/版本/locator/excerpt 全部正确时正常发布。"""
    env = _approved_env(tmp_path)
    citation_id = "CIT-BASE-001"
    _append_issue_evidence(
        env,
        {
            "source_id": CURRENT_BASELINE_ID,
            "citation_id": citation_id,
            "excerpt": BEFORE_CONTENT,
            "document_version": CURRENT_VERSION,
            "page_or_section": _baseline_rule_fragment_locator(env),
            "side": "current_baseline",
        },
    )
    _add_change_evidence_ref(env, citation_id)

    baseline = _use_case(env).execute(_command())

    assert baseline.version == TARGET_VERSION


def test_publish_fails_when_baseline_side_evidence_position_is_unlocatable(tmp_path) -> None:
    """V4-A06: 基线侧证据 citation/版本正确但 locator 伪造时发布失败，可恢复重试。"""
    env = _approved_env(tmp_path)
    citation_id = "CIT-BASE-001"
    legal_locator = _baseline_rule_fragment_locator(env)
    _append_issue_evidence(
        env,
        {
            "source_id": CURRENT_BASELINE_ID,
            "citation_id": citation_id,
            "excerpt": BEFORE_CONTENT,
            "document_version": CURRENT_VERSION,
            "page_or_section": "heading:不存在的章节; line:99",
            "side": "current_baseline",
        },
    )
    _add_change_evidence_ref(env, citation_id)
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_CITATION_UNVERIFIABLE.value
    assert raised.value.detail == f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{citation_id}"
    _assert_release_tree_and_state_unchanged(env, before)

    _update_issue_evidence(env, citation_id, page_or_section=legal_locator)
    baseline = _use_case(env).execute(_command())
    assert baseline.version == TARGET_VERSION


def test_publish_fails_when_formal_evidence_version_is_forged(tmp_path) -> None:
    """V4-A01: 正式来源 citation/excerpt 正确但 document_version 伪造时发布失败。"""
    env = _approved_env(tmp_path)
    before = env.manifest_store.read_and_validate()
    _update_issue_evidence(env, RISK_CHUNK_ID, document_version="FORGED-V999")

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_CITATION_UNVERIFIABLE.value
    assert raised.value.detail == f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{RISK_CHUNK_ID}"
    _assert_release_tree_and_state_unchanged(env, before)

    _update_issue_evidence(env, RISK_CHUNK_ID, document_version="v1.0")
    baseline = _use_case(env).execute(_command())
    assert baseline.version == TARGET_VERSION


def test_publish_fails_when_formal_evidence_locator_is_forged(tmp_path) -> None:
    """V4-A02: 正式来源 citation/excerpt 正确但 page_or_section 伪造时发布失败。"""
    env = _approved_env(tmp_path)
    before = env.manifest_store.read_and_validate()
    _update_issue_evidence(env, RISK_CHUNK_ID, page_or_section="heading:伪造章节; line:999")

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_CITATION_UNVERIFIABLE.value
    assert raised.value.detail == f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{RISK_CHUNK_ID}"
    _assert_release_tree_and_state_unchanged(env, before)

    _update_issue_evidence(env, RISK_CHUNK_ID, page_or_section=RISK_LOCATOR)
    baseline = _use_case(env).execute(_command())
    assert baseline.version == TARGET_VERSION


def test_publish_accepts_formal_evidence_with_full_metadata(tmp_path) -> None:
    """V4-A03: 正式来源 citation/版本/locator/excerpt 四项全部正确时正常发布。"""
    env = _approved_env(tmp_path)
    from src.infrastructure.db.repositories import SqliteIssueRepository

    issue = SqliteIssueRepository(env.db_path).get("ISSUE-001")
    risk = next(item for item in issue.evidence if item.citation_id == RISK_CHUNK_ID)
    assert risk.document_version == "v1.0"
    assert risk.page_or_section == RISK_LOCATOR
    assert risk.excerpt == "风险意见要求收紧客群。"

    baseline = _use_case(env).execute(_command())

    assert baseline.version == TARGET_VERSION


def test_publish_fails_when_baseline_evidence_version_is_forged(tmp_path) -> None:
    """V4-A04: 当前基线 locator/excerpt 正确但 document_version 伪造时发布失败。"""
    env = _approved_env(tmp_path)
    citation_id = "CIT-BASE-001"
    _append_issue_evidence(
        env,
        {
            "source_id": CURRENT_BASELINE_ID,
            "citation_id": citation_id,
            "excerpt": BEFORE_CONTENT,
            "document_version": "FORGED-V999",
            "page_or_section": _baseline_rule_fragment_locator(env),
            "side": "current_baseline",
        },
    )
    _add_change_evidence_ref(env, citation_id)
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_CITATION_UNVERIFIABLE.value
    assert raised.value.detail == f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{citation_id}"
    _assert_release_tree_and_state_unchanged(env, before)

    _update_issue_evidence(env, citation_id, document_version=CURRENT_VERSION)
    baseline = _use_case(env).execute(_command())
    assert baseline.version == TARGET_VERSION


def test_publish_fails_when_baseline_evidence_citation_is_forged(tmp_path) -> None:
    """V4-A05: 当前基线版本/locator/excerpt 正确但 citation_id 伪造时发布失败。"""
    env = _approved_env(tmp_path)
    forged_id = "CIT-BASE-099"
    _append_issue_evidence(
        env,
        {
            "source_id": CURRENT_BASELINE_ID,
            "citation_id": forged_id,
            "excerpt": BEFORE_CONTENT,
            "document_version": CURRENT_VERSION,
            "page_or_section": _baseline_rule_fragment_locator(env),
            "side": "current_baseline",
        },
    )
    _add_change_evidence_ref(env, forged_id)
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError) as raised:
        _use_case(env).execute(_command())

    assert raised.value.code == ErrorCode.PUBLISH_CITATION_UNVERIFIABLE.value
    assert raised.value.detail == f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{forged_id}"
    _assert_release_tree_and_state_unchanged(env, before)

    _update_issue_evidence(env, forged_id, citation_id="CIT-BASE-001")
    _replace_change_evidence_ref(env, forged_id, "CIT-BASE-001")
    baseline = _use_case(env).execute(_command())
    assert baseline.version == TARGET_VERSION


def test_publish_rejects_sandbox_evidence_source(tmp_path) -> None:
    """Catches sandbox material entering the formal baseline through issue evidence."""
    env = _approved_env(tmp_path)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute("UPDATE source_records SET is_sandbox = 1 WHERE id = 'SRC-RISK'")
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError, match="SANDBOX_SOURCE_NOT_ALLOWED"):
        _use_case(env).execute(_command())

    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_rejects_missing_card_source_and_keeps_old_release_tree(tmp_path) -> None:
    """Catches a dangling card source reference entering the release staging area."""
    env = _approved_env(tmp_path)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute("DELETE FROM source_records WHERE id = 'SRC-BASE'")
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError, match="CITATION_INVALID") as raised:
        _use_case(env).execute(_command())

    assert raised.value.detail == "PUBLISH_SOURCE_MISSING:SRC-BASE"
    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_rejects_unimported_card_source(tmp_path) -> None:
    """Catches a still-processing source backing a formal baseline card."""
    env = _approved_env(tmp_path)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE source_records SET ingest_status = 'processing' WHERE id = 'SRC-BASE'"
        )
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError, match="CITATION_INVALID") as raised:
        _use_case(env).execute(_command())

    assert raised.value.detail == "PUBLISH_SOURCE_NOT_IMPORTED:SRC-BASE"
    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_rejects_cross_project_evidence_source(tmp_path) -> None:
    """Catches another project's source backing a formal release."""
    env = _approved_env(tmp_path)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute("UPDATE source_records SET project_id = 'OTHER' WHERE id = 'SRC-RISK'")
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError, match="CITATION_INVALID") as raised:
        _use_case(env).execute(_command())

    assert raised.value.detail == "PUBLISH_SOURCE_PROJECT_MISMATCH:SRC-RISK"
    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_rejects_non_formal_evidence_authority(tmp_path) -> None:
    """Catches professional-opinion material backing a formal baseline."""
    env = _approved_env(tmp_path)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            """
            UPDATE source_records
            SET authority_level = 'professional_opinion'
            WHERE id = 'SRC-RISK'
            """
        )
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError, match="SOURCE_AUTHORITY_NOT_FORMAL"):
        _use_case(env).execute(_command())

    _assert_release_tree_and_state_unchanged(env, before)


def test_publish_rejects_ambiguous_issue_evidence_citation(tmp_path) -> None:
    """Catches duplicate citation ids making evidence matching ambiguous."""
    from src.infrastructure.db.repositories import SqliteIssueRepository

    env = _approved_env(tmp_path)
    issue = SqliteIssueRepository(env.db_path).get("ISSUE-001")
    duplicated = [item.model_dump(mode="json") for item in issue.evidence]
    duplicated.append(duplicated[0])
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE issue_cards SET evidence_json = ? WHERE id = 'ISSUE-001'",
            (json.dumps(duplicated, ensure_ascii=False),),
        )
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError, match="CITATION_INVALID") as raised:
        _use_case(env).execute(_command())

    assert raised.value.detail == f"PUBLISH_EVIDENCE_AMBIGUOUS:{BASE_RULE_CHUNK_ID}"
    _assert_release_tree_and_state_unchanged(env, before)


@pytest.mark.parametrize(
    "refs",
    [":CIT-BASE-001", "SRC-BASE:", "SRC-BASE:CIT:EXTRA"],
    ids=["empty-source-id", "empty-citation", "extra-separator"],
)
def test_publish_rejects_invalid_card_source_refs(tmp_path, refs) -> None:
    """Catches malformed source references slipping through publish validation."""
    env = _approved_env(tmp_path)
    _rewrite_rule_source_refs(env, [refs])
    use_case = _use_case(env, integrity=Mock(validate=Mock(return_value=True)))
    before = env.manifest_store.read_and_validate()

    with pytest.raises(DomainError, match="CITATION_INVALID") as raised:
        use_case.execute(_command())

    assert raised.value.detail == "PUBLISH_SOURCE_REF_INVALID:RULE-001"
    _assert_release_tree_and_state_unchanged(env, before)


@pytest.mark.parametrize("refs", [[], ["   "]], ids=["empty-refs", "whitespace-ref"])
def test_publish_rejects_model_invalid_card_source_refs_at_snapshot_boundary(
    tmp_path,
    refs,
) -> None:
    """Catches blank source references surviving the snapshot model boundary."""
    env = _approved_env(tmp_path)
    _rewrite_rule_source_refs(env, refs)
    use_case = _use_case(env, integrity=Mock(validate=Mock(return_value=True)))
    before = env.manifest_store.read_and_validate()

    with pytest.raises(ValidationError):
        use_case.execute(_command())

    _assert_release_tree_and_state_unchanged(env, before)


def _rewrite_rule_source_refs(env, refs: list[str]) -> None:
    cards_path = env.project_root / ("data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json")
    payload = json.loads(cards_path.read_text(encoding="utf-8"))
    for card in payload:
        if card["id"] == "RULE-001":
            card["source_refs"] = refs
    cards_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    env.manifest_store.atomic_replace(
        env.manifest_store.read_and_validate().model_copy(
            update={
                "card_snapshot_sha256": env.markdown_store.sha256_for(
                    "data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json"
                )
            }
        )
    )
