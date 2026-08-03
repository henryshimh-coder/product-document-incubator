from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.application.dto.query import RunQueryInput
from src.application.ports.dashboard import ManifestSnapshot
from src.application.use_cases.run_query import INSUFFICIENT_EVIDENCE_ANSWER, RunQuery
from src.domain.enums import AuthorityLevel, CallResultMode, KnowledgeStatus, SecurityLevel
from src.domain.errors import DomainError, OutputValidationError
from src.domain.models import Baseline, BaselineManifest, KnowledgeCard, SourceRecord
from src.infrastructure.gateways._common import validate_input
from src.infrastructure.gateways.schemas import QueryWorkflowInput

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _card(
    card_id: str,
    *,
    status: KnowledgeStatus = KnowledgeStatus.EFFECTIVE,
    version: str = "LLD-724_1",
    content: str = "当前目标客群是符合准入要求的存量客户。",
    source_refs: list[str] | None = None,
) -> KnowledgeCard:
    return KnowledgeCard(
        id=card_id,
        project_id="LLD",
        card_type="rule",
        title="目标客群",
        content=content,
        status=status,
        product_version=version,
        applicable_scope="产品方案 > 目标客群",
        source_refs=source_refs or ["SRC-001"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品经理",
        created_at=NOW,
        updated_at=NOW,
    )


def _source(source_id: str = "SRC-001") -> SourceRecord:
    return SourceRecord(
        id=source_id,
        project_id="LLD",
        original_filename="当前产品方案.md",
        archive_path=f"/trusted/{source_id}/当前产品方案.md",
        sha256="a" * 64,
        mime_type="text/markdown",
        size_bytes=10_000,
        source_type="formal_document",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=True,
        is_sandbox=False,
        ingest_status="completed",
        created_at=NOW,
    )


def _manifest(version: str = "LLD-724_1") -> BaselineManifest:
    return BaselineManifest(
        schema_version="1.0",
        project_id="LLD",
        current_baseline_id="BASE-LLD-724_1",
        current_version=version,
        parent_baseline_id=None,
        full_document_path="data/baselines/LLD-724_1/full.md",
        card_snapshot_path="data/baselines/LLD-724_1/cards.json",
        full_document_sha256="b" * 64,
        card_snapshot_sha256="c" * 64,
        change_request_id=None,
        approved_by="产品经理",
        published_at=NOW,
    )


class FakeManifest:
    def __init__(self, events: list[str], manifest: BaselineManifest | None = None) -> None:
        self.events = events
        self.manifest = manifest or _manifest()

    def read_snapshot(self) -> ManifestSnapshot:
        self.events.append("manifest")
        return ManifestSnapshot(self.manifest, "d" * 64)


class FakeKnowledge:
    def __init__(self, events: list[str], cards: list[KnowledgeCard]) -> None:
        self.events = events
        self.cards = cards
        self.requested_versions: list[str] = []

    def list_effective(self, project_id: str, version: str) -> list[KnowledgeCard]:
        self.events.append("knowledge")
        self.requested_versions.append(version)
        return list(self.cards)

    def list_notices(self, project_id: str, version: str) -> list[KnowledgeCard]:
        self.events.append("notices")
        return [
            card
            for card in self.cards
            if card.product_version == version
            and card.status in {KnowledgeStatus.CANDIDATE, KnowledgeStatus.CONFLICT}
        ]


class FakeSources:
    def __init__(self, sources: list[SourceRecord] | None = None) -> None:
        self.sources = {source.id: source for source in sources or [_source()]}

    def get(self, source_id: str) -> SourceRecord:
        if source_id not in self.sources:
            raise KeyError(source_id)
        return self.sources[source_id]


class FakeBaselines:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str]] = []

    def get_by_version(self, project_id: str, version: str) -> Baseline:
        self.requested.append((project_id, version))
        return Baseline(
            id="BASE-LLD-700_1",
            project_id=project_id,
            version=version,
            parent_baseline_id=None,
            status="superseded",
            full_document_path="data/baselines/LLD-700_1/full.md",
            card_snapshot_path="data/baselines/LLD-700_1/cards.json",
            manifest_sha256="e" * 64,
            change_request_id=None,
            approved_by="产品经理",
            effective_at=NOW,
            created_at=NOW,
        )

    def list_for_project(self, project_id: str) -> list[Baseline]:
        return [
            self.get_by_version(project_id, "LLD-700_1"),
            self.get_by_version(project_id, "LLD-724_1").model_copy(update={"status": "effective"}),
        ]


class FakeMaterialReader:
    def __init__(self, total_chars: int = 100_000) -> None:
        self._total_chars = total_chars
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def total_chars(self, baseline_path: str, sources: list[SourceRecord]) -> int:
        self.calls.append((baseline_path, tuple(source.id for source in sources)))
        return self._total_chars


