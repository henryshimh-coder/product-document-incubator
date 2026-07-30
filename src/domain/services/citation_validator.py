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


def _normalize_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


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
