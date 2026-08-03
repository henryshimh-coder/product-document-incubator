from __future__ import annotations

from html import escape

import streamlit as st

from src.domain.models import Project
from src.ui.components.project_context import project_context_html


def render_page_header(project: Project) -> None:
    st.markdown(
        '<section class="pi-page-header" data-testid="project-header">'
        '<div class="pi-eyebrow">当前项目</div>'
        f'<h1 class="pi-page-title">{escape(project.name)}</h1>'
        f'<div class="pi-project-meta">{project_context_html(project)}</div>'
        "</section>",
        unsafe_allow_html=True,
    )
