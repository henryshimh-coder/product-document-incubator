from __future__ import annotations

import streamlit as st


def render_sidebar_chrome() -> None:
    """Render the static product identity and persistent local-safety statement."""
    with st.sidebar:
        st.markdown(
            '<div class="pi-sidebar-wordmark" data-testid="sidebar-wordmark">产品智策</div>'
            '<div class="pi-sidebar-safety" data-testid="sidebar-safety">'
            "本地知识资产 · 脱敏最小化调用"
            "</div>",
            unsafe_allow_html=True,
        )
