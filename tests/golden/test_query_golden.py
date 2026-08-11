from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from src.application.dto.query import RunQueryInput
from src.application.ports.dashboard import ManifestSnapshot
from src.application.use_cases.run_query import RunQuery
from src.domain.enums import AuthorityLevel, KnowledgeStatus, SecurityLevel
from src.domain.models import (
    Baseline,
    BaselineManifest,
    KnowledgeCard,
    Project,
    SourceRecord,
)
from src.infrastructure.files.query_material_reader import (
    VerifiedFragment,
    VerifiedQueryMaterial,
)
from src.infrastructure.gateways.query_gateway import QueryGateway

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)
CURRENT_VERSION = "LLD-724_1"
HISTORICAL_VERSION = "LLD-700_1"
CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/gold_query.json").read_text(encoding="utf-8")
)

# This corpus is fixed and shared by every question. Expected results never shape repository data.
CORPUS = (
    (
        "RULE-LLD-001",
        "目标客群",
        "当前目标客群是符合准入要求的存量客户。",
        "SRC-001",
        CURRENT_VERSION,
    ),
    (
        "RULE-LLD-002",
        "核心价值",
        "当前产品的核心价值是缩短产品口径查找时间。",
        "SRC-002",
        CURRENT_VERSION,
    ),
    (
        "RULE-LLD-003",
        "产品范围",
        "当前产品范围包含资料导入、当前查询和变更追溯。",
        "SRC-003",
        CURRENT_VERSION,
    ),
    (
        "RULE-LLD-004",
        "使用场景",
        "当前主要使用场景是会前查口径和会后追决策。",
        "SRC-004",
        CURRENT_VERSION,
    ),
    (
        "RULE-LLD-005",
        "适用条件",
        "当前规则仅适用于已完成实名认证的客户。",
        "SRC-005",
        CURRENT_VERSION,
    ),
    (
        "RULE-LLD-006",
        "关键约束",
        "当前版本的关键约束是候选内容不得作为生效规则。",
        "SRC-006",
        CURRENT_VERSION,
    ),
    (
        "RULE-LLD-007",
        "权威口径",
        "当前版本仅以 Manifest 指向的基线为权威口径。",
        "SRC-007",
        CURRENT_VERSION,
    ),
    ("RULE-LLD-008", "候选变化", "当前结论与候选变化分开展示。", "SRC-008", CURRENT_VERSION),
    (
        "RULE-LLD-009",
        "直接依据",
        "当前结论的直接依据是可回溯的正式材料原文。",
        "SRC-009",
        CURRENT_VERSION,
    ),
    (
        "RULE-HISTORY-001",
        "历史规则",
        "LLD-700_1 的历史规则是仅服务已签约客户。",
        "SRC-HISTORY",
        HISTORICAL_VERSION,
    ),
)
CANDIDATE = (
    "RULE-CANDIDATE-001",
    "候选提示",
    "候选内容建议将目标客群扩大到新客户。",
    "SRC-CANDIDATE",
    CURRENT_VERSION,
)
CONTENT_BY_SOURCE = {source_id: content for _, _, content, source_id, _ in (*CORPUS, CANDIDATE)}


def _material_text(source_id: str) -> str:
    return CONTENT_BY_SOURCE[source_id] + "\n" + "已脱敏的独立黄金评测材料。" * 2500


