from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer


def render(container: AppContainer) -> None:
    st.title("资料导入")
    st.caption(f"为项目 {container.settings.project_id} 导入并编译新资料")
