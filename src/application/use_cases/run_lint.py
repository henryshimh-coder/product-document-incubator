from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from src.application.dto.lint import LintComparisonPackage, RunLintInput
from src.application.ports.dashboard import ManifestReader
from src.application.ports.repositories import (
    IssueRepository,
    KnowledgeRepository,
    ProjectRepository,
    SourceRepository,
)
from src.domain.enums import (
    BaselineStatus,
    CallResultMode,
    EvidenceSide,
    IssueSeverity,
    IssueStatus,
    SecurityLevel,
)
from src.domain.errors import DomainError, ErrorCode, OutputValidationError
from src.domain.models import Baseline, IssueCard, IssueEvidence, LintReport
from src.domain.policies.security_policy import can_call_external_model
from src.domain.services.deterministic_lint import DeterministicFinding, run_rule
from src.infrastructure.files.markdown_store import MarkdownStore
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader
from src.infrastructure.gateways._common import create_outbound_safety_proof
from src.infrastructure.gateways.schemas import LintIssueOutput, LintWorkflowInput


class LocalLint(Protocol):
    def run(self, command: RunLintInput) -> Sequence[DeterministicFinding | IssueCard]: ...


class ComparisonBuilder(Protocol):
    def build_minimum(
        self,
        command: RunLintInput,
        deterministic: Sequence[DeterministicFinding | IssueCard],
    ) -> LintComparisonPackage: ...


class LintWorkflowGateway(Protocol):
    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        safety_proof: Any,
        user: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]: ...


