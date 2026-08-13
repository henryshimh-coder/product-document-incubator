from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.documents import ArchiveRawSourceInput
from src.domain.enums import AuthorityLevel, SecurityLevel


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    service = container.archive_raw_source
    st.title("原始材料")
    st.caption(f"项目 {project_id} 的不可变本地归档；系统保留完整路径和 SHA-256。")
    if service is None or container.active_project is None:
        st.info("材料归档服务尚未就绪，请先进入一个产品项目。")
        return
    with st.form("materials_archive_form"):
        local_path = st.text_input(
            "本地文件路径",
            placeholder="例如 /Users/name/Documents/需求文档.md",
            key="materials_local_path",
        )
        source_type = st.text_input("材料类型", value="product_requirement", key="materials_type")
        source_department = st.text_input("来源部门", value="产品部", key="materials_department")
        document_version = st.text_input("显示版本", value="v1.0", key="materials_version")
        document_date = st.date_input("文档日期", value=date.today(), key="materials_date")
        authority = st.selectbox(
            "权威级别",
            options=tuple(AuthorityLevel),
            format_func=lambda item: item.value,
            key="materials_authority",
        )
        security = st.selectbox(
            "安全等级",
            options=tuple(SecurityLevel),
            format_func=lambda item: item.value,
            key="materials_security",
        )
        redacted = st.checkbox("已确认脱敏", value=True, key="materials_redacted")
        external = st.checkbox("允许外部模型调用", value=False, key="materials_external")
        submitted = st.form_submit_button("归档到当前项目", type="primary", key="materials_archive")
    if submitted:
        try:
            result = service.execute(
                ArchiveRawSourceInput(
                    project_id=project_id,
                    local_path=Path(local_path),
                    source_type=source_type,
                    authority_level=authority,
                    source_department=source_department,
                    document_date=document_date,
                    document_version=document_version,
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
            f"**{item.get('filename', '未知文件')}** · {item.get('source_id', '--')}  \\n+"
            f"`{str(item.get('sha256', ''))[:12]}` · {item.get('source_type', '--')} · "
            f"{item.get('ingest_status', '--')}"
        )
        st.code(str(item.get("archive_path", "")), language=None)
