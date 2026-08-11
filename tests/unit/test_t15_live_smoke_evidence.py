"""T15-R04-E01：真实调用远端 workflow_run_id 证据的格式与一致性。

docs/qa/evidence/t15-live-smoke-2026-08-10.json 归档整改后三个 Workflow 的
真实 Dify 远端 workflow_run_id（含 Lint 整改重采样十条）。本测试保证：
三个 Workflow 各有远端 ID、ID 为 UUID 形态、文件无 Key/正文残留、
测试报告中的 ID 类型标注与数量同证据文件一致。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = ROOT / "docs/qa/evidence/t15-live-smoke-2026-08-10.json"
REPORT_PATH = ROOT / "docs/qa/test-report-2026-08-30.md"

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_CALL_KEYS = {
    "workflow",
    "called_at",
    "seconds",
    "ok",
    "error",
    "workflow_run_id",
    "app_model_call_id",
}


def _load() -> dict:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def _calls() -> list[dict]:
    document = _load()
    assert document["schema"] == "t15-live-smoke/v1"
    (round_entry,) = document["rounds"]
    return round_entry["calls"]


def test_three_workflows_each_have_remote_workflow_run_id() -> None:
    """Catches any workflow missing its remote Dify run ID or passing an app ID instead."""
    document = _load()
    round_entry = document["rounds"][0]
    assert SHA_PATTERN.fullmatch(round_entry["code_sha"])
    calls = _calls()
    by_workflow: dict[str, list[dict]] = {}
    for call in calls:
        assert set(call) == ALLOWED_CALL_KEYS
        assert call["ok"] is True and call["error"] is None
        assert isinstance(call["seconds"], (int, float)) and call["seconds"] > 0
        assert UUID_PATTERN.fullmatch(call["workflow_run_id"]), (
            f"{call['workflow']} run ID is not a Dify UUID: {call['workflow_run_id']}"
        )
        by_workflow.setdefault(call["workflow"], []).append(call)
    assert {workflow: len(items) for workflow, items in by_workflow.items()} == {
        "ingest": 1,
        "query": 1,
        "lint": 10,
    }
    # 应用模型调用 ID 只允许出现在 Ingest（ModelCallLogger 落库），且必须为 CALL- 形态。
    for call in calls:
        if call["workflow"] == "ingest":
            assert call["app_model_call_id"].startswith("CALL-")
        else:
            assert call["app_model_call_id"] is None
    # reviewer 独立证据同样必须是 UUID 形态的远端 ID。
    reviewer = document["reviewer_independent"]
    assert UUID_PATTERN.fullmatch(reviewer["workflow_run_id"])
    assert SHA_PATTERN.fullmatch(reviewer["code_sha"])


def test_evidence_contains_no_secrets_or_payloads() -> None:
    """Catches keys, request bodies or unsanitized material leaking into the archive."""
    raw = EVIDENCE_PATH.read_text(encoding="utf-8")
    for forbidden in ("sk-", "Bearer ", "/Users/", "api_key", "risk_opinion.md"):
        assert forbidden not in raw


def test_report_id_types_and_counts_match_evidence() -> None:
    """Catches the report claiming IDs that differ from the archived evidence."""
    report = REPORT_PATH.read_text(encoding="utf-8")
    calls = _calls()
    # 报告必须引用证据文件，并内联 Ingest/Query 的远端 ID（与证据一致）。
    assert "t15-live-smoke-2026-08-10.json" in report
    for workflow in ("ingest", "query"):
        run_id = next(call["workflow_run_id"] for call in calls if call["workflow"] == workflow)
        assert run_id in report
    # Lint 十条远端 ID 全部在证据文件内（报告按引用归档，不要求内联十条）。
    lint_ids = [call["workflow_run_id"] for call in calls if call["workflow"] == "lint"]
    assert len(set(lint_ids)) == 10
    # CALL- 应用模型调用 ID 必须被明确标注，不得写作 Dify run ID。
    ingest_call = next(call for call in calls if call["workflow"] == "ingest")
    app_id = ingest_call["app_model_call_id"]
    assert app_id in report
    labeled = next(line for line in report.splitlines() if app_id in line)
    assert "应用模型调用 ID" in labeled
