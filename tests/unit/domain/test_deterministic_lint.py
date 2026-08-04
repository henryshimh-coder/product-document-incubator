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
    """Catches a candidate card being accepted or promoted without independent evidence."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")

    finding = lint.run_rule("GOV-001", card=_card(KnowledgeStatus.CANDIDATE), baseline=_baseline())

    assert finding is not None
    assert finding.severity == IssueSeverity.BLOCKING
    assert finding.rule_id == "GOV-001"
    assert finding.evidence == []
    assert finding.uncertainty == "该结果是基线快照规则事实，缺少独立挑战依据"


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


def test_version_findings_do_not_synthesize_a_second_evidence_source() -> None:
    """Catches CARD-* placeholders making a local rule fact look independently corroborated."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")
    stale = _card(KnowledgeStatus.EFFECTIVE).model_copy(
        update={"product_version": "LLD-700_1", "card_type": "technical_solution"}
    )

    for rule_id in ("VER-001", "VER-002"):
        finding = lint.run_rule(rule_id, card=stale, baseline=_baseline())
        assert finding is not None
        assert (
            finding.severity
            == {
                "VER-001": IssueSeverity.BLOCKING,
                "VER-002": IssueSeverity.PENDING_DECISION,
            }[rule_id]
        )
        assert finding.evidence == []
        assert finding.uncertainty is not None


def test_each_configured_rule_has_an_explicit_trigger_or_safe_no_trigger_path() -> None:
    """Catches configured rules existing only as labels without executable behavior."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")
    baseline = _baseline()
    base = _card(KnowledgeStatus.CANDIDATE)
    scenarios = {
        "STR-001": (base.model_copy(update={"source_refs": []}), {}),
        "STR-002": (base, {"known_source_ids": {baseline.id}}),
        "GOV-001": (base, {}),
        "GOV-002": (
            base,
            {
                "facts": lint.DeterministicRuleFacts(
                    unauthorized_model_call=True,
                )
            },
        ),
        "GOV-003": (
            base.model_copy(update={"card_type": "formal_decision"}),
            {"facts": lint.DeterministicRuleFacts(change_mapping_exists=False)},
        ),
        "VER-001": (base.model_copy(update={"product_version": "LLD-700_1"}), {}),
        "VER-002": (
            base.model_copy(
                update={
                    "product_version": "LLD-700_1",
                    "card_type": "technical_solution",
                }
            ),
            {},
        ),
        "MKT-001": (
            base.model_copy(update={"card_type": "market_judgment", "source_refs": []}),
            {},
        ),
        "COST-001": (
            base.model_copy(
                update={
                    "card_type": "cost_parameter_change",
                    "content": "成本参数由 10 调整为 12，待确认。",
                }
            ),
            {"facts": lint.DeterministicRuleFacts(cost_recalculation_exists=False)},
        ),
    }

    findings = {
        rule_id: lint.run_rule(rule_id, card=card, baseline=baseline, **context)
        for rule_id, (card, context) in scenarios.items()
    }

    assert set(findings) == {
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
    assert all(finding is not None for finding in findings.values())
    assert findings["GOV-002"].title == "未授权资料禁止外部模型调用"
    assert findings["GOV-003"].severity == IssueSeverity.PENDING_DECISION
    assert findings["COST-001"].severity == IssueSeverity.PENDING_DECISION


def test_raw_major_rule_fact_is_downgraded_only_when_converted_to_issue() -> None:
    """Catches configured blocking severity being erased before evidence validation."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")
    run_lint = importlib.import_module("src.application.use_cases.run_lint")
    raw = lint.run_rule(
        "GOV-001",
        card=_card(KnowledgeStatus.CANDIDATE),
        baseline=_baseline(),
    )

    assert raw is not None
    assert raw.severity == IssueSeverity.BLOCKING

    issue = run_lint.LintIssueValidator(now=lambda: NOW).validate_finding(
        raw,
        project_id="LLD",
    )

    assert issue.severity == IssueSeverity.PENDING_INFO
    assert issue.raw_severity == IssueSeverity.BLOCKING
    assert issue.deterministic_rule_id == "GOV-001"
    assert issue.validation_note == ("严重度由 blocking 降级为 pending_info：缺少独立挑战依据")
    assert issue.uncertainty == "缺少独立挑战依据"


def test_context_rules_do_not_invent_violations_without_required_runtime_facts() -> None:
    """Catches GOV-002 and STR-002 firing when authorization/existence is unknown."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")
    card = _card(KnowledgeStatus.CANDIDATE)

    assert lint.run_rule("GOV-002", card=card, baseline=_baseline()) is None
    assert lint.run_rule("STR-002", card=card, baseline=_baseline()) is None
    assert (
        lint.run_rule(
            "GOV-002",
            card=card,
            baseline=_baseline(),
            facts=lint.DeterministicRuleFacts(unauthorized_model_call=False),
        )
        is None
    )


def test_cost_rule_distinguishes_unrecalculated_text_from_completed_result() -> None:
    """Catches cost prose being used instead of a persisted recalculation fact."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")
    card = _card(KnowledgeStatus.CANDIDATE).model_copy(
        update={"card_type": "cost_parameter_change"}
    )

    pending = lint.run_rule(
        "COST-001",
        card=card.model_copy(update={"content": "成本参数已调整，文本声称已重算。"}),
        baseline=_baseline(),
        facts=lint.DeterministicRuleFacts(cost_recalculation_exists=False),
    )
    completed = lint.run_rule(
        "COST-001",
        card=card.model_copy(update={"content": "成本参数已调整，文本声称未重算。"}),
        baseline=_baseline(),
        facts=lint.DeterministicRuleFacts(cost_recalculation_exists=True),
    )

    assert pending is not None
    assert completed is None


def test_change_mapping_rule_uses_persisted_fact_not_card_text_or_refs() -> None:
    """Catches CHANGE-* text conventions replacing the relation/change mapping fact."""
    lint = importlib.import_module("src.domain.services.deterministic_lint")
    card = _card(KnowledgeStatus.CANDIDATE).model_copy(
        update={
            "card_type": "formal_decision",
            "content": "变更单: CHANGE-TEXT-ONLY",
            "source_refs": ["CHANGE-TEXT-ONLY:CIT-001"],
        }
    )

    missing = lint.run_rule(
        "GOV-003",
        card=card,
        baseline=_baseline(),
        facts=lint.DeterministicRuleFacts(change_mapping_exists=False),
    )
    mapped = lint.run_rule(
        "GOV-003",
        card=card.model_copy(update={"content": "无任何变更文本", "source_refs": []}),
        baseline=_baseline(),
        facts=lint.DeterministicRuleFacts(change_mapping_exists=True),
    )

    assert missing is not None
    assert mapped is None
