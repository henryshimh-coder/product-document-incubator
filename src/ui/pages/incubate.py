from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.documents import IncubateDocumentInput
from src.domain.errors import AppError
from src.ui.components.change_diff import render_change_diff


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    service = container.incubate_document
    st.title("文档孵化")
    st.caption("基于已归档材料生成候选产品文档；候选不会直接覆盖当前生效方案。")
    if service is None:
        st.info("文档工作流尚未配置。请设置 DIFY_BASE_URL 与 DIFY_DOCUMENT_API_KEY 后启用。")
        return
    sources = service.list_sources(project_id)
    if not sources:
        st.warning("请先在“原始材料”归档至少一份允许外部模型调用且已脱敏的材料。")
        return
    labels = {item["id"]: item["label"] for item in sources}
    selected = st.multiselect(
        "选择用于本次孵化的材料",
        options=list(labels),
        format_func=labels.__getitem__,
        key="incubate_source_ids",
    )
    if st.button(
        "生成候选产品文档", type="primary", disabled=not selected, key="incubate_generate"
    ):
        try:
            result = service.execute(
                IncubateDocumentInput(
                    project_id=project_id,
                    source_ids=selected,
                    requested_by="Owner",
                )
            )
        except (AppError, OSError, ValueError, KeyError) as error:
            st.error(f"候选文档生成失败：{error}")
        else:
            st.session_state["incubate_selected_draft"] = result.draft.id
            st.success(f"已生成候选版本 {result.draft.version_id}。")
    _render_drafts(container)


def _render_drafts(container: AppContainer) -> None:
    service = container.incubate_document
    assert service is not None
    project_id = container.require_project_id()
    drafts = service.list_drafts(project_id)
    if not drafts:
        st.caption("尚未生成候选产品文档。")
        return
    selected_id = st.selectbox(
        "候选版本",
        options=[item.id for item in drafts],
        format_func=lambda draft_id: next(
            f"{item.version_id} · {item.status.value}" for item in drafts if item.id == draft_id
        ),
        key="incubate_selected_draft",
    )
    draft = next(item for item in drafts if item.id == selected_id)
    markdown_path = container.active_project.paths.project_root / Path(draft.markdown_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    st.subheader("候选内容")
    _render_context(draft.source_ids, draft.missing_sections, draft.evidence_gaps)
    current = container.active_project.paths.wiki_root / "current" / "当前产品方案.md"
    if draft.parent_version_id is not None and current.is_file():
        render_change_diff(
            before=current.read_text(encoding="utf-8"),
            after=markdown,
            before_label="当前生效方案",
            after_label="候选方案",
        )
    else:
        st.markdown(markdown)
    edited = st.text_area(
        "编辑候选 Markdown", value=markdown, height=360, key=f"draft_edit_{draft.id}"
    )
    if st.button("保存候选并提交 Owner 审核", key=f"draft_save_{draft.id}"):
        try:
            updated = service.save_draft(project_id, draft.id, edited)
        except (OSError, ValueError, KeyError) as error:
            st.error(f"候选保存失败：{error}")
        else:
            st.success(f"候选已保存，状态：{updated.status.value}。发布将在下一阶段提供。")


def _render_context(source_ids: list[str], missing: list[str], gaps: list[str]) -> None:
    st.caption(f"材料：{'、'.join(source_ids)}")
    if missing:
        st.warning(f"待补充章节：{'、'.join(missing)}")
    if gaps:
        st.info(f"证据缺口：{'、'.join(gaps)}")
