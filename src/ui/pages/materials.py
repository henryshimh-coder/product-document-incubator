from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.materials import (
    ArchiveRawSourceInput,
    CreateLocalDraftInput,
    DeleteArchivedSourceInput,
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
from src.infrastructure.files.project_library import require_canonical_project_path


def render(container: AppContainer) -> None:
    project_id = container.require_project_id()
    service = container.archive_raw_source
    st.title("原始材料")
    st.caption(f"项目 {project_id} 的不可变本地归档；确认前不会写入项目文件库。")
    if service is None or container.active_project is None:
        st.info("材料归档服务尚未就绪，请先进入一个产品项目。")
        return
    archive_tab, manager_tab = st.tabs(("归档新材料", "已归档材料"))
    archive_tab.__enter__()
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
        redacted = st.checkbox("已确认内容可外发", value=True, key="materials_redacted")
        external = st.checkbox(
            "允许外部模型调用",
            value=False,
            disabled=security in (SecurityLevel.L3_CONFIDENTIAL, SecurityLevel.L4_RESTRICTED),
            key="materials_external",
        )
        st.caption(
            "L1/L2 仅外发必要内容分段；手机号、身份证号、银行卡号和邮箱会在本地自动遮盖。"
            "业务名称和策略术语在 Owner 授权后可外发。"
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
        except AppError as error:
            st.error(f"材料归档失败：{error.user_message}")
        except (OSError, ValueError, RuntimeError) as error:
            st.error(f"材料归档失败：{error}")
        else:
            if result.duplicate:
                st.info("相同材料已存在，未创建重复归档。")
            else:
                st.success("材料已归档到当前项目。")
            st.code(str(result.archive_path), language=None)
            st.caption(f"来源 ID：{result.source_id} · SHA-256：{result.sha256[:12]}")
    archive_tab.__exit__(None, None, None)
    manager_tab.__enter__()
    _render_index(container)
    manager_tab.__exit__(None, None, None)


def _render_index(container: AppContainer) -> None:
    assert container.active_project is not None
    sources = _material_items(container)
    if not sources:
        st.caption("当前还没有已归档材料。")
        return
    keyword = st.text_input("搜索材料", key="materials_filter_keyword").strip().casefold()
    statuses = tuple(sorted({str(item.get("ingest_status", "")) for item in sources}))
    selected_status = st.selectbox(
        "状态",
        options=("", *statuses),
        format_func=lambda value: "全部状态" if not value else _status_label(value),
        key="materials_filter_status",
    )
    source_types = tuple(sorted({str(item.get("source_type", "")) for item in sources}))
    selected_type = st.selectbox(
        "材料类型",
        options=("", *source_types),
        format_func=lambda value: "全部类型" if not value else _type_label(value),
        key="materials_filter_type",
    )
    filtered = [
        item
        for item in sources
        if (not selected_status or item.get("ingest_status") == selected_status)
        and (not selected_type or item.get("source_type") == selected_type)
        and (
            not keyword
            or keyword
            in " ".join(
                str(item.get(field, ""))
                for field in ("material_name", "filename", "material_version")
            ).casefold()
        )
    ]
    if not filtered:
        st.info("没有匹配的已归档材料。")
        return
    groups: dict[str, list[dict]] = {}
    for item in filtered:
        group_id = str(item.get("material_series_id") or item.get("source_id"))
        groups.setdefault(group_id, []).append(item)
    ordered_groups = sorted(
        (sorted(group, key=_created_key, reverse=True) for group in groups.values()),
        key=lambda group: _created_key(group[0]),
        reverse=True,
    )
    for versions in ordered_groups:
        _render_material_group(container, versions)


def _material_items(container: AppContainer) -> list[dict]:
    assert container.active_project is not None
    if container.source_repository is not None:
        return [
            {
                "source_id": source.id,
                "material_name": source.material_name,
                "material_series_id": source.material_series_id,
                "previous_source_id": source.previous_source_id,
                "material_version": source.document_version,
                "filename": source.original_filename,
                "archive_path": source.archive_path,
                "sha256": source.sha256,
                "mime_type": source.mime_type,
                "size_bytes": source.size_bytes,
                "source_type": source.source_type,
                "security_level": source.security_level.value,
                "ingest_status": source.ingest_status,
                "ingest_schema_version": source.ingest_schema_version,
                "ingest_error_code": source.ingest_error_code,
                "source_page_path": source.source_page_path,
                "document_date": source.document_date.isoformat(),
                "created_at": source.created_at.isoformat(),
                "is_redacted": source.is_redacted,
                "allow_external_model": source.allow_external_model,
            }
            for source in container.source_repository.list_for_project(
                container.active_project.project_id
            )
        ]
    index_path = container.active_project.paths.system_root / "source-index.json"
    if not index_path.is_file():
        return []
    import json

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    entries = payload.get("sources", []) if isinstance(payload, dict) else []
    return [item for item in entries if isinstance(item, dict)]


def _render_material_group(container: AppContainer, versions: list[dict]) -> None:
    latest = versions[0]
    name = str(latest.get("material_name") or latest.get("filename") or "未命名材料")
    st.markdown(f"#### {name}")
    st.caption(f"当前版本 · {latest.get('material_version') or '--'}")
    _render_version_row(container, latest)
    historical = versions[1:]
    if historical:
        with st.expander(f"历史版本（{len(historical)}）"):
            for item in historical:
                st.markdown(f"**{item.get('material_version') or '--'}**")
                _render_version_row(container, item)
    with st.expander(f"技术详情 · {name}"):
        for item in versions:
            version = item.get("material_version") or "--"
            st.markdown(f"**{version}**")
            archive_path = Path(str(item.get("archive_path") or ""))
            if not archive_path.is_absolute():
                archive_path = container.active_project.paths.project_root / archive_path
            st.code(str(archive_path.absolute()), language=None)
            st.caption(f"Source ID：{item.get('source_id') or '--'}")
            st.caption(f"SHA-256：{item.get('sha256') or '--'}")
            st.caption(f"Schema：{item.get('ingest_schema_version') or '--'}")


def _render_version_row(container: AppContainer, item: dict) -> None:
    version = str(item.get("material_version") or "--")
    st.markdown(
        f"{_type_label(str(item.get('source_type') or ''))} · "
        f"{_status_label(str(item.get('ingest_status') or ''))} · "
        f"文档日期 {item.get('document_date') or '--'}"
    )
    _render_wiki_ingest(container, item)
    _render_legacy_material_actions(container, item)
    status = item.get("ingest_status")
    if status == "ingested":
        st.caption("已生成 Wiki，不可删除")
    if status in {"pending_ingest", "ingest_failed"}:
        _render_delete_action(container, item, version)


def _render_delete_action(container: AppContainer, item: dict, version: str) -> None:
    service = container.delete_archived_source
    if service is None:
        return
    source_id = str(item.get("source_id") or "")
    state_key = f"material_delete_confirming_{source_id}"
    if st.button("删除此版本", key=f"material_delete_{source_id}"):
        st.session_state[state_key] = True
    if not st.session_state.get(state_key):
        return
    name = str(item.get("material_name") or item.get("filename") or "未命名材料")
    st.caption(f"{name} · {version} · 仅删除此版本")
    confirmed = st.checkbox(
        "我确认将此版本移入本地可恢复回收区",
        key=f"material_delete_confirm_{source_id}",
    )
    if not st.button(
        "确认删除此版本",
        key=f"material_delete_execute_{source_id}",
        type="primary",
        disabled=not confirmed,
    ):
        return
    try:
        service.execute(
            DeleteArchivedSourceInput(
                project_id=container.require_project_id(),
                source_id=source_id,
                requested_by="Owner",
                confirmed=True,
            )
        )
    except AppError as error:
        st.error(f"{name} · {version}：删除失败（{error.code}）")
    except (OSError, RuntimeError, ValueError):
        st.error(f"{name} · {version}：删除失败，原材料已保留。")
    else:
        st.session_state.pop(state_key, None)
        st.rerun()


def _created_key(item: dict) -> str:
    return str(item.get("created_at") or "")


def _status_label(status: str) -> str:
    return {
        "pending_ingest": "待 Ingest",
        "ingest_failed": "Ingest 失败",
        "ingesting": "Ingest 中",
        "ingested": "已 Ingest",
        "local_review_required": "待本地复核",
        "reingest_recommended": "建议重新 Ingest",
    }.get(status, status or "--")


def _type_label(source_type: str) -> str:
    return next(
        (definition.label for definition in MATERIAL_TYPES if definition.code == source_type),
        source_type or "--",
    )


def _render_legacy_material_actions(container: AppContainer, item: dict) -> None:
    """Render actions retained for historical classifications and sensitive local work."""
    if not isinstance(item, dict):
        return
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
        _render_local_wiki_ingest(container, source_id, status, item.get("source_page_path"))
        return
    if status == "ingesting":
        st.button(
            "处理中",
            key=f"material_ingesting_{source_id}",
            disabled=True,
        )
        return
    if status == "ingested":
        _render_wiki_result_action(container, source_id, item.get("source_page_path"))
        return
    if status == "ingest_failed":
        name = item.get("material_name") or item.get("filename") or "未命名材料"
        version = item.get("material_version") or "--"
        st.error(f"{name} · {version}：Wiki Ingest 失败，可重试。")
        if st.button("查看技术错误码", key=f"material_error_code_{source_id}"):
            st.caption(f"安全错误码：{item.get('ingest_error_code') or 'WIKI_CHANGESET_INVALID'}")
    # Historical Wiki outcomes stay visible even if this session has no
    # external credential.  Only a new/retry action requires the gateway.
    if container.wiki_ingest is None:
        return
    if status == "ingest_failed":
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
    if item.get("is_redacted") is True and item.get("allow_external_model") is True:
        st.caption("Owner 已确认并授权必要内容外发")
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
        _render_wiki_result_action(container, source_id, result.source_page_path)


def _render_wiki_result_action(
    container: AppContainer, source_id: str, source_page_path: object
) -> None:
    if not isinstance(source_page_path, str) or not source_page_path:
        st.caption("Wiki 结果路径不可用。")
        return
    if st.button("查看 Wiki 结果", key=f"material_view_wiki_{source_id}"):
        _render_wiki_result(container, source_page_path)


def _render_wiki_result(container: AppContainer, source_page_path: str) -> None:
    assert container.active_project is not None
    if not source_page_path.startswith("wiki/") or Path(source_page_path).suffix != ".md":
        st.error("Wiki 结果路径无效。")
        return
    try:
        result_path = require_canonical_project_path(
            container.active_project.paths,
            source_page_path,
        )
    except ValueError:
        st.error("Wiki 结果路径无效。")
        return
    if not result_path.is_file():
        st.warning("Wiki 结果文件不存在，请重新 Ingest 或检查当前项目 Wiki 目录。")
        return
    try:
        markdown = result_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        st.error("Wiki 结果暂时无法读取。")
        return
    st.markdown(markdown)


def _render_local_wiki_ingest(
    container: AppContainer,
    source_id: str,
    status: object,
    source_page_path: object,
) -> None:
    if status == "ingested":
        _render_wiki_result_action(container, source_id, source_page_path)
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
            _render_wiki_result_action(container, source_id, result.source_page_path)
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
