from __future__ import annotations

import re
from dataclasses import dataclass

from src.domain.models import MarketEvidenceGap

MISSING_MARKET_MATERIALS = ["客户或市场验证材料"]
DEFAULT_VALIDATION_ADVICE = "补充客户或市场验证材料，或制定明确的验证计划"

# 只有这些 source_type 才算"明确的客户/市场验证材料"，其余类型一律不算市场证据。
MARKET_MATERIAL_SOURCE_TYPES = frozenset({"customer_market_material", "market_research_report"})

_ALNUM_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%-]*")
_CJK_RUN = re.compile(r"[一-鿿]{2,}")


@dataclass(frozen=True)
class VerifiedMarketEvidence:
    """已通过存在性、项目归属、归档哈希、定位与非沙箱校验的市场证据片段。"""

    source_id: str
    citation_id: str | None
    locator: str
    excerpt: str

    @property
    def ref(self) -> str:
        return f"{self.source_id}:{self.citation_id}" if self.citation_id else self.source_id


def claim_keywords(claim: str) -> frozenset[str]:
    """Deterministically derive the significant tokens of a market claim."""

    tokens: set[str] = set()
    for run in _CJK_RUN.findall(claim):
        tokens.add(run)
        if len(run) > 4:
            tokens.update(run[index : index + 2] for index in range(0, len(run) - 1, 2))
    tokens.update(token for token in _ALNUM_TOKEN.findall(claim) if len(token) >= 2)
    return frozenset(tokens)


def excerpt_supports_claim(claim: str, excerpt: str) -> bool:
    """One-way support check: the excerpt must visibly carry the claim's keywords."""

    if not excerpt.strip():
        return False
    keywords = claim_keywords(claim)
    if not keywords:
        return False
    hits = sum(1 for token in keywords if token in excerpt)
    return hits >= 2 or (hits >= 1 and len(keywords) == 1)


def classify_market_claim(
    *,
    claim: str,
    evidence: list[VerifiedMarketEvidence],
    validation_plan: str | None,
) -> MarketEvidenceGap:
    """Classify a market judgment from verified evidence only, never inventing support."""

    supporting = [item for item in evidence if excerpt_supports_claim(claim, item.excerpt)]
    if supporting:
        return MarketEvidenceGap(
            claim=claim,
            classification="evidence_supported",
            evidence_sufficiency="sufficient",
            evidence_refs=[item.ref for item in supporting],
            missing_materials=[],
            suggested_validation=None,
        )
    if validation_plan is not None and validation_plan.strip():
        return MarketEvidenceGap(
            claim=claim,
            classification="validation_planned",
            evidence_sufficiency="partial",
            evidence_refs=[],
            missing_materials=list(MISSING_MARKET_MATERIALS),
            suggested_validation=validation_plan.strip(),
        )
    return MarketEvidenceGap(
        claim=claim,
        classification="unvalidated_assumption",
        evidence_sufficiency="insufficient",
        evidence_refs=[],
        missing_materials=list(MISSING_MARKET_MATERIALS),
        suggested_validation=DEFAULT_VALIDATION_ADVICE,
    )
