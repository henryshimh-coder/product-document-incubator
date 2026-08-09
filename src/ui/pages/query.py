from __future__ import annotations

from html import escape

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.query import RunQueryInput
from src.domain.errors import AppError
from src.domain.models import QueryResponse
from src.ui.components.citation_block import render_citation

QUICK_QUESTIONS = (
    "当前目标客群是什么？",
    "当前产品的核心价值是什么？",
    "当前产品范围包含哪些内容？",
    "当前的主要使用场景是什么？",
    "当前规则的适用条件是什么？",
    "当前版本有哪些关键约束？",
)

_SCOPE_LABELS = {
    "effective": "当前生效",
    "effective_with_notices": "当前＋候选提示",
    "historical": "指定历史版本",
}

_MODE_LABELS = {
    "realtime": "实时查询",
    "cache": "冻结缓存",
}


def _fill_question(question: str) -> None:
    st.session_state["query_question"] = question


def _heading(title: str, test_id: str) -> None:
    st.markdown(
        f'<h2 class="pi-section-title" data-testid="{escape(test_id, quote=True)}">'
        f"{escape(title)}</h2>",
        unsafe_allow_html=True,
    )


def render(container: AppContainer) -> None:
    st.title("当前查询")
    st.caption(f"查询项目 {container.settings.project_id} 的当前生效规则与可追溯引用")

    service_available = container.query is not None
    scope = st.radio(
        "查询范围",
        options=tuple(_SCOPE_LABELS),
        format_func=_SCOPE_LABELS.__getitem__,
        horizontal=True,
        index=0,
        key="query_scope",
    )
    historical_version = None
    if scope == "historical":
        try:
            historical_versions = (
                ()
                if container.query is None
                else container.query.list_historical_versions(container.settings.project_id)
            )
        except (KeyError, OSError, ValueError):
            historical_versions = ()
        historical_version = st.selectbox(
            "历史版本",
            options=historical_versions,
            index=None,
            placeholder="请选择明确的历史版本",
            key="query_historical_version",
        )
        st.caption("历史版本 · 只读查询")
    historical_ready = scope != "historical" or bool((historical_version or "").strip())
    preferred_mode = st.radio(
        "查询方式",
        options=tuple(_MODE_LABELS),
        format_func=_MODE_LABELS.__getitem__,
        horizontal=True,
        index=0,
        key="query_mode",
    )
    if preferred_mode == "cache":
        st.caption("冻结缓存 · 仅匹配与演示快照完全相同的问题、版本与材料")

    with st.form("query_form"):
        question = st.text_input(
            "想了解什么？",
            placeholder="请输入关于当前产品的问题",
            max_chars=500,
            key="query_question",
        )
        submitted = st.form_submit_button(
            "查询",
            type="primary",
            key="query_submit",
            disabled=not service_available or not historical_ready,
        )

    st.markdown("#### 常用问题")
    quick_columns = st.columns(3, gap="small")
    for index, quick_question in enumerate(QUICK_QUESTIONS):
        with quick_columns[index % 3]:
            st.button(
                quick_question,
                key=f"query_quick_{index}",
                type="tertiary",
                on_click=_fill_question,
                args=(quick_question,),
                use_container_width=True,
            )

    if not service_available:
        st.info("查询服务尚未就绪，请先完成本地基线和运行配置。")
        return
    if not submitted:
        return
    if not question.strip():
        st.warning("请输入问题后再查询。")
        return
    try:
        response = container.query.execute(
            RunQueryInput(
                project_id=container.settings.project_id,
                question=question,
                scope=scope,
                historical_version=historical_version,
                preferred_mode=preferred_mode,
            )
        )
    except AppError as error:
        st.error(f"{error.user_message}  \n错误码：`{error.code}`")
        return
    except (KeyError, OSError, ValueError):
        st.error("查询未完成，请检查本地版本和材料后重试。")
        return
    _render_response(response, scope)


def _render_response(response: QueryResponse, scope: str) -> None:
    _heading("当前回答", "query-answer")
    st.markdown(
        f'<div class="pi-query-answer">{escape(response.answer)}</div>',
        unsafe_allow_html=True,
    )

    _heading("适用版本和范围", "query-scope")
    scope_label = _SCOPE_LABELS[scope]
    st.markdown(
        '<div class="pi-query-meta">'
        f"<span><strong>产品版本</strong> {escape(response.baseline_version)}</span>"
        f"<span><strong>查询范围</strong> {escape(scope_label)}</span>"
        f"<span><strong>生效规则</strong> {escape(', '.join(response.effective_rules))}</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    _heading("关键结论引用", "query-citations")
    for citation in response.citations:
        render_citation(citation)
    if not response.citations:
        st.caption("暂无可验证引用。")

    _heading("候选／冲突提示（仅 notice）", "query-notices")
    if response.candidate_notice is not None:
        st.info(f"候选提示：{response.candidate_notice}")
    if response.conflict_notice is not None:
        st.warning(f"冲突提示：{response.conflict_notice}")
    if response.candidate_notice is None and response.conflict_notice is None:
        st.caption("当前范围内无候选或冲突提示。")

    _heading("证据充分度", "query-sufficiency")
    sufficiency = {
        "sufficient": "充分",
        "partial": "部分充分",
        "insufficient": "证据不足",
    }[response.evidence_sufficiency]
    st.markdown(
        f'<div class="pi-query-status">{escape(sufficiency)}</div>',
        unsafe_allow_html=True,
    )

    _heading("实时／缓存状态", "query-runtime")
    runtime = {
        "realtime": "实时生成",
        "cache": "冻结缓存",
        "local_only": "本地结果",
    }[response.result_mode.value]
    status_text = runtime
    if response.result_mode.value == "cache" and response.cache_generated_at is not None:
        status_text = (
            f"{runtime} · 缓存生成时间 {response.cache_generated_at.isoformat(timespec='seconds')}"
        )
    st.markdown(
        f'<div class="pi-query-status">{escape(status_text)}</div>',
        unsafe_allow_html=True,
    )
