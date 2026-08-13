from __future__ import annotations

from decimal import Decimal
from html import escape

import streamlit as st

from src.application.container import AppContainer, TraceService
from src.application.dto.trace import BuildTraceInput
from src.domain.errors import AppError
from src.domain.models import CostImpactInput, MarketEvidenceGap
from src.ui.components.trace_chain import render_trace_chain

_CLASSIFICATION_LABELS = {
    "evidence_supported": "有证据支持",
    "validation_planned": "已制定验证计划",
    "unvalidated_assumption": "未验证假设",
}

_SUFFICIENCY_LABELS = {
    "sufficient": "充分",
    "partial": "部分",
    "insufficient": "不足",
}

_TASK_LABELS = {
    "ingest": "资料导入",
    "query": "知识查询",
    "lint": "安全自检",
}

_RESULT_MODE_LABELS = {
    "realtime": "实时",
    "cache": "缓存",
    "local_only": "本地",
}

_CALL_STATUS_LABELS = {
    "started": "进行中",
    "succeeded": "成功",
    "failed": "失败",
    "timeout": "超时",
}

_PANEL_OPEN = (
    '<div style="border:1px solid #E4E7EC;border-radius:8px;padding:12px 14px;'
    'background:#FFFFFF;line-height:1.9;">'
)


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    st.title("追溯与价值")
    st.caption(f"追溯项目 {project_id} 的来源、决定和版本")
    service = container.trace
    if service is None:
        st.info("追溯服务尚未就绪，请先完成本地基线初始化。")
        return
    tab_chain, tab_value, tab_audit = st.tabs(["完整追溯", "价值验证", "调用审计"])
    with tab_chain:
        _render_chain_tab(service, project_id)
    with tab_value:
        _render_value_tab(service, project_id)
    with tab_audit:
        _render_audit_tab(service, project_id)


def _render_chain_tab(service: TraceService, project_id: str) -> None:
    try:
        cards = service.list_entry_cards(project_id)
    except (KeyError, OSError, ValueError):
        cards = []
        st.warning("追溯入口列表暂时不可读取。")
    if not cards:
        st.info("当前生效版本没有可追溯的知识卡片。")
        return
    labels = {card.id: f"{card.title}（{card.id}）" for card in cards}
    options = [card.id for card in cards]
    target = st.session_state.pop("trace_target_card_id", None)
    default_index = options.index(target) if target in options else 0
    selected = st.selectbox(
        "选择要追溯的生效知识卡片",
        options=options,
        format_func=labels.__getitem__,
        index=default_index,
        key="trace_entry_card",
    )
    if selected is None:
        return
    try:
        view = service.execute(BuildTraceInput(entity_id=selected))
    except AppError as error:
        st.error(f"{error.user_message}  \n错误码：`{error.code}`")
        return
    render_trace_chain(view)
    st.divider()
    _render_market_gaps(service, project_id)
    st.divider()
    _render_cost_impact(service, project_id)


def _render_market_gaps(service: TraceService, project_id: str) -> None:
    st.markdown("### 市场证据缺口")
    try:
        gaps = service.market_evidence_gaps(project_id)
    except (KeyError, OSError, ValueError):
        gaps = []
        st.warning("市场证据检查暂时不可读取。")
    if not gaps:
        st.info("当前版本没有市场判断卡片。")
        return
    for gap in gaps:
        _render_gap(gap)


