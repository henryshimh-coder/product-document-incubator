"""T15-R03：性能证据的确定性重算。

docs/qa/evidence/t15-performance-samples.json 是冻结版本随附的性能证据。
本测试从逐条样本确定性重算 n/min/P50/P95/max/失败数，并与文件内记录的
summary 逐项比对；任何手工篡改样本或汇总都会使重算失败。
百分位口径（与原始采样汇总一致）：P50 为中位数，P95 为最近秩
（ceil(0.95 × n) 位，1 起），全部四舍五入到毫秒。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2] / "docs/qa/evidence/t15-performance-samples.json"
)

ALLOWED_SAMPLE_KEYS = {"operation", "iteration", "started_at", "seconds", "ok", "error"}
REQUIRED_OPERATIONS = {"home", "ingest", "query", "lint", "cache", "publish", "reset"}
TARGETED_OPERATIONS = {"home", "ingest", "query", "lint"}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _load() -> dict:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def _percentile(sorted_values: list[float], p: float) -> float:
    rank = max(1, math.ceil(p * len(sorted_values)))
    return sorted_values[rank - 1]


def _recompute(samples: list[dict]) -> dict:
    values = sorted(sample["seconds"] for sample in samples)
    n = len(values)
    if n % 2 == 1:
        median = values[n // 2]
    else:
        median = (values[n // 2 - 1] + values[n // 2]) / 2
    failures = sum(1 for sample in samples if not sample["ok"])
    return {
        "n": n,
        "min": round(values[0], 3),
        "p50": round(median, 3),
        "p95": round(_percentile(values, 0.95), 3),
        "max": round(values[-1], 3),
        "failures": failures,
    }


def _rounds() -> list[dict]:
    document = _load()
    assert document["schema"] == "t15-performance-samples/v1"
    return document["rounds"]


def test_every_sample_is_sanitized_and_well_formed() -> None:
    """Catches secrets, paths or payloads leaking into the persisted evidence."""
    for round_entry in _rounds():
        assert SHA_PATTERN.fullmatch(round_entry["code_sha"])
        assert round_entry["environment"].strip()
        for sample in round_entry["samples"]:
            assert set(sample) == ALLOWED_SAMPLE_KEYS
            assert isinstance(sample["iteration"], int) and sample["iteration"] >= 0
            assert isinstance(sample["seconds"], (int, float)) and sample["seconds"] >= 0
            assert isinstance(sample["ok"], bool)
            assert isinstance(sample["started_at"], str) and sample["started_at"].strip()
            if sample["error"] is not None:
                # 只允许公开错误码形态：异常类型 + 公开目录码，禁止自由文本。
                assert re.fullmatch(r"[A-Za-z]+:[A-Z][A-Z0-9_]*", sample["error"])
            for forbidden in ("/Users/", "app-", "sk-", "Bearer"):
                assert forbidden not in json.dumps(sample, ensure_ascii=False)


def test_summary_matches_deterministic_recompute_for_every_round() -> None:
    """Catches hand-edited summaries that no longer match the raw samples."""
    for round_entry in _rounds():
        by_operation: dict[str, list[dict]] = {}
        for sample in round_entry["samples"]:
            by_operation.setdefault(sample["operation"], []).append(sample)
        for operation, samples in by_operation.items():
            recomputed = _recompute(samples)
            recorded = round_entry["summary"][operation]
            for field in ("n", "min", "p50", "p95", "max", "failures"):
                assert recomputed[field] == recorded[field], (
                    f"{round_entry['round']}/{operation}/{field}: "
                    f"recomputed {recomputed[field]} != recorded {recorded[field]}"
                )
            if operation in TARGETED_OPERATIONS:
                assert recorded["pass"] is True
                assert recomputed["p95"] <= recorded["target"]
                assert recomputed["failures"] == 0


def test_original_round_covers_seven_operations_with_ten_samples_each() -> None:
    """Catches the frozen build shipping partial performance evidence."""
    original = next(entry for entry in _rounds() if entry["round"] == "original")
    by_operation: dict[str, int] = {}
    for sample in original["samples"]:
        by_operation[sample["operation"]] = by_operation.get(sample["operation"], 0) + 1
    assert set(by_operation) == REQUIRED_OPERATIONS
    assert all(count == 10 for count in by_operation.values())


def test_remediation_round_resamples_lint_under_fixed_timeout() -> None:
    """Catches T15-R01 being declared fixed without a post-fix real Lint re-sample.

    remediation 轮必须存在：绑定整改后代码 SHA（不得等于 original 轮）、
    仅含 lint 实时样本、10 次、0 失败且 P95 < 45 秒。
    """
    rounds = _rounds()
    original = next(entry for entry in rounds if entry["round"] == "original")
    remediation = next((entry for entry in rounds if entry["round"] == "remediation"), None)
    assert remediation is not None, "remediation round missing: T15-R01 re-sample not persisted"
    assert remediation["code_sha"] != original["code_sha"]
    operations = {sample["operation"] for sample in remediation["samples"]}
    assert operations == {"lint"}
    recomputed = _recompute(remediation["samples"])
    assert recomputed["n"] == 10
    assert recomputed["failures"] == 0
    assert recomputed["p95"] < 45.0
