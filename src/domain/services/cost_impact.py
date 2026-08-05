from __future__ import annotations

from decimal import Decimal

from src.domain.errors import DomainError, ErrorCode
from src.domain.models import CostImpactInput, CostImpactResult

MONEY = Decimal("0.01")
COST_IMPACT_FORMULA = "单笔有效推荐奖励 × 预计有效推荐笔数"
COST_IMPACT_DISCLAIMER = "仅供业务影响提示，正式口径需财务确认。"

# 轻量期只允许这些 source_type 的沙箱记录充当成本参数来源；正式参数模式暂不开放。
COST_PARAMETER_SOURCE_TYPES = frozenset({"cost_parameter", "demo_cost_parameter"})


def calculate_cost_impact(command: CostImpactInput) -> CostImpactResult:
    """Compute a deterministic cost hint; missing inputs are never filled by a model."""

    if not command.source_refs:
        raise DomainError(ErrorCode.COST_SOURCE_REQUIRED)
    if (
        command.parameter_name is None
        or not command.parameter_name.strip()
        or command.old_value is None
        or command.old_value < 0
        or command.new_value is None
        or command.new_value < 0
        or command.projected_valid_referrals is None
        or command.projected_valid_referrals <= 0
    ):
        raise DomainError(ErrorCode.COST_INPUT_INCOMPLETE)
    old_cost = (command.old_value * command.projected_valid_referrals).quantize(MONEY)
    new_cost = (command.new_value * command.projected_valid_referrals).quantize(MONEY)
    return CostImpactResult(
        formula=COST_IMPACT_FORMULA,
        old_cost=old_cost,
        new_cost=new_cost,
        delta=(new_cost - old_cost).quantize(MONEY),
        source_refs=command.source_refs,
        disclaimer=COST_IMPACT_DISCLAIMER,
    )
