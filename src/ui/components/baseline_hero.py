from __future__ import annotations

from html import escape

import streamlit as st

from src.application.dto.dashboard import DashboardBaselineView
from src.domain.enums import BaselineStatus
from src.ui.components.status_badge import status_badge_html

_STATUS_PRESENTATION = {
    BaselineStatus.EFFECTIVE: ("当前生效", "success"),
    BaselineStatus.DRAFT: ("草稿", "info"),
    BaselineStatus.SUPERSEDED: ("历史版本", "muted"),
    BaselineStatus.FAILED: ("失败", "danger"),
}


def render_baseline_hero(baseline: DashboardBaselineView | None) -> None:
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
    st.markdown(
        '<section class="pi-baseline-hero" data-testid="baseline-hero">'
        '<div class="pi-baseline-hero__main">'
        '<div class="pi-eyebrow">当前产品基线</div>'
        '<div class="pi-baseline-version-row">'
        f'<span class="pi-baseline-version">{escape(baseline.version)}</span>'
        f"{status_badge_html(status_text, status_tone)}"
        "</div>"
        f'<p class="pi-baseline-updated">最近更新 {escape(effective_at)}</p>'
        "</div>"
        '<div class="pi-baseline-actions">'
        '<a class="pi-button pi-button--primary" href="/ingest">导入新资料</a>'
        '<div class="pi-baseline-actions__secondary">'
        '<a class="pi-button pi-button--secondary" href="/query">查询当前产品</a>'
        '<a class="pi-button pi-button--secondary" href="/lint">启动一键自检</a>'
        "</div>"
        "</div>"
        "</section>",
        unsafe_allow_html=True,
    )
