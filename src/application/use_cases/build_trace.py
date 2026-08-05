from __future__ import annotations

from datetime import timedelta

from src.application.dto.trace import BuildTraceInput
from src.application.ports.baseline_cards import BaselineCardReader
from src.application.ports.dashboard import ManifestReader
from src.application.ports.repositories import (
    BaselineRepository,
    ChangeRepository,
    DecisionRepository,
    IssueRepository,
    KnowledgeRepository,
    ModelCallLogRepository,
    RelationRepository,
    SourceRepository,
)
from src.application.use_cases.run_query import QueryMaterialReader
from src.domain.enums import (
    DecisionAction,
    IssueSeverity,
    IssueStatus,
    SecurityLevel,
)
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import (
    Baseline,
    ChangeRequest,
    CostImpactInput,
    CostImpactResult,
    Decision,
    IssueCard,
    KnowledgeCard,
    MarketEvidenceGap,
    ModelCallLog,
    Relation,
    SourceRecord,
    TraceEdge,
    TraceNode,
    TraceView,
    ValueMetric,
)
from src.domain.services.cost_impact import (
    COST_PARAMETER_SOURCE_TYPES,
)
from src.domain.services.cost_impact import (
    calculate_cost_impact as _calculate_cost_impact,
)
from src.domain.services.market_evidence import (
    MARKET_MATERIAL_SOURCE_TYPES,
    VerifiedMarketEvidence,
    classify_market_claim,
)
from src.infrastructure.files.redactor import redact_text

_STAGE_LABELS = {
    "source": "原始资料",
    "knowledge": "结构化知识",
    "issue": "问题",
    "decision": "人工决定",
    "change": "变更单",
    "baseline": "生效基线",
}

_ACTION_LABELS = {
    DecisionAction.ACCEPT_CHANGE: "接受迭代",
    DecisionAction.KEEP_CURRENT: "维持现状",
    DecisionAction.DEFER: "暂缓讨论",
    DecisionAction.FALSE_POSITIVE: "判定误报",
}

# 六节点主链只承认这些持久化 Relation 类型；实体字段绝不用于补边。
_CHAIN_EDGE_TYPES = {
    "issue": "conflicts_with",
    "decision": "resolved_by",
    "change": "proposes_change_to",
    "baseline": "approved_as",
}
_CHAIN_ORDER = ["issue", "decision", "change", "baseline"]
_MARKET_VALIDATION_RULE_ID = "MKT-001"
_EXCERPT_MAX_CHARS = 120
_MODEL_CALL_SCAN_LIMIT = 1000


