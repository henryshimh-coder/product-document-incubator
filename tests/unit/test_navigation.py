from __future__ import annotations

import importlib


def test_navigation_defines_six_routes_in_product_flow_order():
    """Catches missing or reordered workspaces that would break the demo flow."""
    navigation = importlib.import_module("src.ui.navigation")

    routes = navigation.get_page_definitions()

    assert [(item.title, item.url_path) for item in routes] == [
        ("项目首页", "home"),
        ("资料导入", "ingest"),
        ("当前查询", "query"),
        ("一键自检", "lint"),
        ("变更发布", "release"),
        ("追溯与价值", "trace"),
    ]
