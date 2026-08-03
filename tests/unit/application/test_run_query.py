from __future__ import annotations

import hashlib
import importlib
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.application.dto.query import RunQueryInput
from src.application.ports.dashboard import ManifestSnapshot
from src.application.use_cases.run_query import INSUFFICIENT_EVIDENCE_ANSWER, RunQuery
from src.domain.enums import (
    AuthorityLevel,
    BaselineStatus,
    CallResultMode,
    KnowledgeStatus,
    SecurityLevel,
)
from src.domain.errors import DomainError, OutputValidationError
from src.domain.models import Baseline, BaselineManifest, KnowledgeCard, Project, SourceRecord
from src.infrastructure.files.query_material_reader import (
    VerifiedFragment,
    VerifiedQueryMaterial,
)
from src.infrastructure.gateways._common import validate_input
from src.infrastructure.gateways.schemas import QueryWorkflowInput

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _project(*, allow_external_model: bool = True) -> Project:
    return Project(
        id="LLD",
        name="产品智策",
        product_line="轻量交付",
        stage="demo",
        current_baseline_id="BASE-LLD-724_1",
        allow_external_model=allow_external_model,
        created_at=NOW,
        updated_at=NOW,
    )


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
        sha256=hashlib.sha256(source_id.encode()).hexdigest(),
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


def _historical_baseline(**updates) -> Baseline:
    baseline = Baseline(
        id="BASE-LLD-700_1",
        project_id="LLD",
        version="LLD-700_1",
        parent_baseline_id=None,
        status=BaselineStatus.SUPERSEDED,
        full_document_path="data/baselines/LLD-700_1/full.md",
        card_snapshot_path="data/baselines/LLD-700_1/cards.json",
        manifest_sha256="e" * 64,
        change_request_id=None,
        approved_by="产品经理",
        effective_at=NOW,
        created_at=NOW,
    )
    return baseline.model_copy(update=updates)


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
        selected = [_source()] if sources is None else sources
        self.sources = {source.id: source for source in selected}

    def get(self, source_id: str) -> SourceRecord:
        if source_id not in self.sources:
            raise KeyError(source_id)
        return self.sources[source_id]


class FakeProjects:
    def __init__(self, project: Project | None = None) -> None:
        self.project = project or _project()

    def get(self, project_id: str) -> Project:
        return self.project


class FakeBaselines:
    def __init__(self, baseline: Baseline | None = None) -> None:
        self.requested: list[tuple[str, str]] = []
        self.baseline = baseline

    def get_by_version(self, project_id: str, version: str) -> Baseline:
        self.requested.append((project_id, version))
        return self.baseline or Baseline(
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
    def __init__(
        self,
        cards: list[KnowledgeCard] | None = None,
        total_chars: int = 100_000,
    ) -> None:
        self._total_chars = total_chars
        self.calls: list[tuple[str, ...]] = []
        self.baseline_calls: list[dict] = []
        self.source_calls: list[str] = []
        self.card_text_by_source: dict[str, list[str]] = {}
        self.baseline_texts: list[str] = []
        for card in cards or []:
            if card.status == KnowledgeStatus.EFFECTIVE:
                self.baseline_texts.append(card.content)
            for reference in card.source_refs:
                source_id = reference.split(":", 1)[0]
                self.card_text_by_source.setdefault(source_id, []).append(card.content)

    def read_baseline(self, **context) -> VerifiedQueryMaterial:
        self.baseline_calls.append(context)
        text = "# 当前产品方案\n## 目标客群\n" + "\n".join(self.baseline_texts)
        return VerifiedQueryMaterial(
            source_id=context["asset_id"],
            filename="full.md",
            document_version=context["version"],
            sha256=context["expected_sha256"],
            text=text,
            fragments=(
                VerifiedFragment(
                    locator="heading:当前产品方案 > 目标客群; line:3",
                    text="\n".join(self.baseline_texts),
                ),
            ),
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            security_level=SecurityLevel.L2_INTERNAL,
            is_baseline_asset=True,
        )

    def read_source(self, source: SourceRecord) -> VerifiedQueryMaterial:
        self.source_calls.append(source.id)
        text = "\n".join(self.card_text_by_source.get(source.id, []))
        return VerifiedQueryMaterial(
            source_id=source.id,
            filename=source.original_filename,
            document_version=source.document_version,
            sha256=source.sha256,
            text=text,
            fragments=(
                VerifiedFragment(
                    locator="heading:当前产品方案 > 目标客群; line:1",
                    text=text,
                    fragment_id=f"{source.id}-0001",
                ),
            ),
            authority_level=source.authority_level,
            security_level=source.security_level,
            is_baseline_asset=False,
        )

    def total_chars(self, materials: list[VerifiedQueryMaterial]) -> int:
        self.calls.append(tuple(material.source_id for material in materials))
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
                "citations": citations,
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
    manifest: FakeManifest | None = None,
    material_reader: FakeMaterialReader | None = None,
    baselines: FakeBaselines | None = None,
):
    event_log = events if events is not None else []
    selected_gateway = gateway or ProofCheckingGateway()
    selected_baselines = baselines or FakeBaselines()
    reader = material_reader or FakeMaterialReader(cards)
    use_case = RunQuery(
        manifest=manifest or FakeManifest(event_log),
        baselines=selected_baselines,
        projects=FakeProjects(),
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
    return use_case, selected_gateway, selected_baselines, reader


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


def test_query_rejects_external_call_when_project_has_not_authorized_it() -> None:
    """Catches payload proof creation when the trusted local project forbids external models."""
    gateway = ProofCheckingGateway()
    use_case = RunQuery(
        manifest=FakeManifest([]),
        baselines=FakeBaselines(),
        projects=FakeProjects(_project(allow_external_model=False)),
        knowledge=FakeKnowledge([], [_card("RULE-LLD-001")]),
        sources=FakeSources(),
        material_reader=FakeMaterialReader(),
        gateway=gateway,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
    )

    with pytest.raises(DomainError, match="EXTERNAL_CALL_DENIED"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="当前目标客群是什么？",
                scope="effective",
                historical_version=None,
            )
        )

    assert gateway.last_inputs is None


