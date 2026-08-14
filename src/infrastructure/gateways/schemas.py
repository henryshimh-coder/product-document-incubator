from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.domain.enums import AuthorityLevel, CallResultMode, EvidenceSide, IssueSeverity

MAX_IDENTIFIER_CHARS = 128
MAX_METADATA_CHARS = 256
MAX_SCOPE_CHARS = 500
MAX_MATERIAL_CHARS = 2000
MAX_QUESTION_CHARS = 500
MAX_SMALL_COLLECTION_ITEMS = 20
MAX_CITATION_COLLECTION_ITEMS = 50
MAX_ALLOWED_ISSUE_TYPES = 5
MAX_DOCUMENT_MARKDOWN_CHARS = 200_000
MAX_DOCUMENT_SUMMARY_CHARS = 2_000
MAX_DOCUMENT_SECTIONS = 200

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
IdentifierStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_IDENTIFIER_CHARS,
    ),
]
MetadataStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_METADATA_CHARS,
    ),
]
ScopeStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SCOPE_CHARS,
    ),
]
MaterialFragment = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_MATERIAL_CHARS,
    ),
]
QuestionStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
    ),
]


class WorkflowOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CommonWorkflowInput(WorkflowOutput):
    schema_version: Literal["1.0"]
    project_id: IdentifierStr
    baseline_version: IdentifierStr
    task_id: IdentifierStr
    language: Literal["zh-CN"]


class IngestSourceInput(WorkflowOutput):
    id: IdentifierStr
    type: MetadataStr
    authority_level: AuthorityLevel
    document_version: MetadataStr
    document_date: date
    applicable_scope: ScopeStr


class IngestBaselineRuleInput(WorkflowOutput):
    id: IdentifierStr
    title: MetadataStr
    content: MaterialFragment
    status: Literal["effective"]


class IngestSourceChunkInput(WorkflowOutput):
    chunk_id: IdentifierStr
    locator: ScopeStr
    text: MaterialFragment


class IngestWorkflowInput(CommonWorkflowInput):
    source: IngestSourceInput
    baseline_rules: list[IngestBaselineRuleInput] = Field(max_length=MAX_SMALL_COLLECTION_ITEMS)
    source_chunks: list[IngestSourceChunkInput] = Field(
        min_length=1,
        max_length=MAX_SMALL_COLLECTION_ITEMS,
    )


class QueryEffectiveCardInput(WorkflowOutput):
    id: IdentifierStr
    title: MetadataStr
    content: MaterialFragment
    source_citations: list[IdentifierStr] = Field(max_length=MAX_CITATION_COLLECTION_ITEMS)


class QueryNoticeInput(WorkflowOutput):
    type: Literal["candidate", "conflict"]
    id: IdentifierStr
    summary: MaterialFragment


class QueryCitationInput(WorkflowOutput):
    id: IdentifierStr
    source_id: IdentifierStr
    filename: MetadataStr
    document_version: MetadataStr
    section: ScopeStr
    excerpt: MaterialFragment
    authority_level: AuthorityLevel


class QueryWorkflowInput(CommonWorkflowInput):
    scope: Literal["effective", "effective_with_notices", "historical"]
    question: QuestionStr
    effective_cards: list[QueryEffectiveCardInput] = Field(max_length=MAX_SMALL_COLLECTION_ITEMS)
    notices: list[QueryNoticeInput] = Field(max_length=MAX_SMALL_COLLECTION_ITEMS)
    citations: list[QueryCitationInput] = Field(max_length=MAX_CITATION_COLLECTION_ITEMS)


class LintCitationInput(WorkflowOutput):
    id: IdentifierStr
    source_id: IdentifierStr
    citation_id: IdentifierStr
    document_version: MetadataStr
    page_or_section: ScopeStr
    excerpt: MaterialFragment


class LintDeterministicFindingInput(WorkflowOutput):
    id: IdentifierStr
    rule_id: IdentifierStr
    issue_type: Literal[
        "conflict",
        "omission",
        "stale",
        "not_synchronized",
        "insufficient_evidence",
    ]
    severity: IssueSeverity
    title: MetadataStr
    description: MaterialFragment
    target_identity: IdentifierStr
    locally_validated: Literal[True]


