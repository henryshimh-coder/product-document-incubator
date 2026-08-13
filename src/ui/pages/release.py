from __future__ import annotations

from datetime import datetime
from html import escape
from uuid import uuid4

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.release import PublishBaselineInput, ReviewChangeRequestInput
from src.domain.enums import ChangeReviewAction, ChangeStatus
from src.domain.errors import AppError
from src.domain.models import ChangeRequest
from src.ui.components.change_diff import render_change_diff, render_diff_summary

_STATUS_LABELS = {
    ChangeStatus.APPROVED: "已批准·待发布",
    ChangeStatus.PENDING_APPROVAL: "待审批",
    ChangeStatus.NEEDS_INFO: "退回补充",
}

_SECONDARY_ACTIONS = (
    (ChangeReviewAction.REJECT, "驳回"),
    (ChangeReviewAction.DEFER, "暂缓"),
    (ChangeReviewAction.REQUEST_INFO, "退回补充"),
)

_CONFIRM_TEXT = (
    "即将发布新产品基线。发布后，新版本将成为后续查询和自检的默认基准，"
    "原版本转为历史版本。是否继续？"
)


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    st.title("变更发布")
    st.caption(f"检查并发布项目 {project_id} 的候选变更")
    _render_flash(container)
    guard = container.release_guard
    blocked = bool(guard is not None and guard.is_blocked)
    if blocked:
        st.error(
            "**发布已暂停：本地镜像与权威基线不一致**  \n"
            "查询仍可按当前版本只读运行，批准与发布操作已暂时禁用。  \n"
            f"错误码：`RELEASE_BLOCKED`（{escape(str(guard.reason))}）"
        )
        if st.button("重新校验", key="release_recheck_blocked", type="secondary"):
            _recheck(container)
    services = (
        container.release_candidates,
        container.review_change_request,
        container.publish_baseline,
    )
    if any(service is None for service in services):
        st.info("发布服务尚未就绪，请先完成本地基线初始化。")
        return
    try:
        candidates = container.release_candidates.list_release_candidates(project_id)
    except (KeyError, OSError, ValueError):
        candidates = []
        st.warning("候选变更列表暂时不可读取。")
    st.markdown('<div data-layout="38-62"></div>', unsafe_allow_html=True)
    left, right = st.columns([38, 62], gap="large")
    with left:
        selected_id = _render_candidate_list(candidates)
    with right:
        selected = next((item for item in candidates if item.id == selected_id), None)
        if selected is None:
            st.info("选择一个候选变更后检查修改前后、依据与影响。")
            return
        _render_detail(selected)
        if blocked:
            st.warning("镜像恢复完成前，批准与发布操作已禁用。")
            return
        if selected.status == ChangeStatus.NEEDS_INFO:
            st.info("该变更已退回补充，等待责任方更新后重新进入待审批，当前为只读。")
            return
        _render_actions(container, selected)
    if st.session_state.get("release_confirm"):
        _confirm_dialog(container)


def _render_candidate_list(candidates: list[ChangeRequest]) -> str | None:
    st.markdown("### 候选变更")
    if not candidates:
        st.info("当前没有待审批或待发布的候选变更。")
        return None
    options = [item.id for item in candidates]

    def label(change_id: str) -> str:
        item = next(change for change in candidates if change.id == change_id)
        created = item.created_at.strftime("%m-%d %H:%M")
        return (
            f"{_STATUS_LABELS[item.status]}｜{item.target_card_id}｜"
            f"目标 {item.target_version}｜{created}"
        )

    return st.radio(
        "候选变更",
        options=options,
        format_func=label,
        label_visibility="collapsed",
        key="release_candidate",
    )


