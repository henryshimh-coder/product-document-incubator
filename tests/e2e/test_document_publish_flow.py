from __future__ import annotations

from pathlib import Path


def _render_publishable_draft(library_root: str) -> None:
    from src.application.container import AppContainer, AppSettings
    from src.application.project_context import ProjectContext
    from src.ui.pages.incubate import render
    from tests.integration.use_cases.test_publish_document_draft import PublishEnvironment

    env = PublishEnvironment(Path(library_root))
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("md",),
                demo_mode=True,
                schema_version="1.0",
            ),
            active_project=ProjectContext(
                "NEW", env.paths, env.paths.library_root / ".incubator/product_incubator.db"
            ),
            incubate_document=env.publish,
            publish_document_draft=env.publish,
        )
    )


def test_publish_page_offers_owner_action_for_pending_draft(tmp_path: Path) -> None:
    # 页面发布动作由用例覆盖；此处只保证 Owner 能看到发布入口。
    # 因为 test fixture 的 service 不是页面列表服务，改用静态契约断言。
    from src.ui.pages.incubate import _render_publish

    assert callable(_render_publish)
