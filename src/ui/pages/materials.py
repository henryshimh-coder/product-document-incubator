from __future__ import annotations

from datetime import date

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.materials import (
    ArchiveRawSourceInput,
    CreateLocalDraftInput,
    ReclassifySourceInput,
    SensitiveComparisonInput,
)
from src.application.dto.wiki_ingest import (
    ConfirmLocalWikiIngestInput,
    IngestArchivedSourceInput,
    PrepareLocalWikiIngestInput,
)
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import AppError
from src.domain.material_catalog import MATERIAL_TYPES, NEW_AUTHORITY_LEVELS


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    service = container.archive_raw_source
    st.title("原始材料")
    st.caption(f"项目 {project_id} 的不可变本地归档；确认前不会写入项目文件库。")
    if service is None or container.active_project is None:
        st.info("材料归档服务尚未就绪，请先进入一个产品项目。")
        return
    with st.form("materials_archive_form"):
        uploaded = st.file_uploader(
            "选择单份原始材料",
            type=["md", "txt", "pdf", "docx"],
            key="materials_upload",
        )
        material_name = st.text_input("材料名称", key="materials_name")
        archive_mode = st.selectbox(
            "归档方式",
            options=(None, "new_material", "new_version"),
            format_func=lambda item: {
                None: "请选择",
                "new_material": "新材料",
                "new_version": "新版本",
            }[item],
            key="materials_archive_mode",
        )
        target_series_id = None
        if archive_mode == "new_version":
            target_series_id = st.selectbox(
                "要关联的材料系列",
                options=(None, *_series_options(container)),
                format_func=lambda item: "请选择" if item is None else item,
                key="materials_target_series",
            )
        source_type = st.selectbox(
            "材料类型",
            options=(None, *MATERIAL_TYPES),
            format_func=lambda item: "请选择" if item is None else item.label,
            key="materials_type",
        )
        source_department = st.text_input("来源部门", value="产品部", key="materials_department")
        document_version = st.text_input("显示版本", key="materials_version")
        document_date = st.date_input("文档日期", value=date.today(), key="materials_date")
        authority = st.selectbox(
            "权威级别",
            options=(None, *NEW_AUTHORITY_LEVELS),
            format_func=lambda item: (
                "请选择"
                if item is None
                else ("正式基线依据" if item == AuthorityLevel.FORMAL_EFFECTIVE else "参考材料")
            ),
            key="materials_authority",
        )
        security = st.selectbox(
            "安全等级",
            options=tuple(SecurityLevel),
            format_func=lambda item: item.value,
            key="materials_security",
        )
        redacted = st.checkbox("已确认脱敏", value=True, key="materials_redacted")
        external = st.checkbox(
            "允许外部模型调用",
            value=False,
            disabled=security in (SecurityLevel.L3_CONFIDENTIAL, SecurityLevel.L4_RESTRICTED),
            key="materials_external",
        )
        submitted = st.form_submit_button("确认归档", type="primary", key="materials_archive")
    if submitted:
        try:
            if uploaded is None or source_type is None or authority is None or archive_mode is None:
                raise ValueError("请完成文件、归档方式、材料类型和权威级别的选择")
            result = service.execute(
                ArchiveRawSourceInput(
                    project_id=project_id,
                    uploaded_name=uploaded.name,
                    uploaded_bytes=uploaded.getvalue(),
                    material_name=material_name or uploaded.name.rsplit(".", 1)[0],
                    archive_mode=archive_mode,
                    target_series_id=target_series_id,
                    source_type=source_type.code,
                    authority_level=authority,
                    source_department=source_department,
                    document_date=document_date,
                    material_version=document_version,
                    security_level=security,
                    is_redacted_confirmed=redacted,
                    allow_external_model=external,
                )
            )
        except (OSError, ValueError, RuntimeError) as error:
            st.error(f"材料归档失败：{error}")
        else:
            if result.duplicate:
                st.info("相同材料已存在，未创建重复归档。")
            else:
                st.success("材料已归档到当前项目。")
            st.code(str(result.archive_path), language=None)
            st.caption(f"来源 ID：{result.source_id} · SHA-256：{result.sha256[:12]}")
    _render_index(container)


