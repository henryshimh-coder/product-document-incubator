from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import streamlit as st

from src.application.container import AppContainer
from src.ui.pages import home, ingest, lint, query, release, trace

PageRenderer = Callable[[AppContainer], None]


@dataclass(frozen=True)
class PageDefinition:
    title: str
    url_path: str
    render: PageRenderer


def get_page_definitions() -> list[PageDefinition]:
    return [
        PageDefinition("项目首页", "home", home.render),
        PageDefinition("资料导入", "ingest", ingest.render),
        PageDefinition("当前查询", "query", query.render),
        PageDefinition("一键自检", "lint", lint.render),
        PageDefinition("变更发布", "release", release.render),
        PageDefinition("追溯与价值", "trace", trace.render),
    ]


def build_navigation(container: AppContainer) -> Any:
    pages = [
        st.Page(
            partial(definition.render, container),
            title=definition.title,
            url_path=definition.url_path,
        )
        for definition in get_page_definitions()
    ]
    return st.navigation(pages, position="sidebar")
