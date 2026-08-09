"""T13 Step 2：完整成功 E2E——受治理的产品变更全流程。

从 initial 快照起步，真实容器 + 确定性 mock 网关按规定顺序走通：
导入风险材料 → 查询 → 一键自检 → 人工决定 → 创建变更单 → 批准 → 发布 → 发布后查询，
Manifest 落盘 LLD-724_2，旧版本 LLD-724_1 留存为历史版本。
发布产物（full.md / cards.json）与 SQLite 生效卡片内容必须真实等于变更单
after_content；配套破坏性用例证明产物或 SQLite 被篡改时断言必然失败。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.domain.enums import ChangeStatus, EvidenceSide, IssueStatus
from src.infrastructure.files.manifest_store import ManifestStore
from tests.e2e.harness import (
    PUBLISHED_RULE_CONTENT,
    RULE_CARD_CONTENT,
    RULE_CARD_ID,
    TARGET_VERSION,
    DemoHarness,
)


def _run_flow_to_publish(harness: DemoHarness):
    """导入→查询→自检→决定→审批→发布（计划规定顺序），返回 (released, change)。"""
    ingest = harness.import_source("risk_opinion.md")
    assert ingest.conflict_count >= 1

    # 导入后、发布前查询：证明导入未破坏当前基线的查询上下文与引用。
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
    return released, change


def _assert_published_artifacts(demo_root: Path, manifest_store: ManifestStore) -> None:
    """从 Manifest 指向的可信产物验证发布内容，禁止恒真断言。"""
    manifest = manifest_store.read_and_validate()
    full_text = (demo_root / manifest.full_document_path).read_text(encoding="utf-8")
    assert PUBLISHED_RULE_CONTENT in full_text
    assert RULE_CARD_CONTENT not in full_text
    cards = json.loads((demo_root / manifest.card_snapshot_path).read_text(encoding="utf-8"))
    contents = {card["id"]: card["content"] for card in cards}
    assert contents[RULE_CARD_ID] == PUBLISHED_RULE_CONTENT


def _assert_sqlite_effective_card(db_path: Path) -> None:
    """直接验证 SQLite 生效卡片的版本、状态与内容（三方一致性的一方）。"""
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT id, product_version, status, content FROM knowledge_cards"
            " WHERE project_id = 'LLD' AND id = ?",
            (RULE_CARD_ID,),
        ).fetchone()
    assert row == (RULE_CARD_ID, TARGET_VERSION, "effective", PUBLISHED_RULE_CONTENT)


def test_complete_governed_product_change(
    harness: DemoHarness,
    demo_root: Path,
    db_path: Path,
    manifest_store: ManifestStore,
    gateway_calls: list[dict],
) -> None:
    released, change = _run_flow_to_publish(harness)
    assert released.version == TARGET_VERSION
    assert released.full_document_path.endswith("full.md")

    # 顺序敏感：出站网关调用的前三次必须为 导入→查询→自检
    # （把 Query 移回导入前时此断言失败）。
    assert [call["task"] for call in gateway_calls[:3]] == ["ingest", "query", "lint"]

    assert manifest_store.read_and_validate().current_version == TARGET_VERSION
    with sqlite3.connect(db_path) as connection:
        baselines = dict(connection.execute("SELECT version, status FROM baselines").fetchall())
    assert baselines["LLD-724_1"] == "superseded"
    assert baselines[TARGET_VERSION] == "effective"
    assert released.id in manifest_store.read_and_validate().current_baseline_id

    # 三方一致：发布产物（full.md / cards.json）、SQLite 生效卡片、Manifest。
    assert change.after_content == PUBLISHED_RULE_CONTENT
    _assert_published_artifacts(demo_root, manifest_store)
    _assert_sqlite_effective_card(db_path)

    # 发布后实时查询：回答与版本均来自新基线。
    post_query = harness.query("当前目标客群是什么？")
    assert post_query.baseline_version == TARGET_VERSION
    assert post_query.answer == PUBLISHED_RULE_CONTENT


def test_publish_artifact_assertions_fail_when_content_reverted(
    harness: DemoHarness,
    demo_root: Path,
    manifest_store: ManifestStore,
) -> None:
    """破坏性反证：产物被改回旧规则时，发布产物断言必须失败。"""
    _run_flow_to_publish(harness)

    manifest = manifest_store.read_and_validate()
    full_path = demo_root / manifest.full_document_path
    reverted = full_path.read_text(encoding="utf-8").replace(
        PUBLISHED_RULE_CONTENT, RULE_CARD_CONTENT
    )
    assert reverted != full_path.read_text(encoding="utf-8")
    full_path.write_text(reverted, encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_published_artifacts(demo_root, manifest_store)


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("content", RULE_CARD_CONTENT),
        ("product_version", "LLD-724_1"),
        ("status", "candidate"),
    ],
)
def test_sqlite_effective_card_assertions_fail_when_tampered(
    harness: DemoHarness,
    db_path: Path,
    field: str,
    tampered: str,
) -> None:
    """破坏性反证：SQLite 生效卡片内容/版本/状态任一被篡改时断言必须失败。"""
    _run_flow_to_publish(harness)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE knowledge_cards SET {field} = ? WHERE project_id = 'LLD' AND id = ?",
            (tampered, RULE_CARD_ID),
        )

    with pytest.raises(AssertionError):
        _assert_sqlite_effective_card(db_path)