def _render_index(container: AppContainer) -> None:
    assert container.active_project is not None
    index_path = container.active_project.paths.system_root / "source-index.json"
    if not index_path.is_file():
        st.caption("当前还没有已归档材料。")
        return
    import json

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    sources = payload.get("sources", []) if isinstance(payload, dict) else []
    if not sources:
        st.caption("当前还没有已归档材料。")
        return
    st.subheader("已归档材料")
    for item in sources:
        if not isinstance(item, dict):
            continue
        st.markdown(
            f"**{item.get('material_name') or item.get('filename', '未知文件')}** · "
            f"{item.get('material_version', '--')} · {item.get('source_id', '--')}  \\n"
            f"`{str(item.get('sha256', ''))[:12]}` · {item.get('source_type', '--')} · "
            f"{item.get('ingest_status', '--')}"
        )
        st.code(str(item.get("archive_path", "")), language=None)
        _render_wiki_ingest(container, item)
        if item.get("local_sensitive_comparison_required"):
            count = item.get("excluded_sensitive_topic_count", 0)
            st.warning(f"有 {count} 个相关主题仅可在本地核验，未外发给模型。")
        if item.get("security_level") in {"L3", "L4"} and container.compare_sensitive_source:
            if st.button("与当前方案对照", key=f"compare-{item.get('source_id')}"):
                try:
                    comparison = container.compare_sensitive_source.execute(
                        SensitiveComparisonInput(
                            project_id=container.active_project.project_id,
                            source_id=str(item["source_id"]),
                        )
                    )
                except ValueError as error:
                    st.error(f"本地对照失败：{error}")
                else:
                    st.info("仅本地处理，未调用外部模型。")
                    left, right = st.columns(2)
                    left.text_area(comparison.left_label, comparison.left_markdown, height=320)
                    right.text_area("敏感材料", comparison.sensitive_text, height=320)
            if st.button("创建本地候选", key=f"local-draft-{item.get('source_id')}"):
                try:
                    if container.create_local_document_draft is None:
                        raise ValueError("本地候选服务尚未就绪")
                    draft = container.create_local_document_draft.execute(
                        CreateLocalDraftInput(
                            project_id=container.active_project.project_id,
                            source_id=str(item["source_id"]),
                            requested_by="Owner",
                        )
                    )
                except (OSError, ValueError, RuntimeError) as error:
                    st.error(f"创建本地候选失败：{error}")
                else:
                    st.success(f"已创建本地候选：{draft.draft.version_id}")
        if item.get("source_type") not in {definition.code for definition in MATERIAL_TYPES}:
            with st.expander("调整历史材料分类"):
                target = st.selectbox(
                    "调整为",
                    options=(None, *MATERIAL_TYPES),
                    format_func=lambda value: "请选择" if value is None else value.label,
                    key=f"reclassify-type-{item.get('source_id')}",
                )
                if st.button("保存分类", key=f"reclassify-save-{item.get('source_id')}"):
                    try:
                        if target is None or container.reclassify_source is None:
                            raise ValueError("请选择目标分类")
                        container.reclassify_source.execute(
                            ReclassifySourceInput(
                                project_id=container.active_project.project_id,
                                source_id=str(item["source_id"]),
                                new_source_type=target.code,
                                owner_name="Owner",
                            )
                        )
                    except (OSError, ValueError, RuntimeError) as error:
                        st.error(f"材料分类调整失败，原分类保持不变。{error}")
                    else:
                        st.success(f"材料分类已调整为“{target.label}”。")


