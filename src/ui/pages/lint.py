from __future__ import annotations

from html import escape

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.lint import ListLintIssuesInput, RunLintInput
from src.domain.enums import EvidenceSide, IssueStatus
from src.domain.errors import AppError
from src.domain.models import IssueCard, IssueEvidence
from src.ui.components.decision_bar import clear_decision_idempotency, render_decision_bar
from src.ui.components.issue_list import render_issue_list

_SCOPE_LABELS = {
    "current": "当前基线",
    "current_plus_source": "当前基线＋本次新资料",
    "all_current_sources": "全部当前资料",
}

_VIEW_LABELS = {
    "all_open": "全部开放",
    "blocking": "阻断",
    "pending_decision": "待决策",
    "pending_info": "待补充",
    "processed": "已处理",
    "false_positive": "误报",
}


def render(container: AppContainer) -> None:
    st.title("一键自检")
    st.caption(f"检查项目 {container.settings.project_id} 的规则冲突和治理问题")
    flash = st.session_state.get("lint_decision_flash")
    if flash is not None:
        st.success("会议结论已记录，问题状态已更新。")
        if flash.get("change_id"):
            st.info(f"已生成待审批变更 {flash['change_id']}。可从左侧导航前往“变更发布”。")
            if st.button("前往变更发布", key="lint_go_release", type="tertiary"):
                st.session_state.pop("lint_decision_flash", None)
                _go_to_release()
        else:
            st.session_state.pop("lint_decision_flash", None)
    st.markdown('<div data-layout="38-62"></div>', unsafe_allow_html=True)
    left, right = st.columns([38, 62], gap="large")
    service_available = container.lint is not None
    with left:
        scope = st.radio(
            "自检范围",
            options=list(_SCOPE_LABELS),
            format_func=_SCOPE_LABELS.__getitem__,
            index=1,
            key="lint_scope",
        )
        source_id = None
        if scope == "current_plus_source":
            source_id = st.text_input("本次资料 ID *", key="lint_source_id")
        view = st.selectbox(
            "问题视图",
            options=list(_VIEW_LABELS),
            format_func=_VIEW_LABELS.__getitem__,
            key="lint_view",
        )
        sort_by = st.selectbox(
            "排序",
            options=("severity", "updated"),
            format_func={"severity": "严重度", "updated": "最近更新"}.__getitem__,
            key="lint_sort",
        )
        run_clicked = st.button(
            "启动一键自检",
            type="primary",
            key="lint_run",
            use_container_width=True,
            disabled=not service_available,
        )
        if not service_available:
            st.info("自检服务尚未就绪，请检查本地基线和运行配置。")
            issues: list[IssueCard] = []
        else:
            if run_clicked:
                try:
                    container.lint.execute(
                        RunLintInput(
                            project_id=container.settings.project_id,
                            scope=scope,
                            source_id=source_id,
                        )
                    )
                    st.success("自检完成，问题列表已更新。")
                except AppError as error:
                    st.error(f"{error.user_message}  \n错误码：`{error.code}`")
                except (KeyError, OSError, ValueError):
                    st.error("自检未完成，请检查当前基线和比较资料。")
            try:
                issues = container.lint.list_issues(
                    ListLintIssuesInput(
                        project_id=container.settings.project_id,
                        view=view,
                        sort_by=sort_by,
                    )
                )
            except (KeyError, OSError, ValueError):
                issues = []
                st.warning("问题列表暂时不可读取。")
        selected_id = render_issue_list(issues)

    with right:
        selected = next((item for item in issues if item.id == selected_id), None)
        if selected is None:
            st.info("选择一个问题后查看双方依据并记录会议结论。")
            return
        _render_issue(selected)
        if selected.status != IssueStatus.OPEN:
            st.info("该问题已处理，可查看历史依据与结论。")
            return
        if container.record_decision is None:
            st.info("会议决定服务尚未就绪。")
            return
        command = render_decision_bar(selected)
        if command is None:
            return
        try:
            result = container.record_decision.execute(command)
        except AppError as error:
            st.error(f"{error.user_message}  \n错误码：`{error.code}`")
            return
        except (KeyError, OSError, ValueError):
            st.error("会议结论未记录，请核对必填字段后重试。")
            return
        clear_decision_idempotency(selected.id)
        st.session_state["lint_decision_flash"] = {
            "change_id": None if result.change_request is None else result.change_request.id
        }
        st.rerun()


def _go_to_release() -> None:
    release_page = st.session_state.get("_pi_release_page")
    if release_page is None:
        raise RuntimeError("release page is not registered")
    st.switch_page(release_page)


def _render_issue(issue: IssueCard) -> None:
    _section("问题结论", issue.title)
    _section("为什么需要处理", issue.description)
    baseline = next(
        (item for item in issue.evidence if item.side == EvidenceSide.CURRENT_BASELINE),
        None,
    )
    challenging = next(
        (item for item in issue.evidence if item.side == EvidenceSide.CHALLENGING_SOURCE),
        None,
    )
    _evidence("依据 A", "当前基线侧", baseline)
    _evidence("依据 B", "挑战来源侧", challenging)
    _section("影响范围", "、".join(issue.impacted_domains))
    options = (
        "；".join(
            f"{item.get('label', item.get('code', '选项'))}：{item.get('impact', '')}"
            for item in issue.options
        )
        or "暂无结构化选项"
    )
    recommendation = issue.ai_recommendation or "暂无"
    _section("AI 选项和建议", f"{options}\n\nAI 建议：{recommendation}")
    if issue.deterministic_rule_id is not None and issue.raw_severity is not None:
        _section(
            "确定性审计",
            f"确定性规则：{issue.deterministic_rule_id}\n"
            f"原始严重度：{issue.raw_severity.value}\n"
            f"当前严重度：{issue.severity.value}",
        )
    uncertainty = issue.uncertainty or "暂无"
    if issue.validation_note:
        uncertainty = f"{uncertainty}；校验说明：{issue.validation_note}"
    _section("不确定性", uncertainty)
    st.markdown("### 决策条")


def _section(title: str, body: str) -> None:
    st.markdown(f"### {escape(title)}")
    st.markdown(escape(body).replace("\n", "  \n"))


def _evidence(title: str, side_label: str, evidence: IssueEvidence | None) -> None:
    st.markdown(f"### {escape(title)}")
    if evidence is None:
        st.warning(f"{side_label}：缺少依据")
        return
    st.markdown(
        '<div class="pi-lint-evidence">'
        f"<strong>{escape(side_label)}</strong> · {escape(evidence.source_id)} · "
        f"{escape(evidence.document_version)} · {escape(evidence.page_or_section)}<br>"
        f"{escape(evidence.excerpt)}"
        "</div>",
        unsafe_allow_html=True,
    )
