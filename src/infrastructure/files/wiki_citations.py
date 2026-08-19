from __future__ import annotations

import re

CANONICAL_SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_CITATION_SEPARATOR_CHARS = {":", "："}
_CITATION_LIKE_SOURCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<source>[A-Za-z0-9_-]+)\s*(?P<separator>[:：])"
)


def parse_canonical_source_locator(value: str) -> tuple[str, str] | None:
    """Parse the exact canonical source-id grammar accepted by Wiki citations."""

    if not isinstance(value, str):
        return None
    separators = [
        index for index, character in enumerate(value) if character in _CITATION_SEPARATOR_CHARS
    ]
    if not separators:
        return None
    index = separators[0]
    source_id = value[:index].strip()
    locator = value[index + 1 :].strip()
    if (
        not CANONICAL_SOURCE_ID_PATTERN.fullmatch(source_id)
        or not locator
        or (value[index] == ":" and ":" in locator)
    ):
        return None
    return source_id, locator


def contains_citation_like_source_token(value: str) -> bool:
    """Reject metadata that embeds a canonical source-id plus a citation separator."""

    if not isinstance(value, str):
        return False
    for match in _CITATION_LIKE_SOURCE_PATTERN.finditer(value):
        source_id = match.group("source")
        separator = match.group("separator")
        locator = value[match.end() :].lstrip()
        if not CANONICAL_SOURCE_ID_PATTERN.fullmatch(source_id):
            continue
        if not locator:
            return True
        if parse_canonical_source_locator(f"{source_id}{separator}{locator}") is not None:
            return True
    return False
