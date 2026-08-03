from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from src.application.dto.query import RunQueryInput
from src.application.ports.dashboard import ManifestSnapshot
from src.application.use_cases.run_query import RunQuery
from src.domain.enums import AuthorityLevel, KnowledgeStatus, SecurityLevel
from src.domain.models import Baseline, BaselineManifest, KnowledgeCard, SourceRecord
from src.infrastructure.gateways.query_gateway import QueryGateway

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)
CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/gold_query.json").read_text(encoding="utf-8")
)


class GoldenClient:
    def run(self, *, inputs, user, timeout_seconds):
        card = inputs["effective_cards"][0]
        citation_id = card["source_citations"][0]
        citation = next(item for item in inputs["citations"] if item["id"] == citation_id)
        notices = {item["type"]: item["summary"] for item in inputs["notices"]}
        return {
            "workflow_run_id": "WF-GOLDEN",
            "result": {
                "answer": card["content"],
                "effective_rules": [item["id"] for item in inputs["effective_cards"]],
                "citations": [citation],
                "candidate_notice": notices.get("candidate"),
                "conflict_notice": notices.get("conflict"),
                "baseline_version": inputs["baseline_version"],
                "evidence_sufficiency": "sufficient",
                "result_mode": "realtime",
                "model_call_id": "CALL-GOLDEN",
            },
        }


class Manifest:
    def read_snapshot(self):
        return ManifestSnapshot(
            BaselineManifest(
                schema_version="1.0",
                project_id="LLD",
                current_baseline_id="BASE-LLD-724_1",
                current_version="LLD-724_1",
                parent_baseline_id=None,
                full_document_path="data/baselines/LLD-724_1/full.md",
                card_snapshot_path="data/baselines/LLD-724_1/cards.json",
                full_document_sha256="a" * 64,
                card_snapshot_sha256="b" * 64,
                change_request_id=None,
                approved_by="产品经理",
                published_at=NOW,
            ),
            "c" * 64,
        )


class Baselines:
    def get_by_version(self, project_id, version):
        return Baseline(
            id="BASE-HISTORY",
            project_id=project_id,
            version=version,
            parent_baseline_id=None,
            status="superseded",
            full_document_path=f"data/baselines/{version}/full.md",
            card_snapshot_path=f"data/baselines/{version}/cards.json",
            manifest_sha256="d" * 64,
            change_request_id=None,
            approved_by="产品经理",
            effective_at=NOW,
            created_at=NOW,
        )


class Knowledge:
    def __init__(self, cards):
        self.cards = cards

    def list_effective(self, project_id, version):
        return list(self.cards)

    def list_notices(self, project_id, version):
        return list(self.cards)


class Sources:
    def __init__(self, sources):
        self.sources = {source.id: source for source in sources}

    def get(self, source_id):
        return self.sources[source_id]


class MaterialReader:
    def total_chars(self, baseline_path, sources):
        return 100_000


def _card(card_id: str, version: str, source_id: str) -> KnowledgeCard:
    return KnowledgeCard(
        id=card_id,
        project_id="LLD",
        card_type="rule",
        title=f"{card_id} 标题",
        content=f"{card_id} 的当前可验证结论。",
        status=KnowledgeStatus.EFFECTIVE,
        product_version=version,
        applicable_scope=f"产品方案 > {card_id}",
        source_refs=[source_id],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品经理",
        created_at=NOW,
        updated_at=NOW,
    )


def _source(source_id: str, version: str) -> SourceRecord:
    return SourceRecord(
        id=source_id,
        project_id="LLD",
        original_filename=f"{source_id}-产品方案.md",
        archive_path=f"/trusted/{source_id}/source.md",
        sha256=(source_id.encode().hex() + "0" * 64)[:64],
        mime_type="text/markdown",
        size_bytes=100_000,
        source_type="formal_document",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version=version,
        applicable_baseline_version=version,
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=True,
        is_sandbox=False,
        ingest_status="completed",
        created_at=NOW,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_query_golden_scope_isolation_and_major_citations(case) -> None:
    version = case["expected_version"]
    required_cards = case["required_card_ids"]
    source_ids = [
        citation_id.removeprefix("CIT-").removesuffix("-01")
        for citation_id in case["required_citation_ids"]
    ]
    cards = [
        _card(card_id, version, source_id)
        for card_id, source_id in zip(required_cards, source_ids, strict=True)
    ]
    cards.append(
        KnowledgeCard(
            id="RULE-CANDIDATE-001",
            project_id="LLD",
            card_type="rule",
            title="候选变化",
            content="候选内容只能作为 notice。",
            status=KnowledgeStatus.CANDIDATE,
            product_version=version,
            applicable_scope="候选",
            source_refs=["SRC-CANDIDATE"],
            authority_level=AuthorityLevel.PROFESSIONAL_OPINION,
            owner="产品经理",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    gateway = QueryGateway(GoldenClient())
    use_case = RunQuery(
        manifest=Manifest(),
        baselines=Baselines(),
        knowledge=Knowledge(cards),
        sources=Sources([_source(source_id, version) for source_id in source_ids]),
        material_reader=MaterialReader(),
        gateway=gateway,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        task_id_factory=lambda: f"TASK-{case['id']}",
    )

    response = use_case.execute(
        RunQueryInput(
            project_id="LLD",
            question=case["question"],
            scope=case["scope"],
            historical_version=case.get("historical_version"),
        )
    )

    assert response.baseline_version == case["expected_version"]
    assert set(case["required_card_ids"]) <= set(response.effective_rules)
    assert set(case["forbidden_card_ids"]).isdisjoint(response.effective_rules)
    assert set(case["required_citation_ids"]) <= {item.id for item in response.citations}
    assert response.evidence_sufficiency == "sufficient"
