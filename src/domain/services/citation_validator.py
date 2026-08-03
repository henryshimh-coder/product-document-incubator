from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.errors import OutputValidationError

TRUSTED_METADATA_FIELDS = (
    "source_id",
    "filename",
    "document_version",
    "section",
    "excerpt",
)
_CLAIM_SEPARATOR = re.compile(r"[。！？!?；;\n]+")


def _normalize_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def contains_normalized_statement(text: str, statement: str) -> bool:
    """Return whether a complete normalized statement occurs in the surrounding text."""
    normalized_text = _normalize_text(text)
    normalized_statement = _normalize_text(statement)
    return bool(normalized_statement) and normalized_statement in normalized_text


def all_claims_have_direct_support(
    answer: str,
    citations: Sequence[Mapping[str, Any]],
) -> bool:
    """Require every answer sentence to occur one-way inside at least one excerpt."""
    claims = [
        _normalize_text(claim) for claim in _CLAIM_SEPARATOR.split(answer) if _normalize_text(claim)
    ]
    excerpts = [_normalize_text(str(citation.get("excerpt", ""))) for citation in citations]
    return bool(claims) and all(
        any(claim in excerpt for excerpt in excerpts if excerpt) for claim in claims
    )


class CitationValidator:
    """Validate model citations against trusted caller-supplied metadata."""

    def __init__(self, trusted_citations: Sequence[Mapping[str, Any]]) -> None:
        self._trusted = {
            str(citation["id"]): dict(citation)
            for citation in trusted_citations
            if citation.get("id")
        }

    def validate(self, citation: Mapping[str, Any]) -> Mapping[str, Any]:
        citation_id = str(citation.get("id", ""))
        trusted = self._trusted.get(citation_id)
        if trusted is None:
            raise OutputValidationError("UNKNOWN_CITATION")
        for field in TRUSTED_METADATA_FIELDS:
            if field in trusted and citation.get(field) != trusted[field]:
                raise OutputValidationError("CITATION_METADATA_MISMATCH")
        if (
            "authority_level" in trusted
            and citation.get("authority_level") != trusted["authority_level"]
        ):
            raise OutputValidationError("CITATION_METADATA_MISMATCH")
        return trusted

    def has_direct_support(self, claim: str, citation: Mapping[str, Any]) -> bool:
        excerpt = citation.get("excerpt")
        if not isinstance(excerpt, str):
            return False
        normalized_claim = _normalize_text(claim)
        normalized_excerpt = _normalize_text(excerpt)
        if not normalized_claim or not normalized_excerpt:
            return False
        return normalized_claim in normalized_excerpt or normalized_excerpt in normalized_claim
