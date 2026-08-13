from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.dashboard import GetDashboardInput
from src.ui.components.baseline_hero import render_baseline_hero
from src.ui.components.grouped_list import GroupedListItem, render_grouped_list
from src.ui.components.page_header import render_page_header


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    if container.dashboard is None:
        st.markdown(
            '<section class="pi-page-header" data-testid="project-header">'
            '<div class="pi-eyebrow">当前项目</div>'
            f'<h1 class="pi-page-title">{escape(container.settings.name)}</h1>'
            '<div class="pi-project-meta">本地基线尚未初始化</div>'
            "</section>",
            unsafe_allow_html=True,
        )
        render_baseline_hero(None)
        return

    try:
        view = container.dashboard.execute(GetDashboardInput(project_id=project_id))
    except (KeyError, OSError, ValueError):
        st.error("项目数据读取失败，请检查本地配置后重新读取。")
        st.button("重新读取", type="secondary", key="home_retry")
        return

    render_page_header(view.project)
    if not view.integrity_ok:
        st.error(
            "**当前基线镜像需要修复**  \n"
            "查询仍按 Manifest 只读运行，变更发布已暂时禁用。  \n"
            "错误码：`BASELINE_INTEGRITY_FAILED`"
        )
    render_baseline_hero(view.current_baseline)

    overview, activity = st.columns((0.48, 0.52), gap="large")
    with overview:
        render_grouped_list(
            title="项目概况",
            test_id="project-metrics",
            variant="metrics",
            items=[
                GroupedListItem(
                    "问",
                    "开放问题",
                    "待确认或补充的产品问题",
                    str(view.open_issue_count),
                    "/lint?filter=open",
                ),
                GroupedListItem(
                    "变",
                    "候选变更",
                    "待审批后可进入发布",
                    str(view.candidate_change_count),
                    "/release?filter=candidate",
                ),
                GroupedListItem(
                    "资",
                    "已入库资料",
                    "已完成安全存档的本地资料",
                    str(view.source_count),
                    "/ingest?view=history",
                ),
            ],
        )
    with activity:
        render_grouped_list(
            title="最近活动",
            test_id="recent-activity",
            items=[_event_item(event) for event in view.recent_events[:5]]
            or [GroupedListItem("·", "暂无活动", "项目活动将在这里显示", "")],
        )


def _event_item(event: dict) -> GroupedListItem:
    labels = {
        "source_imported": "资料导入",
        "lint_completed": "自检完成",
        "change_created": "新增候选变更",
        "baseline_published": "发布新基线",
        "false_positive_recorded": "判定误报",
        "cache_used": "使用缓存结果",
    }
    payload = event.get("payload")
    description = payload.get("description") if isinstance(payload, dict) else None
    title = (
        description
        if isinstance(description, str)
        else labels.get(
            str(event.get("event_type", "")),
            "项目活动",
        )
    )
    actor = str(event.get("actor", "系统"))
    created_at = event.get("created_at")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            created_at = None
    timestamp = (
        created_at.astimezone().strftime("%m-%d %H:%M")
        if isinstance(created_at, datetime)
        else "--"
    )
    return GroupedListItem("动", title, actor, timestamp)
