from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer


def render(container: AppContainer) -> None:
    st.title("追溯与价值")
    st.caption(f"追溯项目 {container.settings.project_id} 的来源、决定和版本")
