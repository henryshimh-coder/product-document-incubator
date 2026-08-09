from __future__ import annotations

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from src.domain.enums import SecurityLevel
from src.domain.errors import AppError, ErrorCode
from src.ui.pages.ingest import (
    IngestFormState,
    build_feedback,
    can_submit,
    effective_external_permission,
    result_badge,
    stage_states,
    step_labels,
)


def _complete_state(**updates):
    state = IngestFormState(
        uploaded_name="风险意见.md",
        uploaded_bytes=b"risk",
        source_type="risk_opinion",
        authority_level="professional_opinion",
        source_department="风险",
        provider="",
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted_confirmed=True,
        allow_external_model=True,
        is_sandbox=False,
    )
    return state.model_copy(update=updates)


def test_ingest_page_has_exact_three_step_journey_and_safety_gated_submit():
    assert step_labels() == (
        "1 上传文件",
        "2 确认资料属性和安全边界",
        "3 编译并查看结果",
    )
    assert can_submit(_complete_state()) is True
    assert can_submit(_complete_state(uploaded_bytes=None)) is False
    assert can_submit(_complete_state(is_redacted_confirmed=False)) is False
    assert can_submit(_complete_state(document_version="")) is False


def test_l3_l4_and_unconfirmed_redaction_disable_external_calls():
    assert effective_external_permission(_complete_state()) is True
    assert (
        effective_external_permission(_complete_state(security_level=SecurityLevel.L3_CONFIDENTIAL))
        is False
    )
    assert effective_external_permission(_complete_state(is_redacted_confirmed=False)) is False


def test_timeout_feedback_and_result_badges_are_safe_and_visually_distinct():
    timeout = build_feedback(AppError(ErrorCode.MODEL_TIMEOUT))
    assert timeout.title == "实时分析超时"
    assert timeout.impact == "资料已安全保存在本地，尚未写入知识库。"
    assert timeout.next_action == "可继续等待，或使用同材料、同版本的冻结缓存。"
    assert timeout.error_code == "MODEL_TIMEOUT"
    assert timeout.offer_cache is True
    assert "DIFY" not in " ".join(str(value) for value in timeout.model_dump().values())
    assert result_badge("realtime") != result_badge("cache")
    assert result_badge("cache") == ("冻结缓存", "warning")
    assert result_badge("local_only") == ("本地检查", "muted")
    coverage = build_feedback(AppError("OUTBOUND_COVERAGE_EXCEEDED"))
    assert "覆盖率预算" in coverage.next_action
    assert coverage.offer_cache is True
    assert coverage.offer_local is True


def test_sandbox_and_ai_inferred_labels_are_explicit():
    assert result_badge("sandbox") == ("模拟材料", "violet")
    assert result_badge("ai_inferred") == ("AI 推定", "muted")
    assert result_badge("human_confirmed") == ("人工确认", "success")


def test_six_stage_state_machine_covers_waiting_processing_completed_and_failed():
    from src.ui.pages import ingest

    assert len(ingest.stage_states("waiting")) == 6
    assert {item.status for item in ingest.stage_states("processing")} == {
        "processing",
        "waiting",
    }
    assert {item.status for item in ingest.stage_states("completed")} == {"completed"}
    assert "failed" in {item.status for item in ingest.stage_states("failed", failed_index=3)}


def test_rendered_three_step_page_gates_submit_and_offers_cache_after_timeout():
    def render_page():
        from src.application.container import AppContainer, AppSettings
        from src.domain.errors import AppError, ErrorCode
        from src.ui.pages.ingest import render

        class TimeoutService:
            def execute(self, command):
                raise AppError(ErrorCode.MODEL_TIMEOUT)

        container = AppContainer(
            settings=AppSettings(
                name="产品智策",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=(".pdf", ".docx", ".txt", ".md"),
                demo_mode=True,
                schema_version="1.0",
            ),
            import_source=TimeoutService(),
        )
        render(container)

    page = AppTest.from_function(render_page).run()
    assert not page.exception
    assert [item.value for item in page.subheader] == [
        "1 上传文件",
        "2 确认资料属性和安全边界",
        "3 编译并查看结果",
    ]
    assert page.button(key="ingest_submit").disabled is True

    page.file_uploader[0].set_value(("风险意见.md", b"risk", "text/markdown"))
    page.selectbox(key="ingest_source_type").select("risk_opinion")
    page.selectbox(key="ingest_authority").select("professional_opinion")
    page.text_input(key="ingest_department").set_value("风险")
    page.text_input(key="ingest_version").set_value("v1.0")
    page.text_input(key="ingest_baseline").set_value("LLD-724_1")
    page.checkbox(key="ingest_redacted").check()
    page.run()
    page.toggle(key="ingest_external").set_value(True).run()

    assert page.button(key="ingest_submit").disabled is False
    page.button(key="ingest_submit").click().run()

    assert not page.exception
    assert any("MODEL_TIMEOUT" in item.value for item in page.warning)
    assert page.button(key="ingest_use_cache").label == "使用缓存结果"


