from __future__ import annotations

from datetime import UTC, datetime

from streamlit.testing.v1 import AppTest

from src.domain.enums import CallResultMode
from src.domain.models import QueryResponse


def _render_query_page(
    response_json: str,
    *,
    raises: str | None = None,
    cache_raises: str | None = None,
    configured_scope: str = "effective",
) -> None:
    import streamlit as st

    from src.application.container import AppContainer, AppSettings
    from src.domain.errors import DomainError, ErrorCode
    from src.domain.models import QueryResponse
    from src.ui.pages.query import render
    from src.ui.theme.loader import load_theme

    class StaticQuery:
        def list_historical_versions(self, project_id):
            return ("LLD-700_1", "LLD-650_1")

        def execute(self, command):
            st.session_state["query_test_command"] = command.model_dump(mode="json")
            if command.preferred_mode == "cache" and cache_raises is not None:
                raise DomainError(ErrorCode(cache_raises))
            if command.preferred_mode != "cache" and raises is not None:
                raise DomainError(ErrorCode(raises))
            return QueryResponse.model_validate_json(response_json)

    load_theme()
    render(
        AppContainer(
            settings=AppSettings(
                name="产品智策",
                project_id="LLD",
                default_query_scope=configured_scope,
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
            ),
            query=StaticQuery(),
        )
    )


def _response(*, with_notices: bool = False, evidence: str = "sufficient") -> QueryResponse:
    return QueryResponse(
        answer=(
            "现有材料不足以支持确定结论。请补充资料或查看相关引用。"
            if evidence == "insufficient"
            else "当前目标客群是符合准入要求的存量客户。"
        ),
        effective_rules=["RULE-LLD-001"],
        citations=[
            {
                "id": "CIT-SRC-001-01",
                "source_id": "SRC-001",
                "filename": "当前产品方案.md",
                "document_version": "v1.0",
                "section": "目标客群",
                "excerpt": "当前目标客群是符合准入要求的存量客户。",
                "authority_level": "formal_effective",
            }
        ],
        candidate_notice="建议收紧客群。" if with_notices else None,
        conflict_notice="客群口径存在冲突。" if with_notices else None,
        baseline_version="LLD-724_1",
        evidence_sufficiency=evidence,
        result_mode="realtime",
        model_call_id="CALL-QUERY-001",
    )


def _html(page: AppTest) -> str:
    return "\n".join(item.value for item in page.markdown if item.proto.allow_html)


def _submit(page: AppTest, *, question: str = "当前目标客群是什么？") -> AppTest:
    page.text_input(key="query_question").set_value(question)
    page.button(key="query_submit").click()
    return page.run()


def test_query_page_defaults_to_effective_has_six_prompts_and_one_primary_action() -> None:
    """Catches an unsafe scope default, prompt overload, or multiple primary actions."""
    page = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
    ).run()

    assert not page.exception
    assert page.radio(key="query_scope").value == "effective"
    quick_questions = [button for button in page.button if button.key.startswith("query_quick_")]
    assert len(quick_questions) == 6
    assert page.button(key="query_submit").label == "查询"
    assert page.button(key="query_submit").proto.type == "primary"
    assert sum(button.proto.type == "primary" for button in page.button) == 1
    assert page.text_input(key="query_question").proto.max_chars == 500

    configured_historical = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
        kwargs={"configured_scope": "historical"},
    ).run()
    assert configured_historical.radio(key="query_scope").value == "effective"


def test_common_question_only_fills_the_input_and_does_not_submit() -> None:
    """Catches a convenience prompt triggering an external call without explicit Query click."""
    page = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
    ).run()

    page.button(key="query_quick_0").click().run()

    assert page.text_input(key="query_question").value == "当前目标客群是什么？"
    assert "当前回答" not in _html(page)


