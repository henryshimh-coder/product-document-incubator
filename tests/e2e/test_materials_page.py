from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


def _render_materials_page(library_root: str, source_path: str) -> None:
    from datetime import UTC as timezone_utc
    from datetime import datetime as datetime_type
    from pathlib import Path as path_type

    from src.application.container import AppContainer, AppSettings
    from src.application.project_context import ProjectContext
    from src.application.use_cases.archive_raw_source import ArchiveRawSource
    from src.domain.models import Project
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive
    from src.infrastructure.files.source_index_store import SourceIndexStore
    from src.ui.pages.materials import render

    root = path_type(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    paths.raw_root.mkdir(parents=True, exist_ok=True)
    db_path = root / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime_type(2026, 8, 12, tzinfo=timezone_utc)
    projects = SqliteProjectRepository(db_path)
    try:
        projects.add(
            Project(
                id="PROJECT_A",
                name="项目 A",
                product_line="测试",
                stage="待初始化",
                current_baseline_id=None,
                allow_external_model=False,
                created_at=now,
                updated_at=now,
            )
        )
    except Exception:
        pass
    service = ArchiveRawSource(
        paths=paths,
        sources=SqliteSourceRepository(db_path),
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=paths, source_id=source_id, year=year
        ),
        index=SourceIndexStore(paths),
        now=lambda: now,
    )
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("md",),
                demo_mode=True,
                schema_version="1.0",
            ),
            active_project=ProjectContext("PROJECT_A", paths, db_path),
            archive_raw_source=service,
        )
    )


