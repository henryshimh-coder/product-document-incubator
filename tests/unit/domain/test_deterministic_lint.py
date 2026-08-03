from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.domain.enums import (
    AuthorityLevel,
    BaselineStatus,
    IssueSeverity,
    KnowledgeStatus,
)
from src.domain.models import Baseline, KnowledgeCard

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _card(status: KnowledgeStatus) -> KnowledgeCard:
    return KnowledgeCard(
        id="RULE-001",
        project_id="LLD",
        card_type="rule",
        title="目标客群",
        content="当前目标客群是符合准入要求的存量客户。",
        status=status,
        product_version="LLD-724_1",
        applicable_scope="一期",
        source_refs=["SRC-RISK-001:CIT-RISK-001"],
        authority_level=AuthorityLevel.PROFESSIONAL_OPINION,
        owner="风险",
        created_at=NOW,
        updated_at=NOW,
    )


def _baseline() -> Baseline:
    return Baseline(
        id="BASE-LLD-724_1",
        project_id="LLD",
        version="LLD-724_1",
        parent_baseline_id=None,
        status=BaselineStatus.EFFECTIVE,
        full_document_path="data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md",
        card_snapshot_path="data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json",
        manifest_sha256="a" * 64,
        change_request_id=None,
        approved_by="产品经理",
        effective_at=NOW,
        created_at=NOW,
    )


def test_gov_001_blocks_non_effective_card_in_current_baseline() -> None:
    """Catches a candidate card being accepted as part of the effective snapshot."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")

    finding = lint.run_rule("GOV-001", card=_card(KnowledgeStatus.CANDIDATE), baseline=_baseline())

    assert finding is not None
    assert finding.severity == IssueSeverity.BLOCKING
    assert finding.rule_id == "GOV-001"
    assert {item.side.value for item in finding.evidence} == {
        "current_baseline",
        "challenging_source",
    }


def test_gov_001_does_not_flag_effective_current_card() -> None:
    """Catches GOV-001 blocking the valid effective content it is meant to protect."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")

    assert (
        lint.run_rule("GOV-001", card=_card(KnowledgeStatus.EFFECTIVE), baseline=_baseline())
        is None
    )


def test_lint_configuration_contains_the_nine_approved_governed_rules() -> None:
    """Catches a deployment silently omitting an approved deterministic guard."""
    document = yaml.safe_load(Path("config/lint_rules.yaml").read_text(encoding="utf-8"))

    assert {rule["id"] for rule in document["rules"]} == {
        "STR-001",
        "STR-002",
        "GOV-001",
        "GOV-002",
        "GOV-003",
        "VER-001",
        "VER-002",
        "MKT-001",
        "COST-001",
    }


def test_major_version_findings_keep_two_distinct_evidence_sides() -> None:
    """Catches deterministic major version findings failing their own evidence policy."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")
    stale = _card(KnowledgeStatus.EFFECTIVE).model_copy(
        update={"product_version": "LLD-700_1", "card_type": "technical_solution"}
    )

    for rule_id in ("VER-001", "VER-002"):
        finding = lint.run_rule(rule_id, card=stale, baseline=_baseline())
        assert finding is not None
        assert finding.severity in {IssueSeverity.BLOCKING, IssueSeverity.PENDING_DECISION}
        assert len({item.source_id for item in finding.evidence}) == 2
        assert {item.side.value for item in finding.evidence} == {
            "current_baseline",
            "challenging_source",
        }