class ProofCheckingGateway:
    def __init__(self, *, unsupported: bool = False) -> None:
        self.last_inputs = None
        self.unsupported = unsupported

    def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
        self.last_inputs = validate_input(
            QueryWorkflowInput,
            inputs,
            invalid_detail="QUERY_INPUT_INVALID",
            safety_proof=safety_proof,
        )
        cards = self.last_inputs["effective_cards"]
        citations = self.last_inputs["citations"]
        answer = "模型无根据的新结论。" if self.unsupported else cards[0]["content"]
        notices = {item["type"]: item["summary"] for item in self.last_inputs["notices"]}
        return {
            "workflow_run_id": "WF-QUERY-001",
            "result": {
                "answer": answer,
                "effective_rules": [card["id"] for card in cards],
                "citations": citations[:1],
                "candidate_notice": notices.get("candidate"),
                "conflict_notice": notices.get("conflict"),
                "baseline_version": self.last_inputs["baseline_version"],
                "evidence_sufficiency": "sufficient",
                "result_mode": CallResultMode.REALTIME,
                "model_call_id": "CALL-QUERY-001",
            },
        }


def _use_case(
    cards: list[KnowledgeCard],
    *,
    events: list[str] | None = None,
    gateway: ProofCheckingGateway | None = None,
    sources: FakeSources | None = None,
):
    event_log = events if events is not None else []
    selected_gateway = gateway or ProofCheckingGateway()
    baselines = FakeBaselines()
    reader = FakeMaterialReader()
    use_case = RunQuery(
        manifest=FakeManifest(event_log),
        baselines=baselines,
        knowledge=FakeKnowledge(event_log, cards),
        sources=sources or FakeSources(),
        material_reader=reader,
        gateway=selected_gateway,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        task_id_factory=lambda: "TASK-QUERY-001",
    )
    return use_case, selected_gateway, baselines, reader


def test_query_input_rejects_unknown_fields_blank_question_and_more_than_500_chars() -> None:
    """Catches ambiguous/oversized form input reaching the query application service."""
    with pytest.raises(ValidationError):
        RunQueryInput(
            project_id="LLD",
            question=" ",
            scope="effective",
            historical_version=None,
            invented=True,
        )
    with pytest.raises(ValidationError):
        RunQueryInput(
            project_id="LLD",
            question="问" * 501,
            scope="effective",
            historical_version=None,
        )


def test_effective_query_reads_manifest_first_and_never_sends_candidate_cards() -> None:
    """Catches selecting the current version from SQLite or leaking candidate text as evidence."""
    events: list[str] = []
    cards = [
        _card("RULE-LLD-001"),
        _card("RULE-CANDIDATE-001", status=KnowledgeStatus.CANDIDATE, content="候选客群。"),
    ]
    use_case, gateway, _, _ = _use_case(cards, events=events)

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective",
            historical_version=None,
        )
    )

    assert events[:2] == ["manifest", "knowledge"]
    assert {item["id"] for item in gateway.last_inputs["effective_cards"]} == {"RULE-LLD-001"}
    assert set(response.effective_rules) == {"RULE-LLD-001"}
    assert "RULE-CANDIDATE-001" not in gateway.last_inputs["effective_cards"]
    assert gateway.last_inputs["baseline_version"] == "LLD-724_1"


def test_effective_with_notices_keeps_candidate_and_conflict_out_of_evidence_cards() -> None:
    """Catches notices becoming answer evidence instead of separate trusted local warnings."""
    cards = [
        _card("RULE-LLD-001"),
        _card("RULE-CANDIDATE-001", status=KnowledgeStatus.CANDIDATE, content="建议收紧客群。"),
        _card("RULE-CONFLICT-001", status=KnowledgeStatus.CONFLICT, content="客群口径存在冲突。"),
    ]
    use_case, gateway, _, _ = _use_case(cards)

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective_with_notices",
            historical_version=None,
        )
    )

    assert [item["id"] for item in gateway.last_inputs["effective_cards"]] == ["RULE-LLD-001"]
    assert gateway.last_inputs["notices"] == [
        {"type": "candidate", "id": "RULE-CANDIDATE-001", "summary": "建议收紧客群。"},
        {"type": "conflict", "id": "RULE-CONFLICT-001", "summary": "客群口径存在冲突。"},
    ]
    assert response.candidate_notice == "建议收紧客群。"
    assert response.conflict_notice == "客群口径存在冲突。"


def test_notice_source_material_counts_toward_the_real_safety_coverage_denominator() -> None:
    """Catches notice payload chars being measured against only baseline evidence sources."""
    cards = [
        _card("RULE-LLD-001"),
        _card(
            "RULE-CANDIDATE-001",
            status=KnowledgeStatus.CANDIDATE,
            content="建议收紧客群。",
            source_refs=["SRC-CANDIDATE"],
        ),
    ]
    sources = FakeSources([_source(), _source("SRC-CANDIDATE")])
    use_case, gateway, _, reader = _use_case(cards, sources=sources)

    use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective_with_notices",
            historical_version=None,
        )
    )

    assert set(reader.calls[0][1]) == {"SRC-001", "SRC-CANDIDATE"}
    assert {item["source_id"] for item in gateway.last_inputs["citations"]} == {"SRC-001"}