def _render_wiki_ingest(container: AppContainer, item: dict) -> None:
    source_id = str(item.get("source_id", ""))
    status = item.get("ingest_status")
    if not source_id:
        return
    sensitive_levels = {
        SecurityLevel.L3_CONFIDENTIAL.value,
        SecurityLevel.L4_RESTRICTED.value,
    }
    if item.get("security_level") in sensitive_levels:
        _render_local_wiki_ingest(container, source_id, status)
        return
    if status == "ingesting":
        st.button(
            "处理中",
            key=f"material_ingesting_{source_id}",
            disabled=True,
        )
        return
    if status == "ingested":
        source_page_path = item.get("source_page_path")
        if source_page_path:
            st.markdown(f"[查看 Wiki 结果]({source_page_path})")
        else:
            st.markdown("查看 Wiki 结果")
        return
    # Historical Wiki outcomes stay visible even if this session has no
    # external credential.  Only a new/retry action requires the gateway.
    if container.wiki_ingest is None:
        return
    if status == "ingest_failed":
        st.caption(f"安全错误码：{item.get('ingest_error_code') or 'WIKI_CHANGESET_INVALID'}")
        label = "重新 Ingest"
        key = f"material_reingest_{source_id}"
    elif status == "reingest_recommended":
        st.info("当前 Wiki 仍可读；请 Owner 明确重新 Ingest。")
        label = "明确重新 Ingest"
        key = f"material_reingest_{source_id}"
    elif status == "pending_ingest":
        label = "开始 Ingest"
        key = f"material_ingest_{source_id}"
    else:
        return
    if not st.button(label, key=key, type="primary"):
        return
    try:
        result = container.wiki_ingest.execute(
            IngestArchivedSourceInput(
                project_id=container.require_project_id(),
                source_id=source_id,
                requested_by="Owner",
            )
        )
    except AppError as error:
        st.error(f"Wiki Ingest 失败：{error.code}")
    except (OSError, RuntimeError, ValueError):
        st.error("Wiki Ingest 失败：WIKI_CHANGESET_INVALID")
    else:
        st.success("已 Ingest 到当前项目 Wiki。")
        if result.source_page_path:
            st.markdown(f"[已 Ingest · 查看 Wiki 结果]({result.source_page_path})")


def _render_local_wiki_ingest(container: AppContainer, source_id: str, status: object) -> None:
    if status == "ingested":
        st.markdown("查看 Wiki 结果")
        return
    draft_root = container.active_project.paths.wiki_root / "drafts" / "local-ingest" / source_id
    if status == "local_review_required" or (status == "ingest_failed" and draft_root.is_dir()):
        if status == "ingest_failed":
            st.caption("上次本地 Ingest 未提交，草稿已保留，可修正后重新校验。")
        st.code(str(draft_root), language=None)
        if st.button("复制草稿路径", key=f"material_copy_local_draft_{source_id}"):
            st.info("草稿路径已显示，可在本机文件管理器或 Obsidian 中粘贴打开。")
        if not st.button(
            "重新校验并确认本地 Ingest" if status == "ingest_failed" else "校验并确认本地 Ingest",
            key=f"material_confirm_local_ingest_{source_id}",
            type="primary",
        ):
            return
        if container.confirm_local_wiki_ingest is None:
            st.error("本地 Ingest 服务尚未就绪。")
            return
        try:
            result = container.confirm_local_wiki_ingest.execute(
                ConfirmLocalWikiIngestInput(
                    project_id=container.require_project_id(),
                    source_id=source_id,
                    requested_by="Owner",
                )
            )
        except AppError as error:
            st.error(f"本地 Ingest 校验失败：{error.code}")
        except (OSError, RuntimeError, ValueError):
            st.error("本地 Ingest 校验失败：WIKI_CHANGESET_INVALID")
        else:
            st.success("已确认并 Ingest 到当前项目 Wiki。")
            if result.source_page_path:
                st.markdown(f"[已 Ingest · 查看 Wiki 结果]({result.source_page_path})")
        return
    if status not in {"pending_ingest", "ingest_failed", "reingest_recommended"}:
        return
    if container.prepare_local_wiki_ingest is None:
        return
    if not st.button(
        "创建本地 Ingest 草稿",
        key=f"material_prepare_local_ingest_{source_id}",
        type="primary",
    ):
        return
    try:
        draft = container.prepare_local_wiki_ingest.execute(
            PrepareLocalWikiIngestInput(
                project_id=container.require_project_id(),
                source_id=source_id,
                requested_by="Owner",
            )
        )
    except AppError as error:
        st.error(f"创建本地 Ingest 草稿失败：{error.code}")
    except (OSError, RuntimeError, ValueError):
        st.error("创建本地 Ingest 草稿失败：WIKI_CHANGESET_INVALID")
    else:
        st.success("已创建本地 Ingest 草稿，请在本机编辑后返回确认。")
        st.code(str(draft.draft_root), language=None)


def _series_options(container: AppContainer) -> tuple[str, ...]:
    assert container.active_project is not None
    index_path = container.active_project.paths.system_root / "source-index.json"
    if not index_path.is_file():
        return ()
    import json

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    sources = payload.get("sources", []) if isinstance(payload, dict) else []
    return tuple(
        sorted(
            {
                str(item["material_series_id"])
                for item in sources
                if isinstance(item, dict) and item.get("material_series_id")
            }
        )
    )
