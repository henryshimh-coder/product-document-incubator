from __future__ import annotations

from datetime import date
from typing import Any, Literal

import streamlit as st
from pydantic import BaseModel, ConfigDict

from src.application.container import AppContainer, ImportSourceService
from src.application.dto.dashboard import GetDashboardInput
from src.application.dto.ingest import ImportSourceInput
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import AppError, ErrorCode
from src.domain.models import IngestReport
from src.ui.components.feedback import UserFeedback, render_feedback
from src.ui.components.file_upload import render_single_file_upload


class IngestFormState(BaseModel):
    model_config = ConfigDict(frozen=True)

    uploaded_name: str | None = None
    uploaded_bytes: bytes | None = None
    source_type: str | None = None
    authority_level: str | None = None
    source_department: str | None = None
    provider: str = ""
    document_date: date | None = None
    document_version: str | None = None
    applicable_baseline_version: str | None = None
    security_level: SecurityLevel = SecurityLevel.L2_INTERNAL
    is_redacted_confirmed: bool = False
    allow_external_model: bool = False
    is_sandbox: bool = False


class IngestStageState(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    status: Literal["waiting", "processing", "completed", "failed"]


STAGE_LABELS = (
    "本地保存",
    "文本提取",
    "脱敏和授权检查",
    "AI 分析",
    "结构校验",
    "写入知识库",
)


def step_labels() -> tuple[str, str, str]:
    return (
        "1 上传文件",
        "2 确认资料属性和安全边界",
        "3 编译并查看结果",
    )


def can_submit(state: IngestFormState) -> bool:
    return all(
        (
            state.uploaded_name,
            state.uploaded_bytes,
            state.source_type,
            state.authority_level,
            state.source_department,
            state.document_date,
            state.document_version,
            state.applicable_baseline_version,
            state.is_redacted_confirmed,
        )
    )


def effective_external_permission(state: IngestFormState) -> bool:
    return all(
        (
            state.allow_external_model,
            state.is_redacted_confirmed,
            state.security_level
            in {
                SecurityLevel.L1_PUBLIC_SIMULATED,
                SecurityLevel.L2_INTERNAL,
            },
        )
    )


def result_badge(
    value: str,
) -> tuple[str, Literal["primary", "warning", "violet", "muted", "success"]]:
    return {
        "realtime": ("实时分析", "primary"),
        "cache": ("冻结缓存", "warning"),
        "local_only": ("本地检查", "muted"),
        "sandbox": ("模拟材料", "violet"),
        "ai_inferred": ("AI 推定", "muted"),
        "human_confirmed": ("人工确认", "success"),
    }[value]


def stage_states(
    state: Literal["waiting", "processing", "completed", "failed"],
    *,
    failed_index: int = 0,
) -> list[IngestStageState]:
    if state == "completed":
        statuses = ["completed"] * len(STAGE_LABELS)
    elif state == "processing":
        statuses = ["processing", *(["waiting"] * (len(STAGE_LABELS) - 1))]
    elif state == "failed":
        statuses = [
            (
                "completed"
                if index < failed_index
                else "failed"
                if index == failed_index
                else "waiting"
            )
            for index in range(len(STAGE_LABELS))
        ]
    else:
        statuses = ["waiting"] * len(STAGE_LABELS)
    return [
        IngestStageState(label=label, status=status)
        for label, status in zip(STAGE_LABELS, statuses, strict=True)
    ]


def failure_stage_index(error_code: ErrorCode | str) -> int:
    normalized = ErrorCode(error_code)
    if normalized in {
        ErrorCode.FILE_TYPE_NOT_ALLOWED,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.DUPLICATE_SOURCE,
    }:
        return 0
    if normalized == ErrorCode.EXTRACTION_FAILED:
        return 1
    if normalized in {
        ErrorCode.REDACTION_REQUIRED,
        ErrorCode.EXTERNAL_CALL_DENIED,
        ErrorCode.OUTBOUND_COVERAGE_EXCEEDED,
        ErrorCode.SANDBOX_SOURCE_NOT_ALLOWED,
        ErrorCode.SOURCE_AUTHORITY_NOT_FORMAL,
    }:
        return 2
    if normalized in {ErrorCode.MODEL_TIMEOUT, ErrorCode.CACHE_NOT_FOUND}:
        return 3
    if normalized == ErrorCode.INGEST_PERSISTENCE_FAILED:
        return 5
    return 4


def build_feedback(error: AppError) -> UserFeedback:
    if error.code == ErrorCode.MODEL_TIMEOUT:
        return UserFeedback(
            title="实时分析超时",
            impact="资料已安全保存在本地，尚未写入知识库。",
            next_action="可继续等待，或使用同材料、同版本的冻结缓存。",
            error_code=error.code,
            offer_cache=True,
            offer_local=True,
            level="warning",
        )
    if error.code == ErrorCode.OUTBOUND_COVERAGE_EXCEEDED:
        return UserFeedback(
            title=error.user_message,
            impact="未发生外部模型调用，资料仅保存在本地。",
            next_action="当前材料超过 25% 覆盖率预算；可尝试精确缓存或本地确定性检查。",
            error_code=error.code,
            offer_cache=True,
            offer_local=True,
            level="warning",
        )
    next_action = (
        "请检查安全确认和资料属性后重试。"
        if error.code in {ErrorCode.REDACTION_REQUIRED, ErrorCode.EXTERNAL_CALL_DENIED}
        else "请按提示调整后重试；当前基线不受影响。"
    )
    return UserFeedback(
        title=error.user_message,
        impact="本次编译未完成，未显示为已写入。",
        next_action=next_action,
        error_code=error.code,
    )


def _default_baseline_version(container: AppContainer) -> str:
    """适用产品版本默认取 Manifest 当前基线，不再硬编码历史版本。"""
    if container.dashboard is None:
        return ""
    try:
        view = container.dashboard.execute(
            GetDashboardInput(project_id=container.require_project_id())
        )
    except (KeyError, OSError, ValueError):
        return ""
    return "" if view.current_baseline is None else view.current_baseline.version


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    st.title("资料导入")
    st.caption(f"为项目 {project_id} 导入并编译新资料")
    st.markdown(
        """
        <div style="display:flex;gap:12px;margin:12px 0 20px">
          <div style="flex:1;padding:10px 14px;border:1px solid #1769E0;border-radius:8px;
                      color:#1256B8;background:#EEF5FF">1 上传文件</div>
          <div style="flex:1;padding:10px 14px;border:1px solid #D9E2EC;border-radius:8px">
                      2 确认资料属性和安全边界</div>
          <div style="flex:1;padding:10px 14px;border:1px solid #D9E2EC;border-radius:8px">
                      3 编译并查看结果</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.subheader("1 上传文件")
        uploaded = render_single_file_upload()

    with st.container(border=True):
        st.subheader("2 确认资料属性和安全边界")
        st.caption("系统预填内容标记为“AI 推定”；完成确认后标记为“人工确认”。")
        left, right = st.columns(2)
        with left:
            source_type = st.selectbox(
                "材料类型 *",
                options=[None, "risk_opinion", "meeting_minutes", "product_document"],
                format_func=lambda value: {
                    None: "请选择",
                    "risk_opinion": "风险专业意见",
                    "meeting_minutes": "会议纪要",
                    "product_document": "产品文档",
                }[value],
                key="ingest_source_type",
            )
            authority = st.selectbox(
                "权威等级 *",
                options=[None, *list(AuthorityLevel)],
                format_func=lambda value: "请选择" if value is None else value.value,
                key="ingest_authority",
            )
            department = st.text_input("来源部门 *", key="ingest_department")
            provider = st.text_input("提供人", key="ingest_provider")
            document_date = st.date_input("文件日期 *", key="ingest_date")
        with right:
            document_version = st.text_input("文件版本 *", key="ingest_version")
            baseline_version = st.text_input(
                "适用产品版本 *",
                value=_default_baseline_version(container),
                key="ingest_baseline",
            )
            security_level = st.radio(
                "资料安全等级 *",
                options=list(SecurityLevel),
                format_func=lambda value: value.value,
                horizontal=True,
                key="ingest_security",
            )
            redacted = st.checkbox("我确认资料已完成脱敏 *", key="ingest_redacted")
            external_disabled = not redacted or security_level in {
                SecurityLevel.L3_CONFIDENTIAL,
                SecurityLevel.L4_RESTRICTED,
            }
            if external_disabled:
                st.session_state["ingest_external"] = False
            requested_external = st.toggle(
                "允许外部模型调用",
                disabled=external_disabled,
                key="ingest_external",
            )
            sandbox = st.toggle("测试沙盘材料", key="ingest_sandbox")
        if sandbox:
            st.markdown(":violet-badge[模拟材料]")
        st.caption(":gray-badge[AI 推定] → :green-badge[人工确认]")

    payload = None if uploaded is None else uploaded.getvalue()
    state = IngestFormState(
        uploaded_name=None if uploaded is None else uploaded.name,
        uploaded_bytes=payload,
        source_type=source_type,
        authority_level=None if authority is None else authority.value,
        source_department=department,
        provider=provider,
        document_date=document_date,
        document_version=document_version,
        applicable_baseline_version=baseline_version,
        security_level=security_level,
        is_redacted_confirmed=redacted,
        allow_external_model=requested_external,
        is_sandbox=sandbox,
    )

    with st.container(border=True):
        st.subheader("3 编译并查看结果")
        st.caption("本地保存 → 文本提取 → 脱敏和授权检查 → AI 分析 → 结构校验 → 写入知识库")
        realtime_allowed = effective_external_permission(state)
        mode_options = ["realtime", "cache", "local"] if realtime_allowed else ["cache", "local"]
        preferred_mode = st.radio(
            "处理方式",
            options=mode_options,
            format_func=lambda value: {
                "realtime": "实时模型",
                "cache": "精确缓存",
                "local": "本地确定性检查",
            }[value],
            horizontal=True,
            key="ingest_mode",
        )
        stage_target = st.empty()
        _render_stages(stage_states("waiting"), target=stage_target)
        service = container.import_source
        if service is None:
            st.info("导入服务尚未配置；页面可用于确认三步流程与安全边界。")
        submitted = st.button(
            "开始编译",
            type="primary",
            disabled=not can_submit(state) or service is None,
            key="ingest_submit",
        )
        if submitted and service is not None:
            command = _to_command(container, state, preferred_mode=preferred_mode)
            st.session_state["ingest_command"] = command
            _execute_and_render(service, command, stage_target=stage_target)
        cached_command = st.session_state.get("ingest_timeout_command")
        if cached_command is not None and service is not None:
            action_columns = st.columns(4)
            if action_columns[0].button("继续等待", key="ingest_continue_wait"):
                _execute_and_render(
                    service,
                    cached_command.model_copy(update={"preferred_mode": "realtime"}),
                    stage_target=stage_target,
                )
            if action_columns[1].button("使用缓存结果", key="ingest_use_cache"):
                _execute_and_render(
                    service,
                    cached_command.model_copy(update={"preferred_mode": "cache"}),
                    stage_target=stage_target,
                )
            if action_columns[2].button("本地确定性检查", key="ingest_use_local"):
                _execute_and_render(
                    service,
                    cached_command.model_copy(update={"preferred_mode": "local"}),
                    stage_target=stage_target,
                )
            if action_columns[3].button("取消本次处理", key="ingest_cancel"):
                st.session_state.pop("ingest_timeout_command", None)
                st.info("已取消本次处理；本地归档保留，未写入知识库。")


def _to_command(
    container: AppContainer,
    state: IngestFormState,
    *,
    preferred_mode: Literal["realtime", "cache", "local"],
) -> ImportSourceInput:
    return ImportSourceInput(
        project_id=container.require_project_id(),
        uploaded_name=state.uploaded_name,
        uploaded_bytes=state.uploaded_bytes,
        source_type=state.source_type,
        authority_level=state.authority_level,
        source_department=state.source_department,
        provider=state.provider or None,
        document_date=state.document_date,
        document_version=state.document_version,
        applicable_baseline_version=state.applicable_baseline_version,
        security_level=state.security_level,
        is_redacted_confirmed=state.is_redacted_confirmed,
        allow_external_model=effective_external_permission(state),
        is_sandbox=state.is_sandbox,
        preferred_mode=preferred_mode,
    )


def _execute_and_render(
    service: ImportSourceService,
    command: ImportSourceInput,
    *,
    stage_target: Any | None = None,
) -> None:
    _render_stages(stage_states("processing"), target=stage_target)
    try:
        report = service.execute(command)
    except AppError as error:
        feedback = build_feedback(error)
        _render_stages(
            stage_states("failed", failed_index=failure_stage_index(error.code)),
            target=stage_target,
        )
        render_feedback(feedback)
        if feedback.offer_cache or feedback.offer_local:
            st.session_state["ingest_timeout_command"] = command
        return
    st.session_state.pop("ingest_timeout_command", None)
    _render_stages(stage_states("completed"), target=stage_target)
    _render_report(report, sandbox=command.is_sandbox)


def _render_report(report: IngestReport, *, sandbox: bool) -> None:
    badge, _ = result_badge(report.result_mode.value)
    if report.audit_reconciliation_pending:
        st.warning("导入已完成；本地审计 JSONL 待下次启动自动对账。")
    if report.result_mode.value == "cache":
        generated = (
            report.cache_generated_at.isoformat(timespec="seconds")
            if report.cache_generated_at is not None
            else "时间未知"
        )
        st.warning(
            f"编译完成 · {badge} · 材料 {report.source_hash8 or '未知'} · 生成于 {generated}"
        )
    else:
        st.info(f"编译完成 · {badge}")
    if sandbox:
        st.markdown(":violet-badge[模拟材料]")
    knowledge, candidate, conflict, gap = st.columns(4)
    knowledge.metric("新增知识", len(report.created_card_ids))
    candidate.metric("候选变更", report.candidate_count)
    conflict.metric("冲突问题", report.conflict_count)
    gap.metric("信息缺口", max(0, len(report.created_issue_ids) - report.conflict_count))
    st.write(report.summary)
    grouped: dict[str, list] = {}
    for item in report.result_items:
        grouped.setdefault(item.status, []).append(item)
    for status, items in grouped.items():
        with st.expander(f"{status}（{len(items)}）", expanded=True):
            for item in items:
                st.markdown(
                    f"**类型：** {item.item_type}  \n"
                    f"**摘要：** {item.summary}  \n"
                    f"**章节：** {item.section}  \n"
                    f"**引用：** {item.citation}  \n"
                    f"**状态：** {item.status}"
                )
    if report.conflict_count:
        st.button("查看并处理问题", type="primary", key="ingest_review_issues")
    else:
        st.button("完成并返回首页", type="primary", key="ingest_finish")


def _render_stages(states: list[IngestStageState], *, target: Any | None = None) -> None:
    icons = {
        "waiting": "○",
        "processing": "◉",
        "completed": "✓",
        "failed": "!",
    }
    renderer = st if target is None else target
    renderer.markdown(
        "　".join(f"{icons[item.status]} {item.label} · {item.status}" for item in states)
    )
