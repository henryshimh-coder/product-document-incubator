from __future__ import annotations

from datetime import UTC, datetime

from streamlit.testing.v1 import AppTest

from src.domain.enums import EvidenceSide, IssueSeverity, IssueStatus
from src.domain.models import IssueCard, IssueEvidence

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _issue() -> IssueCard:
    return IssueCard(
        id="ISSUE-001",
        project_id="LLD",
        issue_type="conflict",
        severity=IssueSeverity.PENDING_DECISION,
        status=IssueStatus.OPEN,
        title="客群边界不一致",
        description="需要会议确认当前执行口径。",
        evidence=[
            IssueEvidence(
                source_id="SRC-BASE",
                citation_id="CIT-BASE-001",
                excerpt="当前目标客群规则。",
                document_version="LLD-724_1",
                page_or_section="目标客群",
                side=EvidenceSide.CURRENT_BASELINE,
            ),
            IssueEvidence(
                source_id="SRC-RISK",
                citation_id="CIT-RISK-001",
                excerpt="风险意见要求收紧客群。",
                document_version="v1.0",
                page_or_section="客群限制",
                side=EvidenceSide.CHALLENGING_SOURCE,
            ),
        ],
        impacted_domains=["产品", "风险"],
        options=[{"code": "A", "label": "收紧", "impact": "调整产品规则"}],
        ai_recommendation="A",
        ai_confidence=0.78,
        uncertainty="专业意见尚未形成正式决定",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _render(
    issue_json: str,
    configured: bool = True,
    change_result: bool = False,
    navigation_probe: bool = False,
    raises: str | None = None,
    cache_raises: str | None = None,
    report_json: str | None = None,
) -> None:
    import streamlit as st

    from src.application.container import AppContainer, AppSettings
    from src.ui.pages import lint as lint_page
    from src.ui.theme.loader import load_theme

    if navigation_probe:
        st.session_state["_pi_release_page"] = "REGISTERED-RELEASE"

        def capture_switch(page):
            st.session_state["lint_test_switched_page"] = page

        lint_page.st.switch_page = capture_switch

    class LintService:
        def execute(self, command):
            st.session_state["lint_test_run_command"] = command.model_dump(mode="json")
            from src.domain.errors import DomainError, ErrorCode

            if command.preferred_mode == "cache" and cache_raises is not None:
                raise DomainError(ErrorCode(cache_raises))
            if command.preferred_mode != "cache" and raises is not None:
                raise DomainError(ErrorCode(raises))
            if report_json is not None:
                from src.domain.models import LintReport

                return LintReport.model_validate_json(report_json)
            return None

        def list_open(self, project_id):
            from src.domain.models import IssueCard

            return [IssueCard.model_validate_json(issue_json)]

        def list_all(self, project_id):
            from src.domain.models import IssueCard

            return [IssueCard.model_validate_json(issue_json)]

        def list_issues(self, command):
            from src.domain.models import IssueCard

            st.session_state["lint_test_list_command"] = command.model_dump(mode="json")
            return [IssueCard.model_validate_json(issue_json)]

    class DecisionService:
        def execute(self, command):
            st.session_state["lint_test_decision_command"] = command.model_dump(mode="json")
            from datetime import UTC, datetime

            from src.domain.enums import ChangeStatus
            from src.domain.models import ChangeRequest, Decision, DecisionResult

            now = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)

            change = None
            if change_result:
                requested = command.change_request
                change = ChangeRequest(
                    id="CHANGE-UI-001",
                    project_id="LLD",
                    issue_id="ISSUE-001",
                    decision_id="DECISION-001",
                    target_card_id=requested.target_card_id,
                    before_content=requested.before_content,
                    after_content=requested.after_content,
                    rationale=requested.rationale,
                    evidence_refs=requested.evidence_refs,
                    impacted_objects=requested.impacted_objects,
                    responsible_domain=requested.responsible_domain,
                    required_approver_role=requested.required_approver_role,
                    demo_confirmer=requested.demo_confirmer,
                    status=ChangeStatus.PENDING_APPROVAL,
                    review_action=None,
                    reviewed_by=None,
                    review_comment=None,
                    review_idempotency_key=None,
                    reviewed_at=None,
                    target_version=requested.target_version,
                    effective_condition=requested.effective_condition,
                    created_at=now,
                    updated_at=now,
                )

            return DecisionResult(
                decision=Decision(
                    id="DECISION-001",
                    project_id="LLD",
                    issue_id="ISSUE-001",
                    action=command.action,
                    conclusion=command.conclusion,
                    confirmed_by=command.confirmed_by,
                    responsible_party=command.responsible_party,
                    due_at=command.due_at,
                    verification_condition=command.verification_condition,
                    created_at=now,
                ),
                change_request=change,
            )

    load_theme()
    lint_page.render(
        AppContainer(
            settings=AppSettings(
                name="产品智策",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
                lint_input_contract_version="2.0",
            ),
            lint=LintService() if configured else None,
            record_decision=DecisionService(),
        )
    )