@pytest.mark.parametrize(
    ("source_update", "case_id"),
    [
        ({"security_level": SecurityLevel.L3_CONFIDENTIAL}, "l3"),
        ({"security_level": SecurityLevel.L4_RESTRICTED}, "l4"),
        ({"is_redacted": False}, "unredacted"),
        ({"allow_external_model": False}, "source-not-authorized"),
        ({"ingest_status": "processing"}, "not-completed"),
        ({"applicable_baseline_version": "LLD-OTHER"}, "wrong-version"),
        ({"project_id": "OTHER"}, "wrong-project"),
    ],
)
def test_query_rejects_ineligible_source_before_proof_or_gateway(
    source_update: dict,
    case_id: str,
) -> None:
    """Catches unsafe or out-of-scope SourceRecord material crossing the model boundary."""
    gateway = ProofCheckingGateway()
    source = _source().model_copy(update=source_update)
    use_case, _, _, _ = _use_case(
        [_card("RULE-LLD-001")],
        gateway=gateway,
        sources=FakeSources([source]),
    )

    with pytest.raises(DomainError, match="EXTERNAL_CALL_DENIED"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question=f"当前目标客群是什么？ {case_id}",
                scope="effective",
                historical_version=None,
            )
        )

    assert gateway.last_inputs is None


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

    assert set(reader.calls[0]) == {
        "BASE-LLD-724_1",
        "SRC-001",
        "SRC-CANDIDATE",
    }
    assert {item["source_id"] for item in gateway.last_inputs["citations"]} == {"SRC-001"}


