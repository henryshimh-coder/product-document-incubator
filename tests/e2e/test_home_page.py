from __future__ import annotations

from datetime import UTC, datetime, timedelta

from streamlit.testing.v1 import AppTest

from src.application.dto.dashboard import DashboardBaselineView, DashboardView
from src.domain.enums import BaselineStatus
from src.domain.models import Project

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _render_page(view_json: str) -> None:
    from src.application.container import AppContainer, AppSettings
    from src.application.dto.dashboard import DashboardView
    from src.ui.pages.home import render
    from src.ui.theme.loader import load_theme

    class StaticDashboard:
        def execute(self, command):
            return DashboardView.model_validate_json(view_json)

    load_theme()
    render(
        AppContainer(
            settings=AppSettings(
                name="产品智策",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
            ),
            dashboard=StaticDashboard(),
        )
    )


def _render_empty_page() -> None:
    from src.application.container import AppContainer, AppSettings
    from src.ui.pages.home import render
    from src.ui.theme.loader import load_theme

    load_theme()
    render(
        AppContainer(
            settings=AppSettings(
                name='<img src=x onerror="alert(9)">',
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
            )
        )
    )


def _view(*, integrity_ok: bool = True, malicious: bool = False) -> DashboardView:
    project_name = '<img src=x onerror="alert(1)">' if malicious else "推荐官链客计划"
    version = '<script>alert("baseline")</script>' if malicious else "LLD-724_1"
    events = []
    for index in range(6):
        events.append(
            {
                "id": f"EVENT-{index}",
                "project_id": "LLD",
                "event_type": "source_imported",
                "entity_type": "source",
                "entity_id": f"SRC-{index}",
                "actor": '<svg onload="alert(2)">' if malicious else f"操作人-{index}",
                "correlation_id": f"CORR-{index}",
                "payload": {
                    "description": '<iframe src="javascript:alert(3)"></iframe>'
                    if malicious
                    else f"导入资料 {index}"
                },
                "created_at": NOW + timedelta(minutes=index),
            }
        )
    return DashboardView(
        project=Project(
            id="LLD",
            name=project_name,
            product_line="金融科技产品",
            stage="演示验证",
            current_baseline_id="BASE-STALE",
            allow_external_model=False,
            created_at=NOW,
            updated_at=NOW,
        ),
        current_baseline=DashboardBaselineView(
            id="BASE-LLD-724_1",
            project_id="LLD",
            version=version,
            parent_baseline_id=None,
            status=BaselineStatus.EFFECTIVE,
            full_document_path="data/baseline/full.md",
            card_snapshot_path="data/baseline/cards.json",
            change_request_id=None,
            approved_by="产品经理",
            effective_at=NOW,
        ),
        open_issue_count=4,
        candidate_change_count=2,
        source_count=12,
        recent_events=events,
        integrity_ok=integrity_ok,
    )


def _html(page: AppTest) -> str:
    return "\n".join(item.value for item in page.markdown if item.proto.allow_html)


def test_home_normal_state_has_baseline_first_hierarchy_one_primary_and_grouped_metrics() -> None:
    """Catches weakening baseline priority, duplicating primary actions, or KPI-card metrics."""
    page = AppTest.from_function(
        _render_page,
        args=(_view().model_dump_json(),),
    ).run()

    assert not page.exception
    assert not page.error
    rendered = _html(page)
    assert rendered.index('data-testid="project-header"') < rendered.index(
        'data-testid="baseline-hero"'
    )
    assert rendered.index('data-testid="baseline-hero"') < rendered.index(
        'data-testid="project-metrics"'
    )
    assert rendered.index('data-testid="project-metrics"') < rendered.index(
        'data-testid="recent-activity"'
    )
    assert '<span class="pi-baseline-version">LLD-724_1</span>' in rendered
    assert "当前生效" in rendered
    assert rendered.count('class="pi-button pi-button--primary"') == 1
    assert "导入新资料" in rendered
    assert "查询当前产品" in rendered
    assert "启动一键自检" in rendered
    assert rendered.count('class="pi-grouped-list pi-grouped-list--metrics"') == 1
    metrics = rendered.split('data-testid="project-metrics"', 1)[1].split(
        'data-testid="recent-activity"', 1
    )[0]
    assert metrics.count('class="pi-grouped-list__row"') == 3
    assert "pi-kpi-card" not in rendered
    assert 'href="/lint?filter=open"' in metrics
    assert 'href="/release?filter=candidate"' in metrics
    assert 'href="/ingest?view=history"' in metrics
    recent = rendered.split('data-testid="recent-activity"', 1)[1]
    assert recent.count('class="pi-grouped-list__row"') == 5
    assert "overflow-x: hidden" in rendered


def test_home_integrity_warning_keeps_manifest_baseline_and_explains_read_only() -> None:
    """Catches hiding the Manifest version or omitting mutation-blocking guidance."""
    page = AppTest.from_function(
        _render_page,
        args=(_view(integrity_ok=False).model_dump_json(),),
    ).run()

    assert not page.exception
    assert len(page.error) == 1
    assert "当前基线镜像需要修复" in page.error[0].value
    assert "查询仍按 Manifest 只读运行，变更发布已暂时禁用。" in page.error[0].value
    assert "BASELINE_INTEGRITY_FAILED" in page.error[0].value
    rendered = _html(page)
    assert '<span class="pi-baseline-version">LLD-724_1</span>' in rendered
    assert rendered.count('class="pi-button pi-button--primary"') == 1


def test_home_escapes_user_controlled_project_baseline_and_event_text_in_html() -> None:
    """Catches inserting persisted user-controlled text into unsafe HTML without escaping it."""
    page = AppTest.from_function(
        _render_page,
        args=(_view(malicious=True).model_dump_json(),),
    ).run()

    assert not page.exception
    rendered = _html(page)
    assert '<img src=x onerror="alert(1)">' not in rendered
    assert '<script>alert("baseline")</script>' not in rendered
    assert '<svg onload="alert(2)">' not in rendered
    assert '<iframe src="javascript:alert(3)"></iframe>' not in rendered
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in rendered
    assert "&lt;script&gt;alert(&quot;baseline&quot;)&lt;/script&gt;" in rendered
    assert "&lt;svg onload=&quot;alert(2)&quot;&gt;" in rendered
    assert "&lt;iframe src=&quot;javascript:alert(3)&quot;&gt;&lt;/iframe&gt;" in rendered


def test_home_without_local_baseline_has_one_bootstrap_action_and_escapes_settings() -> None:
    """Catches an unusable empty state or unsafe config text interpolation before bootstrap."""
    page = AppTest.from_function(_render_empty_page).run()

    assert not page.exception
    rendered = _html(page)
    assert "尚未建立产品基线" in rendered
    assert "导入当前产品方案" in rendered
    assert rendered.count('class="pi-button pi-button--primary"') == 1
    assert '<img src=x onerror="alert(9)">' not in rendered
    assert "&lt;img src=x onerror=&quot;alert(9)&quot;&gt;" in rendered
