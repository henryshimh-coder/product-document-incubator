from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from src.domain.enums import SecurityLevel

REDACTION_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    "bank_card": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
}
_UNSET = object()


class RedactionMode(StrEnum):
    STRICT = "strict"
    OWNER_CONFIRMED = "owner_confirmed"


class RedactionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    redacted_text: str
    findings: list[dict[str, str]]
    original_chars: int
    redacted_chars: int
    safe_for_external_model: bool


def _dictionary_pattern(terms: Iterable[str]) -> re.Pattern[str] | None:
    normalized = sorted({term for term in terms if term}, key=lambda term: (-len(term), term))
    if not normalized:
        return None
    return re.compile("|".join(re.escape(term) for term in normalized))


def redact_text(
    text: str,
    *,
    mode: RedactionMode = RedactionMode.STRICT,
    security_level: SecurityLevel = SecurityLevel.L2_INTERNAL,
    customer_names: Iterable[str] | object = _UNSET,
    strategy_terms: Iterable[str] | object = _UNSET,
    financial_terms: Iterable[str] | object = _UNSET,
    leader_names: Iterable[str] | object = _UNSET,
    unpublished_decisions: Iterable[str] | object = _UNSET,
) -> RedactionResult:
    """Apply deterministic local redaction without allowing L3/L4 material to leave."""
    redacted = text
    findings: list[dict[str, str]] = []
    patterns: list[tuple[str, re.Pattern[str]]] = list(REDACTION_PATTERNS.items())
    dictionary_inputs = (
        ("customer_name", customer_names),
        ("strategy_term", strategy_terms),
        ("financial_term", financial_terms),
        ("leader_name", leader_names),
        ("unpublished_decision", unpublished_decisions),
    )
    if mode is RedactionMode.STRICT:
        for finding_type, terms in dictionary_inputs:
            if terms is _UNSET:
                continue
            pattern = _dictionary_pattern(terms)
            if pattern is not None:
                patterns.append((finding_type, pattern))

    for finding_type, pattern in patterns:
        redacted, count = pattern.subn(f"[已脱敏:{finding_type}]", redacted)
        if count:
            findings.append({"type": finding_type, "count": str(count)})

    has_sensitive_residue = any(pattern.search(redacted) for _, pattern in patterns)
    has_complete_dictionary_profile = all(terms is not _UNSET for _, terms in dictionary_inputs)
    dictionary_profile_ok = mode is RedactionMode.OWNER_CONFIRMED or has_complete_dictionary_profile
    return RedactionResult(
        redacted_text=redacted,
        findings=findings,
        original_chars=len(text),
        redacted_chars=len(redacted),
        safe_for_external_model=(
            not has_sensitive_residue
            and dictionary_profile_ok
            and security_level in {SecurityLevel.L1_PUBLIC_SIMULATED, SecurityLevel.L2_INTERNAL}
        ),
    )