def test_historical_scope_requires_explicit_version() -> None:
    """Catches a historical query silently falling back to the current Manifest version."""
    use_case, _, _, _ = _use_case([_card("RULE-LLD-001")])

    with pytest.raises(DomainError, match="HISTORICAL_VERSION_REQUIRED"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="历史规则是什么？",
                scope="historical",
                historical_version=None,
            )
        )


def test_historical_query_loads_only_the_explicit_version_without_notices() -> None:
    """Catches historical cards or current notices crossing version boundaries."""
    historical = _card("RULE-HISTORY-001", version="LLD-700_1", content="历史客群规则。")
    current = _card("RULE-LLD-001")
    use_case, gateway, baselines, reader = _use_case([historical, current])

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="历史规则是什么？",
            scope="historical",
            historical_version="LLD-700_1",
        )
    )

    assert baselines.requested == [("LLD", "LLD-700_1")]
    assert gateway.last_inputs["baseline_version"] == "LLD-700_1"
    assert [item["id"] for item in gateway.last_inputs["effective_cards"]] == ["RULE-HISTORY-001"]
    assert gateway.last_inputs["notices"] == []
    assert response.baseline_version == "LLD-700_1"
    assert reader.calls[0][0] == "data/baselines/LLD-700_1/full.md"


def test_historical_version_choices_exclude_the_manifest_current_version() -> None:
    """Catches the history selector treating the authoritative current version as historical."""
    use_case, _, _, _ = _use_case([_card("RULE-LLD-001")])

    assert use_case.list_historical_versions("LLD") == ("LLD-700_1",)


def test_query_builds_citations_only_from_trusted_local_source_metadata() -> None:
    """Catches filename, version, section, or excerpt fabrication in the query prompt."""
    use_case, gateway, _, _ = _use_case([_card("RULE-LLD-001")])

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective",
            historical_version=None,
        )
    )

    assert gateway.last_inputs["citations"] == [
        {
            "id": "CIT-SRC-001-01",
            "source_id": "SRC-001",
            "filename": "当前产品方案.md",
            "document_version": "v1.0",
            "section": "产品方案 > 目标客群",
            "excerpt": "当前目标客群是符合准入要求的存量客户。",
            "authority_level": "formal_effective",
        }
    ]
    assert response.citations[0].filename == "当前产品方案.md"


def test_query_degrades_unsupported_answer_to_the_fixed_insufficient_evidence_copy() -> None:
    """Catches an unsupported model claim surviving application-level direct-support validation."""
    gateway = ProofCheckingGateway(unsupported=True)
    use_case, _, _, _ = _use_case([_card("RULE-LLD-001")], gateway=gateway)

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective",
            historical_version=None,
        )
    )

    assert response.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert response.evidence_sufficiency == "insufficient"


def test_query_rejects_gateway_rules_outside_the_selected_effective_cards() -> None:
    """Catches a fake gateway bypassing the trusted effective-rule boundary."""

    class InventingGateway(ProofCheckingGateway):
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            result = super().run(
                inputs,
                safety_proof=safety_proof,
                user=user,
                timeout_seconds=timeout_seconds,
            )
            result["result"]["effective_rules"] = ["RULE-INVENTED"]
            return result

    use_case, _, _, _ = _use_case([_card("RULE-LLD-001")], gateway=InventingGateway())

    with pytest.raises(OutputValidationError, match="UNKNOWN_EFFECTIVE_RULE"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="当前目标客群是什么？",
                scope="effective",
                historical_version=None,
            )
        )


def test_query_caps_effective_cards_notices_and_citations_at_schema_limits() -> None:
    """Catches oversized local collections crossing the strict Query workflow boundary."""
    effective = [
        _card(
            f"RULE-{index:03d}",
            source_refs=[
                f"SRC-{index:03d}-A",
                f"SRC-{index:03d}-B",
                f"SRC-{index:03d}-C",
            ],
        )
        for index in range(21)
    ]
    notices = [
        _card(
            f"RULE-CANDIDATE-{index:03d}",
            status=KnowledgeStatus.CANDIDATE,
            content=f"候选提示 {index}。",
        )
        for index in range(21)
    ]
    source_ids = [reference for card in effective for reference in card.source_refs]
    use_case, gateway, _, _ = _use_case(
        effective + notices,
        sources=FakeSources([_source(source_id) for source_id in source_ids]),
    )

    use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前规则是什么？",
            scope="effective_with_notices",
            historical_version=None,
        )
    )

    assert len(gateway.last_inputs["effective_cards"]) == 20
    assert len(gateway.last_inputs["notices"]) == 20
    assert len(gateway.last_inputs["citations"]) == 50
