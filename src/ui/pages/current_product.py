from __future__ import annotations

import streamlit as st

from src.application.container import AppContainer
from src.application.dto.documents import ExportCurrentDocumentInput
from src.domain.errors import AppError


def render(container: AppContainer) -> None:
    st.title("当前产品方案")
    st.caption("仅展示并下载 Owner 已发布的当前生效 Markdown 方案。")
    exporter = container.export_current_document
    if exporter is None or container.active_project is None:
        st.info("当前没有生效方案，请先前往“文档孵化”生成并由 Owner 发布。")
        return
    project_id = container.require_project_id()
    try:
        exported = exporter.execute(ExportCurrentDocumentInput(project_id=project_id))
    except AppError as error:
        st.info("当前没有生效方案，请先前往“文档孵化”生成并由 Owner 发布。")
        st.caption(f"状态：{error.code}")
        return
    except (OSError, ValueError, KeyError) as error:
        st.error(f"读取当前产品方案失败：{error}")
        return
    _render_metadata(container)
    st.download_button(
        "下载当前产品方案 (.md)",
        data=exported.content,
        file_name=exported.filename,
        mime="text/markdown; charset=utf-8",
        key="current_product_download",
        type="primary",
    )
    st.subheader("当前内容")
    st.markdown(exported.content.decode("utf-8"))
    _render_embedded_query(container)
    _render_history(container)


def _render_metadata(container: AppContainer) -> None:
    assert container.active_project is not None
    from src.infrastructure.files.manifest_store import ManifestStore

    manifest = ManifestStore(
        container.active_project.paths.manifest_path,
        project_root=container.active_project.paths.project_root,
    ).read_and_validate()
    first, second, third, fourth = st.columns(4)
    first.metric("项目", manifest.project_id)
    second.metric("版本", manifest.display_version or manifest.current_version)
    third.metric("发布人", manifest.approved_by)
    fourth.metric("发布时间", manifest.published_at.strftime("%Y-%m-%d"))
    if manifest.parent_baseline_id:
        st.caption(f"父版本：{manifest.parent_baseline_id}")


def _render_history(container: AppContainer) -> None:
    assert container.active_project is not None
    versions_root = container.active_project.paths.wiki_root / "versions"
    history = (
        sorted(
            (
                path.name
                for path in versions_root.iterdir()
                if path.is_dir() and (path / "产品方案.md").is_file()
            ),
            reverse=True,
        )
        if versions_root.is_dir()
        else []
    )
    st.subheader("历史版本")
    if not history:
        st.caption("暂无历史版本。")
        return
    st.markdown("\n".join(f"- `{version}`" for version in history))


def _render_embedded_query(container: AppContainer) -> None:
    st.subheader("当前方案查询")
    if container.query is None:
        st.caption("实时查询工作流尚未配置；当前方案可正常阅读与下载。")
        return
    with st.expander("打开实时查询", expanded=False):
        from src.ui.pages import query

        query.render(container)
