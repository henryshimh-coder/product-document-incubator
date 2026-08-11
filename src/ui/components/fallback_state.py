"""T15 Step 2：实时失败回退提示的状态构建器。

实时调用失败（超时）时，页面只提供**完全匹配**（同材料、同版本）的
冻结缓存作为继续路径；没有完全匹配缓存时明确告知，不提供近似缓存。
状态为纯数据组件，不触碰 Streamlit，便于在任一页面复用与测试。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CacheFallbackView:
    title: str
    detail: str
    cache_button_enabled: bool


def build_fallback_state(
    *,
    task_type: str,
    realtime_error_code: str,
    exact_cache_available: bool,
) -> CacheFallbackView:
    if not task_type.strip():
        raise ValueError("task_type must be non-empty")
    if realtime_error_code != "DIFY_TIMEOUT":
        raise ValueError(f"unsupported fallback error: {realtime_error_code}")
    detail = (
        "可使用同材料、同版本的冻结缓存继续。"
        if exact_cache_available
        else "未找到同材料、同版本的可用缓存"
    )
    return CacheFallbackView(
        title="实时分析超时",
        detail=detail,
        cache_button_enabled=exact_cache_available,
    )
