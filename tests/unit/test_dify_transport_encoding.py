"""T14-R03：Dify 传输层数组编码的验收测试。

Dify 开始节点不支持数组变量（json_object 仅收 object、paragraph 仅收
string，已对真实 API 实测确认）。线路编码把数组转为 JSON 字符串，由工作流
首个代码节点解析还原；应用层契约（schemas.py 数组模型）不受影响。
"""

from __future__ import annotations

import json

import httpx

from src.infrastructure.gateways.dify_client import (
    DifyClient,
    decode_for_dify_transport,
    encode_for_dify_transport,
)


def test_arrays_are_encoded_as_json_strings_and_round_trip():
    """Catches arrays reaching the Dify form unencoded (live API rejects them 400)."""
    inputs = {
        "effective_cards": [{"id": "RULE-LLD-001", "source_citations": ["CIT-01"]}],
        "notices": [],
        "allowed_issue_types": ["conflict", "omission"],
        "titles": ["目标客群"],
    }

    encoded = encode_for_dify_transport(inputs)

    for key in inputs:
        assert isinstance(encoded[key], str)
        assert json.loads(encoded[key]) == inputs[key]
    assert json.loads(encoded["effective_cards"])[0]["id"] == "RULE-LLD-001"
    assert "目标客群" in encoded["titles"]  # ensure_ascii=False，中文原样传输


def test_mappings_and_scalars_pass_through_unchanged():
    """Catches over-encoding objects or scalars that Dify accepts natively."""
    source = {"id": "SRC-001", "authority_level": "professional_opinion"}
    inputs = {
        "source": source,
        "question": "当前目标客群是什么？",
        "max_items": 3,
        "flag": True,
        "nothing": None,
    }

    encoded = encode_for_dify_transport(inputs)

    assert encoded["source"] == source
    assert isinstance(encoded["source"], dict)
    for key in ("question", "max_items", "flag", "nothing"):
        assert encoded[key] == inputs[key]


def test_dify_client_sends_encoded_arrays_on_the_wire():
    """Catches the client bypassing the transport encoding in run()."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "workflow_run_id": "WF-ENCODE",
                "data": {"outputs": {"result": {"ok": True}}},
            },
        )

    client = DifyClient(
        base_url="https://dify.example.test/v1",
        api_key="app-secret",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.run(
        inputs={
            "task_id": "TASK-1",
            "baseline_rules": [{"id": "R1"}],
            "source": {"id": "SRC-1"},
        },
        user="tester",
        timeout_seconds=30,
    )

    wire_inputs = captured[0]["inputs"]
    assert wire_inputs["task_id"] == "TASK-1"
    assert wire_inputs["source"] == {"id": "SRC-1"}
    assert isinstance(wire_inputs["baseline_rules"], str)
    assert json.loads(wire_inputs["baseline_rules"]) == [{"id": "R1"}]


def test_decode_restores_arrays_and_leaves_plain_strings_alone():
    """Catches the workflow-side parse step mangling legitimate string inputs."""
    inputs = {
        "effective_cards": [{"id": "RULE-LLD-001"}],
        "notices": [],
        "question": "[重点关注] 目标客群是什么？",
        "source": {"id": "SRC-1"},
        "task_id": "TASK-1",
    }

    decoded = decode_for_dify_transport(encode_for_dify_transport(inputs))

    assert decoded == inputs
