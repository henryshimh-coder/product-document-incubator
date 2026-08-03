from __future__ import annotations

from html import escape

import streamlit as st

from src.domain.enums import BaselineStatus
from src.domain.models import Baseline
from src.ui.components.status_badge import status_badge_html

_STATUS_PRESENTATION = {
    BaselineStatus.EFFECTIVE: ("当前生效", "success"),
    BaselineStatus.DRAFT: ("草稿", "info"),
    BaselineStatus.SUPERSEDED: ("历史版本", "muted"),
    BaselineStatus.FAILED: ("失败", "danger"),
}


def render_baseline_hero(baseline: Baseline | None) -> None:
    if baseline is None:
        st.markdown(
            '<section class="pi-baseline-hero pi-baseline-hero--empty" '
            'data-testid="baseline-hero">'
            '<div class="pi-baseline-hero__main">'
            '<div class="pi-eyebrow">当前产品基线</div>'
            '<h2 class="pi-empty-baseline">尚未建立产品基线</h2>'
            '<p class="pi-muted">导入当前产品方案后，系统将建立首个可查询基线。</p>'
            "</div>"
            '<div class="pi-baseline-actions">'
            '<a class="pi-button pi-button--primary" href="/ingest">导入当前产品方案</a>'
            "</div>"
            "</section>",
            unsafe_allow_html=True,
        )
        return

    status_text, status_tone = _STATUS_PRESENTATION[baseline.status]
    effective_at = (
        baseline.effective_at.astimezone().strftime("%Y-%m-%d %H:%M")
        if baseline.effective_at is not None
        else "暂无更新时间"
    )
    with st.container(key="baseline_hero"):
        st.markdown('<span data-testid="baseline-hero"></span>', unsafe_allow_html=True)
        baseline_summary, actions = st.columns((0.48, 0.52), gap="large")
        with baseline_summary:
            st.markdown('<div class="pi-eyebrow">当前产品基线</div>', unsafe_allow_html=True)
            clicked = st.button(
                baseline.version,
                key="home_baseline_version",
                type="tertiary",
                help="查看当前基线详情",
            )
            st.markdown(
                '<div class="pi-baseline-version-meta">'
                f"{status_badge_html(status_text, status_tone)}"
                f'<p class="pi-baseline-updated">最近更新 {escape(effective_at)}</p>'
                "</div>",
                unsafe_allow_html=True,
            )
        with actions:
            st.markdown(
                '<div class="pi-baseline-actions">'
                '<a class="pi-button pi-button--primary" href="/ingest">导入新资料</a>'
                '<div class="pi-baseline-actions__secondary">'
                '<a class="pi-button pi-button--secondary" href="/query">查询当前产品</a>'
                '<a class="pi-button pi-button--secondary" href="/lint">启动一键自检</a>'
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    details_key = "home_baseline_details_id"
    if clicked:
        st.session_state[details_key] = (
            None if st.session_state.get(details_key) == baseline.id else baseline.id
        )
    if st.session_state.get(details_key) == baseline.id:
        _render_baseline_details(baseline, status_text, effective_at)


def _render_baseline_details(
    baseline: Baseline,
    status_text: str,
    effective_at: str,
) -> None:
    st.markdown(
        '<section class="pi-baseline-details" data-testid="baseline-details">'
        '<div class="pi-baseline-details__heading">'
        '<h2 class="pi-section-title">当前基线详情</h2>'
        '<span class="pi-readonly-label">只读</span>'
        "</div>"
        '<dl class="pi-baseline-details__grid">'
        f"<div><dt>版本</dt><dd>{escape(baseline.version)}</dd></div>"
        f"<div><dt>状态</dt><dd>{escape(status_text)}</dd></div>"
        f"<div><dt>最近更新</dt><dd>{escape(effective_at)}</dd></div>"
        f"<div><dt>批准人</dt><dd>{escape(baseline.approved_by)}</dd></div>"
        f"<div><dt>当前基线 ID</dt><dd>{escape(baseline.id)}</dd></div>"
        "</dl>"
        "</section>",
        unsafe_allow_html=True,
    )