class LintWorkflowInput(CommonWorkflowInput):
    input_contract_version: Literal["2.0"]
    baseline_rules: list[LintCitationInput] = Field(max_length=MAX_CITATION_COLLECTION_ITEMS)
    comparison_items: list[LintCitationInput] = Field(max_length=MAX_CITATION_COLLECTION_ITEMS)
    deterministic_findings: list[LintDeterministicFindingInput] = Field(
        max_length=MAX_CITATION_COLLECTION_ITEMS
    )
    allowed_issue_types: list[
        Literal[
            "conflict",
            "omission",
            "stale",
            "not_synchronized",
            "insufficient_evidence",
        ]
    ] = Field(max_length=MAX_ALLOWED_ISSUE_TYPES)


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


class LintWorkflowOutput(WorkflowOutput):
    schema_version: Literal["1.0"]
    issues: list[LintIssueOutput]


class DocumentSourceFragmentInput(WorkflowOutput):
    source_id: IdentifierStr
    chunk_id: IdentifierStr
    locator: ScopeStr
    excerpt: MaterialFragment
    source_type: MetadataStr
    authority_level: AuthorityLevel


class DocumentDraftWorkflowInput(WorkflowOutput):
    schema_version: Literal["2.0"]
    task_type: Literal["document_draft"]
    project_id: IdentifierStr
    project_name: MetadataStr
    project_description: ScopeStr
    schema_headings: list[MetadataStr] = Field(min_length=1, max_length=MAX_DOCUMENT_SECTIONS)
    current_document_markdown: Annotated[
        str | None,
        StringConstraints(max_length=MAX_DOCUMENT_MARKDOWN_CHARS),
    ] = None
    source_fragments: list[DocumentSourceFragmentInput] = Field(
        min_length=1,
        max_length=MAX_DOCUMENT_SECTIONS,
    )


class SectionCitationOutput(WorkflowOutput):
    heading: MetadataStr
    source_id: IdentifierStr
    chunk_id: IdentifierStr
    locator: ScopeStr
    excerpt: MaterialFragment


class DocumentDraftWorkflowOutput(WorkflowOutput):
    schema_version: Literal["2.0"]
    task_type: Literal["document_draft"]
    document_markdown: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=20,
            max_length=MAX_DOCUMENT_MARKDOWN_CHARS,
        ),
    ]
    summary: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=MAX_DOCUMENT_SUMMARY_CHARS,
        ),
    ]
    missing_sections: list[MetadataStr] = Field(max_length=50)
    evidence_gaps: list[MetadataStr] = Field(max_length=50)
    source_ids: list[IdentifierStr] = Field(min_length=1, max_length=100)
    section_citations: list[SectionCitationOutput] = Field(
        min_length=1,
        max_length=MAX_DOCUMENT_SECTIONS,
    )


class StructureReferenceProjectInput(WorkflowOutput):
    project_id: IdentifierStr
    headings: list[MetadataStr] = Field(min_length=1, max_length=MAX_DOCUMENT_SECTIONS)


class StructureSuggestionWorkflowInput(WorkflowOutput):
    schema_version: Literal["2.0"]
    task_type: Literal["structure_suggestion"]
    project_id: IdentifierStr
    current_headings: list[MetadataStr] = Field(max_length=MAX_DOCUMENT_SECTIONS)
    reference_projects: list[StructureReferenceProjectInput] = Field(max_length=20)


class StructureSuggestionOutput(WorkflowOutput):
    title: MetadataStr
    reason: MaterialFragment
    reference_project_ids: list[IdentifierStr] = Field(max_length=20)
    confidence: float = Field(ge=0, le=1)


class StructureSuggestionWorkflowOutput(WorkflowOutput):
    schema_version: Literal["2.0"]
    task_type: Literal["structure_suggestion"]
    suggestions: list[StructureSuggestionOutput] = Field(max_length=20)
