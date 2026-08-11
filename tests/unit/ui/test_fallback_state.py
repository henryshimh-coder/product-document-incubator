"""T15 Step 2：实时失败回退提示的验收测试。

计划给定用例（无完全匹配缓存时按钮禁用且文案明确）之外，补充：
- 有完全匹配缓存时按钮启用且文案指向冻结缓存（变异反证：把
  cache_button_enabled 固定为 False 或文案写反都会失败）；
- 非超时错误码与空 task_type 直接拒绝，防止回退提示掩盖其他故障。
"""

from __future__ import annotations

import pytest

from src.ui.components.fallback_state import build_fallback_state


def test_timeout_state_disables_mismatched_cache():
    """Catches offering an approximate cache when no exact match exists."""
    state = build_fallback_state(
        task_type="query",
        realtime_error_code="DIFY_TIMEOUT",
        exact_cache_available=False,
    )
    assert state.title == "实时分析超时"
    assert state.cache_button_enabled is False
    assert state.detail == "未找到同材料、同版本的可用缓存"


def test_timeout_state_enables_exact_match_cache():
    """Catches the exact-match path losing its cache button or honest wording."""
    state = build_fallback_state(
        task_type="ingest",
        realtime_error_code="DIFY_TIMEOUT",
        exact_cache_available=True,
    )
    assert state.title == "实时分析超时"
    assert state.cache_button_enabled is True
    assert state.detail == "可使用同材料、同版本的冻结缓存继续。"


def test_non_timeout_error_is_rejected():
    """Catches masking non-timeout failures behind the timeout fallback UI."""
    with pytest.raises(ValueError, match="unsupported fallback error"):
        build_fallback_state(
            task_type="lint",
            realtime_error_code="DIFY_RESPONSE_INVALID",
            exact_cache_available=True,
        )


def test_blank_task_type_is_rejected():
    """Catches rendering a fallback without knowing which task failed."""
    with pytest.raises(ValueError, match="task_type"):
        build_fallback_state(
            task_type="  ",
            realtime_error_code="DIFY_TIMEOUT",
            exact_cache_available=False,
        )
