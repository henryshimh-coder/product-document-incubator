from __future__ import annotations

import sqlite3
from unittest.mock import Mock

from src.domain.enums import BaselineStatus, ChangeStatus
from tests.integration.release_env import (
    CURRENT_BASELINE_ID,
    NOW,
    PROJECT_ID,
    TARGET_BASELINE_ID,
    TARGET_VERSION,
    build_release_environment,
    make_change,
)


def _reconciliation(env):
    from src.infrastructure.recovery.reconciliation_service import ReconciliationService

    return ReconciliationService(
        manifest_store=env.manifest_store,
        db_path=env.db_path,
        project_root=env.project_root,
    )


def _publish_manifest(env, change) -> None:
    """Move the authoritative manifest to the target version like a real publish."""
    temp_dir = env.markdown_store.create_release_temp_dir()
    current = env.manifest_store.read_and_validate()
    env.markdown_store.build_release_full_document(current.full_document_path, change, temp_dir)
    env.markdown_store.build_release_cards(
        current.card_snapshot_path, change, temp_dir, updated_at=NOW
    )
    env.markdown_store.write_release_metadata(
        temp_dir,
        change=change,
        parent_version=current.current_version,
        approved_by="产品经理",
        published_at=NOW,
        release_note="完成客群规则调整，保留版本差异与追溯依据。",
    )
    env.markdown_store.commit_release_dir(temp_dir, TARGET_VERSION)
    import hashlib

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    candidate = env.manifest_store.build_candidate(
        current=current,
        change=change,
        approved_by="产品经理",
        published_at=NOW,
        full_document_path=(f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/full.md"),
        card_snapshot_path=(f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/cards.json"),
        full_document_sha256=sha(
            env.project_root / f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/full.md"
        ),
        card_snapshot_sha256=sha(
            env.project_root
            / f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/cards.json"
        ),
    )
    env.manifest_store.atomic_replace(candidate)


def test_validate_manifest_mirror_ok_for_consistent_environment(tmp_path) -> None:
    """Catches a healthy environment being reported as broken."""
    env = build_release_environment(tmp_path)

    result = _reconciliation(env).validate_manifest_mirror()

    assert result.success
    assert result.error_code is None


def test_rebuild_restores_mirror_from_effective_manifest(tmp_path) -> None:
    """Catches a failed publish mirror staying inconsistent after reconciliation."""
    change = make_change(ChangeStatus.APPROVED)
    env = build_release_environment(tmp_path, change=change)
    _publish_manifest(env, change)
    reconciliation = _reconciliation(env)

    validation = reconciliation.validate_manifest_mirror()
    assert not validation.success
    repair = reconciliation.rebuild_current_from_manifest()

    assert repair.success
    assert "baselines" in repair.repaired_entities
    assert "projects" in repair.repaired_entities
    assert "change_requests" in repair.repaired_entities
    assert reconciliation.validate_manifest_mirror().success
    baseline = env.baselines.get(TARGET_BASELINE_ID)
    assert baseline.status == BaselineStatus.EFFECTIVE
    assert baseline.parent_baseline_id == CURRENT_BASELINE_ID
    assert env.baselines.get(CURRENT_BASELINE_ID).status == BaselineStatus.SUPERSEDED
    assert env.projects.get(PROJECT_ID).current_baseline_id == TARGET_BASELINE_ID
    assert env.changes.get("CHANGE-001").status == ChangeStatus.PUBLISHED


def test_rebuild_is_idempotent(tmp_path) -> None:
    """Catches a repeated repair creating duplicates or changing the outcome."""
    change = make_change(ChangeStatus.APPROVED)
    env = build_release_environment(tmp_path, change=change)
    _publish_manifest(env, change)
    reconciliation = _reconciliation(env)

    first = reconciliation.rebuild_current_from_manifest()
    second = reconciliation.rebuild_current_from_manifest()

    assert first.success and second.success
    assert reconciliation.validate_manifest_mirror().success
    with sqlite3.connect(env.db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM baselines WHERE version = ?", (TARGET_VERSION,)
            ).fetchone()[0]
            == 1
        )


def test_invalid_manifest_never_overwrites_sqlite(tmp_path) -> None:
    """Catches a corrupt manifest triggering a reverse overwrite from SQLite."""
    env = build_release_environment(tmp_path)
    env.manifest_path.write_text("{ not json", encoding="utf-8")
    reconciliation = _reconciliation(env)

    validation = reconciliation.validate_manifest_mirror()
    repair = reconciliation.rebuild_current_from_manifest()

    assert not validation.success
    assert validation.error_code == "MANIFEST_INVALID"
    assert not repair.success
    assert repair.error_code == "MANIFEST_INVALID"
    # SQLite mirror keeps pointing at the last known good baseline.
    assert env.projects.get(PROJECT_ID).current_baseline_id == CURRENT_BASELINE_ID
    assert env.baselines.get(CURRENT_BASELINE_ID).status == BaselineStatus.EFFECTIVE


def test_manifest_with_missing_assets_is_not_used_for_rebuild(tmp_path) -> None:
    """Catches rebuilding the mirror from a manifest whose files are gone."""
    change = make_change(ChangeStatus.APPROVED)
    env = build_release_environment(tmp_path, change=change)
    _publish_manifest(env, change)
    (
        env.project_root / f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/full.md"
    ).unlink()
    reconciliation = _reconciliation(env)

    repair = reconciliation.rebuild_current_from_manifest()

    assert not repair.success
    assert repair.error_code == "MANIFEST_ASSETS_INVALID"
    assert env.projects.get(PROJECT_ID).current_baseline_id == CURRENT_BASELINE_ID


def test_missing_project_row_fails_repair_without_inventing_state(tmp_path) -> None:
    """Catches reconciliation synthesizing a Project row the system never had."""
    change = make_change(ChangeStatus.APPROVED)
    env = build_release_environment(tmp_path, change=change)
    _publish_manifest(env, change)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM projects WHERE id = ?", (PROJECT_ID,))
    reconciliation = _reconciliation(env)

    repair = reconciliation.rebuild_current_from_manifest()

    assert not repair.success
    assert repair.error_code == "PROJECT_MISSING"
    with sqlite3.connect(env.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_missing_change_row_fails_repair(tmp_path) -> None:
    """Catches reconciliation inventing a published change record."""
    change = make_change(ChangeStatus.APPROVED)
    env = build_release_environment(tmp_path, change=change)
    _publish_manifest(env, change)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM change_requests WHERE id = ?", ("CHANGE-001",))
    reconciliation = _reconciliation(env)

    repair = reconciliation.rebuild_current_from_manifest()

    assert not repair.success
    assert repair.error_code == "CHANGE_MISSING"


def test_pending_change_cannot_be_marked_published_by_repair(tmp_path) -> None:
    """Catches repair bypassing the human review audit fields on a pending change."""
    change = make_change(ChangeStatus.APPROVED)
    env = build_release_environment(tmp_path, change=change)
    _publish_manifest(env, change)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "UPDATE change_requests SET status = ? WHERE id = ?",
            (ChangeStatus.PENDING_APPROVAL.value, "CHANGE-001"),
        )
    reconciliation = _reconciliation(env)

    repair = reconciliation.rebuild_current_from_manifest()

    assert not repair.success
    assert repair.error_code == "CHANGE_NOT_PUBLISHABLE"
    with sqlite3.connect(env.db_path) as connection:
        status = connection.execute(
            "SELECT status FROM change_requests WHERE id = 'CHANGE-001'"
        ).fetchone()[0]
    assert status == ChangeStatus.PENDING_APPROVAL.value


def test_sqlite_write_failure_reports_repair_failure(tmp_path, monkeypatch) -> None:
    """Catches raw SQLite errors escaping the repair boundary."""
    env = build_release_environment(tmp_path)
    reconciliation = _reconciliation(env)
    monkeypatch.setattr(
        "src.infrastructure.recovery.reconciliation_service.connect",
        Mock(side_effect=sqlite3.OperationalError("cannot open database")),
    )

    repair = reconciliation.rebuild_current_from_manifest()

    assert not repair.success
    assert repair.error_code == "REPAIR_WRITE_FAILED"
