from __future__ import annotations

import pytest

from src.infrastructure.files.wiki_citations import (
    contains_citation_like_source_token,
    parse_canonical_source_locator,
)
from src.infrastructure.files.wiki_topic_metadata import validate_topic_title


@pytest.mark.parametrize(
    "value",
    [
        "abc123:section",
        "12345：section",
        "A:section",
        "_LEGACY:section",
        "产品原则 SRC-PROJECT-B-001:",
        "产品原则 SRC-PROJECT-B-001：",
    ],
)
def test_validate_topic_title_rejects_citation_like_source_patterns(value: str) -> None:
    with pytest.raises(ValueError, match="TOPIC_METADATA_CITATION_INVALID"):
        validate_topic_title(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("SRC-L1：section", ("SRC-L1", "section")),
        ("abc123:section", ("abc123", "section")),
        ("A:section", ("A", "section")),
        ("_LEGACY:section", ("_LEGACY", "section")),
        ("12345：line:9", ("12345", "line:9")),
    ],
)
def test_parse_canonical_source_locator_matches_outbound_citation_grammar(
    value: str,
    expected: tuple[str, str],
) -> None:
    assert parse_canonical_source_locator(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "产品原则",
        "问题：客群边界",
        "Issue A",
        "版本 v2 说明",
    ],
)
def test_validate_topic_title_allows_non_citation_natural_language(value: str) -> None:
    assert validate_topic_title(value) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("产品原则 abc123:section", True),
        ("产品原则 12345：section", True),
        ("产品原则 A:section", True),
        ("产品原则 _LEGACY:section", True),
        ("产品原则 SRC-PROJECT-B-001:", True),
        ("产品原则", False),
        ("问题：客群边界", False),
    ],
)
def test_contains_citation_like_source_token_uses_shared_source_id_grammar(
    value: str,
    expected: bool,
) -> None:
    assert contains_citation_like_source_token(value) is expected