def _render_materials_ingest_page(
    library_root: str,
    status: str,
    security_level: str | None = None,
    source_page_path: str | None = None,
    source_page_content: str | None = None,
) -> None:
    import json
    from pathlib import Path as path_type

    from src.application.container import AppContainer, AppSettings
    from src.application.dto.wiki_ingest import (
        LocalWikiIngestDraftView,
        WikiIngestResultView,
    )
    from src.application.project_context import ProjectContext
    from src.domain.wiki import WikiIngestStatus
    from src.infrastructure.files.project_library import ProjectPaths
    from src.ui.pages.materials import render

    class SuccessfulIngest:
        def execute(self, command):
            return WikiIngestResultView(
                source_id=command.source_id,
                status=WikiIngestStatus.INGESTED,
                source_page_path=f"wiki/sources/{command.source_id}-material.md",
                topic_page_paths=[f"wiki/topics/{command.source_id}-topic-1.md"],
                conflict_count=0,
                evidence_gap_count=0,
            )

    class LocalPrepare:
        def execute(self, command):
            return LocalWikiIngestDraftView(
                source_id=command.source_id,
                status=WikiIngestStatus.LOCAL_REVIEW_REQUIRED,
                draft_root=paths.wiki_root / "drafts" / "local-ingest" / command.source_id,
            )

    class LocalConfirm:
        def execute(self, command):
            return WikiIngestResultView(
                source_id=command.source_id,
                status=WikiIngestStatus.INGESTED,
                source_page_path=f"wiki/sources/{command.source_id}-material.md",
                topic_page_paths=[f"wiki/topics/{command.source_id}-topic-1.md"],
                conflict_count=0,
                evidence_gap_count=0,
            )

    root = path_type(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    paths.system_root.mkdir(parents=True, exist_ok=True)
    stored_source_page_path = source_page_path or "wiki/sources/SRC-PROJECT-A-001-material.md"
    if source_page_content is not None:
        wiki_file = paths.project_root / stored_source_page_path
        wiki_file.parent.mkdir(parents=True, exist_ok=True)
        wiki_file.write_text(source_page_content, encoding="utf-8")
    effective_security_level = security_level or (
        "L4" if status == "local_review_required" else "L2"
    )
    if status == "ingest_failed" and effective_security_level in {"L3", "L4"}:
        (paths.wiki_root / "drafts" / "local-ingest" / "SRC-PROJECT-A-001").mkdir(parents=True)
    (paths.system_root / "source-index.json").write_text(
        json.dumps(
            {
                "schema_version": "2.2",
                "project_id": "PROJECT_A",
                "sources": [
                    {
                        "source_id": "SRC-PROJECT-A-001",
                        "material_name": "产品原则",
                        "material_version": "1.0",
                        "sha256": "a" * 64,
                        "source_type": "product_requirement",
                        "security_level": effective_security_level,
                        "archive_path": "raw/2026/SRC-PROJECT-A-001/material.md",
                        "ingest_status": status,
                        "ingest_error_code": (
                            "MODEL_TIMEOUT" if status == "ingest_failed" else None
                        ),
                        "source_page_path": (
                            stored_source_page_path if status == "ingested" else None
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source_index_path = paths.system_root / "source-index.json"
    source_index = json.loads(source_index_path.read_text(encoding="utf-8"))
    source_index["sources"][0].update(is_redacted=True, allow_external_model=True)
    source_index_path.write_text(json.dumps(source_index), encoding="utf-8")
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("md",),
                demo_mode=True,
                schema_version="1.0",
            ),
            active_project=ProjectContext(
                "PROJECT_A", paths, root / ".incubator/product_incubator.db"
            ),
            archive_raw_source=object(),
            wiki_ingest=SuccessfulIngest(),
            prepare_local_wiki_ingest=LocalPrepare(),
            confirm_local_wiki_ingest=LocalConfirm(),
        )
    )


def _render_existing_materials_index(
    library_root: str,
    project_root: str,
    db_path: str,
) -> None:
    from pathlib import Path as path_type

    from src.application.container import AppContainer, AppSettings
    from src.application.project_context import ProjectContext
    from src.infrastructure.files.project_library import ProjectPaths
    from src.ui.pages.materials import render

    paths = ProjectPaths.for_registered_root(
        path_type(library_root),
        "PROJECT_A",
        path_type(project_root),
    )
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("md",),
                demo_mode=True,
                schema_version="1.0",
            ),
            active_project=ProjectContext("PROJECT_A", paths, path_type(db_path)),
            archive_raw_source=object(),
            wiki_ingest=object(),
        )
    )


def _render_materials_manager(library_root: str, db_path: str) -> None:
    from pathlib import Path as path_type

    from src.application.container import AppContainer, AppSettings
    from src.application.project_context import ProjectContext
    from src.application.use_cases.delete_archived_source import DeleteArchivedSource
    from src.infrastructure.db.repositories import SqliteSourceRepository
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.files.source_index_store import SourceIndexStore
    from src.infrastructure.files.source_trash import SourceTrash
    from src.ui.pages.materials import render

    root = path_type(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    sources = SqliteSourceRepository(path_type(db_path))
    index = SourceIndexStore(paths)
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("md",),
                demo_mode=True,
                schema_version="1.0",
            ),
            active_project=ProjectContext("PROJECT_A", paths, path_type(db_path)),
            archive_raw_source=object(),
            source_repository=sources,
            delete_archived_source=DeleteArchivedSource(
                paths=paths,
                sources=sources,
                index=index,
                trash=SourceTrash(paths),
            ),
        )
    )