@pytest.mark.parametrize(
    "source_ids",
    [
        ("SRC-L1", "SRC-L2"),
        ("SRC-L2", "SRC-L1"),
    ],
    ids=["l1-first", "l2-first"],
)
def test_historical_query_aggregates_strictest_risk_before_same_hash_character_dedupe(
    source_ids: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches same-content source order downgrading an L2 outbound proof to L1."""
    version = "LLD-700_1"
    shared_sha256 = "f" * 64
    card = _card(
        "RULE-HISTORY-001",
        version=version,
        content="历史客群规则。",
        source_refs=list(source_ids),
    )
    source_by_id = {
        "SRC-L1": _source("SRC-L1").model_copy(
            update={
                "sha256": shared_sha256,
                "security_level": SecurityLevel.L1_PUBLIC_SIMULATED,
                "applicable_baseline_version": version,
            }
        ),
        "SRC-L2": _source("SRC-L2").model_copy(
            update={
                "sha256": shared_sha256,
                "security_level": SecurityLevel.L2_INTERNAL,
                "applicable_baseline_version": version,
            }
        ),
    }
    reader = FakeMaterialReader([card])
    captured_levels: list[SecurityLevel] = []
    module = importlib.import_module("src.application.use_cases.run_query")
    real_factory = module.create_outbound_safety_proof

    def recording_factory(*args, **kwargs):
        captured_levels.append(kwargs["security_level"])
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(module, "create_outbound_safety_proof", recording_factory)
    use_case, _, _, _ = _use_case(
        [card],
        sources=FakeSources([source_by_id[source_id] for source_id in source_ids]),
        material_reader=reader,
    )

    use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="历史客群规则是什么？",
            scope="historical",
            historical_version=version,
        )
    )

    assert captured_levels == [SecurityLevel.L2_INTERNAL]
    assert set(reader.calls[0]) == {"SRC-L1", "SRC-L2"}


def test_current_query_preserves_l2_baseline_risk_when_l1_source_has_same_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches content dedupe discarding the current baseline's stricter L2 risk."""
    shared_sha256 = "b" * 64
    card = _card("RULE-LLD-001", source_refs=["SRC-L1"])

    class SameBytesReader(FakeMaterialReader):
        def __init__(self):
            super().__init__([card])
            self.baseline_material = None

        def read_baseline(self, **context):
            self.baseline_material = super().read_baseline(**context)
            return self.baseline_material

        def read_source(self, source):
            material = super().read_source(source)
            return replace(
                material,
                text=self.baseline_material.text,
                sha256=shared_sha256,
            )

    source = _source("SRC-L1").model_copy(
        update={
            "sha256": shared_sha256,
            "security_level": SecurityLevel.L1_PUBLIC_SIMULATED,
        }
    )
    reader = SameBytesReader()
    captured_levels: list[SecurityLevel] = []
    module = importlib.import_module("src.application.use_cases.run_query")
    real_factory = module.create_outbound_safety_proof

    def recording_factory(*args, **kwargs):
        captured_levels.append(kwargs["security_level"])
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(module, "create_outbound_safety_proof", recording_factory)
    use_case, _, _, _ = _use_case(
        [card],
        sources=FakeSources([source]),
        material_reader=reader,
    )

    use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective",
            historical_version=None,
        )
    )

    assert captured_levels == [SecurityLevel.L2_INTERNAL]
    assert set(reader.calls[0]) == {"BASE-LLD-724_1", "SRC-L1"}


@pytest.mark.parametrize(
    "restricted_level",
    [SecurityLevel.L3_CONFIDENTIAL, SecurityLevel.L4_RESTRICTED],
)
def test_current_query_fails_closed_for_restricted_baseline_sharing_l1_source_hash(
    restricted_level: SecurityLevel,
) -> None:
    """Catches a restricted supporting material being mapped to an L1 proof after hash dedupe."""
    shared_sha256 = "b" * 64
    card = _card("RULE-LLD-001", source_refs=["SRC-L1"])

    class RestrictedBaselineReader(FakeMaterialReader):
        def read_baseline(self, **context):
            return replace(
                super().read_baseline(**context),
                security_level=restricted_level,
            )

        def read_source(self, source):
            material = super().read_source(source)
            return replace(
                material,
                text="# 当前产品方案\n## 目标客群\n" + card.content,
                sha256=shared_sha256,
            )

    source = _source("SRC-L1").model_copy(
        update={
            "sha256": shared_sha256,
            "security_level": SecurityLevel.L1_PUBLIC_SIMULATED,
        }
    )
    gateway = ProofCheckingGateway()
    reader = RestrictedBaselineReader([card])
    use_case, _, _, _ = _use_case(
        [card],
        gateway=gateway,
        sources=FakeSources([source]),
        material_reader=reader,
    )

    with pytest.raises(DomainError, match="EXTERNAL_CALL_DENIED"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="当前目标客群是什么？",
                scope="effective",
                historical_version=None,
            )
        )

    assert gateway.last_inputs is None
    assert reader.calls == []


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