class QuestionDrivenClient:
    """Deterministically selects from the fixed corpus using the question, not expectations."""

    def __init__(self) -> None:
        self.last_inputs = None

    def run(self, *, inputs, user, timeout_seconds):
        self.last_inputs = inputs
        question = inputs["question"]
        matches = [card for card in inputs["effective_cards"] if card["title"] in question]
        if len(matches) != 1:
            raise AssertionError(f"question must select exactly one fixed-corpus card: {question}")
        card = matches[0]
        allowed_citation_ids = set(card["source_citations"])
        citations = [
            citation for citation in inputs["citations"] if citation["id"] in allowed_citation_ids
        ]
        notices = {item["type"]: item["summary"] for item in inputs["notices"]}
        return {
            "workflow_run_id": "WF-GOLDEN",
            "result": {
                "answer": card["content"],
                "effective_rules": [card["id"]],
                "citations": citations,
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
                current_version=CURRENT_VERSION,
                parent_baseline_id=None,
                full_document_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"),
                card_snapshot_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json"),
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
            full_document_sha256="e" * 64,
            card_snapshot_sha256="f" * 64,
            change_request_id=None,
            approved_by="产品经理",
            effective_at=NOW,
            created_at=NOW,
        )

    def list_for_project(self, project_id):
        return [
            self.get_by_version(project_id, HISTORICAL_VERSION),
            Baseline(
                id="BASE-LLD-724_1",
                project_id=project_id,
                version=CURRENT_VERSION,
                parent_baseline_id=None,
                status="effective",
                full_document_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"),
                card_snapshot_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json"),
                manifest_sha256="d" * 64,
                full_document_sha256="a" * 64,
                card_snapshot_sha256="b" * 64,
                change_request_id=None,
                approved_by="产品经理",
                effective_at=NOW,
                created_at=NOW,
            ),
        ]


class Projects:
    def get(self, project_id):
        return Project(
            id=project_id,
            name="产品智策",
            product_line="轻量交付",
            stage="golden",
            current_baseline_id="BASE-LLD-724_1",
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )


class Knowledge:
    def __init__(self, cards):
        self.cards = cards

    def list_effective(self, project_id, version):
        return list(self.cards)

    def list_notices(self, project_id, version):
        return list(self.cards)


class BaselineCards:
    """Version-snapshot card reader backed by the fixed corpus, never SQLite."""

    def __init__(self, cards):
        self.cards = cards

    def read_version_cards(self, *, project_id, version, relative_path, expected_sha256):
        return [
            card
            for card in self.cards
            if card.project_id == project_id and card.product_version == version
        ]


class Sources:
    def __init__(self, sources):
        self.sources = {source.id: source for source in sources}

    def get(self, source_id):
        if source_id not in self.sources:
            raise KeyError(source_id)
        return self.sources[source_id]


class MaterialReader:
    def read_baseline(self, **context):
        assert context == {
            "project_id": "LLD",
            "asset_id": "BASE-LLD-724_1",
            "version": CURRENT_VERSION,
            "relative_path": ("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"),
            "expected_sha256": "a" * 64,
        }
        text = "\n".join(row[2] for row in CORPUS if row[4] == CURRENT_VERSION)
        return VerifiedQueryMaterial(
            source_id=context["asset_id"],
            filename="full.md",
            document_version=context["version"],
            sha256=context["expected_sha256"],
            text=text,
            fragments=tuple(
                VerifiedFragment(locator=f"heading:{title}", text=content)
                for _, title, content, _, version in CORPUS
                if version == CURRENT_VERSION
            ),
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            security_level=SecurityLevel.L2_INTERNAL,
            is_baseline_asset=True,
        )

    def read_source(self, source):
        text = _material_text(source.id)
        return VerifiedQueryMaterial(
            source_id=source.id,
            filename=source.original_filename,
            document_version=source.document_version,
            sha256=source.sha256,
            text=text,
            fragments=(
                VerifiedFragment(
                    locator=f"heading:{source.id}",
                    text=CONTENT_BY_SOURCE[source.id],
                    fragment_id=f"{source.id}-0001",
                ),
            ),
            authority_level=source.authority_level,
            security_level=source.security_level,
            is_baseline_asset=False,
        )

    def total_chars(self, materials):
        return sum({material.sha256: len(material.text) for material in materials}.values())


def _card(row, *, status=KnowledgeStatus.EFFECTIVE) -> KnowledgeCard:
    card_id, title, content, source_id, version = row
    return KnowledgeCard(
        id=card_id,
        project_id="LLD",
        card_type="rule",
        title=title,
        content=content,
        status=status,
        product_version=version,
        applicable_scope=f"产品方案 > {title}",
        source_refs=[source_id],
        authority_level=(
            AuthorityLevel.FORMAL_EFFECTIVE
            if status == KnowledgeStatus.EFFECTIVE
            else AuthorityLevel.PROFESSIONAL_OPINION
        ),
        owner="产品经理",
        created_at=NOW,
        updated_at=NOW,
    )


def _source(row) -> SourceRecord:
    _, _, _, source_id, version = row
    text = _material_text(source_id)
    return SourceRecord(
        id=source_id,
        project_id="LLD",
        original_filename=f"{source_id}-产品方案.md",
        archive_path=f"/trusted/{source_id}/{source_id}-产品方案.md",
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        mime_type="text/markdown",
        size_bytes=len(text.encode()),
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


def _build_use_case(client):
    cards = [_card(row) for row in CORPUS]
    cards.append(_card(CANDIDATE, status=KnowledgeStatus.CANDIDATE))
    return RunQuery(
        manifest=Manifest(),
        baselines=Baselines(),
        projects=Projects(),
        knowledge=Knowledge(cards),
        sources=Sources([_source(row) for row in (*CORPUS, CANDIDATE)]),
        baseline_cards=BaselineCards(cards),
        material_reader=MaterialReader(),
        gateway=QueryGateway(client, timeout_seconds=30),
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        task_id_factory=lambda: "TASK-GOLDEN",
    )


def test_query_golden_fixed_multicard_corpus_scores() -> None:
    scores: Counter[str] = Counter()
    client = QuestionDrivenClient()
    use_case = _build_use_case(client)

    for case in CASES:
        response = use_case.execute(
            RunQueryInput(
                project_id="LLD",
                question=case["question"],
                scope=case["scope"],
                historical_version=case.get("historical_version"),
            )
        )
        outbound_rule_ids = {card["id"] for card in client.last_inputs["effective_cards"]}
        returned_citation_ids = {citation.id for citation in response.citations}

        scores["fact"] += response.answer == case["expected_answer"]
        scores["range"] += (
            response.baseline_version == case["expected_version"]
            and client.last_inputs["scope"] == case["scope"]
        )
        scores["rules"] += set(response.effective_rules) == set(case["required_card_ids"])
        scores["citations"] += set(case["required_citation_ids"]) <= returned_citation_ids
        scores["isolation"] += (
            set(case["forbidden_card_ids"]).isdisjoint(response.effective_rules)
            and "RULE-CANDIDATE-001" not in outbound_rule_ids
            and all(
                (rule_id == "RULE-HISTORY-001") == (case["scope"] == "historical")
                for rule_id in outbound_rule_ids
            )
        )

    assert scores["fact"] >= 9
    assert scores["range"] >= 9
    assert scores["rules"] >= 9
    assert scores["citations"] == 10
    assert scores["isolation"] == 10
