from __future__ import annotations

import hashlib

import pytest

from src.domain.enums import SecurityLevel
from src.domain.errors import DomainError
from tests.integration.use_cases.test_wiki_ingest import make_ingest_fixture


def test_owner_confirmed_l2_business_terms_reach_gateway_with_hard_ids_masked(
    tmp_path,
) -> None:
    """Catches Wiki Ingest reverting Owner-confirmed source chunks to strict redaction."""
    fixture = make_ingest_fixture(
        tmp_path,
        raw_text=("某银行采用灰度策略。联系人手机 13812345678，邮箱 owner@example.com。") * 200
        + ("Approved supporting evidence.\n" * 2_000),
        customer_names=("某银行",),
        strategy_terms=("灰度策略",),
    )
    before_raw = fixture.raw_path.read_bytes()
    before_sha = hashlib.sha256(before_raw).hexdigest()

    result = fixture.execute(requested_by="Owner")

    assert result.status.value == "ingested"
    sent = fixture.gateway.calls[0]["inputs"]["source_chunks"][0]["text"]
    assert "某银行" in sent
    assert "灰度策略" in sent
    assert "13812345678" not in sent
    assert "owner@example.com" not in sent
    assert "[已脱敏:phone]" in sent
    assert "[已脱敏:email]" in sent
    assert fixture.raw_path.read_bytes() == before_raw
    assert hashlib.sha256(fixture.raw_path.read_bytes()).hexdigest() == before_sha


@pytest.mark.parametrize(
    ("requested_by", "is_redacted", "allow_external", "level"),
    [
        ("Agent", True, True, SecurityLevel.L2_INTERNAL),
        ("Owner", False, True, SecurityLevel.L2_INTERNAL),
        ("Owner", True, False, SecurityLevel.L2_INTERNAL),
        ("Owner", True, True, SecurityLevel.L3_CONFIDENTIAL),
        ("Owner", True, True, SecurityLevel.L4_RESTRICTED),
    ],
)
def test_missing_owner_authorization_never_reaches_gateway(
    tmp_path,
    requested_by: str,
    is_redacted: bool,
    allow_external: bool,
    level: SecurityLevel,
) -> None:
    """Catches any missing Owner authorization condition invoking the external model."""
    fixture = make_ingest_fixture(
        tmp_path,
        is_redacted=is_redacted,
        allow_external_model=allow_external,
        security_level=level,
    )

    with pytest.raises(DomainError, match="WIKI_EXTERNAL_CALL_DENIED"):
        fixture.execute(requested_by=requested_by)

    assert fixture.gateway.calls == []
