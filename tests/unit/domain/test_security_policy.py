from __future__ import annotations

import importlib
from datetime import UTC, date, datetime

import pytest

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import DomainError
from src.domain.models import Project, SourceRecord

NOW = datetime(2026, 7, 29, tzinfo=UTC)


def project(*, allowed: bool = True) -> Project:
    return Project(
        id="LLD",
        name="推荐官链客计划",
        product_line="零售信贷",
        stage="方案评审",
        current_baseline_id="BASE-LLD-724_1",
        allow_external_model=allowed,
        created_at=NOW,
        updated_at=NOW,
    )


def source(
    *,
    security_level: SecurityLevel = SecurityLevel.L2_INTERNAL,
    authority_level: AuthorityLevel = AuthorityLevel.PROFESSIONAL_OPINION,
    redacted: bool = True,
    allowed: bool = True,
    sandbox: bool = False,
) -> SourceRecord:
    return SourceRecord(
        id="SRC-LLD-001",
        project_id="LLD",
        original_filename="风险意见.md",
        archive_path="data/source_archive/a" + "a" * 63 + ".md",
        sha256="a" * 64,
        mime_type="text/markdown",
        size_bytes=120,
        source_type="risk_opinion",
        authority_level=authority_level,
        source_department="风险",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=security_level,
        is_redacted=redacted,
        allow_external_model=allowed,
        is_sandbox=sandbox,
        ingest_status="pending",
        created_at=NOW,
    )


@pytest.mark.parametrize(
    "security_level",
    [SecurityLevel.L3_CONFIDENTIAL, SecurityLevel.L4_RESTRICTED],
)
def test_confidential_source_cannot_call_external_model(security_level):
    """Catches permissions or redaction incorrectly overriding L3/L4 prohibition."""
    policy = importlib.import_module("src.domain.policies.security_policy")

    assert (
        policy.can_call_external_model(
            project(),
            source(security_level=security_level),
        )
        is False
    )


def test_redacted_authorized_l2_source_can_call_external_model():
    """Catches blocking the approved L2 path needed for live Ingest, Query, and Lint."""
    policy = importlib.import_module("src.domain.policies.security_policy")

    assert policy.can_call_external_model(project(), source()) is True


def test_source_cannot_borrow_another_projects_external_model_authorization():
    """Catches crossing project authorization boundaries during model calls."""
    policy = importlib.import_module("src.domain.policies.security_policy")
    other_project_source = source().model_copy(update={"project_id": "OTHER"})

    assert policy.can_call_external_model(project(), other_project_source) is False


@pytest.mark.parametrize(
    ("project_allowed", "source_allowed", "redacted"),
    [
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_missing_authorization_or_redaction_blocks_external_model(
    project_allowed,
    source_allowed,
    redacted,
):
    """Catches an incomplete three-part authorization check."""
    policy = importlib.import_module("src.domain.policies.security_policy")

    assert (
        policy.can_call_external_model(
            project(allowed=project_allowed),
            source(allowed=source_allowed, redacted=redacted),
        )
        is False
    )


def test_only_l1_sandbox_source_can_call_external_model():
    """Catches sending internal sandbox content outside the approved public-simulated path."""
    policy = importlib.import_module("src.domain.policies.security_policy")

    assert (
        policy.can_call_external_model(
            project(),
            source(
                security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
                sandbox=True,
            ),
        )
        is True
    )
    assert (
        policy.can_call_external_model(
            project(),
            source(
                security_level=SecurityLevel.L2_INTERNAL,
                sandbox=True,
            ),
        )
        is False
    )


def test_sandbox_source_cannot_seed_formal_baseline():
    """Catches simulated material being promoted into the formal current baseline."""
    policy = importlib.import_module("src.domain.policies.authority_policy")

    with pytest.raises(DomainError, match="SANDBOX_SOURCE_NOT_ALLOWED"):
        policy.ensure_formal_baseline_source(
            source(
                security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                sandbox=True,
            )
        )


def test_discussion_reference_cannot_seed_formal_baseline():
    """Catches discussion material being treated as an effective formal source."""
    policy = importlib.import_module("src.domain.policies.authority_policy")

    with pytest.raises(DomainError, match="SOURCE_AUTHORITY_NOT_FORMAL"):
        policy.ensure_formal_baseline_source(
            source(authority_level=AuthorityLevel.DISCUSSION_REFERENCE)
        )


@pytest.mark.parametrize(
    "authority_level",
    [
        AuthorityLevel.FORMAL_EFFECTIVE,
        AuthorityLevel.FORMAL_DECISION,
    ],
)
def test_formal_source_can_seed_formal_baseline(authority_level):
    """Catches rejecting a formal source path required to initialize a baseline."""
    policy = importlib.import_module("src.domain.policies.authority_policy")

    policy.ensure_formal_baseline_source(source(authority_level=authority_level))
