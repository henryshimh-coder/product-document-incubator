from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

from src.domain.enums import (
    AuthorityLevel,
    BaselineStatus,
    IssueSeverity,
    KnowledgeStatus,
)
from src.domain.models import Baseline, KnowledgeCard

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _inputs(case: dict) -> dict:
    baseline_excerpt = case.get("baseline_excerpt", "当前基线规则。")
    comparison_excerpt = case.get("comparison_excerpt")
    return {
        "schema_version": "1.0",
        "input_contract_version": "2.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": f"TASK-{case['name']}",
        "language": "zh-CN",
        "baseline_rules": [
            {
                "id": "RULE-001",
                "source_id": "BASE-LLD-724_1",
                "citation_id": "CIT-BASE-001",
                "document_version": "LLD-724_1",
                "page_or_section": "当前规则",
                "excerpt": baseline_excerpt,
            }
        ],
        "comparison_items": (
            []
            if comparison_excerpt is None
            else [
                {
                    "id": "NOTICE-001",
                    "source_id": "SRC-COMPARE",
                    "citation_id": "CIT-COMPARE-001",
                    "document_version": "v1.0",
                    "page_or_section": "比较资料",
                    "excerpt": comparison_excerpt,
                }
            ]
        ),
        "deterministic_findings": [],
        "allowed_issue_types": [
            "conflict",
            "omission",
            "stale",
            "not_synchronized",
            "insufficient_evidence",
        ],
    }


def _semantic_output(inputs: dict) -> dict:
    comparison = " ".join(item["excerpt"] for item in inputs["comparison_items"])
    issue_type = None
    if "收紧准入" in comparison:
        issue_type = "conflict"
    elif "新增一项风险限制" in comparison:
        issue_type = "not_synchronized"
    if issue_type is None:
        return {"schema_version": "1.0", "issues": []}
    baseline = inputs["baseline_rules"][0]
    challenge = inputs["comparison_items"][0]
    baseline_evidence = {key: value for key, value in baseline.items() if key != "id"}
    challenge_evidence = {key: value for key, value in challenge.items() if key != "id"}
    return {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": issue_type,
                "severity": "pending_decision",
                "title": "独立输入触发的语义问题",
                "description": "基线和比较资料表达不一致。",
                "evidence": [
                    {**baseline_evidence, "side": "current_baseline"},
                    {**challenge_evidence, "side": "challenging_source"},
                ],
                "impacted_domains": ["产品", "风险"],
                "options": [{"code": "A", "label": "处理", "impact": "进入会议决策"}],
                "ai_recommendation": "A",
                "ai_confidence": 0.8,
                "uncertainty": "需会议确认",
            }
        ],
    }


def _deterministic(case: dict):
    if "rule_id" not in case:
        return []
    service = importlib.import_module("src.domain.services.deterministic_lint")
    card = KnowledgeCard(
        id="RULE-001",
        project_id="LLD",
        card_type=case["card_type"],
        title=case["name"],
        content=case["card_content"],
        status=KnowledgeStatus(case["card_status"]),
        product_version=case["card_version"],
        applicable_scope="演示",
        source_refs=case["source_refs"],
        authority_level=AuthorityLevel.PROFESSIONAL_OPINION,
        owner="产品",
        created_at=NOW,
        updated_at=NOW,
    )
    baseline = Baseline(
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
    facts = service.DeterministicRuleFacts.model_validate(case.get("deterministic_facts", {}))
    finding = service.run_rule(case["rule_id"], card=card, baseline=baseline, facts=facts)
    return [] if finding is None else [finding]


def test_lint_golden_runs_independent_inputs_through_the_real_use_case() -> None:
    """Catches a golden test grading pre-authored expected outputs instead of detection."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    cases = json.loads(Path("tests/fixtures/gold_lint.json").read_text(encoding="utf-8"))
    true_positives = 0
    false_negatives = 0
    false_positives = 0
    severity_matches = 0
    majors = []

    for case in cases:
        inputs = _inputs(case)

        deterministic = _deterministic(case)
        semantic_output = _semantic_output(inputs)

        class Local:
            def __init__(self, findings):
                self.findings = findings

            def run(self, command):
                return self.findings

        class Builder:
            def __init__(self, current_inputs):
                self.current_inputs = current_inputs

            def build_minimum(self, command, deterministic):
                return dto.LintComparisonPackage(
                    inputs=self.current_inputs,
                    source_total_chars=100_000,
                    security_level="L2",
                )

        class Gateway:
            def __init__(self, case_name):
                self.case_name = case_name

            def run(self, outbound, *, safety_proof, user=None, timeout_seconds=30):
                return {
                    "workflow_run_id": f"WF-{self.case_name}",
                    "result": _semantic_output(outbound),
                }

        class Issues:
            def upsert_all(self, issues):
                self.saved = issues

        report = module.RunLint(
            local_lint=Local(deterministic),
            comparison_builder=Builder(inputs),
            gateway=Gateway(case["name"]),
            issues=Issues(),
            customer_names=(),
            strategy_terms=(),
            financial_terms=(),
            leader_names=(),
            unpublished_decisions=(),
            now=lambda: NOW,
        ).execute(dto.RunLintInput(project_id="LLD", scope="current"))

        raw_severities = [
            finding.severity.value
            for finding in deterministic
            if finding.issue_type == case["expected_issue_type"]
        ]
        raw_severities.extend(
            issue["severity"]
            for issue in semantic_output["issues"]
            if issue["issue_type"] == case["expected_issue_type"]
        )

        if case["expected_detected"]:
            assert case["expected_raw_severity"] in raw_severities
            matches = [
                issue for issue in report.issues if issue.issue_type == case["expected_issue_type"]
            ]
            if matches:
                true_positives += 1
                severity_matches += any(
                    issue.severity.value == case["expected_validated_severity"] for issue in matches
                )
                assert any(
                    issue.severity.value == case["expected_validated_severity"]
                    and (None if issue.raw_severity is None else issue.raw_severity.value)
                    == case["expected_persisted_raw_severity"]
                    and issue.deterministic_rule_id == case["expected_deterministic_rule_id"]
                    and issue.validation_note == case["expected_validation_note"]
                    for issue in matches
                )
            else:
                false_negatives += 1
        else:
            assert raw_severities == []
            false_positives += bool(report.issues)
        majors.extend(
            issue
            for issue in report.issues
            if issue.severity in {IssueSeverity.BLOCKING, IssueSeverity.PENDING_DECISION}
        )

    assert 8 <= len(cases) <= 10
    assert true_positives / (true_positives + false_negatives) >= 0.80
    assert false_positives == 0
    assert severity_matches / true_positives >= 0.80
    assert majors
    assert all(
        {evidence.side.value for evidence in issue.evidence}
        == {"current_baseline", "challenging_source"}
        for issue in majors
    )
