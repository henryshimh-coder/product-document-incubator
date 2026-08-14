from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from src.domain.enums import AuthorityLevel, SecurityLevel


def _input(**changes):
    from src.application.dto.materials import ArchiveRawSourceInput

    values = {
        "project_id": "PROJECT_A",
        "uploaded_name": "需求说明.md",
        "uploaded_bytes": b"# requirements\n",
        "material_name": "蓝领贷需求说明",
        "archive_mode": "new_material",
        "source_type": "product_requirement",
        "authority_level": AuthorityLevel.FORMAL_EFFECTIVE,
        "source_department": "产品部",
        "document_date": date(2026, 8, 14),
        "material_version": "v1.0",
        "security_level": SecurityLevel.L2_INTERNAL,
        "is_redacted_confirmed": True,
        "allow_external_model": False,
    }
    values.update(changes)
    return ArchiveRawSourceInput(**values)


def test_browser_upload_input_retains_bytes_and_explicit_material_identity() -> None:
    """Catches the new browser archive workflow falling back to a machine-local path."""
    command = _input()

    assert command.uploaded_bytes == b"# requirements\n"
    assert command.uploaded_name == "需求说明.md"
    assert command.material_name == "蓝领贷需求说明"
    assert command.material_version == "v1.0"


@pytest.mark.parametrize("source_type", ["risk_opinion", " RISK_COMPLIANCE "])
def test_browser_upload_input_rejects_legacy_and_whitespace_material_types(
    source_type: str,
) -> None:
    """Catches Pydantic normalization letting a noncanonical new classification pass."""
    with pytest.raises(ValidationError, match="MATERIAL_TYPE_INVALID"):
        _input(source_type=source_type)


def test_browser_upload_input_rejects_historical_authority_values() -> None:
    """Catches a new material preserving the retired four-level authority selection."""
    with pytest.raises(ValidationError, match="MATERIAL_AUTHORITY_INVALID"):
        _input(authority_level=AuthorityLevel.FORMAL_DECISION)