def _render_detail(change: ChangeRequest) -> None:
    st.markdown("### 变更详情")
    _section("目标卡片", change.target_card_id)
    render_change_diff(before=change.before_content, after=change.after_content)
    _section("变更依据", f"{change.rationale}\n依据引用：{'、'.join(change.evidence_refs)}")
    _section("影响对象", "、".join(change.impacted_objects))
    role, confirmer = st.columns(2, gap="medium")
    with role:
        _section("正式应批准角色", change.required_approver_role)
    with confirmer:
        _section("当前演示确认人", change.demo_confirmer)
    version, status = st.columns(2, gap="medium")
    with version:
        _section("目标版本", change.target_version)
    with status:
        status_text = _STATUS_LABELS[change.status]
        if change.reviewed_by is not None and change.reviewed_at is not None:
            reviewed_at = change.reviewed_at.strftime("%m-%d %H:%M")
            status_text += f"\n{change.reviewed_by} · {reviewed_at}"
        _section("人工批准状态", status_text)


def _render_actions(container: AppContainer, change: ChangeRequest) -> None:
    st.markdown("### 发布操作")
    pending = change.status == ChangeStatus.PENDING_APPROVAL
    if pending:
        st.text_input(
            "复核确认人",
            value=change.demo_confirmer,
            key=f"release_reviewer_{change.id}",
        )
        st.text_area(
            "复核意见（10–200 字）",
            key=f"release_comment_{change.id}",
            placeholder="说明已检查修改前后、依据、影响对象和目标版本。",
        )
    else:
        st.info(
            f"该变更已由 {change.reviewed_by} 批准。上次发布未完成，"
            "重新校验后可直接重试发布，无需重复批准。"
        )
    st.checkbox("我已检查修改前后、依据和影响", key=f"release_checked_{change.id}")
    st.text_area(
        "发布说明（20–200 字）",
        key=f"release_note_{change.id}",
        placeholder="说明本次发布内容，将写入新版本 release.json。",
    )
    if pending:
        secondary = st.columns(3, gap="small")
        for column, (action, label) in zip(secondary, _SECONDARY_ACTIONS, strict=True):
            with column:
                if st.button(
                    label,
                    key=f"release_{action.value}_{change.id}",
                    type="secondary",
                    use_container_width=True,
                ):
                    _execute_secondary_review(container, change, action)
    primary_label = "批准并发布" if pending else "重新校验并发布"
    if st.button(
        primary_label,
        key=f"release_publish_{change.id}",
        type="primary",
        use_container_width=True,
    ):
        error = _validate_publish_form(change, pending)
        if error is not None:
            st.warning(error)
            return
        reviewer = (
            st.session_state[f"release_reviewer_{change.id}"].strip()
            if pending
            else (change.reviewed_by or "")
        )
        st.session_state["release_confirm"] = {
            "project_id": container.require_project_id(),
            "change_id": change.id,
            "pending": pending,
            "reviewer": reviewer,
            "comment": (
                st.session_state.get(f"release_comment_{change.id}", "").strip()
                if pending
                else None
            ),
            "note": st.session_state[f"release_note_{change.id}"].strip(),
            "review_key": f"REVIEW-{uuid4().hex.upper()}",
            "before": change.before_content,
            "after": change.after_content,
            "target_card_id": change.target_card_id,
        }
        st.rerun()


def _validate_publish_form(change: ChangeRequest, pending: bool) -> str | None:
    if not st.session_state.get(f"release_checked_{change.id}"):
        return "发布前必须勾选“我已检查修改前后、依据和影响”。"
    note = st.session_state.get(f"release_note_{change.id}", "").strip()
    if not 20 <= len(note) <= 200:
        return "发布说明须为 20–200 个字符。"
    if pending:
        if not st.session_state.get(f"release_reviewer_{change.id}", "").strip():
            return "复核确认人不能为空。"
        comment = st.session_state.get(f"release_comment_{change.id}", "").strip()
        if not 10 <= len(comment) <= 200:
            return "复核意见须为 10–200 个字符。"
    return None


