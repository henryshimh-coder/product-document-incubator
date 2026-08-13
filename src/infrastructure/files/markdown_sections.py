from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_INLINE_MARKUP = re.compile(r"[`*_]+")
_PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}|\b(?:TODO|TBD)\b", re.IGNORECASE)


def validate_product_markdown(markdown: str) -> str:
    """Return normalized Markdown only when it is a publishable candidate shape."""
    if not isinstance(markdown, str):
        raise ValueError("MARKDOWN_ENCODING_INVALID")
    try:
        markdown.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("MARKDOWN_ENCODING_INVALID") from error
    normalized = markdown.strip()
    if not normalized:
        raise ValueError("MARKDOWN_EMPTY")
    headings = list(_HEADING.finditer(normalized))
    h1_count = sum(1 for heading in headings if len(heading.group(1)) == 1)
    if h1_count != 1:
        raise ValueError("MARKDOWN_H1_INVALID")
    if _PLACEHOLDER.search(normalized):
        raise ValueError("MARKDOWN_PLACEHOLDER_INVALID")
    return normalized


def extract_headings(markdown: str) -> list[str]:
    """Extract display-safe H1/H2/H3 titles from valid Markdown text."""
    headings: list[str] = []
    for match in _HEADING.finditer(markdown):
        if len(match.group(1)) > 3:
            continue
        title = _INLINE_MARKUP.sub("", match.group(2)).strip()
        if title:
            headings.append(title)
    return headings
