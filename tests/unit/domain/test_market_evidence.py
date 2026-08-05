from __future__ import annotations

from src.domain.services.market_evidence import (
    MARKET_MATERIAL_SOURCE_TYPES,
    VerifiedMarketEvidence,
    classify_market_claim,
    excerpt_supports_claim,
)

CLAIM = "客户普遍接受该奖励机制"


def _evidence(excerpt: str, source_id: str = "SRC-MKT-001") -> VerifiedMarketEvidence:
    return VerifiedMarketEvidence(
        source_id=source_id,
        citation_id="CIT-MKT-001",
        locator="line:3",
        excerpt=excerpt,
    )


def test_market_material_types_are_explicitly_closed():
    assert MARKET_MATERIAL_SOURCE_TYPES == frozenset(
        {"customer_market_material", "market_research_report"}
    )


def test_claim_without_evidence_becomes_validation_gap():
    result = classify_market_claim(claim=CLAIM, evidence=[], validation_plan=None)
    assert result.classification == "unvalidated_assumption"
    assert result.evidence_sufficiency == "insufficient"
    assert result.missing_materials != []
    assert result.suggested_validation is not None
    assert result.claim == CLAIM


def test_claim_with_supporting_excerpt_is_evidence_supported():
    result = classify_market_claim(
        claim=CLAIM,
        evidence=[_evidence("访谈记录显示客户普遍接受该奖励机制，无显著异议。")],
        validation_plan=None,
    )
    assert result.classification == "evidence_supported"
    assert result.evidence_sufficiency == "sufficient"
    assert result.evidence_refs == ["SRC-MKT-001:CIT-MKT-001"]
    assert result.missing_materials == []


def test_evidence_without_direct_support_does_not_count():
    result = classify_market_claim(
        claim=CLAIM,
        evidence=[_evidence("本文件只记录渠道库存与物流时效。")],
        validation_plan=None,
    )
    assert result.classification == "unvalidated_assumption"
    assert result.evidence_refs == []


def test_empty_excerpt_never_supports():
    assert excerpt_supports_claim(CLAIM, "   ") is False
    assert excerpt_supports_claim(CLAIM, "") is False


def test_claim_with_validation_plan_is_partial():
    result = classify_market_claim(
        claim=CLAIM,
        evidence=[],
        validation_plan="2026-09 前完成 20 个目标客户访谈",
    )
    assert result.classification == "validation_planned"
    assert result.evidence_sufficiency == "partial"
    assert result.suggested_validation == "2026-09 前完成 20 个目标客户访谈"


def test_supporting_evidence_wins_over_validation_plan():
    result = classify_market_claim(
        claim=CLAIM,
        evidence=[_evidence("客户访谈中普遍接受奖励机制。")],
        validation_plan="已有计划但仍以证据为准",
    )
    assert result.classification == "evidence_supported"
    assert result.suggested_validation is None