@st.dialog("人工确认")
def _confirm_dialog(container: AppContainer) -> None:
    payload = st.session_state.get("release_confirm")
    if payload is None:
        return
    st.markdown(_CONFIRM_TEXT)
    st.caption(f"变更单：{payload['change_id']}｜确认人：{payload['reviewer']}")
    confirm = st.button("确认发布新基线", type="primary", key="release_confirm_yes")
    cancel = st.button("取消", type="secondary", key="release_confirm_cancel")
    if cancel:
        st.session_state.pop("release_confirm", None)
        st.rerun()
    if confirm:
        _execute_publish(container, payload)


def _execute_publish(container: AppContainer, payload: dict) -> None:
    project_id = str(payload["project_id"])
    change_id = payload["change_id"]
    try:
        if payload["pending"]:
            container.review_change_request.execute(
                ReviewChangeRequestInput(
                    change_request_id=change_id,
                    action=ChangeReviewAction.APPROVE,
                    reviewed_by=payload["reviewer"],
                    comment=payload["comment"],
                    idempotency_key=payload["review_key"],
                )
            )
        baseline = container.publish_baseline.execute(
            PublishBaselineInput(
                project_id=project_id,
                change_request_id=change_id,
                approved_by=payload["reviewer"],
                impact_reviewed=True,
                release_note=payload["note"],
            )
        )
    except AppError as error:
        step = "人工复核" if payload["pending"] else "原子发布"
        if payload["pending"] and error.code != "CHANGE_NOT_REVIEWABLE":
            detail = container.release_candidates
            try:
                current = next(
                    item
                    for item in detail.list_release_candidates(project_id)
                    if item.id == change_id
                )
                if current.status == ChangeStatus.APPROVED:
                    step = "原子发布"
            except (StopIteration, KeyError, OSError, ValueError):
                pass
        st.session_state["release_flash"] = {
            "kind": "failure",
            "step": step,
            "code": error.code,
            "message": error.user_message,
        }
    except (KeyError, OSError, ValueError):
        st.session_state["release_flash"] = {
            "kind": "failure",
            "step": "原子发布",
            "code": "RELEASE_FAILED",
            "message": "发布未完成，原产品版本仍然生效",
        }
    else:
        st.session_state["release_flash"] = {
            "kind": "success",
            "version": baseline.version,
            "parent_version": (
                None
                if baseline.parent_baseline_id is None
                else baseline.parent_baseline_id.replace("BASE-", "", 1)
            ),
            "baseline_id": baseline.id,
            "approved_by": baseline.approved_by,
            "published_at": baseline.effective_at,
            "change_id": change_id,
            "target_card_id": str(payload.get("target_card_id") or ""),
            "before": payload.get("before", ""),
            "after": payload.get("after", ""),
        }
    st.session_state.pop("release_confirm", None)
    st.rerun()


def _execute_secondary_review(
    container: AppContainer,
    change: ChangeRequest,
    action: ChangeReviewAction,
) -> None:
    comment = st.session_state.get(f"release_comment_{change.id}", "").strip()
    if not 10 <= len(comment) <= 200:
        st.warning("复核意见须为 10–200 个字符。")
        return
    reviewer = st.session_state.get(f"release_reviewer_{change.id}", "").strip()
    if not reviewer:
        st.warning("复核确认人不能为空。")
        return
    key = st.session_state.setdefault(
        f"release_review_key_{change.id}_{action.value}",
        f"REVIEW-{uuid4().hex.upper()}",
    )
    try:
        container.review_change_request.execute(
            ReviewChangeRequestInput(
                change_request_id=change.id,
                action=action,
                reviewed_by=reviewer,
                comment=comment,
                idempotency_key=key,
            )
        )
    except AppError as error:
        st.error(f"{error.user_message}  \n错误码：`{error.code}`")
        return
    st.session_state.pop(f"release_review_key_{change.id}_{action.value}", None)
    st.session_state["release_flash"] = {
        "kind": "reviewed",
        "action": dict(_SECONDARY_ACTIONS)[action],
        "change_id": change.id,
    }
    st.rerun()


