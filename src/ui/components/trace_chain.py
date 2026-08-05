from __future__ import annotations

from html import escape

import streamlit as st

from src.domain.models import TraceNode, TraceView

_KIND_LABELS = {
    "source": "原始资料",
    "knowledge": "结构化知识",
    "issue": "问题",
    "decision": "人工决定",
    "change": "变更单",
    "baseline": "生效基线",
}

_RELATION_LABELS = {
    "derived_from": "来源于",
    "conflicts_with": "冲突于",
    "resolved_by": "会议决定",
    "proposes_change_to": "建议修改",
    "approved_as": "批准形成",
    "supersedes": "替代",
}

_STATUS_LABELS = {
    "completed": "已入库",
    "effective": "生效中",
    "superseded": "已替代",
    "candidate": "候选",
    "conflict": "冲突",
    "open": "待处理",
    "decided": "已决定",
    "deferred": "已暂缓",
    "false_positive": "误报",
    "closed": "已关闭",
    "accept_change": "接受迭代",
    "keep_current": "维持现状",
    "defer": "暂缓讨论",
    "published": "已发布",
    "approved": "已批准",
    "pending_approval": "待审批",
    "draft": "草稿",
}

_NODE_STYLE = (
    "border:1px solid #E4E7EC;border-top:3px solid #2F6FED;border-radius:8px;"
    "padding:10px 12px;background:#FFFFFF;min-height:128px;"
)


def render_trace_chain(view: TraceView) -> None:
    """Render the fixed horizontal traceability chain with relation labels."""

    edges_by_target = {edge.target_id: edge for edge in view.edges}
    columns = st.columns(len(view.main_chain), gap="small")
    for column, node in zip(columns, view.main_chain, strict=True):
        with column:
            edge = edges_by_target.get(node.entity_id)
            relation = (
                _RELATION_LABELS.get(edge.relation_type, edge.relation_type)
                if edge is not None
                else ""
            )
            st.caption(f"— {relation} →" if relation else "　")
            _render_node(node)
    if view.missing_links:
        st.caption("缺失环节：" + "、".join(view.missing_links))


def _render_node(node: TraceNode) -> None:
    sandbox_badge = ""
    if node.is_sandbox:
        sandbox_badge = (
            '<div style="margin-top:6px;"><span style="background:#FFF4E5;color:#B54708;'
            'border-radius:4px;padding:1px 6px;font-size:12px;">模拟材料</span></div>'
        )
    verification_badge = ""
    if node.verification == "unverifiable":
        verification_badge = (
            '<div style="margin-top:6px;"><span style="background:#FEE4E2;color:#B42318;'
            'border-radius:4px;padding:1px 6px;font-size:12px;">引用不可验证</span></div>'
        )
    status = _STATUS_LABELS.get(node.status, node.status)
    happened = node.happened_at.strftime("%Y-%m-%d")
    st.markdown(
        f'<div style="{_NODE_STYLE}">'
        f'<div style="font-size:12px;color:#667085;">{escape(_KIND_LABELS[node.kind])}</div>'
        f'<div style="font-weight:600;font-size:14px;color:#101828;">{escape(node.label)}</div>'
        f'<div style="font-size:12px;color:#475467;">{escape(node.entity_id)}</div>'
        f'<div style="font-size:12px;color:#475467;">状态：{escape(status)}</div>'
        f'<div style="font-size:12px;color:#475467;">{escape(happened)}</div>'
        f"{sandbox_badge}{verification_badge}</div>",
        unsafe_allow_html=True,
    )
    with st.expander("详情"):
        st.markdown(escape(node.summary))
        if node.verification == "unverifiable":
            st.caption("引用不可验证：归档文件或定位片段未通过完整性校验。")
        elif node.excerpt:
            st.caption(f"原文片段（已脱敏）：{node.excerpt}")