class LintIssueValidator:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self.now = now or (lambda: datetime.now(UTC))

    def validate_issue(
        self,
        payload: Mapping[str, Any] | LintIssueOutput,
        *,
        project_id: str = "LLD",
        target_rule_id: str | None = None,
    ) -> IssueCard:
        try:
            issue = (
                payload
                if isinstance(payload, LintIssueOutput)
                else LintIssueOutput.model_validate(payload)
            )
        except ValidationError as error:
            raise OutputValidationError("LINT_DOMAIN_CONVERSION_INVALID") from error
        severity = issue.severity
        validation_note = None
        uncertainty = issue.uncertainty
        if severity in {IssueSeverity.BLOCKING, IssueSeverity.PENDING_DECISION}:
            sides = {item.side for item in issue.evidence}
            sources = {item.source_id for item in issue.evidence}
            if (
                sides
                != {
                    EvidenceSide.CURRENT_BASELINE,
                    EvidenceSide.CHALLENGING_SOURCE,
                }
                or len(sources) < 2
            ):
                severity = IssueSeverity.PENDING_INFO
                validation_note = "缺少对方依据"
                uncertainty = "缺少对方依据"
        if severity == IssueSeverity.PENDING_INFO and not uncertainty:
            uncertainty = "需要补充信息"
        evidence = [IssueEvidence.model_validate(item.model_dump()) for item in issue.evidence]
        fingerprint = issue_fingerprint(
            issue_type=issue.issue_type,
            evidence=evidence,
            impacted_domains=list(issue.impacted_domains),
            target_rule_id=target_rule_id,
        )
        timestamp = self.now()
        return IssueCard(
            id=f"ISSUE-{fingerprint[:20].upper()}",
            project_id=project_id,
            issue_type=issue.issue_type,
            severity=severity,
            status=IssueStatus.OPEN,
            title=issue.title,
            description=issue.description,
            evidence=evidence,
            impacted_domains=list(issue.impacted_domains),
            options=[option.model_dump() for option in issue.options],
            ai_recommendation=issue.ai_recommendation,
            ai_confidence=issue.ai_confidence,
            uncertainty=uncertainty,
            validation_note=validation_note,
            fingerprint=fingerprint,
            target_rule_id=target_rule_id,
            owner=None,
            due_at=None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def validate_finding(self, finding: DeterministicFinding, *, project_id: str) -> IssueCard:
        fingerprint = issue_fingerprint(
            issue_type=finding.issue_type,
            evidence=finding.evidence,
            impacted_domains=finding.impacted_domains,
            target_rule_id=finding.target_rule_id or finding.rule_id,
        )
        timestamp = self.now()
        return IssueCard(
            id=f"ISSUE-{fingerprint[:20].upper()}",
            project_id=project_id,
            issue_type=finding.issue_type,
            severity=finding.severity,
            status=IssueStatus.OPEN,
            title=finding.title,
            description=finding.description,
            evidence=finding.evidence,
            impacted_domains=finding.impacted_domains,
            options=[],
            ai_recommendation=None,
            ai_confidence=None,
            uncertainty=finding.uncertainty,
            validation_note=None,
            fingerprint=fingerprint,
            target_rule_id=finding.target_rule_id or finding.rule_id,
            owner=None,
            due_at=None,
            created_at=timestamp,
            updated_at=timestamp,
        )


class RunLint:
    def __init__(
        self,
        *,
        local_lint: LocalLint,
        comparison_builder: ComparisonBuilder,
        gateway: LintWorkflowGateway,
        issues: IssueRepository,
        customer_names: Iterable[str],
        strategy_terms: Iterable[str],
        financial_terms: Iterable[str],
        leader_names: Iterable[str],
        unpublished_decisions: Iterable[str],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.local_lint = local_lint
        self.comparison_builder = comparison_builder
        self.gateway = gateway
        self.issues = issues
        self.customer_names = tuple(customer_names)
        self.strategy_terms = tuple(strategy_terms)
        self.financial_terms = tuple(financial_terms)
        self.leader_names = tuple(leader_names)
        self.unpublished_decisions = tuple(unpublished_decisions)
        self.validator = LintIssueValidator(now=now)

    def execute(self, command: RunLintInput) -> LintReport:
        if command.scope == "current_plus_source" and not (command.source_id or "").strip():
            raise DomainError(ErrorCode.LINT_SOURCE_REQUIRED)
        deterministic = list(self.local_lint.run(command))
        comparison = self.comparison_builder.build_minimum(command, deterministic)
        proof = create_outbound_safety_proof(
            LintWorkflowInput,
            comparison.inputs,
            security_level=comparison.security_level,
            customer_names=self.customer_names,
            strategy_terms=self.strategy_terms,
            financial_terms=self.financial_terms,
            leader_names=self.leader_names,
            unpublished_decisions=self.unpublished_decisions,
            source_total_chars=comparison.source_total_chars,
        )
        semantic_result = self.gateway.run(
            comparison.inputs,
            safety_proof=proof,
            user=command.project_id,
        )
        try:
            raw_issues = semantic_result["result"]["issues"]
            workflow_run_id = semantic_result["workflow_run_id"]
        except (KeyError, TypeError) as error:
            raise OutputValidationError("LINT_RESULT_INVALID") from error
        citation_targets = {
            item["citation_id"]: item["id"] for item in comparison.inputs.get("baseline_rules", [])
        }
        validated: list[IssueCard] = []
        for item in deterministic:
            if isinstance(item, IssueCard):
                validated.append(item)
            else:
                validated.append(
                    self.validator.validate_finding(item, project_id=command.project_id)
                )
        for payload in raw_issues:
            target_rule_id = next(
                (
                    citation_targets.get(evidence.get("citation_id"))
                    for evidence in payload.get("evidence", [])
                    if evidence.get("side") == EvidenceSide.CURRENT_BASELINE.value
                ),
                None,
            )
            validated.append(
                self.validator.validate_issue(
                    payload,
                    project_id=command.project_id,
                    target_rule_id=target_rule_id,
                )
            )
        deduplicated = _deduplicate(validated)
        self.issues.upsert_all(deduplicated)
        return LintReport(
            issues=deduplicated,
            deterministic_count=len(deterministic),
            semantic_count=len(raw_issues),
            result_mode=CallResultMode.REALTIME,
            model_call_id=workflow_run_id,
        )

    def list_open(self, project_id: str) -> list[IssueCard]:
        return self.issues.list_open(project_id)


class DeterministicLintRunner:
    def __init__(
        self,
        *,
        manifest: ManifestReader,
        card_store: MarkdownStore,
    ) -> None:
        self.manifest = manifest
        self.card_store = card_store

    def run(self, command: RunLintInput) -> list[DeterministicFinding]:
        snapshot = self.manifest.read_snapshot()
        manifest = snapshot.manifest
        if manifest.project_id != command.project_id:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "LINT_PROJECT_MISMATCH")
        if self.card_store.sha256_for(manifest.card_snapshot_path) != manifest.card_snapshot_sha256:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "LINT_CARD_SNAPSHOT_HASH_MISMATCH",
            )
        baseline = Baseline(
            id=manifest.current_baseline_id,
            project_id=manifest.project_id,
            version=manifest.current_version,
            parent_baseline_id=manifest.parent_baseline_id,
            status=BaselineStatus.EFFECTIVE,
            full_document_path=manifest.full_document_path,
            card_snapshot_path=manifest.card_snapshot_path,
            manifest_sha256=snapshot.sha256,
            change_request_id=manifest.change_request_id,
            approved_by=manifest.approved_by,
            effective_at=manifest.published_at,
            created_at=manifest.published_at,
        )
        findings: list[DeterministicFinding] = []
        for card in self.card_store.read_cards(manifest.card_snapshot_path):
            for rule_id in ("GOV-001", "STR-001", "VER-001", "VER-002", "MKT-001"):
                finding = run_rule(rule_id, card=card, baseline=baseline)
                if finding is not None:
                    findings.append(finding)
        return findings


