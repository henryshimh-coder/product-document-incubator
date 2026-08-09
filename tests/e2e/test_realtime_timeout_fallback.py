"""T13 Step 3：实时超时 E2E——超时后可用完全匹配缓存继续。

frozen 快照含三类冻结缓存。实时模式注入网关超时（映射 MODEL_TIMEOUT）后，
同一材料以 cache 模式导入命中冻结缓存，流程不中断。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.domain.enums import CallResultMode
from src.domain.errors import ErrorCode, GatewayError
from tests.e2e.harness import DemoHarness


def test_timeout_can_use_exact_cache_and_continue(
    frozen_root: Path,
    make_container,
) -> None:
    container = make_container(frozen_root, timeout_tasks=frozenset({"ingest"}))
    harness = DemoHarness(container)

    with pytest.raises(GatewayError) as error:
        harness.import_source("risk_opinion.md", preferred_mode="realtime")
    assert error.value.code == ErrorCode.MODEL_TIMEOUT.value

    cached = harness.import_source("risk_opinion.md", preferred_mode="cache")
    assert cached.result_mode == CallResultMode.CACHE
    assert cached.source_id
    assert cached.model_call_id is None
