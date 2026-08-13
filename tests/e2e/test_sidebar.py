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


def test_sidebar_chrome_renders_wordmark_safety_statement_and_keeps_owner_routes() -> None:
    """Catches removing the project or raw-material entrypoints from Owner navigation."""
    from src.ui.navigation import get_page_definitions

    page = AppTest.from_function(_render_sidebar_chrome).run()

    assert not page.exception
    visible_sidebar = "\n".join(item.value for item in page.sidebar.markdown)
    assert "产品智策" in visible_sidebar
    assert "本地知识资产 · 脱敏最小化调用" in visible_sidebar
    assert [(item.title, item.url_path) for item in get_page_definitions()] == [
        ("项目中心", "projects"),
        ("项目首页", "home"),
        ("原始材料", "materials"),
        ("文档孵化", "incubate"),
        ("当前产品", "current-product"),
        ("检查与建议", "checks"),
        ("资料导入", "ingest"),
        ("当前查询", "query"),
        ("一键自检", "lint"),
        ("变更发布", "release"),
        ("追溯与价值", "trace"),
    ]


def test_every_real_page_callable_renders_chrome_inside_page_run_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches emitting shared chrome outside the callable executed by page.run()."""
    from pathlib import Path
    from types import SimpleNamespace

    from streamlit.navigation import page as streamlit_page_module

    from src.ui import navigation as navigation_module

    container = _build_test_container()
    calls: list[str] = []
    captured_pages: list[Any] = []

    def page_renderer(_container: Any, *, route: str) -> None:
        assert _container is container
        calls.append(f"page:{route}")

    definitions = [
        navigation_module.PageDefinition(
            title=title,
            url_path=route,
            render=lambda current, route=route: page_renderer(current, route=route),
        )
        for title, route in [
            ("项目首页", "home"),
            ("资料导入", "ingest"),
            ("当前查询", "query"),
            ("一键自检", "lint"),
            ("变更发布", "release"),
            ("追溯与价值", "trace"),
        ]
    ]

    def capture_navigation(pages: list[Any], *, position: str) -> object:
        assert position == "sidebar"
        captured_pages.extend(pages)
        return object()

    page_context = SimpleNamespace(pages_manager=SimpleNamespace(main_script_parent=Path.cwd()))
    monkeypatch.setattr(
        streamlit_page_module,
        "get_script_run_ctx",
        lambda: page_context,
    )
    monkeypatch.setattr(
        navigation_module,
        "get_page_definitions",
        lambda _container=None: definitions,
    )
    monkeypatch.setattr(navigation_module.st, "navigation", capture_navigation)
    session_state: dict[str, Any] = {}
    monkeypatch.setattr(navigation_module.st, "session_state", session_state)
    monkeypatch.setattr(
        navigation_module,
        "render_sidebar_chrome",
        lambda: calls.append("chrome"),
    )

    navigation_module.build_navigation(container)
    calls.clear()

    assert len(captured_pages) == 6
    assert session_state["_pi_release_page"] is captured_pages[4]
    for route, page in zip(
        ["home", "ingest", "query", "lint", "release", "trace"],
        captured_pages,
        strict=True,
    ):
        page._page()
        assert calls == ["chrome", f"page:{route}"]
        calls.clear()