def _seed_material_versions(tmp_path):
    import hashlib
    from datetime import UTC, date, datetime

    from src.domain.enums import AuthorityLevel, SecurityLevel
    from src.domain.models import Project, SourceRecord
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.files.source_index_store import SourceIndexStore

    root = tmp_path / "library"
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    for directory in (
        paths.raw_root,
        paths.wiki_root,
        paths.schema_root,
        paths.exports_root,
        paths.system_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    db_path = root / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 24, tzinfo=UTC)
    SqliteProjectRepository(db_path).add(
        Project(
            id="PROJECT_A",
            name="项目 A",
            product_line="测试",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=now,
            updated_at=now,
        )
    )
    repository = SqliteSourceRepository(db_path)
    index = SourceIndexStore(paths)
    definitions = (
        (
            "SRC-PROJECT-A-101",
            "MAT-ROADMAP",
            "路线图",
            "v1.0",
            "pending_ingest",
            "product_requirement",
            datetime(2026, 6, 1, tzinfo=UTC),
            None,
        ),
        (
            "SRC-PROJECT-A-102",
            "MAT-ROADMAP",
            "路线图",
            "v2.0",
            "ingest_failed",
            "product_requirement",
            datetime(2026, 7, 1, tzinfo=UTC),
            "SRC-PROJECT-A-101",
        ),
        (
            "SRC-PROJECT-A-103",
            "MAT-ROADMAP",
            "路线图",
            "v3.0",
            "pending_ingest",
            "product_requirement",
            datetime(2026, 8, 1, tzinfo=UTC),
            "SRC-PROJECT-A-102",
        ),
        (
            "SRC-PROJECT-A-201",
            None,
            "用户调研",
            "v1.0",
            "ingested",
            "customer_market_material",
            datetime(2026, 8, 2, tzinfo=UTC),
            None,
        ),
    )
    for (
        source_id,
        series_id,
        name,
        version,
        status,
        source_type,
        created_at,
        previous,
    ) in definitions:
        payload = f"# {name} {version}\n".encode()
        archive_path = paths.raw_root / "2026" / source_id / f"{source_id}.md"
        archive_path.parent.mkdir(parents=True)
        archive_path.write_bytes(payload)
        source = SourceRecord(
            id=source_id,
            project_id="PROJECT_A",
            original_filename=archive_path.name,
            archive_path=str(archive_path),
            sha256=hashlib.sha256(payload).hexdigest(),
            mime_type="text/markdown",
            size_bytes=len(payload),
            source_type=source_type,
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            source_department="产品部",
            provider=None,
            document_date=date(2026, created_at.month, 1),
            document_version=version,
            applicable_baseline_version="未关联基线",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted=True,
            allow_external_model=False,
            is_sandbox=False,
            ingest_status=status,
            created_at=created_at,
            material_name=name,
            material_series_id=series_id,
            previous_source_id=previous,
            ingest_error_code="MODEL_TIMEOUT" if status == "ingest_failed" else None,
            ingest_schema_version="2.2",
            source_page_path=(
                "wiki/sources/SRC-PROJECT-A-201-material.md" if status == "ingested" else None
            ),
        )
        repository.add(source)
        index.upsert(source)
    return root, db_path


def test_materials_page_renders_a_nonwriting_confirmation_form(tmp_path) -> None:
    """Catches a page visit creating a material archive before Owner confirmation."""
    page = AppTest.from_function(
        _render_materials_page,
        args=(str(tmp_path / "library"), str(tmp_path / "unused.md")),
    ).run()

    assert not page.exception
    assert page.button(key="materials_archive")


def test_materials_page_uses_confirmed_browser_upload_instead_of_local_path(tmp_path) -> None:
    """Catches the 2.1 material workflow asking an Owner to type a device-specific path."""
    page = AppTest.from_function(
        _render_materials_page,
        args=(str(tmp_path / "library"), str(tmp_path / "unused.md")),
    ).run()

    with pytest.raises(KeyError):
        page.text_input(key="materials_local_path")
    assert page.file_uploader(key="materials_upload")


def test_materials_page_explains_owner_outbound_authorization(tmp_path) -> None:
    """Catches an Owner confirmation form omitting its outbound-data boundary."""
    page = AppTest.from_function(
        _render_materials_page,
        args=(str(tmp_path / "library"), str(tmp_path / "需求.md")),
    ).run()

    rendered = "\n".join(item.value for item in (*page.caption, *page.info, *page.markdown))
    assert "手机号、身份证号、银行卡号和邮箱会在本地自动遮盖" in rendered
    assert "业务名称和策略术语在 Owner 授权后可外发" in rendered