def _html(page: AppTest) -> str:
    return "\n".join(item.value for item in page.markdown)


def test_lint_page_has_38_62_workspace_one_primary_and_fixed_issue_detail_order() -> None:
    """Catches the meeting workspace losing its hierarchy or exposing multiple main actions."""
    page = AppTest.from_function(_render, args=(_issue().model_dump_json(),)).run()

    assert not page.exception
    rendered = _html(page)
    headings = (
        "问题结论",
        "为什么需要处理",
        "依据 A",
        "依据 B",
        "影响范围",
        "AI 选项和建议",
        "不确定性",
        "决策条",
    )
    positions = [rendered.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert 'data-layout="38-62"' in rendered
    assert "AI 建议" in rendered
    assert "当前基线侧" in rendered
    assert "挑战来源侧" in rendered
    assert page.button(key="lint_run").proto.type == "primary"
    assert sum(button.proto.type == "primary" for button in page.button) == 1
    assert page.radio(key="decision_action").options == [
        "接受迭代",
        "维持现状",
        "暂缓讨论",
        "判定误报",
    ]
    semantic_view = page.selectbox(key="lint_view")
    assert semantic_view.label == "问题视图"
    assert semantic_view.options == [
        "全部开放",
        "阻断",
        "待决策",
        "待补充",
        "已处理",
        "误报",
    ]
    assert semantic_view.value == "all_open"
    assert not page.multiselect
    assert page.selectbox(key="lint_sort").label == "排序"
    assert page.session_state["lint_test_list_command"]["view"] == "all_open"


def test_lint_page_missing_runtime_configuration_degrades_without_exception() -> None:
    """Catches missing Dify configuration turning the page into an exception."""
    page = AppTest.from_function(
        _render,
        args=(_issue().model_dump_json(),),
        kwargs={"configured": False},
    ).run()

    assert not page.exception
    assert page.button(key="lint_run").disabled is True
    assert any("自检服务尚未就绪" in item.value for item in page.info)


def test_lint_page_sends_selected_semantic_view_to_application_service() -> None:
    """Catches processed history being approximated by generic UI-only filters."""
    page = AppTest.from_function(_render, args=(_issue().model_dump_json(),)).run()

    page.selectbox(key="lint_view").select("已处理").run()

    assert not page.exception
    assert page.session_state["lint_test_list_command"]["view"] == "processed"


def test_release_navigation_calls_streamlit_switch_page(monkeypatch) -> None:
    """Catches the post-decision release button rendering without navigation behavior."""
    from src.ui.pages import lint

    release_page = object()
    calls: list[object] = []
    monkeypatch.setattr(lint.st, "session_state", {"_pi_release_page": release_page})
    monkeypatch.setattr(lint.st, "switch_page", calls.append)

    lint._go_to_release()

    assert calls == [release_page]


def test_issue_list_label_displays_type_severity_status_and_update_time() -> None:
    """Catches processed history hiding the issue type or governance state."""
    from src.ui.components.issue_list import _issue_label

    label = _issue_label(_issue())

    assert "conflict" in label
    assert "待决策" in label
    assert "open" in label
    assert "07-29 07:00" in label


def test_lint_detail_displays_deterministic_raw_severity_transition() -> None:
    """Catches persisted deterministic audit identity being hidden from reviewers."""
    issue = _issue().model_copy(
        update={
            "severity": IssueSeverity.PENDING_INFO,
            "uncertainty": "缺少独立挑战依据",
            "validation_note": ("严重度由 blocking 降级为 pending_info：缺少独立挑战依据"),
            "raw_severity": IssueSeverity.BLOCKING,
            "deterministic_rule_id": "GOV-001",
        }
    )

    page = AppTest.from_function(_render, args=(issue.model_dump_json(),)).run()

    assert not page.exception
    rendered = _html(page)
    assert "确定性规则：GOV-001" in rendered
    assert "原始严重度：blocking" in rendered
    assert "当前严重度：pending_info" in rendered


def test_accept_change_success_flashes_pending_change_and_navigates_release() -> None:
    """Catches the real accept form losing its change id or registered-page navigation."""
    page = AppTest.from_function(
        _render,
        args=(_issue().model_dump_json(),),
        kwargs={"change_result": True, "navigation_probe": True},
    ).run()

    page.text_input(key="decision_confirmed_by").input("产品经理")
    page.text_area(key="decision_conclusion").input("会议确认采纳风险意见。")
    page.text_input(key="decision_responsible_party").input("产品负责人")
    page.text_area(key="decision_verification_condition").input("回归测试通过。")
    page.text_input(key="change_target_card_id").input("RULE-001")
    page.text_area(key="change_before").input("当前客群规则。")
    page.text_area(key="change_after").input("收紧后的客群规则。")
    page.text_area(key="change_rationale").input("依据风险意见和会议结论调整。")
    page.text_input(key="change_evidence_refs").input("CIT-BASE-001,CIT-RISK-001")
    page.text_input(key="change_impacted_objects").input("RULE-001")
    page.text_input(key="change_domain").input("产品")
    page.text_input(key="change_approver_role").input("产品经理")
    page.text_input(key="change_demo_confirmer").input("产品经理")
    page.text_input(key="change_target_version").input("LLD-724_2")
    page.text_area(key="change_effective_condition").input("审批通过且验证完成后发布。")

    page.button(key="decision_submit").click().run()

    assert not page.exception
    assert any("CHANGE-UI-001" in item.value for item in page.info)
    page.button(key="lint_go_release").click().run()
    assert not page.exception
    assert page.session_state["lint_test_switched_page"] == "REGISTERED-RELEASE"


def _report(
    *,
    result_mode: str = "realtime",
    cached_at: datetime | None = None,
    baseline_version: str | None = None,
) -> str:
    from src.domain.enums import CallResultMode
    from src.domain.models import LintReport

    return LintReport(
        issues=[_issue()],
        deterministic_count=0,
        semantic_count=1,
        result_mode=CallResultMode(result_mode),
        model_call_id=None if result_mode == "cache" else "CALL-LINT-001",
        cache_generated_at=cached_at,
        baseline_version=baseline_version,
    ).model_dump_json()


def test_lint_timeout_continues_with_exact_frozen_cache() -> None:
    """Catches the lint page dropping the flow instead of continuing via exact cache.

    实时超时后页面必须以同材料、同版本的冻结缓存继续（第二次调用
    preferred_mode == "cache"），且不得显示「未找到缓存」（T15-R02）。
    """
    page = AppTest.from_function(
        _render,
        args=(_issue().model_dump_json(),),
        kwargs={"raises": "MODEL_TIMEOUT", "report_json": _report(result_mode="cache")},
    ).run()

    page.button(key="lint_run").click().run()

    assert not page.exception
    successes = "\n".join(item.value for item in page.success)
    assert "自检完成" in successes
    assert "冻结缓存" in successes
    assert page.session_state["lint_test_run_command"]["preferred_mode"] == "cache"
    warnings = "\n".join(item.value for item in page.warning)
    assert "未找到同材料、同版本的可用缓存" not in warnings


def test_lint_timeout_without_exact_cache_disables_fallback() -> None:
    """Catches offering an approximate cache or hiding the no-exact-cache fact.

    实时超时且探测返回 CACHE_NOT_FOUND 时，必须展示「实时分析超时」与
    「未找到同材料、同版本的可用缓存」，且不得显示自检完成（T15-R02）。
    """
    page = AppTest.from_function(
        _render,
        args=(_issue().model_dump_json(),),
        kwargs={"raises": "MODEL_TIMEOUT", "cache_raises": "CACHE_NOT_FOUND"},
    ).run()

    page.button(key="lint_run").click().run()

    assert not page.exception
    warnings = "\n".join(item.value for item in page.warning)
    assert "实时分析超时" in warnings
    assert "未找到同材料、同版本的可用缓存" in warnings
    assert not page.success


def test_lint_fallback_displays_cache_provenance() -> None:
    """Catches the cache continuation hiding frozen-cache provenance.

    缓存接续必须展示「冻结缓存」、可审计的缓存生成时间与当前基线版本，
    且不得被标注为实时结果（T15-R02）。
    """
    cached_at = datetime(2026, 8, 6, 9, 30, 0, tzinfo=UTC)
    page = AppTest.from_function(
        _render,
        args=(_issue().model_dump_json(),),
        kwargs={
            "raises": "MODEL_TIMEOUT",
            "report_json": _report(
                result_mode="cache",
                cached_at=cached_at,
                baseline_version="LLD-724_1",
            ),
        },
    ).run()

    page.button(key="lint_run").click().run()

    assert not page.exception
    successes = "\n".join(item.value for item in page.success)
    assert "冻结缓存" in successes
    assert cached_at.isoformat(timespec="seconds") in successes
    assert "LLD-724_1" in successes
