from __future__ import annotations

import importlib

from src.application.container import AppContainer, AppSettings


def test_navigation_defines_owner_routes_in_product_flow_order():
    """Catches missing project-center or raw-material routes in the Owner flow."""
    navigation = importlib.import_module("src.ui.navigation")

    routes = navigation.get_page_definitions()

    assert [(item.title, item.url_path) for item in routes] == [
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


def test_navigation_without_an_active_project_exposes_only_project_center():
    """Catches an uninitialized Owner session opening project-bound pages."""
    navigation = importlib.import_module("src.ui.navigation")
    container = AppContainer(
        settings=AppSettings(
            name="产品文档孵化器",
            project_id="LLD",
            default_query_scope="effective",
            max_upload_mb=20,
            accepted_extensions=("md",),
            demo_mode=True,
            schema_version="1.0",
        ),
        manage_projects=object(),
    )

    assert [item.url_path for item in navigation.get_page_definitions(container)] == ["projects"]
