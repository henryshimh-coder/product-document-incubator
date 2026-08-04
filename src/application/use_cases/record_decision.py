from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from src.application.dto.decision import RecordDecisionInput
from src.application.ports.dashboard import ManifestReader
from src.application.ports.repositories import (
    DecisionUnitOfWork,
    IssueRepository,
    KnowledgeRepository,
)
from src.application.use_cases.create_change_request import CreateChangeRequest
from src.domain.enums import DecisionAction, IssueStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import Decision, DecisionResult


class RecordDecision:
    def __init__(
        self,
        *,
        issues: IssueRepository,
        manifest: ManifestReader,
        knowledge: KnowledgeRepository,
        unit_of_work: DecisionUnitOfWork,
        now: Callable[[], datetime],
        decision_id_factory: Callable[[], str] | None = None,
        change_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.issues = issues
        self.unit_of_work = unit_of_work
        self.now = now
        self.decision_id_factory = decision_id_factory or (
            lambda: f"DECISION-{uuid4().hex.upper()}"
        )
        self.change_factory = CreateChangeRequest(
            id_factory=change_id_factory or (lambda: f"CHANGE-{uuid4().hex.upper()}"),
            now=now,
            manifest=manifest,
            knowledge=knowledge,
        )

    def execute(self, command: RecordDecisionInput) -> DecisionResult:
        self._validate(command)
        issue = self.issues.get(command.issue_id or "")
        created_at = self.now()
        decision = Decision(
            id=self.decision_id_factory(),
            project_id=issue.project_id,
            issue_id=issue.id,
            action=command.action,
            conclusion=command.conclusion or "",
            confirmed_by=command.confirmed_by or "",
            responsible_party=command.responsible_party,
            due_at=command.due_at,
            verification_condition=command.verification_condition,
            created_at=created_at,
        )
        change = None
        if command.action == DecisionAction.ACCEPT_CHANGE:
            if command.change_request is None:
                raise DomainError(ErrorCode.CHANGE_FIELDS_REQUIRED)
            change = self.change_factory.build(
                command.change_request,
                issue=issue,
                decision=decision,
            )
        return self.unit_of_work.record(
            decision=decision,
            idempotency_key=command.idempotency_key or "",
            command_fingerprint=_fingerprint(command),
            issue_status=_issue_status(command.action),
            issue_updated_at=created_at,
            change_request=change,
        )

    @staticmethod
    def _validate(command: RecordDecisionInput) -> None:
        if not (command.issue_id or "").strip() or not (command.idempotency_key or "").strip():
            raise DomainError(ErrorCode.DECISION_FIELDS_REQUIRED)
        if not (command.conclusion or "").strip() or not (command.confirmed_by or "").strip():
            raise DomainError(ErrorCode.DECISION_FIELDS_REQUIRED)
        if command.action == DecisionAction.ACCEPT_CHANGE and (
            command.responsible_party is None
            or not command.responsible_party.strip()
            or command.verification_condition is None
            or not command.verification_condition.strip()
        ):
            raise DomainError(ErrorCode.DECISION_FIELDS_REQUIRED)
        if command.action == DecisionAction.DEFER and command.due_at is None:
            raise DomainError(ErrorCode.DECISION_FIELDS_REQUIRED)
        if (
            command.action == DecisionAction.FALSE_POSITIVE
            and len((command.conclusion or "").strip()) < 10
        ):
            raise DomainError(ErrorCode.DECISION_FIELDS_REQUIRED)
        if command.action != DecisionAction.ACCEPT_CHANGE and command.change_request is not None:
            raise DomainError(ErrorCode.DECISION_FIELDS_REQUIRED)


def _issue_status(action: DecisionAction) -> IssueStatus:
    return {
        DecisionAction.ACCEPT_CHANGE: IssueStatus.DECIDED,
        DecisionAction.KEEP_CURRENT: IssueStatus.CLOSED,
        DecisionAction.DEFER: IssueStatus.DEFERRED,
        DecisionAction.FALSE_POSITIVE: IssueStatus.FALSE_POSITIVE,
    }[action]


def _fingerprint(command: RecordDecisionInput) -> str:
    payload = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
