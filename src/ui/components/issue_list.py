from __future__ import annotations

import streamlit as st

from src.domain.models import IssueCard

_SEVERITY_LABELS = {
    "blocking": "阻断",
    "pending_decision": "待决策",
    "pending_info": "待补充",
}


def render_issue_list(issues: list[IssueCard]) -> str | None:
    if not issues:
        st.info("当前范围内没有开放问题。")
        return None
    issue_by_id = {issue.id: issue for issue in issues}

    def label(issue_id: str) -> str:
        issue = issue_by_id[issue_id]
        domains = " / ".join(issue.impacted_domains)
        return (
            f"{issue.id} · {_SEVERITY_LABELS[issue.severity.value]}\n"
            f"{issue.title} · {issue.status.value} · {domains} · "
            f"{issue.updated_at:%m-%d %H:%M}"
        )

    return st.radio(
        "问题列表",
        options=list(issue_by_id),
        format_func=label,
        key="lint_issue_id",
        label_visibility="collapsed",
    )
