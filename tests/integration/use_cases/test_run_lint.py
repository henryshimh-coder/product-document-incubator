from __future__ import annotations

import importlib
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from src.domain.enums import CallResultMode, IssueSeverity

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _inputs() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-LINT-001",
        "language": "zh-CN",
        "baseline_rules": [
            {
                "id": "RULE-001",
                "source_id": "SRC-BASE",
                "citation_id": "CIT-BASE-001",
                "document_version": "LLD-724_1",
                "page_or_section": "目标客群",
                "excerpt": "当前目标客群规则。",
            }
        ],
        "comparison_items": [
            {
                "id": "ITEM-001",
                "source_id": "SRC-RISK",
                "citation_id": "CIT-RISK-001",
                "document_version": "v1.0",
                "page_or_section": "客群限制",
                "excerpt": "风险意见要求收紧客群。",
            }
        ],
        "deterministic_findings": [],
        "allowed_issue_types": [
            "conflict",
            "omission",
            "stale",
            "not_synchronized",
            "insufficient_evidence",
        ],
    }


def _single_sided_output() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": "conflict",
                "severity": "blocking",
                "title": "客群边界不一致",
                "description": "需要会议确认当前执行口径",
                "evidence": [
                    {
                        "source_id": "SRC-BASE",
                        "citation_id": "CIT-BASE-001",
                        "excerpt": "当前目标客群规则。",
                        "document_version": "LLD-724_1",
                        "page_or_section": "目标客群",
                        "side": "current_baseline",
                    }
                ],
                "impacted_domains": ["产品", "风险"],
                "options": [{"code": "A", "label": "收紧", "impact": "调整产品规则"}],
                "ai_recommendation": "A",
                "ai_confidence": 0.78,
                "uncertainty": None,
            }
        ],
    }


def test_run_lint_orders_governed_pipeline_and_downgrades_one_sided_major_issue() -> None:
    """Catches external Lint running first or a one-sided major result surviving as blocking."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    events: list[str] = []

    class Local:
        def run(self, command):
            events.append("local")
            return []

    class Builder:
        def build_minimum(self, command, deterministic):
            events.append("comparison")
            return dto.LintComparisonPackage(
                inputs=_inputs(),
                source_total_chars=100_000,
                security_level="L2",
            )

    class Gateway:
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            common = importlib.import_module("src.infrastructure.gateways._common")
            assert isinstance(safety_proof, common.OutboundSafetyProof)
            events.append("semantic")
            return {"workflow_run_id": "WF-LINT-001", "result": _single_sided_output()}

    class Issues:
        def upsert_all(self, issues):
            events.append("upsert")
            self.saved = deepcopy(issues)

    issues = Issues()
    use_case = module.RunLint(
        local_lint=Local(),
        comparison_builder=Builder(),
        gateway=Gateway(),
        issues=issues,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    )

    report = use_case.execute(
        dto.RunLintInput(project_id="LLD", scope="current_plus_source", source_id="SRC-RISK")
    )

    assert events == ["local", "comparison", "semantic", "upsert"]
    assert report.result_mode == CallResultMode.REALTIME
    assert report.model_call_id == "WF-LINT-001"
    assert len(report.issues) == 1
    assert report.issues[0].severity == IssueSeverity.PENDING_INFO
    assert report.issues[0].validation_note == "缺少对方依据"
    assert report.issues[0].uncertainty == "缺少对方依据"
    assert issues.saved == report.issues


def test_run_lint_deduplicates_same_fingerprint_before_upsert() -> None:
    """Catches duplicate model rows creating duplicate persisted issue cards."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    output = _single_sided_output()
    output["issues"].append(deepcopy(output["issues"][0]))

    class Local:
        def run(self, command):
            return []

    class Builder:
        def build_minimum(self, command, deterministic):
            return dto.LintComparisonPackage(
                inputs=_inputs(), source_total_chars=100_000, security_level="L2"
            )

    class Gateway:
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            return {"workflow_run_id": "WF-LINT-001", "result": output}

    class Issues:
        def upsert_all(self, issues):
            self.saved = issues

    issues = Issues()
    report = module.RunLint(
        local_lint=Local(),
        comparison_builder=Builder(),
        gateway=Gateway(),
        issues=issues,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    ).execute(dto.RunLintInput(project_id="LLD", scope="current", source_id=None))

    assert len(report.issues) == 1
    assert report.issues[0].fingerprint
    assert len(issues.saved) == 1
