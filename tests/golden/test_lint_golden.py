from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

from src.domain.enums import IssueSeverity

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def test_lint_golden_recognition_and_major_two_sided_coverage() -> None:
    """Catches recognition dropping below 80% or a major golden issue losing either side."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    cases = json.loads(Path("tests/fixtures/gold_lint.json").read_text(encoding="utf-8"))
    validator = module.LintIssueValidator(now=lambda: NOW)
    expected_positive = [case for case in cases if case["expected_detected"]]
    recognized = 0
    major = []
    for case in cases:
        raw_issue = case.get("model_issue")
        if raw_issue is None:
            continue
        issue = validator.validate_issue(raw_issue, project_id="LLD", target_rule_id=None)
        if issue.issue_type == case["expected_issue_type"]:
            recognized += 1
        if issue.severity in {IssueSeverity.BLOCKING, IssueSeverity.PENDING_DECISION}:
            major.append(issue)

    recognition = recognized / len(expected_positive)
    two_sided = sum(
        {evidence.side.value for evidence in issue.evidence}
        == {"current_baseline", "challenging_source"}
        for issue in major
    ) / len(major)
    assert 8 <= len(cases) <= 10
    assert recognition >= 0.80
    assert two_sided == 1.0
