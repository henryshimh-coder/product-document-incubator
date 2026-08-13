from __future__ import annotations

from typing import Any

import httpx

from src.infrastructure.gateways.composition import (
    DifyDocumentGatewaySettings,
    DifyGatewaySettings,
    WorkflowTimeouts,
    build_document_gateway,
    build_workflow_gateways,
)


def _recording_http_client(
    authorization_headers: list[str],
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        authorization_headers.append(request.headers["Authorization"])
        return httpx.Response(
            200,
            json={
                "workflow_run_id": "WF-001",
                "data": {"outputs": {"result": {"ok": True}}},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_composition_builds_three_isolated_clients_with_task_specific_keys():
    """Catches one Dify client or API key being reused across governed workflows."""
    recorded_headers: list[list[str]] = [[], [], []]
    http_clients = [_recording_http_client(headers) for headers in recorded_headers]
    remaining_clients = iter(http_clients)

    def http_factory() -> httpx.Client:
        return next(remaining_clients)

    keys = ("app-ingest-secret", "app-query-secret", "app-lint-secret")
    settings = DifyGatewaySettings(
        base_url="https://dify.example.test/v1",
        ingest_api_key=keys[0],
        query_api_key=keys[1],
        lint_api_key=keys[2],
    )

    gateways = build_workflow_gateways(
        settings,
        timeouts=WorkflowTimeouts(ingest_seconds=60, query_seconds=30, lint_seconds=60),
        http_factory=http_factory,
    )

    clients: list[Any] = [
        gateways.ingest.client,
        gateways.query.client,
        gateways.lint.client,
    ]
    assert len({id(client) for client in clients}) == 3
    assert [client.http for client in clients] == http_clients
    for client in clients:
        client.run(inputs={}, user="test-user", timeout_seconds=1)
    assert recorded_headers == [[f"Bearer {key}"] for key in keys]
    assert all(key not in repr(settings) and key not in repr(gateways) for key in keys)


def test_document_composition_is_available_without_legacy_workflow_keys():
    """Catches 2.0 drafting being needlessly coupled to three 1.x API keys."""
    headers: list[str] = []
    gateway = build_document_gateway(
        DifyDocumentGatewaySettings(
            base_url="https://dify.example.test/v1",
            document_api_key="app-document-secret",
        ),
        timeouts=WorkflowTimeouts(ingest_seconds=60, query_seconds=30, lint_seconds=60),
        http_factory=lambda: _recording_http_client(headers),
    )

    gateway.client.run(inputs={}, user="test-user", timeout_seconds=1)

    assert headers == ["Bearer app-document-secret"]
    assert "app-document-secret" not in repr(gateway)