def test_authorized_archived_material_shows_owner_outbound_notice(tmp_path) -> None:
    """Catches a permitted L1/L2 ingest offering its action without Owner context."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / "library"), "pending_ingest", "L2"),
    ).run()

    rendered = "\n".join(item.value for item in (*page.caption, *page.info, *page.markdown))
    assert "Owner 已确认并授权必要内容外发" in rendered
    assert page.button(key="material_ingest_SRC-PROJECT-A-001")


def test_materials_page_shows_a_safe_message_for_domain_upload_rejection(tmp_path) -> None:
    """Catches a rejected upload leaking a Streamlit technical stack trace to an Owner."""

    def render_page(root_path: str) -> None:
        from pathlib import Path

        from src.application.container import AppContainer, AppSettings
        from src.application.project_context import ProjectContext
        from src.domain.errors import DomainError, ErrorCode
        from src.infrastructure.files.project_library import ProjectPaths
        from src.ui.pages.materials import render

        class RejectingArchiveService:
            def execute(self, command):
                raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, "UNSAFE_FILENAME")

        root = Path(root_path)
        paths = ProjectPaths.for_project(root, "PROJECT_A")
        paths.system_root.mkdir(parents=True, exist_ok=True)
        render(
            AppContainer(
                settings=AppSettings(
                    name="产品文档孵化器",
                    project_id="LLD",
                    default_query_scope="effective",
                    max_upload_mb=20,
                    accepted_extensions=("md",),
                    demo_mode=True,
                    schema_version="1.0",
                ),
                active_project=ProjectContext(
                    "PROJECT_A", paths, root / ".incubator/product_incubator.db"
                ),
                archive_raw_source=RejectingArchiveService(),
            )
        )

    page = AppTest.from_function(render_page, args=(str(tmp_path / "library"),)).run()
    page.file_uploader(key="materials_upload").set_value(
        ("需求.md", "# 需求".encode(), "text/markdown")
    )
    page.selectbox(key="materials_archive_mode").select("新材料")
    page.selectbox(key="materials_type").select("产品需求")
    page.selectbox(key="materials_authority").select("正式基线依据")
    page.text_input(key="materials_version").set_value("v1.0")
    page.run()
    page.button(key="materials_archive").click().run()

    assert not page.exception
    assert any("不支持该文件格式" in item.value for item in page.error)


def test_material_page_runs_ingest_after_owner_click(tmp_path) -> None:
    """Catches the pending action failing to invoke the application service."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / "library"), "pending_ingest"),
    ).run()

    button = page.button(key="material_ingest_SRC-PROJECT-A-001")
    assert button
    button.click().run()

    assert page.success
    assert any("已 Ingest" in item.value for item in page.success)
    assert page.button(key="material_view_wiki_SRC-PROJECT-A-001")


def test_material_page_confirms_l4_local_draft_without_external_ingest(tmp_path) -> None:
    """L3/L4 material exposes only the Owner's local draft confirmation route."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / "library"), "local_review_required"),
    ).run()

    assert page.button(key="material_copy_local_draft_SRC-PROJECT-A-001")
    confirm = page.button(key="material_confirm_local_ingest_SRC-PROJECT-A-001")
    assert confirm
    with pytest.raises(KeyError):
        page.button(key="material_ingest_SRC-PROJECT-A-001")
    confirm.click().run()
    assert any("已确认并 Ingest" in item.value for item in page.success)


def test_material_page_retries_failed_l4_draft_with_local_confirmation(tmp_path) -> None:
    """A failed sensitive local transaction must not fall back to create/external actions."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / "library"), "ingest_failed", "L4"),
    ).run()

    assert page.button(key="material_confirm_local_ingest_SRC-PROJECT-A-001")
    with pytest.raises(KeyError):
        page.button(key="material_prepare_local_ingest_SRC-PROJECT-A-001")