def test_rendered_cache_retry_shows_hash_time_and_grouped_result_details():
    def render_page():
        from datetime import UTC, datetime

        from src.application.container import AppContainer, AppSettings
        from src.domain.errors import AppError, ErrorCode
        from src.domain.models import IngestReport, IngestResultView
        from src.ui.pages.ingest import render

        class RetryService:
            def execute(self, command):
                if command.preferred_mode == "realtime":
                    raise AppError(ErrorCode.MODEL_TIMEOUT)
                return IngestReport(
                    source_id="SRC-001",
                    duplicate=False,
                    summary="缓存恢复完成",
                    created_card_ids=["CARD-001"],
                    created_relation_ids=[],
                    created_issue_ids=["ISSUE-001"],
                    candidate_count=0,
                    conflict_count=1,
                    result_mode="cache",
                    model_call_id=None,
                    source_hash8="abcdef12",
                    cache_generated_at=datetime(2026, 7, 29, 8, 0, tzinfo=UTC),
                    result_items=[
                        IngestResultView(
                            item_type="professional_opinion",
                            summary="建议收紧目标客群",
                            section="目标客群",
                            citation="风险意见要求收紧目标客群",
                            status="conflict",
                        )
                    ],
                )

        render(
            AppContainer(
                settings=AppSettings(
                    name="产品智策",
                    project_id="LLD",
                    default_query_scope="effective",
                    max_upload_mb=20,
                    accepted_extensions=(".pdf", ".docx", ".txt", ".md"),
                    demo_mode=True,
                    schema_version="1.0",
                ),
                import_source=RetryService(),
            )
        )

    page = AppTest.from_function(render_page).run()
    page.file_uploader[0].set_value(("风险意见.md", b"risk", "text/markdown"))
    page.selectbox(key="ingest_source_type").select("risk_opinion")
    page.selectbox(key="ingest_authority").select("professional_opinion")
    page.text_input(key="ingest_department").set_value("风险")
    page.text_input(key="ingest_version").set_value("v1.0")
    page.text_input(key="ingest_baseline").set_value("LLD-724_1")
    page.checkbox(key="ingest_redacted").check()
    page.run()
    page.toggle(key="ingest_external").set_value(True).run()
    page.radio(key="ingest_mode").set_value("realtime").run()
    page.button(key="ingest_submit").click().run()
    page.button(key="ingest_use_cache").click().run()

    assert not page.exception
    assert any(
        "abcdef12" in item.value and "2026-07-29T08:00:00" in item.value for item in page.warning
    )
    rendered = "\n".join(item.value for item in page.markdown)
    assert "建议收紧目标客群" in rendered
    assert "风险意见要求收紧目标客群" in rendered
    assert all(item.status == "completed" for item in stage_states("completed"))


def test_rendered_realtime_success_shows_completed_flow():
    def render_page():
        from src.domain.models import IngestReport
        from src.ui.pages.ingest import _render_report, _render_stages, stage_states

        _render_stages(stage_states("completed"))
        _render_report(
            IngestReport(
                source_id="SRC-001",
                duplicate=False,
                summary="实时分析完成",
                created_card_ids=[],
                created_relation_ids=[],
                created_issue_ids=[],
                candidate_count=0,
                conflict_count=0,
                result_mode="realtime",
                model_call_id="CALL-001",
                source_hash8="12345678",
            ),
            sandbox=False,
        )

    page = AppTest.from_function(render_page).run()
    assert not page.exception
    assert any("实时分析" in item.value for item in page.info)
    assert any("completed" in item.value for item in page.markdown)