def _render_gap(gap: MarketEvidenceGap) -> None:
    if gap.classification == "unvalidated_assumption":
        st.warning("以下为未验证判断，不能作为事实依据。")
    classification = _CLASSIFICATION_LABELS[gap.classification]
    sufficiency = _SUFFICIENCY_LABELS[gap.evidence_sufficiency]
    evidence = "、".join(gap.evidence_refs) if gap.evidence_refs else "无"
    missing = "、".join(gap.missing_materials) if gap.missing_materials else "无"
    suggestion = gap.suggested_validation or "无"
    st.markdown(
        f"{_PANEL_OPEN}"
        f"<strong>当前判断：</strong>{escape(gap.claim)}<br>"
        f"<strong>证据类型：</strong>{escape(classification)}<br>"
        f"<strong>证据充分度：</strong>{escape(sufficiency)}<br>"
        f"<strong>证据引用：</strong>{escape(evidence)}<br>"
        f"<strong>缺失材料：</strong>{escape(missing)}<br>"
        f"<strong>建议验证方式：</strong>{escape(suggestion)}"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_cost_impact(
    service: TraceService,
    project_id: str,
) -> None:
    st.markdown("### 轻量成本联动")
    st.caption("只基于沙箱成本参数材料计算业务影响提示，不输出损益结论。")
    try:
        sources = service.list_cost_sources(project_id)
    except (KeyError, OSError, ValueError):
        sources = []
        st.warning("来源列表暂时不可读取。")
    options: dict[str, str] = {
        source.id: f"{source.original_filename}（{source.id}）｜模拟成本参数材料"
        for source in sources
    }
    if not options:
        st.info("当前没有可用的沙箱成本参数材料，无法计算业务影响。")
        return
    st.text_input("参数名称", key="trace_cost_parameter", placeholder="例如：单笔有效推荐奖励")
    old_col, new_col, count_col = st.columns(3, gap="medium")
    with old_col:
        st.number_input("原值（元）", min_value=0.0, step=1.0, format="%.2f", key="trace_cost_old")
    with new_col:
        st.number_input("新值（元）", min_value=0.0, step=1.0, format="%.2f", key="trace_cost_new")
    with count_col:
        st.number_input("预计有效推荐笔数", min_value=0, step=1, key="trace_cost_count")
    st.multiselect(
        "参数来源（必选，仅沙箱成本参数材料）",
        options=list(options),
        format_func=options.__getitem__,
        key="trace_cost_refs",
    )
    if not st.button("计算业务影响", key="trace_cost_run", type="primary"):
        return
    command = CostImpactInput(
        parameter_name=st.session_state["trace_cost_parameter"],
        old_value=Decimal(str(st.session_state["trace_cost_old"])),
        new_value=Decimal(str(st.session_state["trace_cost_new"])),
        projected_valid_referrals=int(st.session_state["trace_cost_count"]),
        source_refs=list(st.session_state["trace_cost_refs"]),
    )
    try:
        result = service.calculate_cost_impact(project_id, command)
    except AppError as error:
        st.warning(f"{error.user_message}  \n错误码：`{error.code}`")
        return
    simulation_note = "（参数来自模拟数据）" if result.is_simulation else ""
    st.markdown(
        f"{_PANEL_OPEN}"
        f"<strong>公式：</strong>{escape(result.formula)}{escape(simulation_note)}<br>"
        f"<strong>原成本：</strong>{escape(str(result.old_cost))} 元<br>"
        f"<strong>新成本：</strong>{escape(str(result.new_cost))} 元<br>"
        f"<strong>差额：</strong>{escape(str(result.delta))} 元<br>"
        f"<strong>来源：</strong>{escape('、'.join(result.source_refs))}<br>"
        f"<strong>免责声明：</strong>{escape(result.disclaimer)}"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_value_tab(service: TraceService, project_id: str) -> None:
    st.markdown("### 价值验证")
    st.caption("只展示来自本地实测数据的指标，未实测指标不显示。")
    try:
        metrics = service.value_metrics(project_id)
    except (KeyError, OSError, ValueError):
        metrics = []
        st.warning("实测指标暂时不可读取。")
    if not metrics:
        st.info("当前没有已完成实测的价值指标。")
        return
    columns = st.columns(3, gap="medium")
    for index, metric in enumerate(metrics):
        with columns[index % 3]:
            st.metric(label=metric.label, value=metric.value)
            st.caption(metric.source_note)


def _render_audit_tab(service: TraceService, project_id: str) -> None:
    st.markdown("### 调用审计")
    st.caption("只展示调用摘要，不展示提示词密钥与敏感片段正文。")
    try:
        logs = service.list_model_calls(project_id, limit=50)
    except (KeyError, OSError, ValueError):
        logs = []
        st.warning("调用审计暂时不可读取。")
    if not logs:
        st.info("当前没有模型调用记录。")
        return
    rows = [
        {
            "调用编号": log.id,
            "任务": _TASK_LABELS.get(log.task_type, log.task_type),
            "模型": log.model_label,
            "来源文件": "、".join(log.source_ids) if log.source_ids else "—",
            "授权状态": "已授权" if log.authorized else "未授权",
            "脱敏状态": "已脱敏" if log.redacted else "未脱敏",
            "实时／缓存": _RESULT_MODE_LABELS.get(log.result_mode.value, log.result_mode.value),
            "开始时间": log.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "耗时": f"{log.elapsed_ms} ms" if log.elapsed_ms is not None else "—",
            "结果状态": _CALL_STATUS_LABELS.get(log.status, log.status),
        }
        for log in logs
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
