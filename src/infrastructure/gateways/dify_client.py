from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from src.domain.errors import GatewayError, OutputValidationError

RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
DENIED_STATUSES = frozenset({400, 401, 403})
SENSITIVE_INPUT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "access_token",
        "refresh_token",
    }
)


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if normalized_key in SENSITIVE_INPUT_KEYS or _contains_sensitive_key(nested):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def encode_for_dify_transport(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Encode workflow inputs for the Dify start-node form.

    Dify start nodes cannot receive arrays: json_object variables only accept
    objects and paragraph variables only accept strings (both verified against
    the live API). Array values therefore travel as JSON strings
    (ensure_ascii=False) and are parsed back by the workflow's first code node;
    mappings and scalars pass through unchanged. The application-level contract
    in schemas.py is unaffected — encoding happens only at the transport edge.
    """
    encoded: dict[str, Any] = {}
    for key, value in inputs.items():
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            encoded[str(key)] = json.dumps(value, ensure_ascii=False)
        else:
            encoded[str(key)] = value
    return encoded


def decode_for_dify_transport(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Inverse of encode_for_dify_transport, mirroring the workflow parse node.

    Only strings that encode a JSON array are restored; any string that does not
    parse as a JSON array passes through unchanged. Mock gateways in tests use
    this to reproduce the workflow's first code node (json.loads per variable).
    """
    decoded: dict[str, Any] = {}
    for key, value in inputs.items():
        restored = value
        if isinstance(value, str) and value.lstrip().startswith("["):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                restored = parsed
        decoded[str(key)] = restored
    return decoded


class DifyClient:
    """Blocking Dify workflow client with bounded retries and safe errors."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.http = http or httpx.Client()

    def __repr__(self) -> str:
        return f"DifyClient(base_url={self.base_url!r})"

    def run(
        self,
        *,
        inputs: dict[str, Any],
        user: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if _contains_sensitive_key(inputs):
            raise GatewayError.input_rejected()
        request_body = {
            "inputs": encode_for_dify_transport(inputs),
            "response_mode": "blocking",
            "user": user,
        }
        for attempt in range(2):
            mapped_error: GatewayError | None = None
            try:
                response = self.http.post(
                    f"{self.base_url}/workflows/run",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=request_body,
                    timeout=timeout_seconds,
                )
            except httpx.TimeoutException:
                mapped_error = GatewayError.timeout()
            except httpx.RequestError:
                mapped_error = GatewayError.transport_failed()
            if mapped_error is not None:
                raise mapped_error

            if response.status_code in RETRYABLE_STATUSES:
                if attempt == 0:
                    continue
                raise GatewayError.temporarily_unavailable()
            if response.status_code == 400:
                raise GatewayError.request_invalid()
            if response.status_code in DENIED_STATUSES:
                raise GatewayError.authorization_failed()
            status_failed = False
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                status_failed = True
            if status_failed:
                raise GatewayError.transport_failed()
            return self._parse_response(response)
        raise GatewayError.temporarily_unavailable()

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        invalid_response = False
        parsed: dict[str, Any] | None = None
        try:
            envelope = response.json()
            workflow_run_id = envelope["workflow_run_id"]
            raw_result = envelope["data"]["outputs"]["result"]
            if not isinstance(workflow_run_id, str) or not workflow_run_id.strip():
                raise ValueError("missing workflow_run_id")
            if isinstance(raw_result, str):
                raw_result = json.loads(raw_result)
            if not isinstance(raw_result, Mapping):
                raise TypeError("result must be a mapping")
            parsed = {
                "workflow_run_id": workflow_run_id,
                "result": dict(raw_result),
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            invalid_response = True
        if invalid_response or parsed is None:
            raise OutputValidationError("DIFY_RESPONSE_INVALID")
        return parsed
