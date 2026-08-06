from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from src.application.dto.query import RunQueryInput
from src.application.ports.baseline_cards import BaselineCardReader
from src.application.ports.dashboard import ManifestReader, ManifestSnapshot
from src.application.ports.repositories import (
    BaselineRepository,
    KnowledgeRepository,
    ProjectRepository,
    SourceRepository,
)
from src.domain.enums import BaselineStatus, KnowledgeStatus, SecurityLevel
from src.domain.errors import DomainError, ErrorCode, OutputValidationError
from src.domain.models import (
    Baseline,
    Citation,
    KnowledgeCard,
    Project,
    QueryResponse,
    SourceRecord,
)
from src.domain.policies.security_policy import can_call_external_model
from src.domain.services.citation_validator import (
    CitationValidator,
    all_claims_have_direct_support,
    contains_normalized_statement,
)
from src.infrastructure.files.query_material_reader import VerifiedQueryMaterial
from src.infrastructure.gateways._common import (
    create_outbound_safety_proof,
    new_workflow_task_id,
)
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
    def read_baseline(
        self,
        *,
        project_id: str,
        asset_id: str,
        version: str,
        relative_path: str,
        expected_sha256: str,
    ) -> VerifiedQueryMaterial: ...

    def read_source(self, source: SourceRecord) -> VerifiedQueryMaterial: ...

    def total_chars(self, materials: list[VerifiedQueryMaterial]) -> int: ...


