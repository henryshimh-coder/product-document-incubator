from __future__ import annotations

import importlib

import pytest

from src.domain.enums import SecurityLevel


def redactor_module():
    """Loads the real local redactor for an observable missing-feature RED."""
    return importlib.import_module("src.infrastructure.files.redactor")


def test_redacts_supported_identifiers_deterministically() -> None:
    """Catches nondeterministic or incomplete replacement of standard sensitive identifiers."""
    text = "手机13800138000，身份证11010519491231002X，卡6222021234567890123，a.user@example.com"

    result = redactor_module().redact_text(
        text,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
    )

    assert result.redacted_text == (
        "手机[已脱敏:phone]，身份证[已脱敏:id_card]，卡[已脱敏:bank_card]，[已脱敏:email]"
    )
    assert result.findings == [
        {"type": "phone", "count": "1"},
        {"type": "id_card", "count": "1"},
        {"type": "bank_card", "count": "1"},
        {"type": "email", "count": "1"},
    ]
    assert result.safe_for_external_model is True


@pytest.mark.parametrize("level", [SecurityLevel.L3_CONFIDENTIAL, SecurityLevel.L4_RESTRICTED])
def test_confidential_levels_never_become_safe_for_external_model(level: SecurityLevel) -> None:
    """Catches redaction incorrectly overriding the existing L3/L4 external-call prohibition."""
    result = redactor_module().redact_text("无敏感字段", security_level=level)

    assert result.safe_for_external_model is False


def test_redacts_configured_sensitive_dictionaries() -> None:
    """Catches customer, strategy, financial, leader, or decision dictionaries leaking locally."""
    result = redactor_module().redact_text(
        "张三确认风险定价策略，真实损益由王总按董事会决定执行。",
        customer_names=["张三"],
        strategy_terms=["风险定价策略"],
        financial_terms=["真实损益"],
        leader_names=["王总"],
        unpublished_decisions=["董事会决定"],
    )

    assert result.redacted_text == (
        "[已脱敏:customer_name]确认[已脱敏:strategy_term]，"
        "[已脱敏:financial_term]由[已脱敏:leader_name]按[已脱敏:unpublished_decision]执行。"
    )
    assert result.findings == [
        {"type": "customer_name", "count": "1"},
        {"type": "strategy_term", "count": "1"},
        {"type": "financial_term", "count": "1"},
        {"type": "leader_name", "count": "1"},
        {"type": "unpublished_decision", "count": "1"},
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {
            "customer_names": (),
            "strategy_terms": (),
            "financial_terms": (),
            "leader_names": (),
        },
    ],
)
def test_l2_requires_all_dictionary_categories_to_be_explicitly_loaded(
    kwargs: dict[str, tuple[str, ...]],
) -> None:
    """Catches L2 redaction being marked safe when a required dictionary was not loaded."""
    result = redactor_module().redact_text("无敏感字段", **kwargs)

    assert result.safe_for_external_model is False


def test_unknown_security_level_fails_closed_after_complete_redaction_profile() -> None:
    """Catches runtime security values outside L1/L2 being treated as safe for external use."""
    result = redactor_module().redact_text(
        "无敏感字段",
        security_level="unexpected",  # type: ignore[arg-type]
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
    )

    assert result.safe_for_external_model is False
