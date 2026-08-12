from __future__ import annotations

from html import escape
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from src.application.container import AppContainer
from src.application.dto.projects import CreateProjectInput
from src.application.use_cases.manage_projects import clear_project_session_state


def render(container: AppContainer) -> None:
    manager = container.manage_projects
    if manager is None:
        st.error("项目中心尚未初始化，请检查本地配置。")
        return

    settings = manager.settings.load()
    if settings is None:
        _render_owner_setup(manager)
        return

    st.session_state.setdefault("incubator_owner", settings.owner_name)
    st.session_state.setdefault("incubator_library_root", settings.library_root)
    if settings.current_project_id:
        st.session_state.setdefault("active_project_id", settings.current_project_id)

    st.title("项目中心")
    st.caption(f"Owner：{settings.owner_name} · 本地项目库：{settings.library_root}")
    _render_create_form(manager)
    _render_project_cards(manager)


def _render_owner_setup(manager) -> None:
    st.title("首次设置")
    st.markdown("设置 Owner 和本地项目库后，即可开始孵化产品文档。")
    with st.form("incubator_owner_setup"):
        owner_name = st.text_input("Owner 姓名", key="incubator_setup_owner")
        library_root = st.text_input(
            "本地项目库",
            value=str(manager.library_root),
            key="incubator_setup_library_root",
        )
        submitted = st.form_submit_button(
            "保存设置",
            type="primary",
            key="incubator_setup_submit",
            use_container_width=True,
        )
    if not submitted:
        return
    try:
        settings = manager.initialize(owner_name, Path(library_root))
    except (OSError, ValueError, ValidationError) as error:
        st.error(f"设置保存失败：{error}")
        return
    st.session_state["incubator_owner"] = settings.owner_name
    st.session_state["incubator_library_root"] = settings.library_root
    st.rerun()


def _render_create_form(manager) -> None:
    with st.expander("新建产品项目", expanded=True):
        with st.form("projects_create_form"):
            project_id = st.text_input(
                "项目 ID",
                placeholder="例如 CREDIT-CARD-01",
                key="projects_create_id",
            )
            name = st.text_input("项目名称", key="projects_create_name")
            description = st.text_area(
                "产品类型或一句话说明",
                key="projects_create_description",
                max_chars=500,
            )
            display_version = st.text_input(
                "初始显示版本（可选）",
                key="projects_create_display_version",
            )
            allow_external_model = st.toggle(
                "允许在 Owner 确认后调用外部模型",
                value=False,
                key="projects_create_external",
            )
            submitted = st.form_submit_button(
                "创建项目",
                type="primary",
                key="projects_create_submit",
                use_container_width=True,
            )
        if submitted:
            try:
                project = manager.create(
                    CreateProjectInput(
                        project_id=project_id,
                        name=name,
                        description=description,
                        initial_display_version=display_version or None,
                        allow_external_model=allow_external_model,
                    )
                )
            except (KeyError, OSError, ValueError, ValidationError) as error:
                st.error(f"项目创建失败：{error}")
            else:
                st.success(f"项目 {project.name} 已创建，状态为“待初始化”。")


def _render_project_cards(manager) -> None:
    projects = manager.list()
    if not projects:
        st.markdown("### 新建第一个产品项目")
        st.caption("创建后会自动生成 raw、wiki、schema 和 exports 本地目录。")
        return

    st.subheader("全部项目")
    for project in projects:
        project_root = (manager.library_root / project.project_id).resolve()
        st.markdown(
            '<section class="pi-surface-card" data-testid="incubator-project-card">'
            f"<h3>{escape(project.name)}</h3>"
            f"<p>{escape(project.project_id)} · {escape(project.stage)}</p>"
            f"<p>材料 {project.source_count} · 候选 {project.draft_count} · "
            f"当前版本 {escape(project.current_version or '尚未发布')}</p>"
            f"<code>{escape(str(project_root))}</code>"
            "</section>",
            unsafe_allow_html=True,
        )
        if st.button(
            "进入项目",
            key=f"project_open_{project.project_id}",
            type="secondary",
        ):
            try:
                selection = manager.switch(project.project_id)
                settings = manager.settings.load()
                if settings is None:
                    raise RuntimeError("Owner settings are missing")
            except (KeyError, OSError, RuntimeError, ValueError) as error:
                st.error(f"项目切换失败：{error}")
                continue
            st.session_state["incubator_owner"] = settings.owner_name
            st.session_state["incubator_library_root"] = settings.library_root
            st.session_state["active_project_id"] = selection.project_id
            clear_project_session_state(st.session_state)
            st.success(f"已进入项目 {project.name}。")
