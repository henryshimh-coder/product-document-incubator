from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import DecisionAction


class DecisionDto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CreateChangeRequestInput(DecisionDto):
    target_card_id: str | None = None
    before_content: str | None = None
    after_content: str | None = None
    rationale: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    impacted_objects: list[str] = Field(default_factory=list)
    responsible_domain: str | None = None
    required_approver_role: str | None = None
    demo_confirmer: str | None = None
    target_version: str | None = None
    effective_condition: str | None = None


class RecordDecisionInput(DecisionDto):
    issue_id: str | None = None
    action: DecisionAction
    conclusion: str | None = None
    confirmed_by: str | None = None
    responsible_party: str | None = None
    due_at: datetime | None = None
    verification_condition: str | None = None
    idempotency_key: str | None = None
    change_request: CreateChangeRequestInput | None = None
