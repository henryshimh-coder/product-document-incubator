"""Shared real release environment for T10 review/publish/reconciliation tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.domain.enums import (
    AuthorityLevel,
    BaselineStatus,
    ChangeReviewAction,
    ChangeStatus,
    DecisionAction,
    EvidenceSide,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)
from src.domain.models import (
    Baseline,
    BaselineManifest,
    ChangeRequest,
    Decision,
    IssueCard,
    IssueEvidence,
    KnowledgeCard,
    Project,
    SourceRecord,
)
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteChangeRepository,
    SqliteDecisionRepository,
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.markdown_store import MarkdownStore
from src.infrastructure.observability.event_logger import EventLogger

NOW = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)
PROJECT_ID = "LLD"
CURRENT_BASELINE_ID = "BASE-LLD-724_1"
CURRENT_VERSION = "LLD-724_1"
TARGET_VERSION = "LLD-724_2"
TARGET_BASELINE_ID = f"BASE-{TARGET_VERSION}"
BEFORE_CONTENT = "当前目标客群是符合准入要求的存量客户。"
AFTER_CONTENT = "收紧后的目标客群仅覆盖高净值存量客户。"
FULL_DOCUMENT = (
    "# 产品智策演示基线\n\n"
    f"当前版本：{CURRENT_VERSION}\n\n"
    "## 目标客群\n\n"
    f"{BEFORE_CONTENT}\n\n"
    "## 附录\n\n"
    "仅作为脱敏演示基线使用。\n"
)
REVIEWER = "产品经理"
REVIEW_COMMENT = "已检查修改前后、依据、影响对象和目标版本。"
RELEASE_NOTE = "完成客群规则调整，保留版本差异与追溯依据。"
CHANGE_ID = "CHANGE-001"
ISSUE_ID = "ISSUE-001"
DECISION_ID = "DECISION-001"

_SOURCE_DOCUMENTS: dict[str, tuple[str, AuthorityLevel, str, str]] = {
    "SRC-BASE": (
        "当前产品方案.md",
        AuthorityLevel.FORMAL_EFFECTIVE,
        "正式基线材料",
        "# 当前产品方案\n\n## 目标客群\n\n"
        + BEFORE_CONTENT
        + "\n\n## 接口约束\n\n客群接口规则。\n\n## 附录\n\n"
        + "已脱敏的演示补充材料。\n" * 500,
    ),
    "SRC-RISK": (
        "风险意见.md",
        AuthorityLevel.FORMAL_DECISION,
        "风险意见",
        "# 风险意见\n\n## 客群限制\n\n风险意见要求收紧客群。\n",
    ),
}

_REVIEWED_FIELDS = {
    ChangeStatus.APPROVED: ChangeReviewAction.APPROVE,
    ChangeStatus.PUBLISHED: ChangeReviewAction.APPROVE,
    ChangeStatus.REJECTED: ChangeReviewAction.REJECT,
    ChangeStatus.DEFERRED: ChangeReviewAction.DEFER,
    ChangeStatus.NEEDS_INFO: ChangeReviewAction.REQUEST_INFO,
}


@dataclass
class ReleaseEnvironment:
    project_root: Path
    db_path: Path
    manifest_path: Path
    manifest_store: ManifestStore
    markdown_store: MarkdownStore
    projects: SqliteProjectRepository
    baselines: SqliteBaselineRepository
    changes: SqliteChangeRepository
    sources: SqliteSourceRepository
    event_logger: EventLogger


def make_change(
    status: ChangeStatus = ChangeStatus.PENDING_APPROVAL,
    *,
    change_id: str = CHANGE_ID,
    target_version: str = TARGET_VERSION,
    idempotency_key: str | None = None,
) -> ChangeRequest:
    review_action = _REVIEWED_FIELDS.get(status)
    reviewed = review_action is not None
    return ChangeRequest(
        id=change_id,
        project_id=PROJECT_ID,
        issue_id=ISSUE_ID,
        decision_id=DECISION_ID,
        target_card_id="RULE-001",
        before_content=BEFORE_CONTENT,
        after_content=AFTER_CONTENT,
        rationale="依据风险意见和会议结论调整。",
        evidence_refs=["CIT-BASE-001", "CIT-RISK-001"],
        impacted_objects=["RULE-001", "API-CUSTOMER"],
        responsible_domain="产品",
        required_approver_role="产品经理",
        demo_confirmer="产品经理",
        status=status,
        review_action=review_action,
        reviewed_by=REVIEWER if reviewed else None,
        review_comment=REVIEW_COMMENT if reviewed else None,
        review_idempotency_key=(idempotency_key or "REVIEW-KEY-001") if reviewed else None,
        reviewed_at=NOW if reviewed else None,
        target_version=target_version,
        effective_condition="审批通过且验证完成后发布。",
        created_at=NOW,
        updated_at=NOW,
    )


def build_release_environment(
    tmp_path: Path,
    *,
    change_status: ChangeStatus = ChangeStatus.PENDING_APPROVAL,
    change: ChangeRequest | None = None,
) -> ReleaseEnvironment:
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = project_root / "data/local_state/product_intelligence.db"
    migrate(db_path)
    markdown_store = MarkdownStore(project_root)
    cards = [
        KnowledgeCard(
            id="RULE-001",
            project_id=PROJECT_ID,
            card_type="rule",
            title="目标客群",
            content=BEFORE_CONTENT,
            status=KnowledgeStatus.EFFECTIVE,
            product_version=CURRENT_VERSION,
            applicable_scope="演示",
            source_refs=["SRC-BASE"],
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            owner="产品",
            created_at=NOW,
            updated_at=NOW,
        ),
        KnowledgeCard(
            id="API-CUSTOMER",
            project_id=PROJECT_ID,
            card_type="api",
            title="客群接口",
            content="客群接口规则。",
            status=KnowledgeStatus.EFFECTIVE,
            product_version=CURRENT_VERSION,
            applicable_scope="演示",
            source_refs=["SRC-BASE"],
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            owner="产品",
            created_at=NOW,
            updated_at=NOW,
        ),
    ]
    full_path, cards_path = markdown_store.write_baseline(CURRENT_VERSION, FULL_DOCUMENT, cards)
    full_sha256 = markdown_store.sha256_for(full_path)
    cards_sha256 = markdown_store.sha256_for(cards_path)
    manifest_path = project_root / "data/local_state/current_baseline.json"
    manifest_store = ManifestStore(manifest_path, project_root=project_root)
    manifest_store.atomic_replace(
        BaselineManifest(
            schema_version="1.0",
            project_id=PROJECT_ID,
            current_baseline_id=CURRENT_BASELINE_ID,
            current_version=CURRENT_VERSION,
            parent_baseline_id=None,
            full_document_path=full_path,
            card_snapshot_path=cards_path,
            full_document_sha256=full_sha256,
            card_snapshot_sha256=cards_sha256,
            change_request_id=None,
            approved_by=REVIEWER,
            published_at=NOW,
        )
    )
    snapshot = manifest_store.read_snapshot()
    projects = SqliteProjectRepository(db_path)
    projects.add(
        Project(
            id=PROJECT_ID,
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id=CURRENT_BASELINE_ID,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    baselines = SqliteBaselineRepository(db_path)
    baselines.add(
        Baseline(
            id=CURRENT_BASELINE_ID,
            project_id=PROJECT_ID,
            version=CURRENT_VERSION,
            parent_baseline_id=None,
            status=BaselineStatus.EFFECTIVE,
            full_document_path=full_path,
            card_snapshot_path=cards_path,
            manifest_sha256=snapshot.sha256,
            full_document_sha256=full_sha256,
            card_snapshot_sha256=cards_sha256,
            change_request_id=None,
            approved_by=REVIEWER,
            effective_at=NOW,
            created_at=NOW,
        )
    )
    SqliteKnowledgeRepository(db_path).upsert_cards(cards)
    sources = SqliteSourceRepository(db_path)
    for source_id, (filename, authority, source_type, content) in _SOURCE_DOCUMENTS.items():
        archive_path, digest, size_bytes = _write_source_archive(
            project_root,
            source_id,
            filename,
            content,
        )
        sources.add(
            SourceRecord(
                id=source_id,
                project_id=PROJECT_ID,
                original_filename=filename,
                archive_path=archive_path,
                sha256=digest,
                mime_type="text/plain",
                size_bytes=size_bytes,
                source_type=source_type,
                authority_level=authority,
                source_department="产品部",
                provider=None,
                document_date=NOW.date(),
                document_version="v1.0",
                applicable_baseline_version=CURRENT_VERSION,
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted=True,
                allow_external_model=True,
                is_sandbox=False,
                ingest_status="completed",
                created_at=NOW,
            )
        )
    SqliteIssueRepository(db_path).add_many(
        [
            IssueCard(
                id=ISSUE_ID,
                project_id=PROJECT_ID,
                issue_type="information_gap",
                severity=IssueSeverity.PENDING_INFO,
                status=IssueStatus.DECIDED,
                title="客群规则待收紧",
                description="风险意见要求收紧客群。",
                evidence=[
                    IssueEvidence(
                        source_id="SRC-BASE",
                        citation_id="CIT-BASE-001",
                        excerpt=BEFORE_CONTENT,
                        document_version="v1.0",
                        page_or_section="目标客群",
                        side=EvidenceSide.CURRENT_BASELINE,
                    ),
                    IssueEvidence(
                        source_id="SRC-RISK",
                        citation_id="CIT-RISK-001",
                        excerpt="风险意见要求收紧客群。",
                        document_version="v1.0",
                        page_or_section="客群限制",
                        side=EvidenceSide.CHALLENGING_SOURCE,
                    ),
                ],
                impacted_domains=["产品"],
                options=[],
                ai_recommendation="accept_change",
                ai_confidence=0.7,
                uncertainty="待会议处理",
                owner=None,
                due_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        ]
    )
    SqliteDecisionRepository(db_path).add(
        Decision(
            id=DECISION_ID,
            project_id=PROJECT_ID,
            issue_id=ISSUE_ID,
            action=DecisionAction.ACCEPT_CHANGE,
            conclusion="会议确认采纳风险意见。",
            confirmed_by=REVIEWER,
            responsible_party="产品负责人",
            due_at=None,
            verification_condition="回归测试通过且审批完成。",
            created_at=NOW,
        ),
        idempotency_key="DECISION-KEY-001",
    )
    changes = SqliteChangeRepository(db_path)
    changes.add(change if change is not None else make_change(change_status))
    event_logger = EventLogger(db_path)
    event_logger.log_path = project_root / "data/local_state/app.log.jsonl"
    return ReleaseEnvironment(
        project_root=project_root,
        db_path=db_path,
        manifest_path=manifest_path,
        manifest_store=manifest_store,
        markdown_store=markdown_store,
        projects=projects,
        baselines=baselines,
        changes=changes,
        sources=sources,
        event_logger=event_logger,
    )


def _write_source_archive(
    project_root: Path,
    source_id: str,
    filename: str,
    content: str,
) -> tuple[str, str, int]:
    archive_dir = project_root / "data/source_archive" / PROJECT_ID / source_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8")
    archive_path = archive_dir / filename
    archive_path.write_bytes(payload)
    return str(archive_path), hashlib.sha256(payload).hexdigest(), len(payload)
