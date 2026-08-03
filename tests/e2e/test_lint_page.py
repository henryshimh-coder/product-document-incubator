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


def _render(issue_json: str, configured: bool = True) -> None:
    import streamlit as st

    from src.application.container import AppContainer, AppSettings
    from src.ui.pages.lint import render
    from src.ui.theme.loader import load_theme

    class LintService:
        def execute(self, command):
            st.session_state["lint_test_run_command"] = command.model_dump(mode="json")

        def list_open(self, project_id):
            from src.domain.models import IssueCard

            return [IssueCard.model_validate_json(issue_json)]

    class DecisionService:
        def execute(self, command):
            st.session_state["lint_test_decision_command"] = command.model_dump(mode="json")
            from src.domain.models import Decision, DecisionResult

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
                    created_at=NOW,
                ),
                change_request=None,
            )

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