def _render_flash(container: AppContainer) -> None:
    flash = st.session_state.get("release_flash")
    if flash is None:
        return
    if flash["kind"] == "reviewed":
        st.success(f"变更 {flash['change_id']} 已{flash['action']}，状态已更新。")
        st.session_state.pop("release_flash", None)
        return
    if flash["kind"] == "success":
        st.success("新基线已发布并生效。")
        published_at = flash["published_at"]
        if isinstance(published_at, datetime):
            published_text = published_at.strftime("%Y-%m-%d %H:%M")
        else:
            published_text = str(published_at)
        st.markdown(
            '<div style="border:1px solid #B7E0C8;background:#F2FBF6;border-radius:8px;'
            'padding:12px 14px;line-height:1.9;">'
            f"<strong>新版本：</strong>{escape(flash['version'])}<br>"
            f"<strong>父版本：</strong>{escape(str(flash['parent_version'] or '无'))}<br>"
            f"<strong>发布人：</strong>{escape(flash['approved_by'])}<br>"
            f"<strong>发布时间：</strong>{escape(published_text)}<br>"
            f"<strong>变更单：</strong>{escape(flash['change_id'])}"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("**差异摘要**")
        render_diff_summary(before=flash.get("before", ""), after=flash.get("after", ""))
        first, second = st.columns(2, gap="small")
        with first:
            if st.button("查看新基线", key="release_go_home", type="secondary"):
                st.session_state.pop("release_flash", None)
                _switch_to("_pi_home_page")
        with second:
            if st.button("查看完整追溯", key="release_go_trace", type="secondary"):
                target_card_id = str(flash.get("target_card_id") or "").strip()
                st.session_state.pop("release_flash", None)
                if target_card_id:
                    st.session_state["trace_target_card_id"] = target_card_id
                _switch_to("_pi_trace_page")
        if st.button("继续处理候选变更", key="release_flash_dismiss", type="tertiary"):
            st.session_state.pop("release_flash", None)
            st.rerun()
        return
    st.error(
        "**发布未完成**  \n"
        f"失败步骤：{escape(flash['step'])}  \n"
        f"{escape(flash['message'])}  \n"
        f"错误码：`{escape(flash['code'])}`"
    )
    if flash["code"] == "RELEASE_MIRROR_REPAIR_REQUIRED":
        st.warning("新版本已生效，但本地镜像待修复。修复完成前发布操作保持禁用。")
    else:
        st.info("原版本仍然生效。变更保持已批准状态，重新校验后可重试发布，不会重复批准。")
    if st.button("重新校验", key="release_recheck_failure", type="secondary"):
        _recheck(container)
    if st.button("知道了", key="release_failure_dismiss", type="tertiary"):
        st.session_state.pop("release_flash", None)
        st.rerun()


def _recheck(container: AppContainer) -> None:
    if container.reconciliation is None:
        st.error("恢复服务尚未就绪。")
        return
    validation = container.reconciliation.validate_manifest_mirror()
    if validation.success:
        if container.release_guard is not None:
            container.release_guard.clear()
        st.session_state.pop("release_flash", None)
        st.success("镜像与权威基线一致，发布已恢复。")
        st.rerun()
        return
    repair = container.reconciliation.rebuild_current_from_manifest()
    if repair.success:
        if container.release_guard is not None:
            container.release_guard.clear()
        st.session_state.pop("release_flash", None)
        st.success("已按权威基线修复本地镜像，发布已恢复。")
        st.rerun()
        return
    if container.release_guard is not None:
        container.release_guard.block(repair.error_code or "manifest_sqlite_mismatch")
    st.error("镜像修复未完成，发布保持禁用。  \n错误码：`RELEASE_BLOCKED`")
    st.rerun()


def _switch_to(session_key: str) -> None:
    page = st.session_state.get(session_key)
    if page is None:
        raise RuntimeError(f"page is not registered: {session_key}")
    st.switch_page(page)


def _section(title: str, body: str) -> None:
    st.markdown(f"### {escape(title)}")
    st.markdown(escape(body).replace("\n", "  \n"))
