"""T15-R01：三个受治理 Workflow 的超时配置必须进入运行时。

回归背景：config/app.yaml 声明 ingest/lint 60 秒、query 30 秒，但修复前
网关 run() 的隐式默认值 30 秒直接进入 HTTP 客户端，真实 Dify Lint 在
30 秒被中断（MODEL_TIMEOUT: DIFY_TIMEOUT）。本文件用 spy/fake client
观测各网关实际下发的 timeout_seconds，并用“模拟 35 秒才返回的响应”
验证 60 秒配置不被提前中断、30 秒配置仍会超时。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domain.enums import SecurityLevel
from src.domain.errors import ErrorCode, GatewayError
from src.infrastructure.gateways._common import create_outbound_safety_proof
from src.infrastructure.gateways.ingest_gateway import IngestGateway
from src.infrastructure.gateways.lint_gateway import LintGateway
from src.infrastructure.gateways.query_gateway import QueryGateway
from src.infrastructure.gateways.schemas import (
    IngestWorkflowInput,
    LintWorkflowInput,
    QueryWorkflowInput,
)


class SimulatedLatencyClient:
    """记录每次调用下发的 timeout_seconds，并按模拟时延决定成功或超时。"""

    def __init__(self, result: dict[str, Any], *, simulated_elapsed_seconds: int) -> None:
        self.result = result
        self.simulated_elapsed_seconds = simulated_elapsed_seconds
        self.observed_timeouts: list[int] = []

    def run(self, *, inputs: dict, user: str, timeout_seconds: int) -> dict[str, Any]:
        del inputs, user
        self.observed_timeouts.append(timeout_seconds)
        if self.simulated_elapsed_seconds > timeout_seconds:
            raise GatewayError.timeout()
        return {"workflow_run_id": "WF-SLOW", "result": self.result}


def _proof(schema: Any, inputs: dict[str, Any]) -> Any:
    return create_outbound_safety_proof(
        schema,
        inputs,
        security_level=SecurityLevel.L2_INTERNAL,
        customer_names=[],
        strategy_terms=[],
        financial_terms=[],
        leader_names=[],
        unpublished_decisions=[],
        source_total_chars=100_000,
    )


def _ingest_input() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-001",
        "language": "zh-CN",
        "source": {
            "id": "SRC-RISK-001",
            "type": "risk_opinion",
            "authority_level": "professional_opinion",
            "document_version": "v1.0",
            "document_date": "2026-07-29",
            "applicable_scope": "一期",
        },
        "baseline_rules": [],
        "source_chunks": [
            {
                "chunk_id": "CHUNK-001",
                "locator": "第2页/客群要求",
                "text": "风险意见要求收紧目标客群。",
            }
        ],
    }


def _ingest_output() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_id": "TASK-001",
        "summary": "未识别到候选知识。",
        "items": [],
        "relations": [],
    }


def _lint_input() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "input_contract_version": "2.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-001",
        "language": "zh-CN",
        "baseline_rules": [],
        "comparison_items": [],
        "deterministic_findings": [],
        "allowed_issue_types": [
            "conflict",
            "omission",
            "stale",
            "not_synchronized",
            "insufficient_evidence",
        ],
    }


def _lint_output() -> dict[str, Any]:
    return {"schema_version": "1.0", "issues": []}


def _query_input() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-001",
        "language": "zh-CN",
        "scope": "effective",
        "question": "当前目标客群是什么？",
        "effective_cards": [],
        "notices": [],
        "citations": [],
    }


def _query_output() -> dict[str, Any]:
    return {
        "answer": "当前目标客群为符合准入要求的存量客户。",
        "effective_rules": [],
        "citations": [],
        "candidate_notice": None,
        "conflict_notice": None,
        "baseline_version": "LLD-724_1",
        "evidence_sufficiency": "sufficient",
        "result_mode": "realtime",
        "model_call_id": None,
    }


def test_ingest_and_lint_allow_response_after_thirty_seconds() -> None:
    """Catches ingest/lint responses slower than 30s being cut by a hidden default."""
    cases = (
        (IngestGateway, IngestWorkflowInput, _ingest_input(), _ingest_output()),
        (LintGateway, LintWorkflowInput, _lint_input(), _lint_output()),
    )
    for gateway_type, schema, inputs, output in cases:
        for simulated_elapsed in (35, 45):
            client = SimulatedLatencyClient(output, simulated_elapsed_seconds=simulated_elapsed)
            gateway = gateway_type(client, timeout_seconds=60)

            result = gateway.run(inputs, safety_proof=_proof(schema, inputs))

            assert client.observed_timeouts == [60]
            assert result["workflow_run_id"] == "WF-SLOW"


def test_query_still_times_out_at_configured_thirty_seconds() -> None:
    """Catches query silently inheriting the longer ingest/lint timeout."""
    client = SimulatedLatencyClient(_query_output(), simulated_elapsed_seconds=35)
    gateway = QueryGateway(client, timeout_seconds=30)

    with pytest.raises(GatewayError) as error:
        gateway.run(_query_input(), safety_proof=_proof(QueryWorkflowInput, _query_input()))

    assert client.observed_timeouts == [30]
    assert error.value.code == ErrorCode.MODEL_TIMEOUT.value
    assert error.value.detail == "DIFY_TIMEOUT"
