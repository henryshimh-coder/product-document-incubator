from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
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
    """Dify workflow client with safe blocking and streaming transports."""

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
        on_started: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        if _contains_sensitive_key(inputs):
            raise GatewayError.input_rejected()
        if on_started is not None:
            return self._run_streaming(
                inputs=inputs,
                user=user,
                timeout_seconds=timeout_seconds,
                on_started=on_started,
            )
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

    def _run_streaming(
        self,
        *,
        inputs: dict[str, Any],
        user: str,
        timeout_seconds: int,
        on_started: Callable[[str, str], None],
    ) -> dict[str, Any]:
        request_body = {
            "inputs": encode_for_dify_transport(inputs),
            "response_mode": "streaming",
            "user": user,
        }
        try:
            with self.http.stream(
                "POST",
                f"{self.base_url}/workflows/run",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=request_body,
                timeout=timeout_seconds,
            ) as response:
                self._raise_for_status(response)
                return self._parse_stream(response, on_started=on_started)
        except httpx.TimeoutException:
            raise GatewayError.timeout() from None
        except httpx.RequestError:
            raise GatewayError.transport_failed() from None

    def get_run(
        self,
        *,
        workflow_run_id: str,
        user: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if not workflow_run_id.strip() or not user.strip():
            raise GatewayError.request_invalid()
        for attempt in range(2):
            try:
                response = self.http.get(
                    f"{self.base_url}/workflows/run/{workflow_run_id}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=timeout_seconds,
                )
            except httpx.TimeoutException:
                raise GatewayError.timeout() from None
            except httpx.RequestError:
                raise GatewayError.transport_failed() from None
            if response.status_code in RETRYABLE_STATUSES:
                if attempt == 0:
                    continue
                raise GatewayError.temporarily_unavailable()
            self._raise_for_status(response)
            return self._parse_run_detail(response)
        raise GatewayError.temporarily_unavailable()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 400:
            raise GatewayError.request_invalid()
        if response.status_code in DENIED_STATUSES:
            raise GatewayError.authorization_failed()
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise GatewayError.transport_failed() from None

    @classmethod
    def _parse_stream(
        cls,
        response: httpx.Response,
        *,
        on_started: Callable[[str, str], None],
    ) -> dict[str, Any]:
        workflow_run_id: str | None = None
        for event_name, payload in cls._iter_sse_events(response):
            effective_event = payload.get("event", event_name)
            if effective_event == "ping":
                continue
            if effective_event == "workflow_started":
                task_id = payload.get("task_id")
                event_run_id = payload.get("workflow_run_id")
                data = payload.get("data")
                if not isinstance(event_run_id, str) and isinstance(data, Mapping):
                    event_run_id = data.get("id")
                if not isinstance(task_id, str) or not task_id.strip():
                    raise OutputValidationError("DIFY_RESPONSE_INVALID")
                if not isinstance(event_run_id, str) or not event_run_id.strip():
                    raise OutputValidationError("DIFY_RESPONSE_INVALID")
                workflow_run_id = event_run_id
                on_started(task_id, workflow_run_id)
                continue
            if effective_event in {"workflow_failed", "error"}:
                raise GatewayError.transport_failed()
            if effective_event != "workflow_finished":
                continue
            event_run_id = payload.get("workflow_run_id", workflow_run_id)
            data = payload.get("data")
            if not isinstance(event_run_id, str) or not event_run_id.strip():
                raise OutputValidationError("DIFY_RESPONSE_INVALID")
            if not isinstance(data, Mapping) or data.get("status") != "succeeded":
                raise GatewayError.transport_failed()
            outputs = data.get("outputs")
            if not isinstance(outputs, Mapping):
                raise OutputValidationError("DIFY_RESPONSE_INVALID")
            result = cls._parse_result(outputs.get("result"))
            return {"workflow_run_id": event_run_id, "result": result}
        raise OutputValidationError("DIFY_RESPONSE_INVALID")

    @staticmethod
    def _iter_sse_events(response: httpx.Response) -> Iterator[tuple[str, dict[str, Any]]]:
        event_name = "message"
        data_lines: list[str] = []
        for line in response.iter_lines():
            if line == "":
                if data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        raise OutputValidationError("DIFY_RESPONSE_INVALID") from None
                    if not isinstance(payload, Mapping):
                        raise OutputValidationError("DIFY_RESPONSE_INVALID")
                    yield event_name, dict(payload)
                event_name = "message"
                data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
        if data_lines:
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                raise OutputValidationError("DIFY_RESPONSE_INVALID") from None
            if not isinstance(payload, Mapping):
                raise OutputValidationError("DIFY_RESPONSE_INVALID")
            yield event_name, dict(payload)

    @classmethod
    def _parse_run_detail(cls, response: httpx.Response) -> dict[str, Any]:
        try:
            envelope = response.json()
            workflow_run_id = envelope["id"]
            status = envelope["status"]
            if not isinstance(workflow_run_id, str) or not workflow_run_id.strip():
                raise ValueError
            if status not in {
                "running",
                "succeeded",
                "failed",
                "stopped",
                "partial-succeeded",
                "paused",
            }:
                raise ValueError
            parsed: dict[str, Any] = {
                "workflow_run_id": workflow_run_id,
                "status": status,
            }
            if status == "succeeded":
                outputs = envelope["outputs"]
                if not isinstance(outputs, Mapping):
                    raise TypeError
                parsed["result"] = cls._parse_result(outputs.get("result"))
            return parsed
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise OutputValidationError("DIFY_RESPONSE_INVALID") from None

    @staticmethod
    def _parse_result(raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, str):
            try:
                raw_result = json.loads(raw_result)
            except json.JSONDecodeError:
                raise OutputValidationError("DIFY_RESPONSE_INVALID") from None
        if not isinstance(raw_result, Mapping):
            raise OutputValidationError("DIFY_RESPONSE_INVALID")
        return dict(raw_result)

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
            parsed = {
                "workflow_run_id": workflow_run_id,
                "result": DifyClient._parse_result(raw_result),
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OutputValidationError):
            invalid_response = True
        if invalid_response or parsed is None:
            raise OutputValidationError("DIFY_RESPONSE_INVALID")
        return parsed
