from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer


def render(container: AppContainer) -> None:
    st.title("项目首页")
    st.caption(f"{container.settings.name} · 当前项目 {container.settings.project_id}")