def test_material_page_reads_real_failed_lifecycle_without_writing_index(tmp_path) -> None:
    """Catches a real failed ingest remaining pending or the UI repairing its index itself."""
    from src.domain.errors import GatewayError
    from tests.integration.use_cases.test_wiki_ingest import make_ingest_fixture

    fixture = make_ingest_fixture(tmp_path)
    fixture.gateway.fail(GatewayError.timeout())
    with pytest.raises(GatewayError):
        fixture.execute()
    index_path = fixture.page(".incubator/source-index.json")
    before_render = index_path.read_bytes()

    page = AppTest.from_function(
        _render_existing_materials_index,
        args=(
            str(fixture.paths.library_root),
            str(fixture.paths.project_root),
            str(fixture.db_path),
        ),
    ).run()

    page.button(key=f"material_error_code_{fixture.source_id}").click().run()
    rendered = "\n".join(item.value for item in (*page.markdown, *page.caption, *page.info))
    assert "MODEL_TIMEOUT" in rendered
    assert page.button(key=f"material_reingest_{fixture.source_id}")
    assert index_path.read_bytes() == before_render


@pytest.mark.parametrize(
    ("status", "button_key", "disabled", "expected_text"),
    [
        ("ingesting", "material_ingesting_SRC-PROJECT-A-001", True, "处理中"),
        ("ingested", None, False, "查看 Wiki 结果"),
        ("ingest_failed", "material_reingest_SRC-PROJECT-A-001", False, "MODEL_TIMEOUT"),
        (
            "reingest_recommended",
            "material_reingest_SRC-PROJECT-A-001",
            False,
            "明确重新 Ingest",
        ),
    ],
)
def test_material_page_renders_wiki_ingest_lifecycle(
    tmp_path, status, button_key, disabled, expected_text
) -> None:
    """Catches a lifecycle state exposing the wrong Owner action."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / status), status),
    ).run()

    assert not page.exception
    if status == "ingest_failed":
        page.button(key="material_error_code_SRC-PROJECT-A-001").click().run()
    rendered = "\n".join(item.value for item in (*page.markdown, *page.caption, *page.info))
    rendered += "\n" + "\n".join(item.label for item in page.button)
    assert expected_text in rendered
    if button_key is not None:
        assert page.button(key=button_key).disabled is disabled


def test_material_page_displays_generated_wiki_result_in_place(tmp_path) -> None:
    """Catches an ingested Wiki file being opened as an unserved browser URL."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(
            str(tmp_path / "library"),
            "ingested",
            None,
            "wiki/sources/SRC-PROJECT-A-001-material.md",
            "# 已生成 Wiki\n\n这是当前项目内可查看的 Wiki 结果。",
        ),
    ).run()

    page.button(key="material_view_wiki_SRC-PROJECT-A-001").click().run()

    assert not page.exception
    assert any("当前项目内可查看的 Wiki 结果" in item.value for item in page.markdown)


