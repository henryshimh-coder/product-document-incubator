from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

import streamlit as st

from src.application.dto.decision import CreateChangeRequestInput, RecordDecisionInput
from src.domain.enums import DecisionAction
from src.domain.models import IssueCard

_ACTION_LABELS = {
    DecisionAction.ACCEPT_CHANGE: "接受迭代",
    DecisionAction.KEEP_CURRENT: "维持现状",
    DecisionAction.DEFER: "暂缓讨论",
    DecisionAction.FALSE_POSITIVE: "判定误报",
}


def render_decision_bar(issue: IssueCard) -> RecordDecisionInput | None:
    action = st.radio(
        "会议操作",
        options=list(DecisionAction),
        format_func=_ACTION_LABELS.__getitem__,
        horizontal=True,
        key="decision_action",
        label_visibility="collapsed",
    )
    idempotency_state_key = f"decision_idempotency_{issue.id}"
    if idempotency_state_key not in st.session_state:
        st.session_state[idempotency_state_key] = f"DECISION-CLICK-{uuid4().hex.upper()}"

    with st.form(f"decision_form_{issue.id}"):
        confirmed_by = st.text_input("会议确认人 *", key="decision_confirmed_by")
        conclusion_label = {
            DecisionAction.ACCEPT_CHANGE: "会议结论 *",
            DecisionAction.KEEP_CURRENT: "维持依据 *",
            DecisionAction.DEFER: "暂缓说明 *",
            DecisionAction.FALSE_POSITIVE: "误报理由 *",
        }[action]
        conclusion = st.text_area(conclusion_label, key="decision_conclusion")
        responsible_party = None
        verification_condition = None
        due_at = None
        change_request = None
        if action == DecisionAction.ACCEPT_CHANGE:
            responsible_party = st.text_input("责任方 *", key="decision_responsible_party")
            verification_condition = st.text_area(
                "验证条件 *", key="decision_verification_condition"
            )
            st.caption("以下字段将明确生成待审批变更单，不会自动批准或发布。")
            target_card_id = st.text_input("影响卡片 ID *", key="change_target_card_id")
            before_content = st.text_area("修改前 *", key="change_before")
            after_content = st.text_area("修改后 *", key="change_after")
            rationale = st.text_area("变更依据 *", key="change_rationale")
            evidence_refs = st.text_input("依据引用 ID（逗号分隔） *", key="change_evidence_refs")
            impacted_objects = st.text_input(
                "影响对象（逗号分隔） *", key="change_impacted_objects"
            )
            responsible_domain = st.text_input("责任专业 *", key="change_domain")
            required_approver_role = st.text_input("应批准角色 *", key="change_approver_role")
            demo_confirmer = st.text_input("演示确认人 *", key="change_demo_confirmer")
            target_version = st.text_input("目标版本 *", key="change_target_version")
            effective_condition = st.text_area("生效条件 *", key="change_effective_condition")
            change_request = CreateChangeRequestInput(
                target_card_id=target_card_id,
                before_content=before_content,
                after_content=after_content,
                rationale=rationale,
                evidence_refs=_split(evidence_refs),
                impacted_objects=_split(impacted_objects),
                responsible_domain=responsible_domain,
                required_approver_role=required_approver_role,
                demo_confirmer=demo_confirmer,
                target_version=target_version,
                effective_condition=effective_condition,
            )
        elif action == DecisionAction.DEFER:
            due_date = st.date_input("下次处理时间 *", key="decision_due_date")
            due_at = datetime.combine(due_date, time.min, tzinfo=UTC)
        submitted = st.form_submit_button(
            "记录会议结论",
            key="decision_submit",
        )
    if not submitted:
        return None
    return RecordDecisionInput(
        issue_id=issue.id,
        action=action,
        conclusion=conclusion,
        confirmed_by=confirmed_by,
        responsible_party=responsible_party,
        due_at=due_at,
        verification_condition=verification_condition,
        idempotency_key=st.session_state[idempotency_state_key],
        change_request=change_request,
    )


def clear_decision_idempotency(issue_id: str) -> None:
    st.session_state.pop(f"decision_idempotency_{issue_id}", None)


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
