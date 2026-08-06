"""当前基线 citation 身份的唯一生成规则。

`run_lint` 生成比较包、`publish_baseline` 复验 IssueEvidence 都必须使用同一份
映射，避免一端生成、另一端猜测。citation ID 保持既有 `CIT-BASE-{index:03d}`
兼容格式；映射条目至少包含 citation ID、卡片 ID、基线版本、locator、excerpt，
发布侧据此做全字段一致性校验。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.domain.errors import DomainError, ErrorCode
from src.domain.models import KnowledgeCard
from src.infrastructure.files.query_material_reader import VerifiedFragment

# 与 run_lint 比较包一致的卡片上限，超过上限的卡不产生 citation 身份。
BASELINE_CITATION_CARD_LIMIT = 50


@dataclass(frozen=True)
class BaselineCitation:
    citation_id: str
    card_id: str
    baseline_version: str
    locator: str
    excerpt: str


def build_baseline_citations(
    *,
    baseline_version: str,
    cards: Sequence[KnowledgeCard],
    fragments: Sequence[VerifiedFragment],
) -> tuple[BaselineCitation, ...]:
    """按 Manifest 指向的卡片快照与已验证基线片段重建合法 citation 映射。

    任一前 50 张卡片的正文无法在基线材料片段中定位时 fail closed——基线
    full.md 必须覆盖全部卡片正文，这是发布与 lint 共用的完整性前提。
    """
    entries: list[BaselineCitation] = []
    for index, card in enumerate(cards[:BASELINE_CITATION_CARD_LIMIT], start=1):
        fragment = next((item for item in fragments if card.content in item.text), None)
        if fragment is None:
            raise DomainError(
                ErrorCode.CITATION_INVALID,
                f"BASELINE_CITATION_TEXT_MISMATCH:{card.id}",
            )
        entries.append(
            BaselineCitation(
                citation_id=f"CIT-BASE-{index:03d}",
                card_id=card.id,
                baseline_version=baseline_version,
                locator=fragment.locator,
                excerpt=card.content,
            )
        )
    return tuple(entries)
