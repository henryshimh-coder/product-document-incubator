from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.domain.enums import AuthorityLevel, CallResultMode, EvidenceSide, IssueSeverity

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WorkflowOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CommonWorkflowInput(WorkflowOutput):
    schema_version: Literal["1.0"]
    project_id: NonEmptyStr
    baseline_version: NonEmptyStr
    task_id: NonEmptyStr
    language: Literal["zh-CN"]


class IngestSourceInput(WorkflowOutput):
    id: NonEmptyStr
    type: NonEmptyStr
    authority_level: AuthorityLevel
    document_version: NonEmptyStr
    document_date: date
    applicable_scope: NonEmptyStr


class IngestBaselineRuleInput(WorkflowOutput):
    id: NonEmptyStr
    title: NonEmptyStr
    content: NonEmptyStr
    status: Literal["effective"]


class IngestSourceChunkInput(WorkflowOutput):
    chunk_id: NonEmptyStr
    locator: NonEmptyStr
    text: NonEmptyStr


class IngestWorkflowInput(CommonWorkflowInput):
    source: IngestSourceInput
    baseline_rules: list[IngestBaselineRuleInput]
    source_chunks: list[IngestSourceChunkInput] = Field(min_length=1)


class QueryEffectiveCardInput(WorkflowOutput):
    id: NonEmptyStr
    title: NonEmptyStr
    content: NonEmptyStr
    source_citations: list[NonEmptyStr]


class QueryNoticeInput(WorkflowOutput):
    type: Literal["candidate", "conflict"]
    id: NonEmptyStr
    summary: NonEmptyStr


class QueryCitationInput(WorkflowOutput):
    id: NonEmptyStr
    source_id: NonEmptyStr
    filename: NonEmptyStr
    document_version: NonEmptyStr
    section: NonEmptyStr
    excerpt: NonEmptyStr
    authority_level: AuthorityLevel


class QueryWorkflowInput(CommonWorkflowInput):
    scope: Literal["effective", "effective_with_notices", "historical"]
    question: NonEmptyStr
    effective_cards: list[QueryEffectiveCardInput]
    notices: list[QueryNoticeInput]
    citations: list[QueryCitationInput]


class LintCitationInput(WorkflowOutput):
    id: NonEmptyStr
    source_id: NonEmptyStr
    citation_id: NonEmptyStr
    document_version: NonEmptyStr
    page_or_section: NonEmptyStr
    excerpt: NonEmptyStr


class LintDeterministicFindingInput(LintCitationInput):
    side: EvidenceSide


class LintWorkflowInput(CommonWorkflowInput):
    baseline_rules: list[LintCitationInput]
    comparison_items: list[LintCitationInput]
    deterministic_findings: list[LintDeterministicFindingInput]
    allowed_issue_types: list[
        Literal[
            "conflict",
            "omission",
            "stale",
            "not_synchronized",
            "insufficient_evidence",
        ]
    ]


class IngestCitationOutput(WorkflowOutput):
    source_id: NonEmptyStr
    chunk_id: NonEmptyStr
    locator: NonEmptyStr
    excerpt: NonEmptyStr


class IngestItemOutput(WorkflowOutput):
    item_id: NonEmptyStr
    item_type: Literal["professional_opinion", "discussion_reference"]
    title: NonEmptyStr
    content: NonEmptyStr
    target_card_id: NonEmptyStr | None
    result_type: Literal["candidate", "conflict_discussion", "information_gap"]
    status: Literal["ai_inferred", "candidate", "conflict"]
    source_citations: list[IngestCitationOutput] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: NonEmptyStr | None


class IngestRelationOutput(WorkflowOutput):
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


class IngestWorkflowOutput(WorkflowOutput):
    schema_version: Literal["1.0"]
    task_id: NonEmptyStr
    summary: NonEmptyStr
    items: list[IngestItemOutput]
    relations: list[IngestRelationOutput]


class QueryCitationOutput(QueryCitationInput):
    pass


class QueryWorkflowOutput(WorkflowOutput):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    effective_rules: list[NonEmptyStr]
    citations: list[QueryCitationOutput]
    candidate_notice: NonEmptyStr | None
    conflict_notice: NonEmptyStr | None
    baseline_version: NonEmptyStr
    evidence_sufficiency: Literal["sufficient", "partial", "insufficient"]
    result_mode: CallResultMode
    model_call_id: NonEmptyStr | None


class LintEvidenceOutput(WorkflowOutput):
    source_id: NonEmptyStr
    citation_id: NonEmptyStr
    excerpt: NonEmptyStr
    document_version: NonEmptyStr
    page_or_section: NonEmptyStr
    side: EvidenceSide


class LintOptionOutput(WorkflowOutput):
    code: NonEmptyStr
    label: NonEmptyStr
    impact: NonEmptyStr


class LintIssueOutput(WorkflowOutput):
    issue_type: NonEmptyStr
    severity: IssueSeverity
    title: NonEmptyStr
    description: NonEmptyStr
    evidence: list[LintEvidenceOutput]
    impacted_domains: list[NonEmptyStr] = Field(min_length=1)
    options: list[LintOptionOutput]
    ai_recommendation: NonEmptyStr | None
    ai_confidence: float | None = Field(default=None, ge=0, le=1)
    uncertainty: NonEmptyStr | None

    @model_validator(mode="after")
    def require_two_sided_major_evidence(self) -> Self:
        if self.severity not in {
            IssueSeverity.BLOCKING,
            IssueSeverity.PENDING_DECISION,
        }:
            return self
        sides = {evidence.side for evidence in self.evidence}
        sources = {evidence.source_id for evidence in self.evidence}
        if (
            sides
            != {
                EvidenceSide.CURRENT_BASELINE,
                EvidenceSide.CHALLENGING_SOURCE,
            }
            or len(sources) < 2
        ):
            raise ValueError("major issues require two-sided evidence")
        return self


class LintWorkflowOutput(WorkflowOutput):
    schema_version: Literal["1.0"]
    issues: list[LintIssueOutput]
