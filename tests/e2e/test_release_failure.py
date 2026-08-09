"""T13 Step 4：发布失败 E2E——注入发布写入失败，旧版本保持生效。

已批准变更在发布原子替换阶段注入持久化不确定错误，发布 fail closed
（RELEASE_FAILED），Manifest 逐字节不变，变更单保持已批准状态可安全重试。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.bootstrap_demo import BASELINE_VERSION
from src.domain.enums import ChangeStatus, EvidenceSide, IssueStatus
from src.domain.errors import DomainError
from src.infrastructure.files.manifest_store import (
    ManifestDurabilityUncertainError,
    ManifestStore,
)
from tests.e2e.harness import DemoHarness


def test_release_failure_keeps_old_version(
    harness: DemoHarness,
    db_path: Path,
    manifest_store: ManifestStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingest = harness.import_source("risk_opinion.md")
    assert ingest.conflict_count >= 1
    lint = harness.run_lint()
    issue = next(item for item in lint.issues if item.status == IssueStatus.OPEN)
    challenging = next(
        evidence for evidence in issue.evidence if evidence.side == EvidenceSide.CHALLENGING_SOURCE
    )
    decision = harness.record_accept_change(issue.id, evidence_ref=challenging.citation_id)
    change = decision.change_request
    assert change is not None
    approved = harness.approve_change(change.id)
    assert approved.status == ChangeStatus.APPROVED

    before_bytes = (
        manifest_store.project_root / "data/local_state/current_baseline.json"
    ).read_bytes()
    before = manifest_store.read_and_validate()

    def injected_replace(self, candidate):  # noqa: ARG002
        raise ManifestDurabilityUncertainError("E2E injected write failure")

    monkeypatch.setattr(ManifestStore, "atomic_replace", injected_replace)
    with pytest.raises(DomainError, match="RELEASE_FAILED"):
        harness.publish(approved.id)

    assert manifest_store.read_and_validate() == before
    assert (
        manifest_store.project_root / "data/local_state/current_baseline.json"
    ).read_bytes() == before_bytes
    with sqlite3.connect(db_path) as connection:
        status = connection.execute(
            "SELECT status FROM change_requests WHERE id = ?",
            (approved.id,),
        ).fetchone()[0]
        baselines = dict(connection.execute("SELECT version, status FROM baselines").fetchall())
    assert status == ChangeStatus.APPROVED.value
    assert baselines[BASELINE_VERSION] == "effective"
