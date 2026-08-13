from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.documents import SuggestStructureInput
from src.domain.errors import AppError


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    st.title("检查与建议")
    st.caption("结构建议只会读取本次由 Owner 勾选项目的 H1/H2/H3 标题，不会外发正文。")
    _render_lint_state(container)
    service = container.suggest_document_structure
    if service is None or container.manage_projects is None:
        st.info("结构建议工作流尚未配置。请设置 DIFY_BASE_URL 与 DIFY_DOCUMENT_API_KEY 后启用。")
        return
    options = [
        summary for summary in container.manage_projects.list() if summary.project_id != project_id
    ]
    labels = {summary.project_id: f"{summary.name} · {summary.project_id}" for summary in options}
    selected = st.multiselect(
        "授权用作结构参考的项目",
        options=list(labels),
        format_func=labels.__getitem__,
        key="structure_reference_projects",
    )
    if st.button("生成结构建议", disabled=not selected, key="structure_suggestion_generate"):
        try:
            results = service.execute(
                SuggestStructureInput(
                    project_id=project_id,
                    reference_project_ids=selected,
                    requested_by="Owner",
                )
            )
        except (AppError, OSError, ValueError, KeyError):
            st.error("建议生成失败，可稍后重试。")
        else:
            st.success(f"已生成 {len(results)} 条结构建议。")
    _render_suggestions(service, project_id)


def _render_lint_state(container: AppContainer) -> None:
    st.subheader("基础自检")
    if container.lint is None:
        st.caption("实时自检工作流尚未配置。")
    else:
        with st.expander("打开实时自检", expanded=False):
            from src.ui.pages import lint

            lint.render(container)


def _render_suggestions(service, project_id: str) -> None:
    st.subheader("AI 结构完善建议")
    suggestions = service.list(project_id)
    if not suggestions:
        st.caption("尚无建议。请先选择参考项目并生成。")
        return
    for suggestion in suggestions:
        st.markdown(
            f"**{suggestion.title}** · 置信度 {suggestion.confidence:.0%}\n\n"
            f"{suggestion.reason}\n\n参考项目：{'、'.join(suggestion.reference_project_ids)}"
        )
        if suggestion.status.value == "open":
            left, right = st.columns(2)
            if left.button("采纳", key=f"suggestion_accept_{suggestion.id}"):
                service.accept(project_id=project_id, suggestion_id=suggestion.id)
                st.rerun()
            if right.button("忽略", key=f"suggestion_ignore_{suggestion.id}"):
                service.ignore(project_id=project_id, suggestion_id=suggestion.id)
                st.rerun()
        else:
            st.caption(f"状态：{suggestion.status.value}")
