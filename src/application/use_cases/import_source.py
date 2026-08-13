from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from src.application.dto.ingest import ImportSourceInput
from src.application.ports.repositories import (
    IngestUnitOfWork,
    KnowledgeRepository,
    ProjectRepository,
    SourceRepository,
)
from src.domain.enums import (
    CallResultMode,
    EvidenceSide,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)
from src.domain.errors import AppError, DomainError, ErrorCode, OutputValidationError
from src.domain.models import (
    EventLog,
    IngestReport,
    IngestResultView,
    IssueCard,
    IssueEvidence,
    KnowledgeCard,
    ModelCallLog,
    Relation,
    SourceRecord,
)
from src.domain.policies.security_policy import can_call_external_model
from src.domain.services.citation_validator import CitationValidator
from src.domain.services.file_safety import detect_mime_type, validate_upload
from src.infrastructure.cache.ai_cache import AiCache, CacheIdentity
from src.infrastructure.files.archive import SourceArchive
from src.infrastructure.files.extractor import ExtractedDocument
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.redactor import redact_text
from src.infrastructure.gateways._common import (
    MAX_CANONICAL_PAYLOAD_CHARS,
    MAX_OUTBOUND_COVERAGE,
    create_outbound_safety_proof,
)
from src.infrastructure.gateways.ingest_gateway import IngestGateway
from src.infrastructure.gateways.schemas import IngestWorkflowInput, IngestWorkflowOutput
from src.infrastructure.observability.event_logger import (
    AuditDurabilityUncertainError,
    EventLogger,
)
from src.infrastructure.observability.model_call_logger import ModelCallLogger


class ArchiveFactory(Protocol):
    def __call__(self, project_id: str, source_id: str) -> SourceArchive: ...


Extractor = Callable[..., ExtractedDocument]


