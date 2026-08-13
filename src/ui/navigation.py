from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import streamlit as st

from src.application.container import AppContainer
from src.ui.components.sidebar import render_sidebar_chrome
from src.ui.pages import home, ingest, lint, materials, projects, query, release, trace

PageRenderer = Callable[[AppContainer], None]


@dataclass(frozen=True)
class PageDefinition:
    title: str
    url_path: str
    render: PageRenderer


def get_page_definitions(container: AppContainer | None = None) -> list[PageDefinition]:
    definitions = [
        PageDefinition("项目中心", "projects", projects.render),
        PageDefinition("项目首页", "home", home.render),
        PageDefinition("原始材料", "materials", materials.render),
        PageDefinition("资料导入", "ingest", ingest.render),
        PageDefinition("当前查询", "query", query.render),
        PageDefinition("一键自检", "lint", lint.render),
        PageDefinition("变更发布", "release", release.render),
        PageDefinition("追溯与价值", "trace", trace.render),
    ]
    if (
        container is not None
        and container.manage_projects is not None
        and container.active_project is None
    ):
        return [definitions[0]]
    return definitions


def _render_page(render: PageRenderer, container: AppContainer) -> None:
    render_sidebar_chrome()
    render(container)


def build_navigation(container: AppContainer) -> Any:
    pages = [
        st.Page(
            partial(_render_page, definition.render, container),
            title=definition.title,
            url_path=definition.url_path,
        )
        for definition in get_page_definitions(container)
    ]
    st.session_state["_pi_release_page"] = next(
        page for page in pages if page.url_path == "release"
    )
    st.session_state["_pi_home_page"] = next(page for page in pages if page.url_path == "home")
    st.session_state["_pi_trace_page"] = next(page for page in pages if page.url_path == "trace")
    return st.navigation(pages, position="sidebar")
