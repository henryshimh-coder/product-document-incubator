"""T15-R02：Lint 实时超时后以完全匹配冻结缓存继续，并走通决定→审批→发布。

frozen 快照的 Lint 缓存绑定「当前基线＋风险材料」（scope=current_plus_source）。
注入 Lint 网关超时后，实时自检失败；以 cache 模式探测命中同材料、同版本
冻结缓存，流程不中断，且缓存产生的问题可以继续完成会议决定、审批与本地发布。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.application.dto.lint import RunLintInput
from src.domain.enums import CallResultMode, ChangeStatus, EvidenceSide, IssueStatus
from src.domain.errors import ErrorCode, GatewayError
from tests.e2e.harness import TARGET_VERSION, DemoHarness


def test_lint_fallback_can_continue_to_decision_and_publish(
    frozen_root: Path,
    make_container,
) -> None:
    container = make_container(frozen_root, timeout_tasks=frozenset({"lint"}))
    harness = DemoHarness(container)

    # 冻结缓存绑定「基线＋风险材料」组合：先以缓存模式导入同一风险材料，
    # 重建与冻结时刻完全一致的对比上下文。
    imported = harness.import_source("risk_opinion.md", preferred_mode="cache")
    assert imported.result_mode == CallResultMode.CACHE

    # 实时自检被注入超时；页面接续路径会再以 cache 模式探测完全匹配缓存。
    with pytest.raises(GatewayError) as error:
        harness.run_lint(source_id=imported.source_id)
    assert error.value.code == ErrorCode.MODEL_TIMEOUT.value

    cached = container.lint.execute(
        RunLintInput(
            project_id="LLD",
            scope="current_plus_source",
            source_id=imported.source_id,
            preferred_mode="cache",
        )
    )
    assert cached.result_mode == CallResultMode.CACHE
    assert cached.model_call_id is None
    assert cached.baseline_version == "LLD-724_1"
    assert cached.cache_generated_at is not None

    # 缓存产生的问题与实时结果同构：可以继续完成决定→审批→发布。
    issue = next(item for item in cached.issues if item.status == IssueStatus.OPEN)
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
