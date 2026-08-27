from __future__ import annotations

import importlib
import traceback
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from src.domain import errors


def _client(handler, api_key: str = "test-secret-key") -> Any:
    dify_client = importlib.import_module("src.infrastructure.gateways.dify_client")
    return dify_client.DifyClient(
        base_url="https://dify.test/v1/",
        api_key=api_key,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _success(result: str | dict = "{}") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "workflow_run_id": "WF-001",
            "data": {"outputs": {"result": result}},
        },
    )


def _sse(*events: tuple[str, dict[str, Any]]) -> httpx.Response:
    lines: list[str] = []
    for event_name, data in events:
        lines.extend(
            (
                f"event: {event_name}",
                f"data: {__import__('json').dumps(data, ensure_ascii=False)}",
                "",
            )
        )
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="\n".join(lines).encode(),
    )


def _exception_graph_text(error: BaseException) -> str:
    graph: list[str] = ["".join(traceback.format_exception(error))]
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        graph.extend((str(current), repr(current)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(graph)


@pytest.mark.parametrize("status_code", [429, 502, 503, 504])
def test_dify_retries_retryable_status_once_and_returns_parsed_result(status_code: int):
    """Catches retryable Dify failures being abandoned or retried more than once."""
    responses: Iterator[httpx.Response] = iter(
        [httpx.Response(status_code), _success('{"ok":true}')]
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    result = _client(handler).run(
        inputs={"schema_version": "1.0"},
        user="LLD",
        timeout_seconds=30,
    )

    assert result == {"workflow_run_id": "WF-001", "result": {"ok": True}}
    assert len(requests) == 2
    assert requests[0].headers["Authorization"] == "Bearer test-secret-key"
    assert requests[0].url == "https://dify.test/v1/workflows/run"
    assert requests[0].read() == (
        b'{"inputs":{"schema_version":"1.0"},"response_mode":"blocking","user":"LLD"}'
    )


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [(400, "DIFY_REQUEST_INVALID"), (401, "DIFY_AUTH_FAILED"), (403, "DIFY_AUTH_FAILED")],
)
def test_dify_does_not_retry_denied_requests(status_code: int, detail: str):
    """Catches invalid or unauthorized requests causing duplicate external calls."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, json={"message": "must not leak"})

    with pytest.raises(errors.GatewayError, match=detail) as caught:
        _client(handler).run(inputs={}, user="LLD", timeout_seconds=30)

    assert caught.value.code == "EXTERNAL_CALL_DENIED"
    assert len(requests) == 1
    assert "must not leak" not in str(caught.value)
    assert "test-secret-key" not in str(caught.value)


def test_dify_rejects_sensitive_workflow_input_before_external_call():
    """Catches an API key or prompt credential being sent inside workflow inputs."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _success()

    with pytest.raises(errors.GatewayError, match="DIFY_INPUT_REJECTED"):
        _client(handler).run(
            inputs={"source": {"api_key": "must-never-leave"}},
            user="LLD",
            timeout_seconds=30,
        )

    assert requests == []


def test_dify_maps_timeout_without_exposing_secret():
    """Catches transport timeouts leaking credentials or losing the public timeout code."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out with test-secret-key", request=request)

    client = _client(handler)
    with pytest.raises(errors.GatewayError) as caught:
        client.run(inputs={}, user="LLD", timeout_seconds=1)

    assert caught.value.code == "MODEL_TIMEOUT"
    assert caught.value.detail == "DIFY_TIMEOUT"
    assert "test-secret-key" not in str(caught.value)
    assert "test-secret-key" not in _exception_graph_text(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "test-secret-key" not in repr(client)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"body": "TRACE-SECRET-MARKER"}),
        httpx.Response(200, content=b"TRACE-SECRET-MARKER"),
    ],
)
def test_dify_mapped_errors_discard_secret_bearing_exception_graph(
    response: httpx.Response,
):
    """Catches requests, responses, and parse exceptions surviving in audit tracebacks."""
    client = _client(lambda request: response, api_key="TRACE-SECRET-MARKER")

    with pytest.raises((errors.GatewayError, errors.OutputValidationError)) as caught:
        client.run(inputs={}, user="LLD", timeout_seconds=30)

    assert "TRACE-SECRET-MARKER" not in _exception_graph_text(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"workflow_run_id": "WF-001", "data": {}}),
        _success("not-json"),
        httpx.Response(200, content=b"not-json"),
    ],
)
def test_dify_rejects_malformed_envelope_or_result(response: httpx.Response):
    """Catches malformed model bytes reaching task-specific workflow schemas."""

    with pytest.raises(errors.OutputValidationError) as caught:
        _client(lambda request: response).run(inputs={}, user="LLD", timeout_seconds=30)

    assert caught.value.code == "MODEL_OUTPUT_INVALID"
    assert caught.value.detail == "DIFY_RESPONSE_INVALID"


def test_dify_accepts_mapping_result_without_mutating_it():
    """Catches already-decoded Dify output being rejected or wrapped inconsistently."""
    result = _client(lambda request: _success({"schema_version": "1.0"})).run(
        inputs={},
        user="LLD",
        timeout_seconds=30,
    )

    assert result == {
        "workflow_run_id": "WF-001",
        "result": {"schema_version": "1.0"},
    }


def test_dify_streams_workflow_and_reports_started_identifiers():
    """Catches long document workflows falling back to Cloudflare-prone blocking mode."""
    requests: list[httpx.Request] = []
    started: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse(
            (
                "workflow_started",
                {
                    "task_id": "TASK-001",
                    "workflow_run_id": "WF-STREAM-001",
                    "data": {"id": "WF-STREAM-001"},
                },
            ),
            ("ping", {"task_id": "TASK-001"}),
            (
                "workflow_finished",
                {
                    "task_id": "TASK-001",
                    "workflow_run_id": "WF-STREAM-001",
                    "data": {"status": "succeeded", "outputs": {"result": '{"ok":true}'}},
                },
            ),
        )

    result = _client(handler).run(
        inputs={"schema_version": "2.0"},
        user="PROJECT_A",
        timeout_seconds=300,
        on_started=lambda task_id, run_id: started.append((task_id, run_id)),
    )

    assert result == {"workflow_run_id": "WF-STREAM-001", "result": {"ok": True}}
    assert started == [("TASK-001", "WF-STREAM-001")]
    assert len(requests) == 1
    assert requests[0].read() == (
        b'{"inputs":{"schema_version":"2.0"},"response_mode":"streaming","user":"PROJECT_A"}'
    )


@pytest.mark.parametrize(
    ("status", "outputs", "expected"),
    [
        ("running", None, {"workflow_run_id": "WF-DETAIL-001", "status": "running"}),
        (
            "succeeded",
            {"result": '{"schema_version":"2.0"}'},
            {
                "workflow_run_id": "WF-DETAIL-001",
                "status": "succeeded",
                "result": {"schema_version": "2.0"},
            },
        ),
        ("failed", None, {"workflow_run_id": "WF-DETAIL-001", "status": "failed"}),
    ],
)
def test_dify_gets_safe_workflow_run_detail(status: str, outputs: dict | None, expected: dict):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload: dict[str, Any] = {"id": "WF-DETAIL-001", "status": status}
        if outputs is not None:
            payload["outputs"] = outputs
        return httpx.Response(200, json=payload)

    result = _client(handler).get_run(
        workflow_run_id="WF-DETAIL-001",
        user="PROJECT_A",
        timeout_seconds=30,
    )

    assert result == expected
    assert requests[0].method == "GET"
    assert requests[0].url == "https://dify.test/v1/workflows/run/WF-DETAIL-001"
