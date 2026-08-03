from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from src.application.dto.query import RunQueryInput
from src.application.ports.dashboard import ManifestReader
from src.application.ports.repositories import (
    BaselineRepository,
    KnowledgeRepository,
    SourceRepository,
)
from src.domain.enums import BaselineStatus, KnowledgeStatus, SecurityLevel
from src.domain.errors import DomainError, ErrorCode, OutputValidationError
from src.domain.models import Citation, KnowledgeCard, QueryResponse, SourceRecord
from src.domain.services.citation_validator import CitationValidator
from src.infrastructure.gateways._common import create_outbound_safety_proof
from src.infrastructure.gateways.schemas import QueryWorkflowInput

INSUFFICIENT_EVIDENCE_ANSWER = "现有材料不足以支持确定结论。请补充资料或查看相关引用。"


class QueryWorkflowGateway(Protocol):
    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        safety_proof: Any,
        user: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]: ...


class QueryMaterialReader(Protocol):
    def total_chars(self, baseline_path: str, sources: list[SourceRecord]) -> int: ...


class RunQuery:
    def __init__(
        self,
        *,
        manifest: ManifestReader,
        baselines: BaselineRepository,
        knowledge: KnowledgeRepository,
        sources: SourceRepository,
        material_reader: QueryMaterialReader,
        gateway: QueryWorkflowGateway,
        customer_names: Iterable[str],
        strategy_terms: Iterable[str],
        financial_terms: Iterable[str],
        leader_names: Iterable[str],
        unpublished_decisions: Iterable[str],
        task_id_factory: Callable[[], str] | None = None,
        schema_version: str = "1.0",
    ) -> None:
        self.manifest = manifest
        self.baselines = baselines
        self.knowledge = knowledge
        self.sources = sources
        self.material_reader = material_reader
        self.gateway = gateway
        self.customer_names = tuple(customer_names)
        self.strategy_terms = tuple(strategy_terms)
        self.financial_terms = tuple(financial_terms)
        self.leader_names = tuple(leader_names)
        self.unpublished_decisions = tuple(unpublished_decisions)
        self.task_id_factory = task_id_factory or (lambda: f"TASK-QUERY-{uuid4().hex.upper()}")
        self.schema_version = schema_version

    def list_historical_versions(self, project_id: str) -> tuple[str, ...]:
        manifest = self.manifest.read_snapshot().manifest
        if manifest.project_id != project_id:
            raise ValueError("query project does not match baseline manifest project")
        return tuple(
            baseline.version
            for baseline in self.baselines.list_for_project(project_id)
            if baseline.status == BaselineStatus.SUPERSEDED
            and baseline.version != manifest.current_version
        )

    def execute(self, command: RunQueryInput) -> QueryResponse:
        version, baseline_path = self._resolve_scope(command)
        cards = self._effective_cards(command.project_id, version)
        notice_cards = self._notice_cards(command, version)
        notices = self._notices(notice_cards)
        effective_cards, citations, source_records = self._trusted_evidence(
            cards,
            baseline_path=baseline_path,
            version=version,
        )
        source_records = self._include_notice_sources(source_records, notice_cards)
        inputs = QueryWorkflowInput(
            schema_version=self.schema_version,
            project_id=command.project_id,
            baseline_version=version,
            task_id=self.task_id_factory(),
            language="zh-CN",
            scope=command.scope,
            question=command.question,
            effective_cards=effective_cards,
            notices=notices,
            citations=citations,
        ).model_dump(mode="json")
        source_total_chars = self.material_reader.total_chars(baseline_path, source_records)
        proof = create_outbound_safety_proof(
            QueryWorkflowInput,
            inputs,
            security_level=SecurityLevel.L2_INTERNAL,
            customer_names=self.customer_names,
            strategy_terms=self.strategy_terms,
            financial_terms=self.financial_terms,
            leader_names=self.leader_names,
            unpublished_decisions=self.unpublished_decisions,
            source_total_chars=source_total_chars,
        )
        gateway_result = self.gateway.run(inputs, safety_proof=proof, user=command.project_id)
        return self._validate_response(
            gateway_result,
            version=version,
            cards=cards,
            citations=citations,
            notices=notices,
        )

    def _resolve_scope(self, command: RunQueryInput) -> tuple[str, str]:
        if command.scope == "historical":
            if command.historical_version is None:
                raise DomainError(ErrorCode.HISTORICAL_VERSION_REQUIRED)
            baseline = self.baselines.get_by_version(
                command.project_id,
                command.historical_version,
            )
            return baseline.version, baseline.full_document_path
        snapshot = self.manifest.read_snapshot()
        manifest = snapshot.manifest
        if manifest.project_id != command.project_id:
            raise ValueError("query project does not match baseline manifest project")
        return manifest.current_version, manifest.full_document_path

    def _effective_cards(self, project_id: str, version: str) -> list[KnowledgeCard]:
        return [
            card
            for card in self.knowledge.list_effective(project_id, version)
            if card.project_id == project_id
            and card.product_version == version
            and card.status == KnowledgeStatus.EFFECTIVE
        ][:20]

    def _notice_cards(self, command: RunQueryInput, version: str) -> list[KnowledgeCard]:
        if command.scope != "effective_with_notices":
            return []
        notice_cards = [
            card
            for card in self.knowledge.list_notices(command.project_id, version)
            if card.project_id == command.project_id
            and card.product_version == version
            and card.status in {KnowledgeStatus.CANDIDATE, KnowledgeStatus.CONFLICT}
        ]
        order = {KnowledgeStatus.CANDIDATE: 0, KnowledgeStatus.CONFLICT: 1}
        notice_cards.sort(key=lambda card: (order[card.status], card.id))
        return notice_cards[:20]

    @staticmethod
    def _notices(notice_cards: list[KnowledgeCard]) -> list[dict[str, str]]:
        return [
            {
                "type": "candidate" if card.status == KnowledgeStatus.CANDIDATE else "conflict",
                "id": card.id,
                "summary": card.content,
            }
            for card in notice_cards
        ]

    def _include_notice_sources(
        self,
        source_records: list[SourceRecord],
        notice_cards: list[KnowledgeCard],
    ) -> list[SourceRecord]:
        records = {source.id: source for source in source_records}
        for card in notice_cards:
            for reference in card.source_refs:
                source_id = reference.split(":", 1)[0]
                try:
                    source = self.sources.get(source_id)
                except KeyError:
                    continue
                if source.project_id == card.project_id:
                    records[source.id] = source
        return list(records.values())

    def _trusted_evidence(
        self,
        cards: list[KnowledgeCard],
        *,
        baseline_path: str,
        version: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[SourceRecord]]:
        citations: list[dict[str, Any]] = []
        card_citation_ids: dict[str, list[str]] = {card.id: [] for card in cards}
        source_records: dict[str, SourceRecord] = {}
        counters: Counter[str] = Counter()

        references: list[tuple[KnowledgeCard, str]] = []
        references.extend((card, card.source_refs[0]) for card in cards)
        references.extend((card, ref) for card in cards for ref in card.source_refs[1:])
        for card, reference in references:
            if len(citations) >= 50:
                break
            source_id = reference.split(":", 1)[0]
            try:
                source = self.sources.get(source_id)
            except KeyError:
                source = None
            if source is not None and source.project_id == card.project_id:
                source_records[source.id] = source
                filename = source.original_filename
                document_version = source.document_version
                authority_level = source.authority_level
            else:
                filename = Path(baseline_path).name
                document_version = version
                authority_level = card.authority_level
            counters[source_id] += 1
            citation_id = f"CIT-{source_id}-{counters[source_id]:02d}"
            citation = Citation(
                id=citation_id,
                source_id=source_id,
                filename=filename,
                document_version=document_version,
                section=card.applicable_scope,
                excerpt=card.content,
                authority_level=authority_level,
            ).model_dump(mode="json")
            citations.append(citation)
            card_citation_ids[card.id].append(citation_id)

        effective_cards = [
            {
                "id": card.id,
                "title": card.title,
                "content": card.content,
                "source_citations": card_citation_ids[card.id],
            }
            for card in cards
        ]
        return effective_cards, citations, list(source_records.values())

    def _validate_response(
        self,
        gateway_result: Mapping[str, Any],
        *,
        version: str,
        cards: list[KnowledgeCard],
        citations: list[dict[str, Any]],
        notices: list[dict[str, str]],
    ) -> QueryResponse:
        try:
            response = QueryResponse.model_validate(gateway_result["result"])
        except (KeyError, TypeError, ValidationError) as error:
            raise OutputValidationError("QUERY_DOMAIN_CONVERSION_INVALID") from error
        if response.baseline_version != version:
            raise OutputValidationError("BASELINE_VERSION_MISMATCH")
        allowed_rules = {card.id for card in cards}
        if not set(response.effective_rules) <= allowed_rules:
            raise OutputValidationError("UNKNOWN_EFFECTIVE_RULE")
        validator = CitationValidator(citations)
        for citation in response.citations:
            validator.validate(citation.model_dump(mode="json"))
        allowed_notices = {
            notice_type: {notice["summary"] for notice in notices if notice["type"] == notice_type}
            for notice_type in ("candidate", "conflict")
        }
        if (
            response.candidate_notice is not None
            and response.candidate_notice not in allowed_notices["candidate"]
        ):
            raise OutputValidationError("UNKNOWN_CANDIDATE_NOTICE")
        if (
            response.conflict_notice is not None
            and response.conflict_notice not in allowed_notices["conflict"]
        ):
            raise OutputValidationError("UNKNOWN_CONFLICT_NOTICE")
        directly_supported = any(
            validator.has_direct_support(response.answer, citation.model_dump(mode="json"))
            for citation in response.citations
        )
        if response.evidence_sufficiency == "insufficient" or not directly_supported:
            response = response.model_copy(
                update={
                    "answer": INSUFFICIENT_EVIDENCE_ANSWER,
                    "evidence_sufficiency": "insufficient",
                }
            )
        return response
