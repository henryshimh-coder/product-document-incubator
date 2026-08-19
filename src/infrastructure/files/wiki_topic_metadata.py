from __future__ import annotations

import re

from src.infrastructure.files.wiki_citations import contains_citation_like_source_token

_TOPIC_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_topic_id(value: str) -> str:
    normalized = _normalize_metadata(value)
    if _TOPIC_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("TOPIC_ID_INVALID")
    return normalized


def validate_topic_title(value: str) -> str:
    normalized = _normalize_metadata(value)
    if normalized.startswith(("#", "-", "*", "+")):
        raise ValueError("TOPIC_TITLE_INVALID")
    return normalized


def _normalize_metadata(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("TOPIC_METADATA_INVALID")
    normalized = value.strip()
    if not normalized:
        raise ValueError("TOPIC_METADATA_INVALID")
    if any(token in normalized for token in ("【", "】", "[[", "]]")):
        raise ValueError("TOPIC_METADATA_CITATION_INVALID")
    if "\r" in normalized or "\n" in normalized:
        raise ValueError("TOPIC_METADATA_MULTILINE_INVALID")
    if contains_citation_like_source_token(normalized):
        raise ValueError("TOPIC_METADATA_CITATION_INVALID")
    return normalized
