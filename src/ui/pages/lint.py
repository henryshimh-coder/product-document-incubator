from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer


def render(container: AppContainer) -> None:
    st.title("一键自检")
    st.caption(f"检查项目 {container.settings.project_id} 的规则冲突和治理问题")