def _complete_rendered_form(page):
    page.file_uploader[0].set_value(("风险意见.md", b"risk", "text/markdown"))
    page.selectbox(key="ingest_source_type").select("risk_opinion")
    page.selectbox(key="ingest_authority").select("professional_opinion")
    page.text_input(key="ingest_department").set_value("风险")
    page.text_input(key="ingest_version").set_value("v1.0")
    page.text_input(key="ingest_baseline").set_value("LLD-724_1")
    page.checkbox(key="ingest_redacted").check()
    page.run()
    page.toggle(key="ingest_external").set_value(True).run()
    page.radio(key="ingest_mode").set_value("realtime").run()
    return page


def _stage_snapshot(states) -> str:
    statuses = [item.status for item in states]
    if "failed" in statuses:
        return f"failed:{statuses.index('failed')}"
    if "processing" in statuses:
        return "processing"
    if set(statuses) == {"completed"}:
        return "completed"
    return "waiting"


def test_formal_render_click_path_progresses_waiting_processing_completed(monkeypatch):
    from src.ui.pages import ingest

    trace: list[str] = []
    original_render_stages = ingest._render_stages

    def recording_render_stages(states, **kwargs):
        trace.append(_stage_snapshot(states))
        return original_render_stages(states, **kwargs)

    monkeypatch.setattr(ingest, "_render_stages", recording_render_stages)

    def render_page():
        from src.application.container import AppContainer, AppSettings
        from src.domain.models import IngestReport
        from src.ui.pages import ingest

        class SuccessService:
            def execute(self, command):
                return IngestReport(
                    source_id="SRC-001",
                    duplicate=False,
                    summary="完成",
                    created_card_ids=[],
                    created_relation_ids=[],
                    created_issue_ids=[],
                    candidate_count=0,
                    conflict_count=0,
                    result_mode="realtime",
                    model_call_id="CALL-001",
                    source_hash8="12345678",
                )

        ingest.render(
            AppContainer(
                settings=AppSettings(
                    name="产品智策",
                    project_id="LLD",
                    default_query_scope="effective",
                    max_upload_mb=20,
                    accepted_extensions=(".pdf", ".docx", ".txt", ".md"),
                    demo_mode=True,
                    schema_version="1.0",
                ),
                import_source=SuccessService(),
            )
        )

    page = _complete_rendered_form(AppTest.from_function(render_page).run())
    page.button(key="ingest_submit").click().run()

    assert not page.exception
    processing_index = trace.index("processing")
    assert "waiting" in trace[:processing_index]
    assert "completed" in trace[processing_index + 1 :]


@pytest.mark.parametrize(
    ("error_code", "failed_index"),
    [
        (ErrorCode.FILE_TYPE_NOT_ALLOWED, 0),
        (ErrorCode.EXTRACTION_FAILED, 1),
        (ErrorCode.EXTERNAL_CALL_DENIED, 2),
        (ErrorCode.CACHE_NOT_FOUND, 3),
        (ErrorCode.MODEL_OUTPUT_INVALID, 4),
        (ErrorCode.INGEST_PERSISTENCE_FAILED, 5),
    ],
)
def test_formal_render_click_path_maps_errors_to_the_failed_stage(
    monkeypatch,
    error_code,
    failed_index,
):
    from src.ui.pages import ingest

    trace: list[str] = []
    original_render_stages = ingest._render_stages

    def recording_render_stages(states, **kwargs):
        trace.append(_stage_snapshot(states))
        return original_render_stages(states, **kwargs)

    monkeypatch.setattr(ingest, "_render_stages", recording_render_stages)

    def render_page(error_code_value):
        from src.application.container import AppContainer, AppSettings
        from src.domain.errors import AppError
        from src.ui.pages import ingest

        class FailedService:
            def execute(self, command):
                raise AppError(error_code_value)

        ingest.render(
            AppContainer(
                settings=AppSettings(
                    name="产品智策",
                    project_id="LLD",
                    default_query_scope="effective",
                    max_upload_mb=20,
                    accepted_extensions=(".pdf", ".docx", ".txt", ".md"),
                    demo_mode=True,
                    schema_version="1.0",
                ),
                import_source=FailedService(),
            )
        )

    page = _complete_rendered_form(
        AppTest.from_function(render_page, args=(error_code.value,)).run()
    )
    page.button(key="ingest_submit").click().run()

    assert not page.exception
    processing_index = trace.index("processing")
    assert f"failed:{failed_index}" in trace[processing_index + 1 :]