@pytest.mark.parametrize(
    "baseline",
    [
        _historical_baseline(status=BaselineStatus.DRAFT),
        _historical_baseline(status=BaselineStatus.FAILED),
        _historical_baseline(status=BaselineStatus.EFFECTIVE),
        _historical_baseline(project_id="OTHER"),
        _historical_baseline(version="LLD-OTHER"),
        _historical_baseline(version="LLD-724_1"),
    ],
    ids=[
        "draft",
        "failed",
        "effective",
        "wrong-project",
        "wrong-version",
        "manifest-current",
    ],
)
def test_historical_query_accepts_only_same_project_superseded_non_current_baseline(
    baseline: Baseline,
) -> None:
    """Catches treating an arbitrary Baseline row as an authorized historical scope."""
    requested_version = "LLD-724_1" if baseline.version == "LLD-724_1" else "LLD-700_1"
    use_case, gateway, _, _ = _use_case(
        [_card("RULE-HISTORY-001", version=requested_version)],
        baselines=FakeBaselines(baseline),
    )

    with pytest.raises(DomainError, match="HISTORICAL_VERSION_INVALID"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="历史规则是什么？",
                scope="historical",
                historical_version=requested_version,
            )
        )

    assert gateway.last_inputs is None


def test_historical_query_loads_only_the_explicit_version_without_notices() -> None:
    """Catches historical cards or current notices crossing version boundaries."""
    historical = _card("RULE-HISTORY-001", version="LLD-700_1", content="历史客群规则。")
    current = _card("RULE-LLD-001")
    historical_source = _source().model_copy(
        update={
            "document_version": "v0.9",
            "applicable_baseline_version": "LLD-700_1",
        }
    )
    use_case, gateway, baselines, reader = _use_case(
        [historical, current],
        sources=FakeSources([historical_source]),
    )

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
    assert reader.baseline_calls == []
    assert reader.calls[0] == ("SRC-001",)


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
            "section": "heading:当前产品方案 > 目标客群; line:1",
            "excerpt": "当前目标客群是符合准入要求的存量客户。",
            "authority_level": "formal_effective",
        }
    ]
    assert response.citations[0].filename == "当前产品方案.md"


def test_missing_source_uses_verified_manifest_asset_not_synthesized_source_metadata() -> None:
    """Catches fallback citations that invent metadata for a missing SourceRecord."""
    card = _card(
        "RULE-LLD-001",
        source_refs=["SRC-MISSING"],
    ).model_copy(update={"applicable_scope": "伪造章节"})
    use_case, gateway, _, reader = _use_case([card], sources=FakeSources([]))

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective",
            historical_version=None,
        )
    )

    assert reader.baseline_calls == [
        {
            "project_id": "LLD",
            "asset_id": "BASE-LLD-724_1",
            "version": "LLD-724_1",
            "relative_path": "data/baselines/LLD-724_1/full.md",
            "expected_sha256": "b" * 64,
        }
    ]
    assert gateway.last_inputs["citations"] == [
        {
            "id": "CIT-BASE-LLD-724_1-01",
            "source_id": "BASE-LLD-724_1",
            "filename": "full.md",
            "document_version": "LLD-724_1",
            "section": "heading:当前产品方案 > 目标客群; line:3",
            "excerpt": "当前目标客群是符合准入要求的存量客户。",
            "authority_level": "formal_effective",
        }
    ]
    assert response.citations[0].source_id == "BASE-LLD-724_1"


def test_card_text_not_found_in_verified_source_or_baseline_is_rejected() -> None:
    """Catches a persisted card self-proving its own excerpt without matching real source text."""

    class MismatchReader(FakeMaterialReader):
        def read_baseline(self, **context):
            material = super().read_baseline(**context)
            return material.__class__(
                **{
                    **material.__dict__,
                    "text": "# 当前产品方案\n原文不包含卡片断言。",
                    "fragments": (VerifiedFragment(locator="line:2", text="原文不包含卡片断言。"),),
                }
            )

        def read_source(self, source):
            material = super().read_source(source)
            return material.__class__(
                **{
                    **material.__dict__,
                    "text": "来源原文不包含卡片断言。",
                    "fragments": (
                        VerifiedFragment(
                            locator="line:1",
                            text="来源原文不包含卡片断言。",
                            fragment_id="SRC-001-0001",
                        ),
                    ),
                }
            )

    gateway = ProofCheckingGateway()
    reader = MismatchReader([_card("RULE-LLD-001")])
    use_case, _, _, _ = _use_case(
        [_card("RULE-LLD-001")],
        gateway=gateway,
        material_reader=reader,
    )

    with pytest.raises(DomainError, match="CITATION_INVALID"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="当前目标客群是什么？",
                scope="effective",
                historical_version=None,
            )
        )

    assert gateway.last_inputs is None


