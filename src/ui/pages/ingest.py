from __future__ import annotations

from datetime import date
from typing import Literal

import streamlit as st
from pydantic import BaseModel, ConfigDict

from src.application.container import AppContainer, ImportSourceService
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
        "sandbox": ("模拟材料", "violet"),
        "ai_inferred": ("AI 推定", "muted"),
        "human_confirmed": ("人工确认", "success"),
    }[value]


def build_feedback(error: AppError) -> UserFeedback:
    if error.code == ErrorCode.MODEL_TIMEOUT:
        return UserFeedback(
            title="实时分析超时",
            impact="资料已安全保存在本地，尚未写入知识库。",
            next_action="可继续等待，或使用同材料、同版本的冻结缓存。",
            error_code=error.code,
            offer_cache=True,
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


def render(container: AppContainer) -> None:
    st.title("资料导入")
    st.caption(f"为项目 {container.settings.project_id} 导入并编译新资料")
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
                value="LLD-724_1",
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
            command = _to_command(container, state, preferred_mode="realtime")
            st.session_state["ingest_command"] = command
            _execute_and_render(service, command)
        cached_command = st.session_state.get("ingest_timeout_command")
        if (
            cached_command is not None
            and service is not None
            and st.button(
                "使用缓存结果",
                key="ingest_use_cache",
            )
        ):
            _execute_and_render(
                service,
                cached_command.model_copy(update={"preferred_mode": "cache"}),
            )


def _to_command(
    container: AppContainer,
    state: IngestFormState,
    *,
    preferred_mode: Literal["realtime", "cache"],
) -> ImportSourceInput:
    return ImportSourceInput(
        project_id=container.settings.project_id,
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


def _execute_and_render(service: ImportSourceService, command: ImportSourceInput) -> None:
    try:
        report = service.execute(command)
    except AppError as error:
        feedback = build_feedback(error)
        render_feedback(feedback)
        if feedback.offer_cache:
            st.session_state["ingest_timeout_command"] = command
        return
    st.session_state.pop("ingest_timeout_command", None)
    _render_report(report, sandbox=command.is_sandbox)


def _render_report(report: IngestReport, *, sandbox: bool) -> None:
    badge, _ = result_badge(report.result_mode.value)
    if report.result_mode.value == "cache":
        st.warning(f"编译完成 · {badge}（同材料、同版本精确匹配）")
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
    if report.conflict_count:
        st.button("查看并处理问题", type="primary", key="ingest_review_issues")
    else:
        st.button("完成并返回首页", type="primary", key="ingest_finish")