class ImportSource:
    """Securely ingest one source without mutating the effective baseline."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        sources: SourceRepository,
        knowledge: KnowledgeRepository,
        unit_of_work: IngestUnitOfWork,
        archive_factory: ArchiveFactory,
        extractor: Extractor,
        gateway: IngestGateway,
        cache: AiCache,
        manifest_store: ManifestStore,
        model_call_logger: ModelCallLogger,
        event_logger: EventLogger | None = None,
        customer_names: Iterable[str],
        strategy_terms: Iterable[str],
        financial_terms: Iterable[str],
        leader_names: Iterable[str],
        unpublished_decisions: Iterable[str],
        now: Callable[[], datetime] | None = None,
        prompt_version: str = "ingest-v1",
        model_label: str = "dify-ingest",
        schema_version: str = "1.0",
    ) -> None:
        self.projects = projects
        self.sources = sources
        self.knowledge = knowledge
        self.unit_of_work = unit_of_work
        self.archive_factory = archive_factory
        self.extractor = extractor
        self.gateway = gateway
        self.cache = cache
        self.manifest_store = manifest_store
        self.model_call_logger = model_call_logger
        self.event_logger = event_logger or EventLogger(model_call_logger.db_path)
        self.customer_names = tuple(customer_names)
        self.strategy_terms = tuple(strategy_terms)
        self.financial_terms = tuple(financial_terms)
        self.leader_names = tuple(leader_names)
        self.unpublished_decisions = tuple(unpublished_decisions)
        self.now = now or (lambda: datetime.now(UTC))
        self.prompt_version = prompt_version
        self.model_label = model_label
        self.schema_version = schema_version

    def execute(self, command: ImportSourceInput) -> IngestReport:
        safe_name = validate_upload(command.uploaded_name, command.uploaded_bytes)
        digest = hashlib.sha256(command.uploaded_bytes).hexdigest()
        command_fingerprint = self._command_fingerprint(command, digest)
        manifest_before = self._read_manifest()
        self._require_manifest_context(command, manifest_before)
        existing = self.sources.find_by_sha256(command.project_id, digest)
        if existing is not None and existing.ingest_status == "completed":
            try:
                return self.unit_of_work.duplicate_report(existing, command_fingerprint)
            except ValueError as error:
                raise DomainError(
                    ErrorCode.SOURCE_METADATA_MISMATCH,
                    "COMPLETED_SOURCE_COMMAND_MISMATCH",
                ) from error

        source = existing or self._archive_new_source(command, safe_name, digest)
        extracted = self.extractor(Path(source.archive_path), source_id=source.id)
        redaction = redact_text(
            extracted.text,
            security_level=command.security_level,
            customer_names=self.customer_names,
            strategy_terms=self.strategy_terms,
            financial_terms=self.financial_terms,
            leader_names=self.leader_names,
            unpublished_decisions=self.unpublished_decisions,
        )
        source = source.model_copy(
            update={
                "is_redacted": command.is_redacted_confirmed and redaction.safe_for_external_model,
                "allow_external_model": (
                    command.allow_external_model
                    and command.is_redacted_confirmed
                    and command.security_level
                    in {
                        SecurityLevel.L1_PUBLIC_SIMULATED,
                        SecurityLevel.L2_INTERNAL,
                    }
                ),
                "ingest_status": "processing",
            }
        )
        self.sources.update(source)

        project = self.projects.get(command.project_id)
        effective_cards = self.knowledge.list_effective(
            command.project_id,
            command.applicable_baseline_version,
        )
        inputs = self._workflow_inputs(command, source, extracted, effective_cards)
        identity = self._cache_identity(
            command.project_id,
            digest,
            command.applicable_baseline_version,
        )
        call_id = f"CALL-{uuid4().hex.upper()}"
        correlation_id = f"CORR-{uuid4().hex.upper()}"
        authorized = can_call_external_model(project, source)
        outbound_chars = 0
        outbound_coverage = 0.0
        workflow_run_id: str | None = None
        started_at: datetime | None = None
        result_mode = self._result_mode(command.preferred_mode)
        cache_generated_at: datetime | None = None
        try:
            if command.preferred_mode == "cache":
                cache_entry = self.cache.get_with_created_at(identity)
                if cache_entry is None:
                    raise DomainError(ErrorCode.CACHE_NOT_FOUND)
                raw_result, cache_generated_at = cache_entry
                result = self._validate_result(raw_result, inputs)
            elif command.preferred_mode == "local":
                result = IngestWorkflowOutput(
                    schema_version="1.0",
                    task_id=inputs["task_id"],
                    summary="已完成本地确定性结构检查；未调用外部模型，未生成候选知识。",
                    items=[],
                    relations=[],
                )
            else:
                if not authorized:
                    raise DomainError(ErrorCode.EXTERNAL_CALL_DENIED)
                outbound_chars = self._canonical_input_chars(inputs)
                outbound_coverage = outbound_chars / len(extracted.text)
                if (
                    outbound_chars > MAX_CANONICAL_PAYLOAD_CHARS
                    or outbound_coverage > MAX_OUTBOUND_COVERAGE
                ):
                    raise DomainError(ErrorCode.OUTBOUND_COVERAGE_EXCEEDED)
                proof = create_outbound_safety_proof(
                    IngestWorkflowInput,
                    inputs,
                    security_level=source.security_level,
                    customer_names=self.customer_names,
                    strategy_terms=self.strategy_terms,
                    financial_terms=self.financial_terms,
                    leader_names=self.leader_names,
                    unpublished_decisions=self.unpublished_decisions,
                    source_total_chars=len(extracted.text),
                )
                started_at = self.now()
                self._record_model_call(
                    call_id=call_id,
                    correlation_id=correlation_id,
                    source=source,
                    mode=CallResultMode.REALTIME,
                    status="started",
                    authorized=True,
                    started_at=started_at,
                    outbound_chars=outbound_chars,
                    outbound_coverage=outbound_coverage,
                )
                gateway_result = self.gateway.run(
                    inputs,
                    safety_proof=proof,
                    user=command.project_id,
                )
                workflow_run_id = gateway_result["workflow_run_id"]
                result = self._validate_result(gateway_result["result"], inputs)
            try:
                cards, relations, issues = self._to_domain(source, effective_cards, result)
            except (KeyError, ValidationError, ValueError) as error:
                raise OutputValidationError("INGEST_DOMAIN_CONVERSION_INVALID") from error
            manifest_after = self._read_manifest()
            if manifest_after != manifest_before:
                raise DomainError(
                    ErrorCode.BASELINE_INTEGRITY_FAILED,
                    "MANIFEST_CHANGED_DURING_INGEST",
                )
        except AppError as error:
            status = "timeout" if error.code == ErrorCode.MODEL_TIMEOUT else "failed"
            if started_at is not None:
                self._record_model_call(
                    call_id=call_id,
                    correlation_id=correlation_id,
                    source=source,
                    mode=CallResultMode.REALTIME,
                    status=status,
                    authorized=True,
                    started_at=started_at,
                    workflow_run_id=workflow_run_id,
                    outbound_chars=outbound_chars,
                    outbound_coverage=outbound_coverage,
                    error_code=error.code,
                )
            security_errors = {
                ErrorCode.EXTERNAL_CALL_DENIED,
                ErrorCode.OUTBOUND_COVERAGE_EXCEEDED,
                ErrorCode.REDACTION_REQUIRED,
            }
            if started_at is None and error.code in security_errors:
                self.sources.update_ingest_status(source.id, "security_blocked")
                self._record_safe_event(
                    source,
                    "source_ingest_security_blocked",
                    error.code,
                )
            else:
                self.sources.update_ingest_status(
                    source.id,
                    "realtime_failed"
                    if error.code == ErrorCode.MODEL_TIMEOUT
                    else "validation_failed",
                )
            if started_at is None and error.code not in security_errors:
                self._record_safe_event(source, "source_ingest_failed", error.code)
            raise

        finished_at = self.now()
        if started_at is not None:
            self._record_model_call(
                call_id=call_id,
                correlation_id=correlation_id,
                source=source,
                mode=CallResultMode.REALTIME,
                status="succeeded",
                authorized=True,
                started_at=started_at,
                workflow_run_id=workflow_run_id,
                outbound_chars=outbound_chars,
                outbound_coverage=outbound_coverage,
                finished_at=finished_at,
            )
        report = IngestReport(
            source_id=source.id,
            duplicate=False,
            summary=result.summary,
            created_card_ids=[card.id for card in cards],
            created_relation_ids=[relation.id for relation in relations],
            created_issue_ids=[issue.id for issue in issues],
            candidate_count=sum(item.result_type == "candidate" for item in result.items),
            conflict_count=sum(item.result_type == "conflict_discussion" for item in result.items),
            result_mode=result_mode,
            model_call_id=call_id if started_at is not None else None,
            source_hash8=source.sha256[:8],
            cache_generated_at=cache_generated_at,
            result_items=self._result_views(result, effective_cards),
        )
        if result_mode == CallResultMode.LOCAL_ONLY:
            self.sources.update_ingest_status(source.id, "local_checked")
            try:
                self.event_logger.record(
                    EventLog(
                        id=f"EVENT-{uuid4().hex.upper()}",
                        project_id=source.project_id,
                        event_type="source_ingest_local_checked",
                        entity_type="source",
                        entity_id=source.id,
                        actor="system",
                        correlation_id=correlation_id,
                        payload={
                            "status": "local_checked",
                            "result_mode": CallResultMode.LOCAL_ONLY.value,
                        },
                        created_at=finished_at,
                    )
                )
            except AuditDurabilityUncertainError:
                report = report.model_copy(update={"audit_reconciliation_pending": True})
            return report
        event = EventLog(
            id=f"EVENT-INGEST-{source.sha256[:16].upper()}",
            project_id=source.project_id,
            event_type="source_ingest_completed",
            entity_type="source",
            entity_id=source.id,
            actor="system",
            correlation_id=correlation_id,
            payload={
                "status": "succeeded",
                "result_mode": result_mode.value,
                "created_card_ids": report.created_card_ids,
                "created_relation_ids": report.created_relation_ids,
                "created_issue_ids": report.created_issue_ids,
                "candidate_count": report.candidate_count,
                "conflict_count": report.conflict_count,
                "model_call_id": report.model_call_id,
                "command_fingerprint": command_fingerprint,
                "cache_generated_at": (
                    None
                    if report.cache_generated_at is None
                    else report.cache_generated_at.isoformat()
                ),
                "result_items": [item.model_dump(mode="json") for item in report.result_items],
            },
            created_at=finished_at,
        )
        try:
            audit_reconciliation_pending = self.unit_of_work.complete(
                source,
                cards,
                relations,
                issues,
                event,
            )
        except sqlite3.Error as error:
            self.sources.update_ingest_status(source.id, "persistence_failed")
            self._record_safe_event(
                source,
                "source_ingest_persistence_failed",
                ErrorCode.INGEST_PERSISTENCE_FAILED,
            )
            raise DomainError(
                ErrorCode.INGEST_PERSISTENCE_FAILED,
                "SQLITE_TRANSACTION_ROLLED_BACK",
            ) from error
        if audit_reconciliation_pending:
            report = report.model_copy(update={"audit_reconciliation_pending": True})
        if result_mode == CallResultMode.REALTIME:
            try:
                self.cache.put(identity, result.model_dump(mode="json"))
            except (OSError, sqlite3.Error):
                # The authoritative ingest transaction has already committed. Cache
                # population is recoverable and must not turn that success into a failure.
                pass
        return report

    def _archive_new_source(
        self,
        command: ImportSourceInput,
        safe_name: str,
        digest: str,
    ) -> SourceRecord:
        source_id = f"SRC-{digest[:16].upper()}"
        archive = self.archive_factory(command.project_id, source_id).save(
            safe_name,
            command.uploaded_bytes,
        )
        source = SourceRecord(
            id=source_id,
            project_id=command.project_id,
            original_filename=safe_name,
            archive_path=str(archive.path),
            sha256=digest,
            mime_type=detect_mime_type(command.uploaded_bytes) or "application/octet-stream",
            size_bytes=archive.size_bytes,
            source_type=command.source_type,
            authority_level=command.authority_level,
            source_department=command.source_department,
            provider=command.provider,
            document_date=command.document_date,
            document_version=command.document_version,
            applicable_baseline_version=command.applicable_baseline_version,
            security_level=command.security_level,
            is_redacted=False,
            allow_external_model=False,
            is_sandbox=command.is_sandbox,
            ingest_status="processing",
            created_at=self.now(),
        )
        self.sources.add(source)
        return source

    def _workflow_inputs(
        self,
        command: ImportSourceInput,
        source: SourceRecord,
        extracted: ExtractedDocument,
        effective_cards: list[KnowledgeCard],
    ) -> dict[str, Any]:
        inputs: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_id": command.project_id,
            "baseline_version": command.applicable_baseline_version,
            "task_id": f"INGEST-{source.sha256[:16].upper()}",
            "language": "zh-CN",
            "source": {
                "id": source.id,
                "type": source.source_type,
                "authority_level": source.authority_level.value,
                "document_version": source.document_version,
                "document_date": source.document_date.isoformat(),
                "applicable_scope": source.source_department,
            },
            "baseline_rules": [
                {
                    "id": card.id,
                    "title": card.title[:256],
                    "content": card.content[:2000],
                    "status": "effective",
                }
                for card in effective_cards[:20]
            ],
            "source_chunks": [],
        }
        selected_chunks: list[dict[str, str]] = []
        max_outbound_chars = min(
            MAX_CANONICAL_PAYLOAD_CHARS,
            int(len(extracted.text) * MAX_OUTBOUND_COVERAGE),
        )
        for chunk in extracted.chunks[:20]:
            chunk_redaction = redact_text(
                chunk.text,
                security_level=command.security_level,
                customer_names=self.customer_names,
                strategy_terms=self.strategy_terms,
                financial_terms=self.financial_terms,
                leader_names=self.leader_names,
                unpublished_decisions=self.unpublished_decisions,
            )
            candidate = {
                "chunk_id": chunk.chunk_id,
                "locator": chunk.locator[:500],
                "text": chunk_redaction.redacted_text[:2000],
            }
            proposed = [*selected_chunks, candidate]
            serialized = IngestWorkflowInput.model_validate(
                {**inputs, "source_chunks": proposed}
            ).model_dump(mode="json")
            payload_chars = len(
                json.dumps(
                    serialized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            if payload_chars <= max_outbound_chars:
                selected_chunks = proposed
        if not selected_chunks:
            first = extracted.chunks[0]
            first_redaction = redact_text(
                first.text,
                security_level=command.security_level,
                customer_names=self.customer_names,
                strategy_terms=self.strategy_terms,
                financial_terms=self.financial_terms,
                leader_names=self.leader_names,
                unpublished_decisions=self.unpublished_decisions,
            )
            selected_chunks = [
                {
                    "chunk_id": first.chunk_id,
                    "locator": first.locator[:500],
                    "text": first_redaction.redacted_text[:2000],
                }
            ]
        inputs["source_chunks"] = selected_chunks
        return inputs

    def _validate_result(
        self,
        raw_result: Mapping[str, Any],
        inputs: Mapping[str, Any],
    ) -> IngestWorkflowOutput:
        try:
            result = IngestWorkflowOutput.model_validate(raw_result)
        except ValidationError as error:
            raise OutputValidationError("INGEST_OUTPUT_INVALID") from error
        if result.schema_version != inputs["schema_version"] or result.task_id != inputs["task_id"]:
            raise OutputValidationError("INGEST_OUTPUT_CONTEXT_MISMATCH")
        source = inputs["source"]
        chunks = {chunk["chunk_id"]: chunk for chunk in inputs["source_chunks"]}
        baseline_ids = {card["id"] for card in inputs["baseline_rules"]}
        item_ids = {item.item_id for item in result.items}
        if len(item_ids) != len(result.items):
            raise OutputValidationError("DUPLICATE_INGEST_ITEM_ID")
        relations_by_source: dict[str, list[Any]] = {}
        for relation in result.relations:
            relations_by_source.setdefault(relation.source_id, []).append(relation)
        for item in result.items:
            if item.target_card_id is not None and item.target_card_id not in baseline_ids:
                raise OutputValidationError("UNKNOWN_TARGET_CARD")
            expected_status = {
                "candidate": "candidate",
                "conflict_discussion": "conflict",
                "information_gap": "ai_inferred",
            }[item.result_type]
            if item.status != expected_status:
                raise OutputValidationError("INGEST_ITEM_STATUS_MISMATCH")
            item_relations = relations_by_source.get(item.item_id, [])
            if item.result_type == "conflict_discussion":
                if (
                    item.target_card_id is None
                    or len(item_relations) != 1
                    or item_relations[0].relation_type != "conflicts_with"
                    or item_relations[0].target_id != item.target_card_id
                ):
                    raise OutputValidationError("CONFLICT_RELATION_REQUIRED")
            elif item.result_type == "candidate" and item.target_card_id is not None:
                if (
                    len(item_relations) != 1
                    or item_relations[0].relation_type != "proposes_change_to"
                    or item_relations[0].target_id != item.target_card_id
                ):
                    raise OutputValidationError("CANDIDATE_RELATION_REQUIRED")
            elif item.result_type == "candidate" and item_relations:
                raise OutputValidationError("CANDIDATE_RELATION_INVALID")
            elif item.result_type == "information_gap" and (
                item.target_card_id is not None or item_relations
            ):
                raise OutputValidationError("INFORMATION_GAP_RELATION_INVALID")
            for citation in item.source_citations:
                chunk = chunks.get(citation.chunk_id)
                if (
                    citation.source_id != source["id"]
                    or chunk is None
                    or citation.locator != chunk["locator"]
                ):
                    raise OutputValidationError("UNKNOWN_CITATION")
                validator = CitationValidator(
                    [
                        {
                            "id": citation.chunk_id,
                            "source_id": citation.source_id,
                            "excerpt": chunk["text"],
                        }
                    ]
                )
                if not validator.has_direct_support(citation.excerpt, {"excerpt": chunk["text"]}):
                    raise OutputValidationError("CITATION_METADATA_MISMATCH")
        for relation in result.relations:
            if relation.source_id not in item_ids:
                raise OutputValidationError("UNKNOWN_RELATION_SOURCE")
            if relation.target_id not in baseline_ids | item_ids:
                raise OutputValidationError("UNKNOWN_RELATION_TARGET")
        return result

    def _to_domain(
        self,
        source: SourceRecord,
        effective_cards: list[KnowledgeCard],
        result: IngestWorkflowOutput,
    ) -> tuple[list[KnowledgeCard], list[Relation], list[IssueCard]]:
        now = self.now()
        baseline_by_id = {card.id: card for card in effective_cards}
        card_ids = {
            item.item_id: f"CARD-{_stable_id(source.id, item.item_id)}" for item in result.items
        }
        cards: list[KnowledgeCard] = []
        issues: list[IssueCard] = []
        for item in result.items:
            status = {
                "conflict": KnowledgeStatus.CONFLICT,
                "candidate": KnowledgeStatus.CANDIDATE,
                "ai_inferred": KnowledgeStatus.AI_INFERRED,
            }[item.status]
            cards.append(
                KnowledgeCard(
                    id=card_ids[item.item_id],
                    project_id=source.project_id,
                    card_type=item.item_type,
                    title=item.title,
                    content=item.content,
                    status=status,
                    product_version=source.applicable_baseline_version,
                    applicable_scope=source.source_department,
                    source_refs=[
                        f"{citation.source_id}:{citation.chunk_id}"
                        for citation in item.source_citations
                    ],
                    authority_level=source.authority_level,
                    owner=source.source_department,
                    confidence=item.confidence,
                    created_at=now,
                    updated_at=now,
                )
            )
            if item.result_type == "conflict_discussion":
                target = baseline_by_id[item.target_card_id or ""]
                citation = item.source_citations[0]
                issues.append(
                    IssueCard(
                        id=f"ISSUE-{_stable_id(source.id, item.item_id)}",
                        project_id=source.project_id,
                        issue_type="conflict",
                        severity=IssueSeverity.PENDING_DECISION,
                        status=IssueStatus.OPEN,
                        title=item.title,
                        description=item.content,
                        target_rule_id=item.target_card_id,
                        evidence=[
                            IssueEvidence(
                                source_id=target.source_refs[0],
                                citation_id=target.source_refs[0],
                                excerpt=target.content,
                                document_version=target.product_version,
                                page_or_section=target.title,
                                side=EvidenceSide.CURRENT_BASELINE,
                            ),
                            IssueEvidence(
                                source_id=source.id,
                                citation_id=citation.chunk_id,
                                excerpt=citation.excerpt,
                                document_version=source.document_version,
                                page_or_section=citation.locator,
                                side=EvidenceSide.CHALLENGING_SOURCE,
                            ),
                        ],
                        impacted_domains=[source.source_department],
                        options=[
                            {"code": "keep", "label": "维持当前规则"},
                            {"code": "change", "label": "接受候选意见"},
                        ],
                        ai_recommendation=None,
                        ai_confidence=item.confidence,
                        uncertainty=item.uncertainty,
                        owner=None,
                        due_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif item.result_type == "information_gap":
                issues.append(
                    IssueCard(
                        id=f"ISSUE-{_stable_id(source.id, item.item_id)}",
                        project_id=source.project_id,
                        issue_type="information_gap",
                        severity=IssueSeverity.PENDING_INFO,
                        status=IssueStatus.OPEN,
                        title=item.title,
                        description=item.content,
                        evidence=[],
                        impacted_domains=[source.source_department],
                        options=[{"code": "supplement", "label": "补充材料"}],
                        ai_recommendation=None,
                        ai_confidence=item.confidence,
                        uncertainty=item.uncertainty or "需要补充信息",
                        owner=None,
                        due_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
        relations = [
            Relation(
                id=f"REL-{_stable_id(source.id, str(index), relation.source_id)}",
                project_id=source.project_id,
                source_id=card_ids[relation.source_id],
                relation_type=relation.relation_type,
                target_id=card_ids.get(relation.target_id, relation.target_id),
                source_ref=source.id,
                created_at=now,
            )
            for index, relation in enumerate(result.relations, start=1)
        ]
        card_id_set = {card.id for card in cards}
        relations.extend(
            Relation(
                id=f"REL-{_stable_id(source.id, 'derived_from', card_id)}",
                project_id=source.project_id,
                source_id=source.id,
                relation_type="derived_from",
                target_id=card_id,
                source_ref=source.id,
                created_at=now,
            )
            for card_id in sorted(card_id_set)
        )
        # 与 run_lint 同约定：冲突问题必须回连目标规则卡，否则发布后
        # 追溯主链无法从规则卡走到新决定/变更单/基线（六节点主链只承认持久化 Relation）。
        relations.extend(
            Relation(
                id=f"REL-{issue.target_rule_id}-CONFLICTS-WITH-{issue.id}",
                project_id=source.project_id,
                source_id=issue.target_rule_id or "",
                relation_type="conflicts_with",
                target_id=issue.id,
                source_ref=source.id,
                created_at=now,
            )
            for issue in issues
            if issue.target_rule_id
        )
        return cards, relations, issues

    def _cache_identity(
        self,
        project_id: str,
        digest: str,
        baseline_version: str,
    ) -> CacheIdentity:
        return CacheIdentity(
            project_id=project_id,
            task_type="ingest",
            source_sha256=digest,
            baseline_version=baseline_version,
            prompt_version=self.prompt_version,
            model_label=self.model_label,
            schema_version=self.schema_version,
        )

    @staticmethod
    def _result_views(
        result: IngestWorkflowOutput,
        effective_cards: list[KnowledgeCard],
    ) -> list[IngestResultView]:
        baseline_by_id = {card.id: card for card in effective_cards}
        views: list[IngestResultView] = []
        for item in result.items:
            citation = item.source_citations[0]
            target = baseline_by_id.get(item.target_card_id or "")
            views.append(
                IngestResultView(
                    item_type=item.item_type,
                    summary=item.content,
                    section=target.title if target is not None else citation.locator,
                    citation=citation.excerpt,
                    status=item.status,
                )
            )
        return views

    def _read_manifest(self):
        try:
            return self.manifest_store.read_and_validate()
        except ValueError as error:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "MANIFEST_READ_FAILED",
            ) from error

    @staticmethod
    def _require_manifest_context(command: ImportSourceInput, manifest: Any) -> None:
        if (
            manifest.project_id != command.project_id
            or manifest.current_version != command.applicable_baseline_version
        ):
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "BASELINE_CONTEXT_MISMATCH",
            )

    @staticmethod
    def _result_mode(preferred_mode: str) -> CallResultMode:
        return {
            "realtime": CallResultMode.REALTIME,
            "cache": CallResultMode.CACHE,
            "local": CallResultMode.LOCAL_ONLY,
        }[preferred_mode]

    @staticmethod
    def _command_fingerprint(command: ImportSourceInput, digest: str) -> str:
        metadata = {
            "sha256": digest,
            "project_id": command.project_id,
            "uploaded_name": command.uploaded_name,
            "source_type": command.source_type,
            "authority_level": command.authority_level.value,
            "source_department": command.source_department,
            "provider": command.provider,
            "document_date": command.document_date.isoformat(),
            "document_version": command.document_version,
            "applicable_baseline_version": command.applicable_baseline_version,
            "security_level": command.security_level.value,
            "is_redacted_confirmed": command.is_redacted_confirmed,
            "allow_external_model": command.allow_external_model,
            "is_sandbox": command.is_sandbox,
        }
        canonical = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_input_chars(inputs: Mapping[str, Any]) -> int:
        serialized = IngestWorkflowInput.model_validate(inputs).model_dump(mode="json")
        return len(
            json.dumps(
                serialized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    def _record_safe_event(
        self,
        source: SourceRecord,
        event_type: str,
        error_code: ErrorCode | str,
    ) -> None:
        try:
            self.event_logger.record(
                EventLog(
                    id=f"EVENT-{uuid4().hex.upper()}",
                    project_id=source.project_id,
                    event_type=event_type,
                    entity_type="source",
                    entity_id=source.id,
                    actor="system",
                    correlation_id=f"CORR-{uuid4().hex.upper()}",
                    payload={
                        "status": "blocked" if "security" in event_type else "failed",
                        "error_code": str(error_code),
                    },
                    created_at=self.now(),
                ),
                level="WARNING",
            )
        except AuditDurabilityUncertainError:
            pass

    def _record_model_call(
        self,
        *,
        call_id: str,
        correlation_id: str,
        source: SourceRecord,
        mode: CallResultMode,
        status: str,
        authorized: bool,
        started_at: datetime,
        outbound_chars: int,
        outbound_coverage: float,
        workflow_run_id: str | None = None,
        finished_at: datetime | None = None,
        error_code: str | None = None,
    ) -> None:
        terminal = status != "started"
        completed_at = finished_at or (self.now() if terminal else None)
        elapsed_ms = (
            max(0, int((completed_at - started_at).total_seconds() * 1000))
            if completed_at is not None
            else None
        )
        self.model_call_logger.record(
            ModelCallLog(
                id=call_id,
                project_id=source.project_id,
                task_type="ingest",
                workflow_run_id=workflow_run_id,
                correlation_id=correlation_id,
                source_ids=[source.id],
                baseline_version=source.applicable_baseline_version,
                model_label=self.model_label,
                prompt_version=self.prompt_version,
                schema_version=self.schema_version,
                authorized=authorized,
                redacted=source.is_redacted,
                outbound_chars=outbound_chars,
                outbound_coverage=outbound_coverage,
                result_mode=mode,
                status=status,
                started_at=started_at,
                finished_at=completed_at,
                elapsed_ms=elapsed_ms,
                error_code=error_code,
            )
        )


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16].upper()