def test_query_rejects_result_if_manifest_changes_during_gateway_call() -> None:
    """Catches mixing evidence from one Manifest snapshot with a newer current answer."""

    class ChangingManifest(FakeManifest):
        def read_snapshot(self):
            snapshot = super().read_snapshot()
            if len(self.events) == 1:
                return snapshot
            return ManifestSnapshot(
                snapshot.manifest.model_copy(update={"current_version": "LLD-724_2"}),
                "e" * 64,
            )

    events: list[str] = []
    gateway = ProofCheckingGateway()
    use_case, _, _, _ = _use_case(
        [_card("RULE-LLD-001")],
        gateway=gateway,
        manifest=ChangingManifest(events),
    )

    with pytest.raises(DomainError, match="BASELINE_INTEGRITY_FAILED"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="当前目标客群是什么？",
                scope="effective",
                historical_version=None,
            )
        )

    assert gateway.last_inputs is not None
    assert events.count("manifest") == 2


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


def test_query_rejects_notice_content_repeated_as_the_answer() -> None:
    """Catches candidate or conflict notices being promoted into the current answer."""

    class NoticeAnswerGateway(ProofCheckingGateway):
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            result = super().run(
                inputs,
                safety_proof=safety_proof,
                user=user,
                timeout_seconds=timeout_seconds,
            )
            result["result"]["answer"] = "建议收紧客群。"
            return result

    cards = [
        _card("RULE-LLD-001"),
        _card(
            "RULE-CANDIDATE-001",
            status=KnowledgeStatus.CANDIDATE,
            content="建议收紧客群。",
            source_refs=["SRC-CANDIDATE"],
        ),
    ]
    use_case, _, _, _ = _use_case(
        cards,
        gateway=NoticeAnswerGateway(),
        sources=FakeSources([_source(), _source("SRC-CANDIDATE")]),
    )

    with pytest.raises(OutputValidationError, match="NOTICE_CONTENT_IN_ANSWER"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="当前客群是什么？",
                scope="effective_with_notices",
                historical_version=None,
            )
        )


def test_query_rejects_returned_rule_without_one_of_its_own_citations() -> None:
    """Catches a cited first rule laundering an uncited second returned rule."""

    class MissingRuleCitationGateway(ProofCheckingGateway):
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            result = super().run(
                inputs,
                safety_proof=safety_proof,
                user=user,
                timeout_seconds=timeout_seconds,
            )
            result["result"]["citations"] = result["result"]["citations"][:1]
            return result

    cards = [
        _card("RULE-LLD-001"),
        _card(
            "RULE-LLD-002",
            content="当前客群需通过实名认证。",
            source_refs=["SRC-002"],
        ),
    ]
    use_case, _, _, _ = _use_case(
        cards,
        gateway=MissingRuleCitationGateway(),
        sources=FakeSources([_source(), _source("SRC-002")]),
    )

    with pytest.raises(OutputValidationError, match="EFFECTIVE_RULE_CITATION_MISSING"):
        use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question="当前客群规则是什么？",
                scope="effective",
                historical_version=None,
            )
        )


def test_query_degrades_when_any_answer_claim_lacks_one_way_excerpt_support() -> None:
    """Catches symmetric substring matching accepting an extra unsupported assertion."""

    class ExtraClaimGateway(ProofCheckingGateway):
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            result = super().run(
                inputs,
                safety_proof=safety_proof,
                user=user,
                timeout_seconds=timeout_seconds,
            )
            result["result"]["answer"] += "额外的新断言。"
            return result

    use_case, _, _, _ = _use_case(
        [_card("RULE-LLD-001")],
        gateway=ExtraClaimGateway(),
    )

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前客群是什么？",
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
        sources=FakeSources([_source(), *[_source(source_id) for source_id in source_ids]]),
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