class SafeLintComparisonBuilder:
    def __init__(
        self,
        *,
        manifest: ManifestReader,
        projects: ProjectRepository,
        knowledge: KnowledgeRepository,
        sources: SourceRepository,
        card_store: MarkdownStore,
        material_reader: LocalQueryMaterialReader,
        task_id_factory: Callable[[], str] | None = None,
        schema_version: str = "1.0",
    ) -> None:
        self.manifest = manifest
        self.projects = projects
        self.knowledge = knowledge
        self.sources = sources
        self.card_store = card_store
        self.material_reader = material_reader
        self.task_id_factory = task_id_factory or (lambda: f"TASK-LINT-{uuid4().hex.upper()}")
        self.schema_version = schema_version

    def build_minimum(
        self,
        command: RunLintInput,
        deterministic: Sequence[DeterministicFinding | IssueCard],
    ) -> LintComparisonPackage:
        snapshot = self.manifest.read_snapshot()
        manifest = snapshot.manifest
        project = self.projects.get(command.project_id)
        if manifest.project_id != command.project_id:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "LINT_PROJECT_MISMATCH")
        if not project.allow_external_model:
            raise DomainError(ErrorCode.EXTERNAL_CALL_DENIED, "LINT_PROJECT_NOT_AUTHORIZED")
        baseline_material = self.material_reader.read_baseline(
            project_id=command.project_id,
            asset_id=manifest.current_baseline_id,
            version=manifest.current_version,
            relative_path=manifest.full_document_path,
            expected_sha256=manifest.full_document_sha256,
        )
        if self.card_store.sha256_for(manifest.card_snapshot_path) != manifest.card_snapshot_sha256:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "LINT_CARD_SNAPSHOT_HASH_MISMATCH",
            )
        baseline_cards = self.card_store.read_cards(manifest.card_snapshot_path)
        baseline_rules: list[dict[str, str]] = []
        for index, card in enumerate(baseline_cards[:50], start=1):
            fragment = next(
                (item for item in baseline_material.fragments if card.content in item.text),
                None,
            )
            if fragment is None:
                raise DomainError(
                    ErrorCode.CITATION_INVALID,
                    f"LINT_BASELINE_TEXT_MISMATCH:{card.id}",
                )
            baseline_rules.append(
                {
                    "id": card.id,
                    "source_id": manifest.current_baseline_id,
                    "citation_id": f"CIT-BASE-{index:03d}",
                    "document_version": manifest.current_version,
                    "page_or_section": fragment.locator,
                    "excerpt": card.content,
                }
            )

        notices = self.knowledge.list_notices(command.project_id, manifest.current_version)
        if command.scope == "current":
            notices = []
        elif command.scope == "current_plus_source":
            source_id = command.source_id or ""
            notices = [
                card
                for card in notices
                if any(ref.partition(":")[0] == source_id for ref in card.source_refs)
            ]
        comparison_items: list[dict[str, str]] = []
        materials = [baseline_material]
        for index, card in enumerate(notices[:50], start=1):
            source_id = card.source_refs[0].partition(":")[0] if card.source_refs else ""
            try:
                source = self.sources.get(source_id)
            except KeyError as error:
                raise DomainError(ErrorCode.CITATION_INVALID, "LINT_SOURCE_NOT_FOUND") from error
            if (
                source.project_id != project.id
                or source.applicable_baseline_version != manifest.current_version
                or source.ingest_status != "completed"
                or not can_call_external_model(project, source)
            ):
                raise DomainError(
                    ErrorCode.EXTERNAL_CALL_DENIED,
                    f"LINT_SOURCE_NOT_AUTHORIZED:{source.id}",
                )
            material = self.material_reader.read_source(source)
            fragment = next(
                (item for item in material.fragments if card.content in item.text),
                None,
            )
            if fragment is None:
                raise DomainError(
                    ErrorCode.CITATION_INVALID,
                    f"LINT_COMPARISON_TEXT_MISMATCH:{card.id}",
                )
            materials.append(material)
            comparison_items.append(
                {
                    "id": card.id,
                    "source_id": source.id,
                    "citation_id": f"CIT-COMPARE-{index:03d}",
                    "document_version": source.document_version,
                    "page_or_section": fragment.locator,
                    "excerpt": card.content,
                }
            )
        deterministic_inputs = []
        for item in deterministic:
            if not isinstance(item, DeterministicFinding):
                continue
            for evidence in item.evidence:
                deterministic_inputs.append(
                    {
                        "id": item.rule_id,
                        "source_id": evidence.source_id,
                        "citation_id": evidence.citation_id,
                        "document_version": evidence.document_version,
                        "page_or_section": evidence.page_or_section,
                        "excerpt": evidence.excerpt,
                        "side": evidence.side.value,
                    }
                )
        inputs = LintWorkflowInput(
            schema_version=self.schema_version,
            project_id=command.project_id,
            baseline_version=manifest.current_version,
            task_id=self.task_id_factory(),
            language="zh-CN",
            baseline_rules=baseline_rules,
            comparison_items=comparison_items,
            deterministic_findings=deterministic_inputs[:50],
            allowed_issue_types=[
                "conflict",
                "omission",
                "stale",
                "not_synchronized",
                "insufficient_evidence",
            ],
        ).model_dump(mode="json")
        security_level = (
            SecurityLevel.L2_INTERNAL
            if any(item.security_level == SecurityLevel.L2_INTERNAL for item in materials)
            else SecurityLevel.L1_PUBLIC_SIMULATED
        )
        return LintComparisonPackage(
            inputs=inputs,
            source_total_chars=self.material_reader.total_chars(materials),
            security_level=security_level,
        )


def issue_fingerprint(
    *,
    issue_type: str,
    evidence: Sequence[IssueEvidence],
    impacted_domains: Sequence[str],
    target_rule_id: str | None,
) -> str:
    normalized = "\n".join(
        (
            issue_type.strip().casefold(),
            "|".join(sorted(item.citation_id for item in evidence)),
            "|".join(sorted(domain.strip().casefold() for domain in impacted_domains)),
            (target_rule_id or "").strip().casefold(),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _deduplicate(issues: Sequence[IssueCard]) -> list[IssueCard]:
    by_fingerprint: dict[str, IssueCard] = {}
    for issue in issues:
        key = issue.fingerprint or issue.id
        by_fingerprint[key] = issue
    return list(by_fingerprint.values())
