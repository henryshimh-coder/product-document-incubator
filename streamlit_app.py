from __future__ import annotations

import streamlit as st

from src.application.container import build_container
from src.ui.navigation import build_navigation
from src.ui.theme.loader import load_theme

st.set_page_config(
    page_title="产品文档孵化器",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

load_theme()
container = build_container()
page = build_navigation(container)
page.run()
