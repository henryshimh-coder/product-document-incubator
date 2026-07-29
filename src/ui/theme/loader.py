from __future__ import annotations

from pathlib import Path

import streamlit as st


def load_theme(css_path: Path | None = None) -> None:
    path = css_path or Path(__file__).with_name("tokens.css")
    css = path.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