class RunQuery:
    def __init__(
        self,
        *,
        manifest: ManifestReader,
        baselines: BaselineRepository,
        projects: ProjectRepository,
        knowledge: KnowledgeRepository,
        sources: SourceRepository,
        baseline_cards: BaselineCardReader,
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
        self.projects = projects
        self.knowledge = knowledge
        self.sources = sources
        self.baseline_cards = baseline_cards
        self.material_reader = material_reader
        self.gateway = gateway
        self.customer_names = tuple(customer_names)
        self.strategy_terms = tuple(strategy_terms)
        self.financial_terms = tuple(financial_terms)
        self.leader_names = tuple(leader_names)
        self.unpublished_decisions = tuple(unpublished_decisions)
        self.task_id_factory = task_id_factory or (lambda: new_workflow_task_id("TASK-QUERY"))
        self.schema_version = schema_version

    def list_historical_versions(self, project_id: str) -> tuple[str, ...]:
        manifest = self.manifest.read_snapshot().manifest
        if manifest.project_id != project_id:
            raise ValueError("query project does not match baseline manifest project")
        return tuple(
            baseline.version
            for baseline in self.baselines.list_for_project(project_id)
            if baseline.project_id == project_id
            and baseline.status == BaselineStatus.SUPERSEDED
            and baseline.version != manifest.current_version
        )

    def execute(self, command: RunQueryInput) -> QueryResponse:
        project = self.projects.get(command.project_id)
        if project.id != command.project_id or not project.allow_external_model:
            raise DomainError(ErrorCode.EXTERNAL_CALL_DENIED, "QUERY_PROJECT_NOT_AUTHORIZED")

        snapshot_before = self.manifest.read_snapshot()
        if snapshot_before.manifest.project_id != command.project_id:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "QUERY_MANIFEST_PROJECT_MISMATCH",
            )
        version, baseline_material, snapshot_path, snapshot_sha256 = self._resolve_scope(
            command,
            snapshot_before,
        )
        cards = self._effective_cards(
            command.project_id,
            version,
            relative_path=snapshot_path,
            expected_sha256=snapshot_sha256,
        )
        eligible_versions = self._eligible_source_versions(command.project_id, version)
        notice_cards = self._notice_cards(command, version)
        effective_cards, citations, evidence_materials, card_citation_ids = self._trusted_evidence(
            cards,
            project=project,
            baseline_material=baseline_material,
            eligible_versions=eligible_versions,
        )
        notices, notice_materials = self._trusted_notices(
            notice_cards,
            project=project,
            eligible_versions=eligible_versions,
        )
        supporting_materials = evidence_materials + notice_materials
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
        proof_security_level = _proof_security_level(supporting_materials)
        source_total_chars = self.material_reader.total_chars(supporting_materials)
        proof = create_outbound_safety_proof(
            QueryWorkflowInput,
            inputs,
            security_level=proof_security_level,
            customer_names=self.customer_names,
            strategy_terms=self.strategy_terms,
            financial_terms=self.financial_terms,
            leader_names=self.leader_names,
            unpublished_decisions=self.unpublished_decisions,
            source_total_chars=source_total_chars,
        )
        gateway_result = self.gateway.run(inputs, safety_proof=proof, user=command.project_id)
        snapshot_after = self.manifest.read_snapshot()
        if snapshot_after != snapshot_before:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "MANIFEST_CHANGED_DURING_QUERY",
            )
        return self._validate_response(
            gateway_result,
            version=version,
            cards=cards,
            card_citation_ids=card_citation_ids,
            citations=citations,
            notices=notices,
        )

    def _resolve_scope(
        self,
        command: RunQueryInput,
        snapshot: ManifestSnapshot,
    ) -> tuple[str, VerifiedQueryMaterial | None, str, str]:
        manifest = snapshot.manifest
        if command.scope == "historical":
            if command.historical_version is None:
                raise DomainError(ErrorCode.HISTORICAL_VERSION_REQUIRED)
            try:
                baseline = self.baselines.get_by_version(
                    command.project_id,
                    command.historical_version,
                )
            except KeyError as error:
                raise DomainError(ErrorCode.HISTORICAL_VERSION_INVALID) from error
            if (
                baseline.project_id != command.project_id
                or baseline.version != command.historical_version
                or baseline.status != BaselineStatus.SUPERSEDED
                or baseline.version == manifest.current_version
            ):
                raise DomainError(ErrorCode.HISTORICAL_VERSION_INVALID)
            if baseline.card_snapshot_sha256 is None:
                raise DomainError(
                    ErrorCode.BASELINE_INTEGRITY_FAILED,
                    "HISTORICAL_ASSET_UNVERIFIABLE",
                )
            return (
                baseline.version,
                None,
                baseline.card_snapshot_path,
                baseline.card_snapshot_sha256,
            )

        baseline_material = self.material_reader.read_baseline(
            project_id=command.project_id,
            asset_id=manifest.current_baseline_id,
            version=manifest.current_version,
            relative_path=manifest.full_document_path,
            expected_sha256=manifest.full_document_sha256,
        )
        return (
            manifest.current_version,
            baseline_material,
            manifest.card_snapshot_path,
            manifest.card_snapshot_sha256,
        )

    def _effective_cards(
        self,
        project_id: str,
        version: str,
        *,
        relative_path: str,
        expected_sha256: str,
    ) -> list[KnowledgeCard]:
        snapshot_cards = self.baseline_cards.read_version_cards(
            project_id=project_id,
            version=version,
            relative_path=relative_path,
            expected_sha256=expected_sha256,
        )
        return [
            card
            for card in snapshot_cards
            if card.project_id == project_id
            and card.product_version == version
            and card.status == KnowledgeStatus.EFFECTIVE
        ][:20]

    def _eligible_source_versions(self, project_id: str, version: str) -> set[str]:
        """A source stays eligible for the version it was imported under and for
        every descendant version whose same-project baseline chain includes it.
        A broken, cyclic or ambiguous chain fails closed instead of trusting a
        partial ancestry."""
        baselines = [
            baseline
            for baseline in self.baselines.list_for_project(project_id)
            if baseline.project_id == project_id
        ]
        by_version: dict[str, Baseline] = {}
        for baseline in baselines:
            if baseline.version in by_version:
                raise DomainError(
                    ErrorCode.BASELINE_INTEGRITY_FAILED,
                    f"BASELINE_DUPLICATE_VERSION:{baseline.version}",
                )
            by_version[baseline.version] = baseline
        by_id = {baseline.id: baseline for baseline in baselines}
        current = by_version.get(version)
        if current is None:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                f"BASELINE_VERSION_ROW_MISSING:{version}",
            )
        eligible = {version}
        visited = {current.id}
        while current.parent_baseline_id is not None:
            parent = by_id.get(current.parent_baseline_id)
            if parent is None:
                raise DomainError(
                    ErrorCode.BASELINE_INTEGRITY_FAILED,
                    f"BASELINE_PARENT_CHAIN_BROKEN:{current.parent_baseline_id}",
                )
            if parent.id in visited:
                raise DomainError(
                    ErrorCode.BASELINE_INTEGRITY_FAILED,
                    f"BASELINE_PARENT_CHAIN_CYCLE:{parent.id}",
                )
            visited.add(parent.id)
            eligible.add(parent.version)
            current = parent
        return eligible

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

    def _trusted_evidence(
        self,
        cards: list[KnowledgeCard],
        *,
        project: Project,
        baseline_material: VerifiedQueryMaterial | None,
        eligible_versions: set[str],
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[VerifiedQueryMaterial],
        dict[str, set[str]],
    ]:
        source_materials = self._verified_source_materials(
            cards,
            project=project,
            eligible_versions=eligible_versions,
        )
        citations: list[dict[str, Any]] = []
        supporting_materials: list[VerifiedQueryMaterial] = []
        card_citation_ids: dict[str, set[str]] = {card.id: set() for card in cards}
        counters: Counter[str] = Counter()
        candidates_by_card: dict[
            str,
            list[tuple[VerifiedQueryMaterial, str]],
        ] = {}

        for card in cards:
            seen_evidence: set[tuple[str, str]] = set()
            candidates: list[tuple[VerifiedQueryMaterial, str]] = []
            for reference in card.source_refs:
                source_id, fragment_id = _split_reference(reference)
                material = source_materials.get(source_id)
                fragment = _supporting_fragment(material, card.content, fragment_id)
                if fragment is None:
                    continue
                key = (material.source_id, fragment.locator)
                if key in seen_evidence:
                    continue
                seen_evidence.add(key)
                candidates.append((material, fragment.locator))

            baseline_fragment = _supporting_fragment(baseline_material, card.content)
            if baseline_fragment is not None:
                if candidates:
                    supporting_materials.append(baseline_material)
                else:
                    candidates.append((baseline_material, baseline_fragment.locator))

            if not candidates:
                raise DomainError(
                    ErrorCode.CITATION_INVALID,
                    f"QUERY_CARD_SOURCE_TEXT_MISMATCH:{card.id}",
                )
            candidates_by_card[card.id] = candidates
            supporting_materials.extend(material for material, _ in candidates)

        citation_candidates = [(card, candidates_by_card[card.id][0]) for card in cards]
        citation_candidates.extend(
            (card, candidate) for card in cards for candidate in candidates_by_card[card.id][1:]
        )
        for card, (material, locator) in citation_candidates[:50]:
            citation = _build_citation(material, locator, card.content, counters)
            citations.append(citation)
            card_citation_ids[card.id].add(citation["id"])

        effective_cards = [
            {
                "id": card.id,
                "title": card.title,
                "content": card.content,
                "source_citations": sorted(card_citation_ids[card.id]),
            }
            for card in cards
        ]
        return effective_cards, citations, supporting_materials, card_citation_ids

    def _trusted_notices(
        self,
        cards: list[KnowledgeCard],
        *,
        project: Project,
        eligible_versions: set[str],
    ) -> tuple[list[dict[str, str]], list[VerifiedQueryMaterial]]:
        source_materials = self._verified_source_materials(
            cards,
            project=project,
            eligible_versions=eligible_versions,
        )
        notices: list[dict[str, str]] = []
        supporting_materials: list[VerifiedQueryMaterial] = []
        for card in cards:
            card_supporting: list[VerifiedQueryMaterial] = []
            for reference in card.source_refs:
                source_id, fragment_id = _split_reference(reference)
                material = source_materials.get(source_id)
                if _supporting_fragment(material, card.content, fragment_id) is not None:
                    card_supporting.append(material)
            if not card_supporting:
                raise DomainError(
                    ErrorCode.CITATION_INVALID,
                    f"QUERY_NOTICE_SOURCE_TEXT_MISMATCH:{card.id}",
                )
            notices.append(
                {
                    "type": (
                        "candidate" if card.status == KnowledgeStatus.CANDIDATE else "conflict"
                    ),
                    "id": card.id,
                    "summary": card.content,
                }
            )
            supporting_materials.extend(card_supporting)
        return notices, supporting_materials

    def _verified_source_materials(
        self,
        cards: list[KnowledgeCard],
        *,
        project: Project,
        eligible_versions: set[str],
    ) -> dict[str, VerifiedQueryMaterial]:
        materials: dict[str, VerifiedQueryMaterial] = {}
        for card in cards:
            for reference in card.source_refs:
                source_id, _ = _split_reference(reference)
                if source_id in materials:
                    continue
                try:
                    source = self.sources.get(source_id)
                except KeyError:
                    continue
                self._require_source_eligible(
                    source,
                    project=project,
                    eligible_versions=eligible_versions,
                )
                material = self.material_reader.read_source(source)
                if (
                    material.source_id != source.id
                    or material.document_version != source.document_version
                    or material.sha256 != source.sha256
                    or material.security_level != source.security_level
                    or material.is_baseline_asset
                ):
                    raise DomainError(
                        ErrorCode.CITATION_INVALID,
                        f"QUERY_SOURCE_MATERIAL_MISMATCH:{source.id}",
                    )
                materials[source_id] = material
        return materials

    @staticmethod
    def _require_source_eligible(
        source: SourceRecord,
        *,
        project: Project,
        eligible_versions: set[str],
    ) -> None:
        if (
            source.project_id != project.id
            or source.applicable_baseline_version not in eligible_versions
            or source.ingest_status != "completed"
            or not can_call_external_model(project, source)
        ):
            raise DomainError(
                ErrorCode.EXTERNAL_CALL_DENIED,
                f"QUERY_SOURCE_NOT_AUTHORIZED:{source.id}",
            )

    def _validate_response(
        self,
        gateway_result: Mapping[str, Any],
        *,
        version: str,
        cards: list[KnowledgeCard],
        card_citation_ids: dict[str, set[str]],
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
        returned_citation_ids = {citation.id for citation in response.citations}
        for rule_id in response.effective_rules:
            if not returned_citation_ids & card_citation_ids[rule_id]:
                raise OutputValidationError("EFFECTIVE_RULE_CITATION_MISSING")

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

        if any(
            contains_normalized_statement(response.answer, notice["summary"]) for notice in notices
        ):
            raise OutputValidationError("NOTICE_CONTENT_IN_ANSWER")

        directly_supported = all_claims_have_direct_support(
            response.answer,
            [citation.model_dump(mode="json") for citation in response.citations],
        )
        if response.evidence_sufficiency == "insufficient" or not directly_supported:
            response = response.model_copy(
                update={
                    "answer": INSUFFICIENT_EVIDENCE_ANSWER,
                    "evidence_sufficiency": "insufficient",
                }
            )
        return response


def _split_reference(reference: str) -> tuple[str, str | None]:
    source_id, separator, fragment_id = reference.partition(":")
    return source_id, fragment_id if separator and fragment_id else None


def _supporting_fragment(material, excerpt: str, fragment_id: str | None = None):
    if material is None:
        return None
    for fragment in material.fragments:
        if fragment_id is not None and fragment.fragment_id != fragment_id:
            continue
        if excerpt in fragment.text:
            return fragment
    return None


def _build_citation(material, locator: str, excerpt: str, counters: Counter[str]) -> dict[str, Any]:
    counters[material.source_id] += 1
    citation = Citation(
        id=f"CIT-{material.source_id}-{counters[material.source_id]:02d}",
        source_id=material.source_id,
        filename=material.filename,
        document_version=material.document_version,
        section=locator,
        excerpt=excerpt,
        authority_level=material.authority_level,
    )
    return citation.model_dump(mode="json")


def _proof_security_level(materials: list[VerifiedQueryMaterial]) -> SecurityLevel:
    if any(
        material.security_level
        not in {SecurityLevel.L1_PUBLIC_SIMULATED, SecurityLevel.L2_INTERNAL}
        for material in materials
    ):
        raise DomainError(
            ErrorCode.EXTERNAL_CALL_DENIED,
            "QUERY_SUPPORTING_MATERIAL_NOT_EXPORTABLE",
        )
    if any(material.security_level == SecurityLevel.L2_INTERNAL for material in materials):
        return SecurityLevel.L2_INTERNAL
    return SecurityLevel.L1_PUBLIC_SIMULATED
