from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from collections.abc import Iterable, Mapping
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.enums import SecurityLevel
from src.domain.errors import GatewayError, OutputValidationError
from src.infrastructure.files.redactor import REDACTION_PATTERNS, redact_text

InputModel = TypeVar("InputModel", bound=BaseModel)
MAX_OUTBOUND_COVERAGE = 0.25
MAX_CANONICAL_PAYLOAD_CHARS = 20_000
_PROOF_HMAC_KEY = secrets.token_bytes(32)
_PROOF_ISSUER = object()


def new_workflow_task_id(prefix: str) -> str:
    """Return a random task ID that can never resemble numeric sensitive data.

    Outbound payloads are scanned by REDACTION_PATTERNS (phone/id_card/bank_card,
    minimum 11 consecutive digits). Bare hex UUIDs hit those patterns with ~0.6%
    probability per ID and make the outbound safety proof fail closed at random.
    Grouping into 4-char blocks bounds any digit run to 4, so a match is
    structurally impossible.
    """
    hex32 = uuid4().hex.upper()
    return f"{prefix}-{'-'.join(hex32[offset : offset + 4] for offset in range(0, 32, 4))}"


class OutboundSafetyProof:
    """Opaque, process-local integrity proof issued only by the local factory."""

    __slots__ = (
        "_payload_digest",
        "_outbound_chars",
        "_source_total_chars",
        "_coverage",
        "_signature",
    )

    def __init__(
        self,
        issuer: object,
        *,
        payload_digest: bytes,
        outbound_chars: int,
        source_total_chars: int,
        coverage: float,
        signature: bytes,
    ) -> None:
        if issuer is not _PROOF_ISSUER:
            raise TypeError("OutboundSafetyProof must be created by the local factory")
        object.__setattr__(self, "_payload_digest", payload_digest)
        object.__setattr__(self, "_outbound_chars", outbound_chars)
        object.__setattr__(self, "_source_total_chars", source_total_chars)
        object.__setattr__(self, "_coverage", coverage)
        object.__setattr__(self, "_signature", signature)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("OutboundSafetyProof is immutable")

    def __repr__(self) -> str:
        return "OutboundSafetyProof(<opaque>)"


