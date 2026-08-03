from __future__ import annotations

from typing import Any

import pytest
from streamlit.testing.v1 import AppTest


def _render_sidebar_chrome() -> None:
    from src.ui.components.sidebar import render_sidebar_chrome

    render_sidebar_chrome()


def _build_test_container() -> Any:
    from src.application.container import AppContainer, AppSettings

    return AppContainer(
        settings=AppSettings(
            name="产品智策",
            project_id="LLD",
            default_query_scope="effective",
            max_upload_mb=20,
            accepted_extensions=("pdf", "docx", "txt", "md"),
            demo_mode=True,
            schema_version="1.0",
        )
    )


def test_sidebar_chrome_renders_wordmark_safety_statement_and_keeps_six_routes() -> None:
    """Catches removing required sidebar chrome or changing the six-workspace flow."""
    from src.ui.navigation import get_page_definitions

    page = AppTest.from_function(_render_sidebar_chrome).run()

    assert not page.exception
    visible_sidebar = "\n".join(item.value for item in page.sidebar.markdown)
    assert "产品智策" in visible_sidebar
    assert "本地知识资产 · 脱敏最小化调用" in visible_sidebar
    assert [(item.title, item.url_path) for item in get_page_definitions()] == [
        ("项目首页", "home"),
        ("资料导入", "ingest"),
        ("当前查询", "query"),
        ("一键自检", "lint"),
        ("变更发布", "release"),
        ("追溯与价值", "trace"),
    ]


def test_navigation_composition_keeps_chrome_after_navigation_output_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches rendering chrome before st.navigation clears prior sidebar output."""
    from src.ui import navigation as navigation_module

    calls: list[str] = []
    expected_navigation = object()

    def capture_navigation(pages: list[Any], *, position: str) -> object:
        calls.append("navigation")
        assert len(pages) == 6
        assert position == "sidebar"
        return expected_navigation

    monkeypatch.setattr(navigation_module.st, "navigation", capture_navigation)
    monkeypatch.setattr(
        navigation_module,
        "render_sidebar_chrome",
        lambda: calls.append("chrome"),
    )

    result = navigation_module.build_navigation(_build_test_container())

    assert result is expected_navigation
    assert calls == ["navigation", "chrome"]
