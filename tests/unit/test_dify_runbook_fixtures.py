"""T14-R02：Dify runbook 六份 fixture 的文档契约测试。

每份 fixture 必须通过对应 Pydantic 模型校验；Query/Lint 再叠加
application 层语义检查（可信卡片 ID、引用相交、双侧证据）。
变异测试把任一枚举改回手册旧错误值时必须失败。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.infrastructure.gateways.schemas import (
    IngestWorkflowInput,
    IngestWorkflowOutput,
    LintWorkflowInput,
    LintWorkflowOutput,
    QueryWorkflowInput,
    QueryWorkflowOutput,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "docs" / "runbook" / "fixtures" / "dify"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _assert_query_semantics(
    query_input: QueryWorkflowInput, query_output: QueryWorkflowOutput
) -> None:
    trusted_card_ids = {card.id for card in query_input.effective_cards}
    assert set(query_output.effective_rules) <= trusted_card_ids, (
        "effective_rules 必须只含输入 effective_cards 的卡片 ID"
        "（否则应用报 UNKNOWN_EFFECTIVE_RULE）"
    )
    returned_citation_ids = {citation.id for citation in query_output.citations}
    card_citations = {card.id: set(card.source_citations) for card in query_input.effective_cards}
    for rule_id in query_output.effective_rules:
        assert returned_citation_ids & card_citations[rule_id], (
            f"生效卡片 {rule_id} 的返回引用必须与其来源引用相交"
            "（否则应用报 EFFECTIVE_RULE_CITATION_MISSING）"
        )


def _assert_lint_semantics(lint_output: LintWorkflowOutput) -> None:
    for issue in lint_output.issues:
        sides = {evidence.side for evidence in issue.evidence}
        assert sides == {"current_baseline", "challenging_source"}, (
            "每个问题必须同时包含 current_baseline 与 challenging_source 两侧证据"
        )
        sources = {evidence.source_id for evidence in issue.evidence}
        assert len(sources) >= 2, "每个问题的证据必须来自至少两个不同来源"


@pytest.mark.parametrize(
    ("fixture", "model"),
    [
        ("ingest-input.json", IngestWorkflowInput),
        ("ingest-output.json", IngestWorkflowOutput),
        ("query-input.json", QueryWorkflowInput),
        ("query-output.json", QueryWorkflowOutput),
        ("lint-input.json", LintWorkflowInput),
        ("lint-output.json", LintWorkflowOutput),
    ],
)
def test_runbook_fixtures_validate_against_gateway_models(fixture, model):
    """Catches runbook examples drifting from the strict gateway contracts."""
    model.model_validate(_load(fixture))


def test_query_fixture_rules_are_trusted_card_ids_with_intersecting_citations():
    """Catches rule text or unknown IDs in effective_rules (UNKNOWN_EFFECTIVE_RULE)."""
    query_input = QueryWorkflowInput.model_validate(_load("query-input.json"))
    query_output = QueryWorkflowOutput.model_validate(_load("query-output.json"))
    _assert_query_semantics(query_input, query_output)


def test_lint_fixture_issues_carry_two_sided_evidence_from_two_sources():
    """Catches one-sided or single-source evidence the dual-citation gate rejects."""
    lint_output = LintWorkflowOutput.model_validate(_load("lint-output.json"))
    _assert_lint_semantics(lint_output)


@pytest.mark.parametrize(
    ("fixture", "model", "path", "wrong_value"),
    [
        ("ingest-input.json", IngestWorkflowInput, ("source", "authority_level"), "L2"),
        ("query-input.json", QueryWorkflowInput, ("citations", 0, "authority_level"), "L1"),
        ("lint-output.json", LintWorkflowOutput, ("issues", 0, "severity"), "critical"),
        ("lint-output.json", LintWorkflowOutput, ("issues", 0, "evidence", 0, "side"), "baseline"),
        ("ingest-output.json", IngestWorkflowOutput, ("items", 0, "status"), "effective"),
    ],
)
def test_mutating_enums_back_to_legacy_manual_values_fails_validation(
    fixture, model, path, wrong_value
):
    """Catches the manual regressing to the rejected legacy enum spellings."""
    payload = _load(fixture)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = wrong_value

    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_mutating_effective_rules_to_rule_text_fails_semantic_check():
    """Catches effective_rules carrying rule text instead of trusted card IDs."""
    query_input = QueryWorkflowInput.model_validate(_load("query-input.json"))
    payload = _load("query-output.json")
    payload["effective_rules"] = ["当前目标客群是符合准入要求的存量客户。"]
    query_output = QueryWorkflowOutput.model_validate(payload)

    with pytest.raises(AssertionError, match="UNKNOWN_EFFECTIVE_RULE"):
        _assert_query_semantics(query_input, query_output)