def test_query_result_renders_required_hierarchy_citation_and_runtime_status() -> None:
    """Catches answers that hide scope, citations, sufficiency, or realtime/cache provenance."""
    page = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
    ).run()

    page = _submit(page)

    assert not page.exception
    rendered = _html(page)
    headings = (
        "当前回答",
        "适用版本和范围",
        "关键结论引用",
        "候选／冲突提示（仅 notice）",
        "证据充分度",
        "实时／缓存状态",
    )
    positions = [rendered.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "LLD-724_1" in rendered
    assert "当前生效" in rendered
    assert "充分" in rendered
    assert "实时生成" in rendered
    assert ".pi-query-answer" in rendered
    assert "font-size: 18px" in rendered
    assert "overflow-x: hidden" in rendered
    assert len(page.expander) == 1
    assert "CIT-SRC-001-01" in page.expander[0].label
    assert "当前产品方案.md" in page.expander[0].label


def test_cached_result_shows_frozen_cache_baseline_and_generation_time() -> None:
    """Catches the query page hiding frozen-cache provenance or mislabeling it as realtime.

    移除 ``cache`` 标签映射会触发 KeyError 使本用例失败；缓存响应必须同时展示
    「冻结缓存」、当前基线版本与可审计的缓存生成时间，且不得出现「实时生成」。
    """
    cached_at = datetime(2026, 8, 6, 9, 30, 0, tzinfo=UTC)
    cached_response = QueryResponse.model_validate_json(
        _response()
        .model_copy(
            update={
                "result_mode": CallResultMode.CACHE,
                "model_call_id": None,
                "cache_generated_at": cached_at,
            }
        )
        .model_dump_json()
    )
    page = AppTest.from_function(
        _render_query_page,
        args=(cached_response.model_dump_json(),),
    ).run()

    page.radio(key="query_mode").set_value("cache").run()
    page = _submit(page)

    assert not page.exception
    rendered = _html(page)
    assert "冻结缓存" in rendered
    assert "LLD-724_1" in rendered
    assert cached_at.isoformat(timespec="seconds") in rendered
    assert "实时生成" not in rendered
    assert page.session_state["query_test_command"]["preferred_mode"] == "cache"


def test_candidate_and_conflict_are_not_interpolated_into_the_answer_block() -> None:
    """Catches notice text being presented as part of the current effective answer."""
    response = _response(with_notices=True)
    page = AppTest.from_function(
        _render_query_page,
        args=(response.model_dump_json(),),
    ).run()
    page.radio(key="query_scope").set_value("effective_with_notices").run()

    page = _submit(page)

    rendered = _html(page)
    answer_section = rendered.split("当前回答", 1)[1].split("适用版本和范围", 1)[0]
    assert "建议收紧客群" not in answer_section
    assert "客群口径存在冲突" not in answer_section
    assert any("建议收紧客群" in item.value for item in page.info)
    assert any("客群口径存在冲突" in item.value for item in page.warning)


def test_insufficient_evidence_uses_the_fixed_non_factual_copy() -> None:
    """Catches the UI inventing a company fact when application evidence is insufficient."""
    page = AppTest.from_function(
        _render_query_page,
        args=(_response(evidence="insufficient").model_dump_json(),),
    ).run()

    page = _submit(page)

    assert "现有材料不足以支持确定结论。请补充资料或查看相关引用。" in _html(page)
    assert "证据不足" in _html(page)


def test_historical_scope_outside_form_shows_dropdown_without_prior_submit_and_gates_query() -> (
    None
):
    """Catches the historical dropdown requiring an erroneous first Query submission."""
    page = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
    ).run()

    assert not page.radio(key="query_scope").proto.form_id
    assert page.text_input(key="query_question").proto.form_id == "query_form"
    assert page.button(key="query_submit").proto.form_id == "query_form"
    page.radio(key="query_scope").set_value("historical").run()

    assert not page.selectbox(key="query_historical_version").proto.form_id
    assert page.selectbox(key="query_historical_version").value is None
    assert page.button(key="query_submit").disabled is True
    page.selectbox(key="query_historical_version").select("LLD-700_1").run()
    assert page.button(key="query_submit").disabled is False


def test_historical_query_submits_the_explicitly_selected_version_once() -> None:
    """Catches historical selection being lost between its immediate rerun and form submit."""
    page = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
    ).run()
    page.radio(key="query_scope").set_value("historical").run()
    page.selectbox(key="query_historical_version").select("LLD-700_1").run()

    page = _submit(page, question="历史客群规则是什么？")

    assert page.session_state["query_test_command"] == {
        "project_id": "LLD",
        "question": "历史客群规则是什么？",
        "scope": "historical",
        "historical_version": "LLD-700_1",
        "preferred_mode": "realtime",
    }


def test_switching_scope_clears_response_instead_of_relabeling_old_answer() -> None:
    """Catches a prior effective response being displayed under a newly selected scope."""
    page = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
    ).run()
    page = _submit(page)
    assert "当前回答" in _html(page)

    page.radio(key="query_scope").set_value("effective_with_notices").run()

    assert "当前回答" not in _html(page)


def test_realtime_timeout_continues_with_exact_frozen_cache() -> None:
    """Catches the timeout path ignoring an exact cache or hiding its provenance.

    实时超时后页面必须以同问题、同版本、同材料的冻结缓存继续（第二次调用
    preferred_mode == "cache"），且按缓存口径标注，不得显示“未找到缓存”。
    """
    cached = _response().model_copy(
        update={
            "result_mode": CallResultMode.CACHE,
            "model_call_id": None,
            "cache_generated_at": datetime(2026, 8, 6, 9, 30, 0, tzinfo=UTC),
        }
    )
    page = AppTest.from_function(
        _render_query_page,
        args=(cached.model_dump_json(),),
        kwargs={"raises": "MODEL_TIMEOUT"},
    ).run()

    page = _submit(page)

    assert not page.exception
    rendered = _html(page)
    assert "当前回答" in rendered
    assert "冻结缓存" in rendered
    assert "实时生成" not in rendered
    assert page.session_state["query_test_command"]["preferred_mode"] == "cache"
    warnings = "\n".join(item.value for item in page.warning)
    assert "未找到同材料、同版本的可用缓存" not in warnings


def test_realtime_timeout_without_exact_cache_shows_disabled_fallback() -> None:
    """Catches offering an approximate cache or hiding the no-exact-cache fact.

    实时超时且探测返回 CACHE_NOT_FOUND 时，必须展示「实时分析超时」与
    「未找到同材料、同版本的可用缓存」，且不得渲染回答区。
    """
    page = AppTest.from_function(
        _render_query_page,
        args=(_response().model_dump_json(),),
        kwargs={"raises": "MODEL_TIMEOUT", "cache_raises": "CACHE_NOT_FOUND"},
    ).run()

    page = _submit(page)

    assert not page.exception
    warnings = "\n".join(item.value for item in page.warning)
    assert "实时分析超时" in warnings
    assert "未找到同材料、同版本的可用缓存" in warnings
    assert "当前回答" not in _html(page)
