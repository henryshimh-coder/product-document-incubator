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
from src.domain.enums import AuthorityLevel, SecurityLevel
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
            f"{item.get('material_version', '--')} · {item.get('source_id', '--')}  \\n+"
            f"`{str(item.get('sha256', ''))[:12]}` · {item.get('source_type', '--')} · "
            f"{item.get('ingest_status', '--')}"
        )
        st.code(str(item.get("archive_path", "")), language=None)
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
