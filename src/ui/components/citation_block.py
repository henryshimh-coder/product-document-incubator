from __future__ import annotations

import streamlit as st

from src.domain.models import Citation

_AUTHORITY_LABELS = {
    "formal_effective": "正式生效",
    "formal_decision": "正式决定",
    "professional_opinion": "专业意见",
    "discussion_reference": "讨论参考",
}


def render_citation(citation: Citation) -> None:
    label = f"{citation.id} · {citation.filename} · {citation.section}"
    with st.expander(label):
        st.caption(
            f"来源 {citation.source_id} · 文档版本 {citation.document_version} · "
            f"{_AUTHORITY_LABELS[citation.authority_level.value]}"
        )
        st.markdown(citation.excerpt)
