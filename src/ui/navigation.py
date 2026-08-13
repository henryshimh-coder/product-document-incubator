from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import streamlit as st

from src.application.container import AppContainer
from src.ui.components.sidebar import render_sidebar_chrome
from src.ui.pages import checks, current_product, incubate, materials, projects

PageRenderer = Callable[[AppContainer], None]


@dataclass(frozen=True)
class PageDefinition:
    title: str
    url_path: str
    render: PageRenderer


def get_page_definitions(container: AppContainer | None = None) -> list[PageDefinition]:
    definitions = [
        PageDefinition("项目中心", "projects", projects.render),
        PageDefinition("原始材料", "materials", materials.render),
        PageDefinition("文档孵化", "incubate", incubate.render),
        PageDefinition("当前产品", "current-product", current_product.render),
        PageDefinition("检查与建议", "checks", checks.render),
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
    return st.navigation(pages, position="sidebar")
