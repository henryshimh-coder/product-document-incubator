from __future__ import annotations

from src.domain.enums import AuthorityLevel
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import SourceRecord

FORMAL_BASELINE_AUTHORITIES = frozenset(
    {
        AuthorityLevel.FORMAL_EFFECTIVE,
        AuthorityLevel.FORMAL_DECISION,
    }
)


def ensure_formal_baseline_source(source: SourceRecord) -> None:
    if source.is_sandbox:
        raise DomainError(ErrorCode.SANDBOX_SOURCE_NOT_ALLOWED)
    if source.authority_level not in FORMAL_BASELINE_AUTHORITIES:
        raise DomainError(
            ErrorCode.SOURCE_AUTHORITY_NOT_FORMAL,
            source.authority_level.value,
        )