def _canonical_payload(serialized: Mapping[str, Any]) -> str:
    return json.dumps(
        serialized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _proof_message(
    payload_digest: bytes,
    outbound_chars: int,
    source_total_chars: int,
    coverage: float,
) -> bytes:
    return "\n".join(
        (
            payload_digest.hex(),
            str(outbound_chars),
            str(source_total_chars),
            coverage.hex(),
        )
    ).encode("ascii")


def _sign_proof(
    payload_digest: bytes,
    outbound_chars: int,
    source_total_chars: int,
    coverage: float,
) -> bytes:
    return hmac.digest(
        _PROOF_HMAC_KEY,
        _proof_message(
            payload_digest,
            outbound_chars,
            source_total_chars,
            coverage,
        ),
        "sha256",
    )


def create_outbound_safety_proof(
    schema: type[InputModel],
    inputs: Mapping[str, Any],
    *,
    security_level: SecurityLevel,
    customer_names: Iterable[str],
    strategy_terms: Iterable[str],
    financial_terms: Iterable[str],
    leader_names: Iterable[str],
    unpublished_decisions: Iterable[str],
    source_total_chars: int,
) -> OutboundSafetyProof:
    """Validate, redact, size, and sign the exact canonical outbound payload."""

    if not isinstance(source_total_chars, int) or source_total_chars <= 0:
        raise GatewayError.outbound_safety_proof_invalid()
    try:
        validated = schema.model_validate(inputs)
    except ValidationError:
        raise GatewayError.outbound_safety_proof_invalid() from None
    serialized = validated.model_dump(mode="json")
    canonical_payload = _canonical_payload(serialized)
    outbound_chars = len(canonical_payload)
    coverage = outbound_chars / source_total_chars
    if outbound_chars > MAX_CANONICAL_PAYLOAD_CHARS or coverage > MAX_OUTBOUND_COVERAGE:
        raise GatewayError.outbound_safety_proof_invalid()
    redaction = redact_text(
        canonical_payload,
        security_level=security_level,
        customer_names=customer_names,
        strategy_terms=strategy_terms,
        financial_terms=financial_terms,
        leader_names=leader_names,
        unpublished_decisions=unpublished_decisions,
    )
    if (
        not redaction.safe_for_external_model
        or redaction.redacted_text != canonical_payload
        or redaction.original_chars != outbound_chars
        or redaction.redacted_chars != outbound_chars
    ):
        raise GatewayError.outbound_safety_proof_invalid()
    payload_digest = hashlib.sha256(canonical_payload.encode("utf-8")).digest()
    signature = _sign_proof(
        payload_digest,
        outbound_chars,
        source_total_chars,
        coverage,
    )
    return OutboundSafetyProof(
        _PROOF_ISSUER,
        payload_digest=payload_digest,
        outbound_chars=outbound_chars,
        source_total_chars=source_total_chars,
        coverage=coverage,
        signature=signature,
    )


def _contains_sensitive_residue(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in REDACTION_PATTERNS.values())
    if isinstance(value, Mapping):
        return any(_contains_sensitive_residue(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_residue(item) for item in value)
    return False


def validate_input(
    schema: type[InputModel],
    inputs: Mapping[str, Any],
    *,
    invalid_detail: str,
    safety_proof: OutboundSafetyProof,
) -> dict[str, Any]:
    validation_failed = False
    validated: InputModel | None = None
    try:
        validated = schema.model_validate(inputs)
    except ValidationError:
        validation_failed = True
    if validation_failed or validated is None:
        raise GatewayError.workflow_input_invalid(invalid_detail)
    serialized = validated.model_dump(mode="json")
    canonical_payload = _canonical_payload(serialized)
    if not isinstance(safety_proof, OutboundSafetyProof):
        raise GatewayError.outbound_safety_proof_invalid()
    proof_valid = False
    try:
        outbound_chars = len(canonical_payload)
        source_total_chars = safety_proof._source_total_chars
        coverage = outbound_chars / source_total_chars
        payload_digest = hashlib.sha256(canonical_payload.encode("utf-8")).digest()
        expected_signature = _sign_proof(
            payload_digest,
            outbound_chars,
            source_total_chars,
            coverage,
        )
        proof_valid = all(
            (
                outbound_chars <= MAX_CANONICAL_PAYLOAD_CHARS,
                source_total_chars > 0,
                math.isfinite(coverage),
                coverage <= MAX_OUTBOUND_COVERAGE,
                hmac.compare_digest(
                    payload_digest,
                    safety_proof._payload_digest,
                ),
                outbound_chars == safety_proof._outbound_chars,
                coverage == safety_proof._coverage,
                hmac.compare_digest(
                    expected_signature,
                    safety_proof._signature,
                ),
            )
        )
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        proof_valid = False
    if not proof_valid:
        raise GatewayError.outbound_safety_proof_invalid()
    if _contains_sensitive_residue(serialized):
        raise GatewayError.sensitive_input_detected()
    return serialized


def invoke(
    client: WorkflowGateway,
    inputs: Mapping[str, Any],
    user: str | None,
    timeout_seconds: int,
) -> tuple[str, dict[str, Any]]:
    response = client.run(
        inputs=dict(inputs),
        user=user or str(inputs.get("project_id", "workflow")),
        timeout_seconds=timeout_seconds,
    )
    workflow_run_id = response.get("workflow_run_id")
    result = response.get("result")
    if not isinstance(workflow_run_id, str) or not workflow_run_id.strip():
        raise OutputValidationError("DIFY_RESPONSE_INVALID")
    if not isinstance(result, Mapping):
        raise OutputValidationError("DIFY_RESPONSE_INVALID")
    return workflow_run_id, dict(result)
