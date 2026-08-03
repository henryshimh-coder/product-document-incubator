from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_sidebar_chrome() -> None:
    from src.ui.components.sidebar import render_sidebar_chrome

    render_sidebar_chrome()


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
