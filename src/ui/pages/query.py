from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer


def render(container: AppContainer) -> None:
    st.title("当前查询")
    st.caption(f"查询项目 {container.settings.project_id} 的当前生效规则")
