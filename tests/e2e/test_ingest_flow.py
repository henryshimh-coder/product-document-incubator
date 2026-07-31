from __future__ import annotations

from datetime import date

from streamlit.testing.v1 import AppTest

from src.domain.enums import SecurityLevel
from src.domain.errors import AppError, ErrorCode
from src.ui.pages.ingest import (
    IngestFormState,
    build_feedback,
    can_submit,
    effective_external_permission,
    result_badge,
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


def test_sandbox_and_ai_inferred_labels_are_explicit():
    assert result_badge("sandbox") == ("模拟材料", "violet")
    assert result_badge("ai_inferred") == ("AI 推定", "muted")
    assert result_badge("human_confirmed") == ("人工确认", "success")


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
    page.checkbox(key="ingest_redacted").check()
    page.run()
    page.toggle(key="ingest_external").set_value(True).run()

    assert page.button(key="ingest_submit").disabled is False
    page.button(key="ingest_submit").click().run()

    assert not page.exception
    assert any("MODEL_TIMEOUT" in item.value for item in page.warning)
    assert page.button(key="ingest_use_cache").label == "使用缓存结果"
