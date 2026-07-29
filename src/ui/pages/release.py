from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer


def render(container: AppContainer) -> None:
    st.title("变更发布")
    st.caption(f"检查并发布项目 {container.settings.project_id} 的候选变更")