class BuildTrace:
    """Assemble the fixed six-stage chain exclusively from persisted Relations."""

    def __init__(
        self,
        *,
        manifest: ManifestReader,
        baseline_cards: BaselineCardReader,
        relations: RelationRepository,
        knowledge: KnowledgeRepository,
        sources: SourceRepository,
        issues: IssueRepository,
        decisions: DecisionRepository,
        changes: ChangeRepository,
        baselines: BaselineRepository,
        model_calls: ModelCallLogRepository,
        material_reader: QueryMaterialReader,
        customer_names: tuple[str, ...] = (),
        strategy_terms: tuple[str, ...] = (),
        financial_terms: tuple[str, ...] = (),
        leader_names: tuple[str, ...] = (),
        unpublished_decisions: tuple[str, ...] = (),
    ) -> None:
        self.manifest = manifest
        self.baseline_cards = baseline_cards
        self.relations = relations
        self.knowledge = knowledge
        self.sources = sources
        self.issues = issues
        self.decisions = decisions
        self.changes = changes
        self.baselines = baselines
        self.model_calls = model_calls
        self.material_reader = material_reader
        self.customer_names = customer_names
        self.strategy_terms = strategy_terms
        self.financial_terms = financial_terms
        self.leader_names = leader_names
        self.unpublished_decisions = unpublished_decisions

    def execute(self, command: BuildTraceInput) -> TraceView:
        card = self._snapshot_card(command.entity_id)
        project_id = card.project_id
        graph = self.relations.load_connected(project_id, card.id, max_depth=6)

        nodes: list[TraceNode] = []
        edges: list[TraceEdge] = []

        source_relations = [
            relation
            for relation in graph
            if relation.relation_type == "derived_from" and relation.target_id == card.id
        ]
        source = self._first_entity(
            source_relations,
            self.sources.get,
            key=lambda rel: rel.source_id,
        )
        if source is None:
            source_node = None
        else:
            verification, excerpt, reason = self._verify_source_excerpt(source, card)
            source_node = self._source_node(
                source,
                verification=verification,
                excerpt=excerpt,
                unverifiable_reason=reason,
            )
            nodes.append(source_node)
            edges.append(
                TraceEdge(
                    source_id=source.id,
                    target_id=card.id,
                    relation_type="derived_from",
                )
            )
        knowledge_node = self._knowledge_node(card)
        nodes.append(knowledge_node)

        chain_nodes, chain_edges, first_missing = self._walk_chain(card, graph)
        nodes.extend(chain_nodes)
        edges.extend(chain_edges)

        missing: list[str] = []
        if source_node is None:
            missing.append(_STAGE_LABELS["source"])
        if first_missing is not None:
            start = _CHAIN_ORDER.index(first_missing)
            missing.extend(_STAGE_LABELS[kind] for kind in _CHAIN_ORDER[start:])
        return TraceView(main_chain=nodes, edges=edges, missing_links=missing)

    def list_entry_cards(self, project_id: str) -> list[KnowledgeCard]:
        return self._snapshot_cards(project_id)

    def list_model_calls(self, project_id: str, *, limit: int = 50) -> list[ModelCallLog]:
        return self.model_calls.list_for_project(project_id, limit=limit)

    def market_evidence_gaps(self, project_id: str) -> list[MarketEvidenceGap]:
        snapshot = self.manifest.read_snapshot()
        version = snapshot.manifest.current_version
        cards = [
            card for card in self._snapshot_cards(project_id) if card.card_type == "market_judgment"
        ]
        cards.extend(
            card
            for card in self.knowledge.list_notices(project_id, version)
            if card.card_type == "market_judgment"
        )
        all_issues = self.issues.list_all(project_id)
        gaps: list[MarketEvidenceGap] = []
        for card in cards:
            evidence: list[VerifiedMarketEvidence] = []
            for ref in card.source_refs:
                verified = self._verify_market_ref(project_id, ref)
                if verified is not None:
                    evidence.append(verified)
            plan = next(
                (
                    issue.validation_note.strip()
                    for issue in all_issues
                    if issue.deterministic_rule_id == _MARKET_VALIDATION_RULE_ID
                    and issue.target_rule_id == card.id
                    and issue.validation_note
                    and issue.validation_note.strip()
                ),
                None,
            )
            gaps.append(
                classify_market_claim(
                    claim=card.content,
                    evidence=evidence,
                    validation_plan=plan,
                )
            )
        return gaps

    def list_cost_sources(self, project_id: str) -> list[SourceRecord]:
        return [
            source
            for source in self.sources.list_for_project(project_id)
            if source.is_sandbox and source.source_type in COST_PARAMETER_SOURCE_TYPES
        ]

    def calculate_cost_impact(
        self,
        project_id: str,
        command: CostImpactInput,
    ) -> CostImpactResult:
        """Server-side guard: only sandbox cost-parameter records may feed the hint."""

        if not command.source_refs:
            raise DomainError(ErrorCode.COST_SOURCE_REQUIRED)
        records: list[SourceRecord] = []
        for ref in command.source_refs:
            try:
                record = self.sources.get(ref)
            except KeyError as error:
                raise DomainError(
                    ErrorCode.COST_SOURCE_REQUIRED,
                    f"COST_SOURCE_INVALID:{ref}",
                ) from error
            if (
                record.project_id != project_id
                or not record.is_sandbox
                or record.source_type not in COST_PARAMETER_SOURCE_TYPES
            ):
                raise DomainError(
                    ErrorCode.COST_SOURCE_REQUIRED,
                    f"COST_SOURCE_INVALID:{ref}",
                )
            records.append(record)
        result = _calculate_cost_impact(command)
        return result.model_copy(
            update={"is_simulation": all(record.is_sandbox for record in records)}
        )

    def value_metrics(self, project_id: str) -> list[ValueMetric]:
        metrics: list[ValueMetric] = []
        calls = self.model_calls.list_for_project(project_id, limit=_MODEL_CALL_SCAN_LIMIT)
        query_elapsed = [
            call.elapsed_ms
            for call in calls
            if call.task_type == "query"
            and call.status == "succeeded"
            and call.elapsed_ms is not None
        ]
        if query_elapsed:
            average_ms = sum(query_elapsed) / len(query_elapsed)
            value = (
                f"{average_ms / 1000:.1f} 秒" if average_ms >= 1000 else f"{average_ms:.0f} 毫秒"
            )
            metrics.append(
                ValueMetric(
                    label="系统查询耗时",
                    value=value,
                    source_note=f"来自本地 SQLite 实测数据（{len(query_elapsed)} 次成功查询）",
                )
            )
        all_issues = self.issues.list_all(project_id)
        effective_conflicts = [
            issue
            for issue in all_issues
            if issue.severity in {IssueSeverity.BLOCKING, IssueSeverity.PENDING_DECISION}
            and issue.status != IssueStatus.FALSE_POSITIVE
        ]
        if effective_conflicts:
            metrics.append(
                ValueMetric(
                    label="有效冲突数量",
                    value=str(len(effective_conflicts)),
                    source_note="来自本地 SQLite 实测数据（阻断或待决定的非误报问题）",
                )
            )
        false_positives = [
            issue for issue in all_issues if issue.status == IssueStatus.FALSE_POSITIVE
        ]
        if false_positives:
            metrics.append(
                ValueMetric(
                    label="误报数量",
                    value=str(len(false_positives)),
                    source_note="来自本地 SQLite 实测数据（已判定误报的问题）",
                )
            )
        all_changes = self.changes.list_for_project(project_id)
        issues_by_id = {issue.id: issue for issue in all_issues}
        if all_changes:
            latest_change = max(all_changes, key=lambda change: (change.created_at, change.id))
            issue = issues_by_id.get(latest_change.issue_id)
            if issue is not None:
                duration = latest_change.created_at - issue.created_at
                metrics.append(
                    ValueMetric(
                        label="变更单形成耗时",
                        value=_format_duration(duration),
                        source_note="来自本地 SQLite 实测数据（问题提出到变更单创建）",
                    )
                )
        return metrics

    def _snapshot_cards(self, project_id: str) -> list[KnowledgeCard]:
        snapshot = self.manifest.read_snapshot()
        manifest = snapshot.manifest
        return self.baseline_cards.read_version_cards(
            project_id=project_id,
            version=manifest.current_version,
            relative_path=manifest.card_snapshot_path,
            expected_sha256=manifest.card_snapshot_sha256,
        )

    def _snapshot_card(self, card_id: str) -> KnowledgeCard:
        snapshot = self.manifest.read_snapshot()
        for candidate in self._snapshot_cards(snapshot.manifest.project_id):
            if candidate.id == card_id:
                return candidate
        raise DomainError(ErrorCode.NOT_FOUND, "CARD_NOT_FOUND")

    def _walk_chain(
        self,
        card: KnowledgeCard,
        graph: list[Relation],
    ) -> tuple[list[TraceNode], list[TraceEdge], str | None]:
        loaders = {
            "issue": self.issues.get,
            "decision": self.decisions.get,
            "change": self.changes.get,
            "baseline": self.baselines.get,
        }
        node_factories = {
            "issue": self._issue_node,
            "decision": self._decision_node,
            "change": self._change_node,
            "baseline": self._baseline_node,
        }

        def walk(entity_id: str, index: int):
            kind = _CHAIN_ORDER[index]
            edge_type = _CHAIN_EDGE_TYPES[kind]
            candidates = [
                relation
                for relation in graph
                if relation.relation_type == edge_type and relation.source_id == entity_id
            ]
            resolved: list[tuple[Relation, object]] = []
            for relation in candidates:
                try:
                    entity = loaders[kind](relation.target_id)
                except KeyError:
                    continue
                resolved.append((relation, entity))
            resolved.sort(key=lambda pair: (pair[1].created_at, pair[1].id))
            best: tuple[tuple[TraceNode, ...], tuple[TraceEdge, ...], str | None] | None = None
            for relation, entity in resolved:
                if index + 1 < len(_CHAIN_ORDER):
                    sub_nodes, sub_edges, sub_missing = walk(relation.target_id, index + 1)
                else:
                    sub_nodes, sub_edges, sub_missing = (), (), None
                node = node_factories[kind](entity)
                chain_nodes = (node, *sub_nodes)
                chain_edges = (
                    TraceEdge(
                        source_id=relation.source_id,
                        target_id=relation.target_id,
                        relation_type=relation.relation_type,
                    ),
                    *sub_edges,
                )
                candidate = (chain_nodes, chain_edges, sub_missing)
                if best is None or len(candidate[0]) > len(best[0]):
                    best = candidate
            if best is None:
                return (), (), kind
            return best

        nodes, edges, missing = walk(card.id, 0)
        return list(nodes), list(edges), missing

    def _first_entity(
        self,
        relations: list[Relation],
        loader,
        *,
        key,
    ) -> SourceRecord | None:
        resolved = []
        for relation in relations:
            try:
                entity = loader(key(relation))
            except KeyError:
                continue
            resolved.append(entity)
        if not resolved:
            return None
        resolved.sort(key=lambda entity: (entity.created_at, entity.id))
        return resolved[0]

    def _verify_source_excerpt(
        self,
        source: SourceRecord,
        card: KnowledgeCard,
    ) -> tuple[str, str | None, str | None]:
        """Verify archive integrity and locate a minimal redacted excerpt.

        三态语义：归档与 citation 均通过才返回 verified；来源存在但卡片未提供
        可定位 citation 返回 unverifiable/no_citation；归档哈希或 citation 定位
        失败返回 unverifiable/integrity_failed。不回退到其他片段。
        """

        try:
            material = self.material_reader.read_source(source)
        except DomainError:
            return "unverifiable", None, "integrity_failed"
        citations = [
            citation
            for ref in card.source_refs
            if (parsed := _parse_ref(ref)) is not None
            and parsed[0] == source.id
            and (citation := parsed[1]) is not None
        ]
        if not citations:
            return "unverifiable", None, "no_citation"
        fragment = next(
            (
                item
                for item in material.fragments
                if item.fragment_id is not None and item.fragment_id in citations
            ),
            None,
        )
        if fragment is None:
            return "unverifiable", None, "integrity_failed"
        redacted = self._redact(fragment.text, material.security_level)
        if len(redacted) > _EXCERPT_MAX_CHARS:
            redacted = f"{redacted[:_EXCERPT_MAX_CHARS]}…"
        return "verified", f"{fragment.locator}｜{redacted}", None

    def _verify_market_ref(
        self,
        project_id: str,
        ref: str,
    ) -> VerifiedMarketEvidence | None:
        parsed = _parse_ref(ref)
        if parsed is None:
            return None
        source_id, citation_id = parsed
        if citation_id is None:
            return None
        try:
            source = self.sources.get(source_id)
        except KeyError:
            return None
        if (
            source.project_id != project_id
            or source.is_sandbox
            or source.source_type not in MARKET_MATERIAL_SOURCE_TYPES
        ):
            return None
        try:
            material = self.material_reader.read_source(source)
        except DomainError:
            return None
        fragment = next(
            (item for item in material.fragments if item.fragment_id == citation_id),
            None,
        )
        if fragment is None:
            return None
        return VerifiedMarketEvidence(
            source_id=source.id,
            citation_id=citation_id,
            locator=fragment.locator,
            excerpt=self._redact(fragment.text, material.security_level),
        )

    def _redact(self, text: str, security_level: SecurityLevel) -> str:
        return redact_text(
            text,
            security_level=security_level,
            customer_names=self.customer_names,
            strategy_terms=self.strategy_terms,
            financial_terms=self.financial_terms,
            leader_names=self.leader_names,
            unpublished_decisions=self.unpublished_decisions,
        ).redacted_text

    @staticmethod
    def _source_node(
        source: SourceRecord,
        *,
        verification: str = "not_applicable",
        excerpt: str | None = None,
        unverifiable_reason: str | None = None,
    ) -> TraceNode:
        return TraceNode(
            kind="source",
            entity_id=source.id,
            label=source.original_filename,
            status=source.ingest_status,
            happened_at=source.created_at,
            summary=(
                f"文件版本 {source.document_version}，权威级别 {source.authority_level.value}，"
                f"提供部门 {source.source_department}"
            ),
            is_sandbox=source.is_sandbox,
            verification=verification,
            unverifiable_reason=unverifiable_reason,
            excerpt=excerpt,
        )

    @staticmethod
    def _knowledge_node(card: KnowledgeCard) -> TraceNode:
        return TraceNode(
            kind="knowledge",
            entity_id=card.id,
            label=card.title,
            status=card.status.value,
            happened_at=card.updated_at,
            summary=card.content,
            verification="verified",
        )

    @staticmethod
    def _issue_node(issue: IssueCard) -> TraceNode:
        return TraceNode(
            kind="issue",
            entity_id=issue.id,
            label=issue.title,
            status=issue.status.value,
            happened_at=issue.created_at,
            summary=issue.description,
        )

    @staticmethod
    def _decision_node(decision: Decision) -> TraceNode:
        return TraceNode(
            kind="decision",
            entity_id=decision.id,
            label=_ACTION_LABELS[decision.action],
            status=decision.action.value,
            happened_at=decision.created_at,
            summary=f"{decision.conclusion}（{decision.confirmed_by} 确认）",
        )

    @staticmethod
    def _change_node(change: ChangeRequest) -> TraceNode:
        return TraceNode(
            kind="change",
            entity_id=change.id,
            label=f"目标版本 {change.target_version}",
            status=change.status.value,
            happened_at=change.created_at,
            summary=change.rationale,
        )

    @staticmethod
    def _baseline_node(baseline: Baseline) -> TraceNode:
        return TraceNode(
            kind="baseline",
            entity_id=baseline.id,
            label=f"版本 {baseline.version}",
            status=baseline.status.value,
            happened_at=baseline.effective_at or baseline.created_at,
            summary=f"审批人 {baseline.approved_by}",
        )


def _parse_ref(ref: str) -> tuple[str, str | None] | None:
    source_id, separator, citation = ref.partition(":")
    source_id = source_id.strip()
    citation = citation.strip()
    if not source_id or (separator and not citation) or ":" in citation:
        return None
    return source_id, citation or None


def _format_duration(duration: timedelta) -> str:
    seconds = max(int(duration.total_seconds()), 0)
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时"
    return f"{seconds / 86400:.1f} 天"
