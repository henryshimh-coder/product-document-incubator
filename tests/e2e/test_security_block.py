"""T13 Step 5：安全阻断 E2E——L3 机密材料绝不发起模型调用。

两段证据：L3 来源以实时模式导入被 EXTERNAL_CALL_DENIED 直接阻断；以本地
模式导入完成 LOCAL_ONLY 留档，禁网 factory 下任何 HTTP 请求都会直接
AssertionError，模型调用日志中该来源的 started 记录为零。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import httpx
import pytest

from src.domain.enums import CallResultMode, SecurityLevel
from src.domain.errors import DomainError
from tests.e2e.harness import FIXTURES_DIR, DemoHarness


def _forbidden_factory() -> httpx.Client:
    def boom(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise AssertionError("NETWORK_FORBIDDEN")

    return httpx.Client(transport=httpx.MockTransport(boom))


def _started_calls_for_source(db_path: Path, source_sha256: str) -> int:
    with sqlite3.connect(db_path) as connection:
        source = connection.execute(
            "SELECT id FROM source_records WHERE sha256 = ?",
            (source_sha256,),
        ).fetchone()
        if source is None:
            return 0
        return connection.execute(
            "SELECT COUNT(*) FROM model_call_logs WHERE status = 'started'"
            " AND source_ids_json LIKE '%' || ? || '%'",
            (source[0],),
        ).fetchone()[0]


def test_l3_source_realtime_call_is_blocked(demo_root: Path, make_container) -> None:
    container = make_container(demo_root, http_factory=_forbidden_factory)
    harness = DemoHarness(container)
    digest = hashlib.sha256((FIXTURES_DIR / "risk_opinion.md").read_bytes()).hexdigest()

    with pytest.raises(DomainError, match="EXTERNAL_CALL_DENIED"):
        harness.import_source("risk_opinion.md", security=SecurityLevel.L3_CONFIDENTIAL)

    db_path = demo_root / "data/local_state/product_intelligence.db"
    assert _started_calls_for_source(db_path, digest) == 0


def test_l3_source_local_import_never_starts_model_call(
    demo_root: Path,
    make_container,
) -> None:
    container = make_container(demo_root, http_factory=_forbidden_factory)
    harness = DemoHarness(container)

    result = harness.import_source(
        "risk_opinion.md",
        preferred_mode="local",
        security=SecurityLevel.L3_CONFIDENTIAL,
    )
    assert result.result_mode == CallResultMode.LOCAL_ONLY
    assert result.model_call_id is None

    digest = hashlib.sha256((FIXTURES_DIR / "risk_opinion.md").read_bytes()).hexdigest()
    db_path = demo_root / "data/local_state/product_intelligence.db"
    assert _started_calls_for_source(db_path, digest) == 0
