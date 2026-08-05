from __future__ import annotations

from decimal import Decimal

import pytest

from src.domain.errors import DomainError
from src.domain.models import CostImpactInput
from src.domain.services.cost_impact import calculate_cost_impact


def _command(**overrides) -> CostImpactInput:
    payload = {
        "parameter_name": "单笔有效推荐奖励",
        "old_value": Decimal("50.00"),
        "new_value": Decimal("60.00"),
        "projected_valid_referrals": 100,
        "source_refs": ["RULE-REWARD-001"],
    }
    payload.update(overrides)
    return CostImpactInput(**payload)


def test_cost_impact_uses_decimal_and_fixed_disclaimer():
    result = calculate_cost_impact(
        CostImpactInput(
            parameter_name="单笔有效推荐奖励",
            old_value=Decimal("50.00"),
            new_value=Decimal("60.00"),
            projected_valid_referrals=100,
            source_refs=["RULE-REWARD-001"],
        )
    )
    assert result.old_cost == Decimal("5000.00")
    assert result.new_cost == Decimal("6000.00")
    assert result.delta == Decimal("1000.00")
    assert result.disclaimer == "仅供业务影响提示，正式口径需财务确认。"


def test_cost_impact_quantizes_to_fen():
    result = calculate_cost_impact(
        _command(
            old_value=Decimal("33.336"),
            new_value=Decimal("33.333"),
            projected_valid_referrals=3,
        )
    )
    assert result.old_cost == Decimal("100.01")
    assert result.new_cost == Decimal("100.00")
    assert result.delta == Decimal("-0.01")
    assert result.old_cost.as_tuple().exponent == -2
    assert result.new_cost.as_tuple().exponent == -2
    assert result.delta.as_tuple().exponent == -2


def test_cost_impact_formula_and_sources_are_preserved():
    result = calculate_cost_impact(_command(source_refs=["RULE-REWARD-001", "SRC-FIN-002"]))
    assert result.formula == "单笔有效推荐奖励 × 预计有效推荐笔数"
    assert result.source_refs == ["RULE-REWARD-001", "SRC-FIN-002"]


def test_cost_impact_requires_source_refs():
    with pytest.raises(DomainError, match="COST_SOURCE_REQUIRED"):
        calculate_cost_impact(_command(source_refs=[]))


@pytest.mark.parametrize(
    "overrides",
    [
        {"parameter_name": None},
        {"parameter_name": "   "},
        {"old_value": None},
        {"new_value": None},
        {"projected_valid_referrals": None},
        {"old_value": Decimal("-1")},
        {"new_value": Decimal("-0.01")},
        {"projected_valid_referrals": 0},
        {"projected_valid_referrals": -5},
    ],
)
def test_cost_impact_rejects_missing_or_invalid_parameters(overrides):
    with pytest.raises(DomainError, match="COST_INPUT_INCOMPLETE"):
        calculate_cost_impact(_command(**overrides))


def test_cost_impact_allows_zero_new_value():
    result = calculate_cost_impact(_command(new_value=Decimal("0")))
    assert result.new_cost == Decimal("0.00")
    assert result.delta == Decimal("-5000.00")
