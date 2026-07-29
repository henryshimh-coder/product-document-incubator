from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.domain.enums import (
    AuthorityLevel,
    BaselineStatus,
    CallResultMode,
    ChangeReviewAction,
    ChangeStatus,
    DecisionAction,
    EvidenceSide,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ReviewCommentStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=10, max_length=200),
]
Sha256Str = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class Project(DomainModel):
    id: NonEmptyStr
    name: NonEmptyStr
    product_line: NonEmptyStr
    stage: NonEmptyStr
    current_baseline_id: NonEmptyStr | None
    allow_external_model: bool
    created_at: datetime
    updated_at: datetime


class SourceRecord(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    original_filename: NonEmptyStr
    archive_path: NonEmptyStr
    sha256: Sha256Str
    mime_type: NonEmptyStr
    size_bytes: NonNegativeInt
    source_type: NonEmptyStr
    authority_level: AuthorityLevel
    source_department: NonEmptyStr
    provider: NonEmptyStr | None
    document_date: date
    document_version: NonEmptyStr
    applicable_baseline_version: NonEmptyStr
    security_level: SecurityLevel
    is_redacted: bool
    allow_external_model: bool
    is_sandbox: bool
    ingest_status: NonEmptyStr
    created_at: datetime


class KnowledgeCard(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    card_type: NonEmptyStr
    title: NonEmptyStr
    content: NonEmptyStr
    status: KnowledgeStatus
    product_version: NonEmptyStr
    applicable_scope: NonEmptyStr
    source_refs: list[NonEmptyStr]
    authority_level: AuthorityLevel
    owner: NonEmptyStr
    confidence: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def ensure_effective_card_is_traceable(self) -> Self:
        if self.status == KnowledgeStatus.EFFECTIVE and not self.source_refs:
            raise ValueError("source_refs are required for an effective knowledge card")
        return self


class Relation(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    source_id: NonEmptyStr
    relation_type: Literal[
        "derived_from",
        "supports",
        "conflicts_with",
        "proposes_change_to",
        "approved_as",
        "supersedes",
        "impacts",
        "to_be_verified_by",
    ]
    target_id: NonEmptyStr
    source_ref: NonEmptyStr | None
    created_at: datetime


class Baseline(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    version: NonEmptyStr
    parent_baseline_id: NonEmptyStr | None
    status: BaselineStatus
    full_document_path: NonEmptyStr
    card_snapshot_path: NonEmptyStr
    manifest_sha256: Sha256Str
    change_request_id: NonEmptyStr | None
    approved_by: NonEmptyStr
    effective_at: datetime | None
    created_at: datetime


class IssueEvidence(DomainModel):
    source_id: NonEmptyStr
    citation_id: NonEmptyStr
    excerpt: NonEmptyStr
    document_version: NonEmptyStr
    page_or_section: NonEmptyStr
    side: EvidenceSide


class IssueCard(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    issue_type: NonEmptyStr
    severity: IssueSeverity
    status: IssueStatus
    title: NonEmptyStr
    description: NonEmptyStr
    evidence: list[IssueEvidence]
    impacted_domains: list[NonEmptyStr] = Field(min_length=1)
    options: list[dict[str, str]]
    ai_recommendation: NonEmptyStr | None
    ai_confidence: float | None = Field(default=None, ge=0, le=1)
    uncertainty: NonEmptyStr | None
    owner: NonEmptyStr | None
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def ensure_major_issue_has_two_sides(self) -> Self:
        if self.severity in {
            IssueSeverity.BLOCKING,
            IssueSeverity.PENDING_DECISION,
        }:
            unique_sources = {item.source_id for item in self.evidence}
            if len(unique_sources) < 2:
                raise ValueError("evidence must come from two distinct sources for a major issue")
            evidence_sides = {item.side for item in self.evidence}
            if evidence_sides != {
                EvidenceSide.CURRENT_BASELINE,
                EvidenceSide.CHALLENGING_SOURCE,
            }:
                raise ValueError("a major issue must contain both evidence sides")
        if self.severity == IssueSeverity.PENDING_INFO and self.uncertainty is None:
            raise ValueError("uncertainty must describe the missing content")
        return self


class Decision(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    issue_id: NonEmptyStr
    action: DecisionAction
    conclusion: NonEmptyStr
    confirmed_by: NonEmptyStr
    responsible_party: NonEmptyStr | None
    due_at: datetime | None
    verification_condition: NonEmptyStr | None
    created_at: datetime

    @model_validator(mode="after")
    def ensure_action_has_execution_fields(self) -> Self:
        if self.action == DecisionAction.ACCEPT_CHANGE and (
            self.responsible_party is None or self.verification_condition is None
        ):
            raise ValueError(
                "decision accept_change requires responsible_party and verification_condition"
            )
        if self.action == DecisionAction.DEFER and self.due_at is None:
            raise ValueError("decision defer requires due_at")
        if self.action == DecisionAction.FALSE_POSITIVE and len(self.conclusion) < 10:
            raise ValueError("decision false_positive conclusion must explain the reason")
        return self


class ChangeRequest(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    issue_id: NonEmptyStr
    decision_id: NonEmptyStr
    target_card_id: NonEmptyStr
    before_content: NonEmptyStr
    after_content: NonEmptyStr
    rationale: NonEmptyStr
    evidence_refs: list[NonEmptyStr] = Field(min_length=1)
    impacted_objects: list[NonEmptyStr] = Field(min_length=1)
    responsible_domain: NonEmptyStr
    required_approver_role: NonEmptyStr
    demo_confirmer: NonEmptyStr
    status: ChangeStatus
    review_action: ChangeReviewAction | None
    reviewed_by: NonEmptyStr | None
    review_comment: ReviewCommentStr | None
    review_idempotency_key: NonEmptyStr | None
    reviewed_at: datetime | None
    target_version: NonEmptyStr
    effective_condition: NonEmptyStr
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def ensure_reviewed_state_has_audit_fields(self) -> Self:
        expected_actions = {
            ChangeStatus.APPROVED: ChangeReviewAction.APPROVE,
            ChangeStatus.PUBLISHED: ChangeReviewAction.APPROVE,
            ChangeStatus.REJECTED: ChangeReviewAction.REJECT,
            ChangeStatus.DEFERRED: ChangeReviewAction.DEFER,
            ChangeStatus.NEEDS_INFO: ChangeReviewAction.REQUEST_INFO,
        }
        expected_action = expected_actions.get(self.status)
        if expected_action is None:
            return self
        review_fields = (
            self.reviewed_by,
            self.review_comment,
            self.review_idempotency_key,
            self.reviewed_at,
        )
        if self.review_action != expected_action or any(item is None for item in review_fields):
            raise ValueError("review audit fields do not match the reviewed change status")
        return self


class Citation(DomainModel):
    id: NonEmptyStr
    source_id: NonEmptyStr
    filename: NonEmptyStr
    document_version: NonEmptyStr
    section: NonEmptyStr
    excerpt: NonEmptyStr
    authority_level: AuthorityLevel


class QueryResponse(DomainModel):
    answer: NonEmptyStr
    effective_rules: list[NonEmptyStr]
    citations: list[Citation]
    candidate_notice: NonEmptyStr | None
    conflict_notice: NonEmptyStr | None
    baseline_version: NonEmptyStr
    evidence_sufficiency: Literal["sufficient", "partial", "insufficient"]
    result_mode: CallResultMode
    model_call_id: NonEmptyStr | None


class IngestReport(DomainModel):
    source_id: NonEmptyStr
    duplicate: bool
    summary: NonEmptyStr
    created_card_ids: list[NonEmptyStr]
    created_relation_ids: list[NonEmptyStr]
    created_issue_ids: list[NonEmptyStr]
    candidate_count: NonNegativeInt
    conflict_count: NonNegativeInt
    result_mode: CallResultMode
    model_call_id: NonEmptyStr | None


class LintReport(DomainModel):
    issues: list[IssueCard]
    deterministic_count: NonNegativeInt
    semantic_count: NonNegativeInt
    result_mode: CallResultMode
    model_call_id: NonEmptyStr | None


class DecisionResult(DomainModel):
    decision: Decision
    change_request: ChangeRequest | None


class RepairResult(DomainModel):
    success: bool
    repaired_entities: list[NonEmptyStr]
    error_code: NonEmptyStr | None


class BaselineManifest(DomainModel):
    schema_version: NonEmptyStr
    project_id: NonEmptyStr
    current_baseline_id: NonEmptyStr
    current_version: NonEmptyStr
    parent_baseline_id: NonEmptyStr | None
    full_document_path: NonEmptyStr
    card_snapshot_path: NonEmptyStr
    full_document_sha256: Sha256Str
    card_snapshot_sha256: Sha256Str
    change_request_id: NonEmptyStr | None
    approved_by: NonEmptyStr
    published_at: datetime


class ModelCallLog(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    task_type: Literal["ingest", "query", "lint"]
    source_ids: list[NonEmptyStr]
    baseline_version: NonEmptyStr
    model_label: NonEmptyStr
    prompt_version: NonEmptyStr
    schema_version: NonEmptyStr
    authorized: bool
    redacted: bool
    outbound_chars: NonNegativeInt
    outbound_coverage: float = Field(ge=0, le=1)
    result_mode: CallResultMode
    status: Literal["started", "succeeded", "failed", "timeout"]
    started_at: datetime
    finished_at: datetime | None
    elapsed_ms: NonNegativeInt | None
    error_code: NonEmptyStr | None
