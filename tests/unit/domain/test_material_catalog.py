from __future__ import annotations

import pytest

from src.domain.enums import AuthorityLevel


def test_material_catalog_exposes_the_eight_owner_choices_in_product_order() -> None:
    """Catches a new-material form showing a missing, reordered, or extra category."""
    from src.domain.material_catalog import MATERIAL_TYPES

    assert [(item.code, item.label, item.order) for item in MATERIAL_TYPES] == [
        ("product_requirement", "产品需求", 1),
        ("business_rule", "业务规则", 2),
        ("customer_market_material", "用户与市场研究", 3),
        ("meeting_minutes", "会议与决策", 4),
        ("risk_compliance", "风险与合规", 5),
        ("technical_specification", "技术与接口", 6),
        ("operation_feedback", "运营与反馈", 7),
        ("other", "其他参考材料", 8),
    ]


def test_material_catalog_rejects_noncanonical_new_material_types() -> None:
    """Catches silently accepting legacy, case-variant, or whitespace-variant new types."""
    from src.domain.material_catalog import require_new_material_type

    assert require_new_material_type("risk_compliance") == "risk_compliance"

    for invalid in ("risk_opinion", "RISK_COMPLIANCE", " risk_compliance "):
        with pytest.raises(ValueError, match="MATERIAL_TYPE_INVALID"):
            require_new_material_type(invalid)


def test_authority_labels_keep_new_and_historical_values_distinguishable() -> None:
    """Catches a compatibility view that hides whether an authority value is historical."""
    from src.domain.material_catalog import authority_label

    assert authority_label(AuthorityLevel.FORMAL_EFFECTIVE) == "正式基线依据"
    assert authority_label(AuthorityLevel.FORMAL_DECISION) == "正式基线依据（历史值）"
    assert authority_label(AuthorityLevel.PROFESSIONAL_OPINION) == "参考材料（历史值）"
    assert authority_label(AuthorityLevel.DISCUSSION_REFERENCE) == "参考材料"
