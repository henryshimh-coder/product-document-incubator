from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from src.domain.enums import ChangeReviewAction, ChangeStatus
from src.domain.errors import DomainError
from src.domain.models import BaselineManifest, ChangeRequest

NOW = datetime(2026, 7, 29, tzinfo=UTC)


@dataclass(frozen=True)
class ReleaseCommand:
    project_id: str = "LLD"
    approved_by: str = "产品经理"
    impact_reviewed: bool = True
    release_note: str = "完成目标客群规则调整并保留来源、决定和版本追溯记录。"


def manifest() -> BaselineManifest:
    return BaselineManifest(
        schema_version="1.0",
        project_id="LLD",
        current_baseline_id="BASE-LLD-724_1",
        current_version="LLD-724_1",
        parent_baseline_id=None,
        full_document_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"),
        card_snapshot_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json"),
        full_document_sha256="a" * 64,
        card_snapshot_sha256="b" * 64,
        change_request_id=None,
        approved_by="产品经理",
        published_at=NOW,
    )


def change(status: ChangeStatus = ChangeStatus.APPROVED) -> ChangeRequest:
    reviewed = status in {ChangeStatus.APPROVED, ChangeStatus.PUBLISHED}
    return ChangeRequest(
        id="CHG-LLD-001",
        project_id="LLD",
        issue_id="ISSUE-LLD-001",
        decision_id="DEC-LLD-001",
        target_card_id="RULE-LLD-001",
        before_content="原规则",
        after_content="新规则",
        rationale="采纳风险意见",
        evidence_refs=["CIT-RISK-001"],
        impacted_objects=["产品方案/目标客群"],
        responsible_domain="产品",
        required_approver_role="产品与风险负责人",
        demo_confirmer="产品经理",
        status=status,
        review_action=ChangeReviewAction.APPROVE if reviewed else None,
        reviewed_by="产品经理" if reviewed else None,
        review_comment="已检查修改前后、依据、影响对象和目标版本。" if reviewed else None,
        review_idempotency_key="REVIEW-CHG-001" if reviewed else None,
        reviewed_at=NOW if reviewed else None,
        target_version="LLD-724_2",
        effective_condition="发布后生效",
        created_at=NOW,
        updated_at=NOW,
    )


def _policy():
    return importlib.import_module("src.domain.policies.release_policy").ReleasePolicy()


def test_release_rejects_change_that_has_not_been_approved():
    """Catches publishing a pending change without an explicit review decision."""
    with pytest.raises(DomainError, match="CHANGE_NOT_APPROVED"):
        _policy().validate(
            ReleaseCommand(impact_reviewed=False),
            manifest(),
            change(ChangeStatus.PENDING_APPROVAL),
        )


def test_release_requires_impact_review_for_approved_change():
    """Catches publishing an approved change before its impact is checked."""
    with pytest.raises(DomainError, match="IMPACT_REVIEW_REQUIRED"):
        _policy().validate(
            ReleaseCommand(impact_reviewed=False),
            manifest(),
            change(),
        )


@pytest.mark.parametrize(
    "release_note",
    [
        "过短",
        "变" * 201,
    ],
)
def test_release_note_must_be_between_20_and_200_characters(release_note):
    """Catches an unauditable or oversized release explanation."""
    with pytest.raises(DomainError, match="INVALID_RELEASE_NOTE"):
        _policy().validate(
            ReleaseCommand(release_note=release_note),
            manifest(),
            change(),
        )


def test_release_requires_same_project_across_command_manifest_and_change():
    """Catches a change being published into another project's current baseline."""
    with pytest.raises(DomainError, match="RELEASE_PROJECT_MISMATCH"):
        _policy().validate(
            ReleaseCommand(project_id="OTHER"),
            manifest(),
            change(),
        )


def test_release_rejects_current_version_as_target():
    """Catches overwriting the currently effective version directory."""
    current_change = change().model_copy(update={"target_version": "LLD-724_1"})

    with pytest.raises(DomainError, match="TARGET_VERSION_ALREADY_EFFECTIVE"):
        _policy().validate(ReleaseCommand(), manifest(), current_change)


def test_release_requires_nonblank_approver():
    """Catches publishing a baseline without a traceable accountable approver."""
    with pytest.raises(DomainError, match="RELEASE_APPROVER_REQUIRED"):
        _policy().validate(
            ReleaseCommand(approved_by=" "),
            manifest(),
            change(),
        )


def test_approved_reviewed_change_with_impact_check_can_be_released():
    """Catches over-restricting the valid governed release path."""
    _policy().validate(ReleaseCommand(), manifest(), change())
