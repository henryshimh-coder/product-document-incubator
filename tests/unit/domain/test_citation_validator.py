from __future__ import annotations

import importlib

import pytest

from src.domain.errors import OutputValidationError


def _validator(trusted):
    module = importlib.import_module("src.domain.services.citation_validator")
    return module.CitationValidator(trusted)


def _trusted() -> list[dict[str, str]]:
    return [
        {
            "id": "CIT-BASE-001",
            "source_id": "SRC-BASE",
            "filename": "当前产品方案.md",
            "document_version": "LLD-724_1",
            "section": "目标客群",
            "excerpt": "当前目标客群为符合准入要求的存量客户。",
        }
    ]


def test_citation_validator_rejects_invented_source_metadata():
    """Catches a model attaching a real citation ID to an invented filename or version."""
    validator = _validator(_trusted())
    citation = dict(_trusted()[0], filename="虚构文件.md")

    with pytest.raises(OutputValidationError, match="CITATION_METADATA_MISMATCH"):
        validator.validate(citation)


def test_citation_validator_rejects_unknown_citation_id():
    """Catches a model inventing a citation outside the trusted input universe."""
    validator = _validator(_trusted())

    with pytest.raises(OutputValidationError, match="UNKNOWN_CITATION"):
        validator.validate(dict(_trusted()[0], id="CIT-INVENTED"))


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("当前目标客群为符合准入要求的存量客户", True),
        ("当前目标客群包括从未合作的新客户", False),
    ],
)
def test_direct_support_check_is_deterministic(claim: str, expected: bool):
    """Catches Query/Lint degradation relying on a mock or nondeterministic model call."""
    validator = _validator(_trusted())

    assert validator.has_direct_support(claim, _trusted()[0]) is expected
