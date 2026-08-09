"""T13 Step 2：完整成功 E2E——受治理的产品变更全流程。

从 initial 快照起步，真实容器 + 确定性 mock 网关走通：
导入风险材料 → 查询 → 一键自检 → 人工决定 → 创建变更单 → 批准 → 发布，
Manifest 落盘 LLD-724_2，旧版本 LLD-724_1 留存为历史版本。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.domain.enums import ChangeStatus, EvidenceSide, IssueStatus
from tests.e2e.harness import PUBLISHED_RULE_CONTENT, TARGET_VERSION, DemoHarness


def test_complete_governed_product_change(
    harness: DemoHarness,
    db_path: Path,
    manifest_store,
) -> None:
    ingest = harness.import_source("risk_opinion.md")
    assert ingest.conflict_count >= 1

    query = harness.query("当前目标客群是什么？")
    assert query.baseline_version == "LLD-724_1"
    assert query.citations

    lint = harness.run_lint()
    issue = next(item for item in lint.issues if item.status == IssueStatus.OPEN)
    challenging = next(
        evidence for evidence in issue.evidence if evidence.side == EvidenceSide.CHALLENGING_SOURCE
    )

    decision = harness.record_accept_change(issue.id, evidence_ref=challenging.citation_id)
    change = decision.change_request
    assert change is not None
    assert change.status == ChangeStatus.PENDING_APPROVAL

    approved = harness.approve_change(change.id)
    assert approved.status == ChangeStatus.APPROVED

    released = harness.publish(change.id)
    assert released.version == TARGET_VERSION
    assert released.full_document_path.endswith("full.md")

    assert manifest_store.read_and_validate().current_version == TARGET_VERSION
    with sqlite3.connect(db_path) as connection:
        baselines = dict(connection.execute("SELECT version, status FROM baselines").fetchall())
    assert baselines["LLD-724_1"] == "superseded"
    assert baselines[TARGET_VERSION] == "effective"
    # 发布内容与变更单 after_content 一致（追溯链完整）。
    assert released.id in manifest_store.read_and_validate().current_baseline_id
    assert PUBLISHED_RULE_CONTENT