def test_material_page_rejects_wiki_result_path_outside_wiki_root(tmp_path) -> None:
    """Catches a tampered source index making the page read another project file."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / "library"), "ingested", None, "raw/unsafe.md"),
    ).run()

    page.button(key="material_view_wiki_SRC-PROJECT-A-001").click().run()

    assert not page.exception
    assert any("Wiki 结果路径无效" in item.value for item in page.error)


def test_materials_manager_groups_versions_and_keeps_newest_outside_history(tmp_path) -> None:
    """Catches a flat archive list obscuring series and current-version precedence."""
    root, db_path = _seed_material_versions(tmp_path)

    page = AppTest.from_function(_render_materials_manager, args=(str(root), str(db_path))).run()

    assert not page.exception
    headings = [item.value for item in page.markdown if item.value.startswith("#### ")]
    assert headings == ["#### 用户调研", "#### 路线图"]
    history = next(item for item in page.expander if item.label == "历史版本（2）")
    assert any("当前版本 · v3.0" in item.value for item in page.caption)
    history_text = "\n".join(item.value for item in (*history.markdown, *history.caption))
    assert "v2.0" in history_text
    assert "v1.0" in history_text
    assert "v3.0" not in history_text


@pytest.mark.parametrize(
    ("widget_key", "selection", "expected_heading"),
    [
        ("materials_filter_keyword", "调研", "#### 用户调研"),
        ("materials_filter_status", "Ingest 失败", "#### 路线图"),
        ("materials_filter_type", "用户与市场研究", "#### 用户调研"),
    ],
)
def test_materials_manager_filters_by_keyword_status_and_type(
    tmp_path, widget_key, selection, expected_heading
) -> None:
    """Catches any management filter leaving unrelated material groups visible."""
    root, db_path = _seed_material_versions(tmp_path)
    page = AppTest.from_function(_render_materials_manager, args=(str(root), str(db_path))).run()

    if "keyword" in widget_key:
        page.text_input(key=widget_key).set_value(selection).run()
    else:
        page.selectbox(key=widget_key).select(selection).run()

    headings = [item.value for item in page.markdown if item.value.startswith("#### ")]
    assert headings == [expected_heading]


def test_materials_manager_keeps_paths_ids_hashes_and_schema_in_technical_details(
    tmp_path,
) -> None:
    """Catches implementation metadata leaking into the compact group summary."""
    root, db_path = _seed_material_versions(tmp_path)

    page = AppTest.from_function(_render_materials_manager, args=(str(root), str(db_path))).run()

    technical = [item for item in page.expander if item.label.startswith("技术详情")]
    assert len(technical) == 2
    technical_text = "\n".join(
        element.value
        for expander in technical
        for element in (*expander.markdown, *expander.caption, *expander.code)
    )
    assert str(root / "PROJECT_A" / "raw") in technical_text
    assert "SRC-PROJECT-A-103" in technical_text
    assert "2.2" in technical_text
    assert "SHA-256" in technical_text
    assert sum(len(expander.code) for expander in technical) == len(page.code)


def test_materials_manager_renders_failure_and_error_code_control_in_its_version_row(
    tmp_path,
) -> None:
    """Catches one failed version becoming a page-wide warning or exposing raw codes."""
    root, db_path = _seed_material_versions(tmp_path)

    page = AppTest.from_function(_render_materials_manager, args=(str(root), str(db_path))).run()

    assert len(page.error) == 1
    assert "路线图 · v2.0" in page.error[0].value
    assert "MODEL_TIMEOUT" not in page.error[0].value
    assert page.button(key="material_error_code_SRC-PROJECT-A-102")


def test_materials_manager_hides_delete_for_running_and_ingested_versions(tmp_path) -> None:
    """Catches protected lifecycle states exposing any destructive UI entry."""
    root, db_path = _seed_material_versions(tmp_path)
    from src.infrastructure.db.repositories import SqliteSourceRepository

    repository = SqliteSourceRepository(db_path)
    pending = repository.get("SRC-PROJECT-A-103")
    repository.update(pending.model_copy(update={"ingest_status": "ingesting"}))

    page = AppTest.from_function(_render_materials_manager, args=(str(root), str(db_path))).run()

    with pytest.raises(KeyError):
        page.button(key="material_delete_SRC-PROJECT-A-103")
    with pytest.raises(KeyError):
        page.button(key="material_delete_SRC-PROJECT-A-201")
    rendered = "\n".join(item.value for item in (*page.caption, *page.markdown))
    assert "已生成 Wiki，不可删除" in rendered


def test_materials_manager_requires_two_steps_and_promotes_previous_version_after_delete(
    tmp_path,
) -> None:
    """Catches a one-click delete or a stale group summary after deleting its newest version."""
    root, db_path = _seed_material_versions(tmp_path)
    page = AppTest.from_function(_render_materials_manager, args=(str(root), str(db_path))).run()

    page.button(key="material_delete_SRC-PROJECT-A-103").click().run()

    assert page.checkbox(key="material_delete_confirm_SRC-PROJECT-A-103")
    assert page.button(key="material_delete_execute_SRC-PROJECT-A-103")
    rendered = "\n".join(item.value for item in (*page.caption, *page.markdown))
    assert "路线图" in rendered
    assert "v3.0" in rendered
    assert "仅删除此版本" in rendered

    page.checkbox(key="material_delete_confirm_SRC-PROJECT-A-103").check().run()
    page.button(key="material_delete_execute_SRC-PROJECT-A-103").click().run()

    assert not page.exception
    assert any("当前版本 · v2.0" in item.value for item in page.caption)
