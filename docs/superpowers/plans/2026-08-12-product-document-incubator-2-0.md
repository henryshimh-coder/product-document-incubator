# 产品文档孵化器 2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 15 人日内把现有单一 LLD 比赛版升级为单 Owner、多项目、本地优先的“产品文档孵化器 2.0”，完成自动建库、原始材料归档、候选文档孵化、Owner 发布、当前 Markdown 下载和授权项目结构建议。

**Architecture:** 使用一个中央 SQLite 状态库和每项目独立文件夹。新增项目库、项目上下文、原始材料归档、文档孵化和结构建议五类边界服务；既有 Query、Lint、引用校验和原子发布原则继续复用。产品全文生成与结构建议共用一个新增的 Dify Document Workflow，但使用两个严格区分的任务契约，不能侵入或改变 1.x 的 Ingest、Query、Lint Workflow 输出契约。

**Tech Stack:** Python 3.12、Streamlit 1.60、Pydantic 2、SQLite、Markdown、Dify Workflow、httpx、filelock、pytest 9、Ruff；不新增运行时依赖。

**Requirement baseline:** `产品文档孵化器_2.0迭代产品文档_v1.0.md`

## Global Constraints

- 产品名称统一为“产品文档孵化器”；1.x 历史材料保持“产品智策”原名。
- 单 Owner、无登录、无多角色、无多级审批。
- 默认项目库为 `~/Documents/产品文档孵化器项目库/`；Owner 可修改并持久化。
- 计划投入 13.5 人日，风险缓冲 1.5 人日，总投入不得超过 15 人日。
- 任一工作包实际投入超过计划 20%，或总投入预计超过 15 人日，立即停下与 Owner 确认裁减。
- 不可裁减：多项目自动建库、`raw/` 不可变归档、候选经 Owner 发布为当前版本、当前 Markdown 单文件下载。
- `raw/` 只追加，AI 只读；归档前后 SHA-256 必须一致。
- 所有数据库查询、缓存键、服务命令、日志和文件解析必须包含 `project_id`。
- 候选文档不能直接覆盖当前生效文档；当前版本由每项目 Manifest 唯一声明。
- 跨项目建议只读取 Owner 本次勾选项目的标题结构，不传递完整产品文档、业务数值或敏感原文。
- 只下载 UTF-8 Markdown；不实现 DOCX、PDF、ZIP。
- 不实现项目删除、项目归档、富文本、文件监听、复杂图谱或历史一键回滚。
- 保持 1.x 全量测试兼容；迁移脚本不得改写冻结快照和历史验收材料。
- 每个任务使用 TDD：先写失败测试，确认失败原因，再做最小实现。
- 每次提交只暂存本任务列出的文件；不得暂存工作区中已有的无关未提交文档。

---

## File and Interface Map

### 新增的核心文件

| 文件 | 单一职责 |
| --- | --- |
| `src/domain/incubator.py` | 2.0 项目设置、草稿、结构建议领域模型和状态枚举 |
| `src/application/dto/projects.py` | 创建、切换和列出项目的命令／视图 |
| `src/application/project_context.py` | 活动项目的路径、Manifest 与中央数据库组合上下文 |
| `src/application/dto/documents.py` | 归档、孵化、发布、导出和建议命令／结果 |
| `src/application/ports/incubator.py` | 项目库、草稿、建议、Document Workflow 的稳定接口 |
| `src/application/use_cases/manage_projects.py` | 原子创建、列出、切换项目 |
| `src/application/use_cases/archive_raw_source.py` | 原始文件复制归档、去重和索引 |
| `src/application/use_cases/incubate_document.py` | 首版／增量候选 Markdown 生成和持久化 |
| `src/application/use_cases/publish_document_draft.py` | 初始／增量草稿的 Owner 原子发布 |
| `src/application/use_cases/export_current_document.py` | 当前 Markdown 字节读取和导出副本 |
| `src/application/use_cases/suggest_document_structure.py` | 授权项目标题结构比较和建议落库 |
| `src/infrastructure/files/project_library.py` | 根目录解析、路径防逃逸、设置指针和项目路径 |
| `src/infrastructure/files/project_scaffolder.py` | 在临时目录生成固定 Wiki-LLM 框架并原子提交 |
| `src/infrastructure/files/source_index_store.py` | 原子维护每项目 `source-index.json` |
| `src/infrastructure/files/project_source_archive.py` | 在项目 `raw/{year}/{source_id}` 内只追加归档，不改变 1.x Archive |
| `src/infrastructure/files/document_store.py` | 草稿、当前镜像、不可变版本、导出文件的耐久写入 |
| `src/infrastructure/files/markdown_sections.py` | Markdown 校验、标题提取和规则卡编译 |
| `src/infrastructure/gateways/document_gateway.py` | Dify Document Workflow 传输与输出校验 |
| `src/ui/pages/projects.py` | 项目中心 |
| `src/ui/pages/materials.py` | 原始材料归档和 Ingest 入口 |
| `src/ui/pages/incubate.py` | 候选 Markdown、Diff、编辑和 Owner 发布 |
| `src/ui/pages/current_product.py` | 当前方案、查询、历史和 Markdown 下载 |
| `src/ui/pages/checks.py` | Lint 和结构完善建议 |
| `scripts/migrate_lld_to_v2.py` | 可演练、可重复执行的 LLD 迁移 |

### 必须保持一致的接口

```python
@dataclass(frozen=True)
class ProjectPaths:
    library_root: Path
    project_id: str
    project_root: Path
    raw_root: Path
    wiki_root: Path
    schema_root: Path
    exports_root: Path
    system_root: Path
    manifest_path: Path

class ManageProjects:
    def create(self, command: CreateProjectInput) -> Project: ...
    def list(self) -> list[ProjectSummary]: ...
    def switch(self, project_id: str) -> ProjectSelection: ...

class ArchiveRawSource:
    def execute(self, command: ArchiveRawSourceInput) -> ArchivedSourceView: ...

class IncubateDocument:
    def execute(self, command: IncubateDocumentInput) -> DocumentDraft: ...

class PublishDocumentDraft:
    def execute(self, command: PublishDocumentDraftInput) -> Baseline: ...

class ExportCurrentDocument:
    def execute(self, command: ExportCurrentDocumentInput) -> ExportedDocument: ...

class SuggestDocumentStructure:
    def execute(self, command: SuggestStructureInput) -> list[StructureSuggestion]: ...
```

后续任务不得自行改名；确需改接口时先更新本计划和所有消费方，再开始实现。

---

## Batch B1 — 多项目内核与自动建库（2.5 人日）

### Task 1: 项目库设置、路径安全与数据库模型（1.0 人日）

**Files:**
- Create: `src/domain/incubator.py`
- Create: `src/infrastructure/files/project_library.py`
- Modify: `src/domain/models.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `.gitignore`
- Test: `tests/unit/domain/test_project_library.py`
- Test: `tests/integration/db/test_migrations.py`
- Test: `tests/integration/db/test_repositories.py`

**Interfaces:**
- Produces: `IncubatorSettings`, `ProjectPaths`, `ProjectSummary`, `SqliteProjectRepository.list_all()`。
- Consumes: 既有 `Project` 和中央 SQLite 连接。

- [x] **Step 1: 写项目路径和非法 ID 的失败测试**

```python
def test_project_paths_stay_inside_library_root(tmp_path):
    from src.infrastructure.files.project_library import ProjectPaths

    paths = ProjectPaths.for_project(tmp_path / "library", "CREDIT-CARD-01")

    assert paths.project_root == (tmp_path / "library/CREDIT-CARD-01").resolve()
    assert paths.raw_root == paths.project_root / "raw"
    assert paths.manifest_path == paths.system_root / "current-baseline.json"


@pytest.mark.parametrize("project_id", ["../LLD", "a/b", "lld", "", "A B"])
def test_project_paths_reject_unsafe_project_id(tmp_path, project_id):
    from src.infrastructure.files.project_library import ProjectPaths

    with pytest.raises(ValueError, match="project_id"):
        ProjectPaths.for_project(tmp_path / "library", project_id)
```

- [x] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_project_library.py -q`

Expected: FAIL with `ModuleNotFoundError: src.infrastructure.files.project_library`。

- [x] **Step 3: 实现设置与路径对象**

`src/domain/incubator.py` 定义：

```python
class IncubatorSettings(DomainModel):
    owner_name: NonEmptyStr
    library_root: NonEmptyStr
    current_project_id: NonEmptyStr | None = None


class ProjectSummary(DomainModel):
    project_id: NonEmptyStr
    name: NonEmptyStr
    stage: NonEmptyStr
    current_version: NonEmptyStr | None
    source_count: int = Field(ge=0)
    draft_count: int = Field(ge=0)
    updated_at: datetime
```

`ProjectPaths.for_project()` 必须使用正则 `^[A-Z0-9][A-Z0-9_-]{0,63}$`，对 `library_root` 和所有派生路径调用 `resolve()` 与 `is_relative_to()`。

- [x] **Step 4: 保持 Project 表兼容并增加项目列表接口**

不为 `projects` 表新增重复的 description/display version 字段：`Project.product_line` 保存产品类型或一句话说明，显示版本属于 Baseline。`Project` 现有字段保持兼容；新增仓库方法：

```python
def list_all(self) -> list[Project]:
    with connect(self.db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC, id"
        ).fetchall()
    return [self._to_model(row) for row in rows]
```

旧数据库迁移后必须保持 LLD 可读取；不得要求旧行回填虚假内容。

- [x] **Step 5: 实现项目库根目录定位顺序**

`ProjectLibraryLocator.resolve()` 按以下顺序取值，取到后统一 `expanduser().resolve()`：

```text
INCUBATOR_LIBRARY_ROOT 环境变量
→ data/local_state/incubator-root.json 中的 library_root
→ ~/Documents/产品文档孵化器项目库
```

`save_pointer()` 只写 `data/local_state/incubator-root.json`，使用临时文件＋`os.replace`；`.gitignore` 增加该文件。项目库中的 Owner 和当前项目仍保存在 `.incubator/settings.json`。

- [x] **Step 6: 运行领域、迁移和仓库测试**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_project_library.py tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py -q`

Expected: PASS，且旧迁移测试无回归。

- [x] **Step 7: 提交本任务**

```bash
git add .gitignore src/domain/incubator.py src/domain/models.py src/infrastructure/files/project_library.py src/infrastructure/db/migrations.py src/infrastructure/db/repositories.py tests/unit/domain/test_project_library.py tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py
git commit -m "feat: add multi-project library primitives"
```

### Task 2: 原子项目创建、切换与项目中心（1.5 人日）

**Files:**
- Create: `src/application/dto/projects.py`
- Create: `src/application/ports/incubator.py`
- Create: `src/application/use_cases/manage_projects.py`
- Create: `src/infrastructure/files/project_scaffolder.py`
- Create: `assets/incubator_schema/AGENTS.md`
- Create: `assets/incubator_schema/product-document-template.md`
- Create: `assets/incubator_schema/field-conventions.md`
- Create: `src/ui/pages/projects.py`
- Modify: `src/application/container.py`
- Test: `tests/integration/use_cases/test_manage_projects.py`
- Test: `tests/e2e/test_projects_page.py`

**Interfaces:**
- Consumes: `ProjectPaths`, `SqliteProjectRepository.add/list_all/get`。
- Produces: `ManageProjects.create/list/switch` 和 `AppContainer.manage_projects`。

- [x] **Step 1: 写创建成功、重复 ID 和失败清理测试**

```python
def test_create_project_scaffolds_complete_wiki_atomically(project_manager, library_root):
    project = project_manager.create(
        CreateProjectInput(
            project_id="NEW_PRODUCT",
            name="新产品",
            description="验证产品方案孵化",
            initial_display_version=None,
            allow_external_model=False,
        )
    )

    root = library_root / "NEW_PRODUCT"
    assert project.stage == "待初始化"
    assert (root / "raw").is_dir()
    assert (root / "wiki/current").is_dir()
    assert (root / "wiki/drafts").is_dir()
    assert (root / "wiki/versions").is_dir()
    assert (root / "schema/AGENTS.md").is_file()
    assert (root / ".incubator/project.json").is_file()
    assert not list(library_root.glob(".NEW_PRODUCT.tmp-*"))


def test_create_project_failure_leaves_no_registered_or_visible_project(
    project_manager, library_root, monkeypatch
):
    monkeypatch.setattr(project_manager.scaffolder, "commit", lambda *_: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(OSError, match="disk"):
        project_manager.create(new_project_command("BROKEN"))

    assert not (library_root / "BROKEN").exists()
    assert all(item.id != "BROKEN" for item in project_manager.projects.list_all())
```

- [x] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_manage_projects.py -q`

Expected: FAIL because `ManageProjects` and DTOs do not exist。

- [x] **Step 3: 实现项目 DTO、端口和 Scaffolder**

固定接口：

```python
class CreateProjectInput(BaseModel):
    project_id: str
    name: str
    description: str
    initial_display_version: str | None = None
    allow_external_model: bool = False


class ProjectSelection(BaseModel):
    project_id: str
    project_root: Path
```

`ProjectScaffolder.prepare()` 在项目库根目录建立 `.PROJECT.tmp-{uuid}`；复制三个 Schema 模板，写入空 `wiki/index.md`、追加式 `wiki/log.md`、`project.json` 和空 `source-index.json`。`commit()` 使用 `os.replace(temp_root, final_root)`；正式目录已存在时拒绝覆盖。

- [x] **Step 4: 实现 ManageProjects 的补偿事务**

执行顺序固定为：准备临时目录 → 校验 → 提交正式目录 → 数据库插入。数据库插入失败时将刚创建且仍为空业务状态的正式目录移动到 `.incubator/quarantine/PROJECT-{uuid}`，不能递归删除用户文件。

`switch(project_id)` 必须先验证数据库项目与正式目录同时存在，再原子更新 `.incubator/settings.json` 的 `current_project_id`。第一次进入项目中心时，如果 `.incubator/settings.json` 不存在，页面先收集 Owner 姓名和项目库路径；保存成功后才显示新建项目表单。

项目切换成功后调用 `clear_project_session_state()`，只保留白名单键 `incubator_owner`、`incubator_library_root`、`active_project_id` 和 Streamlit 页面对象；清除上传文件、候选、Query、Lint、缓存、决定和发布确认状态。页面测试必须先在 A 项目写入 `query_result`/`release_confirm`，切换 B 后断言这些键不存在。

- [x] **Step 5: 实现项目中心最小页面**

页面只包含：项目卡片、新建项目表单、进入项目按钮、本地路径。Streamlit key 必须包含项目 ID，例如 `project_open_NEW_PRODUCT`。没有项目时显示“新建第一个产品项目”。

- [x] **Step 6: 运行项目服务和页面测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_manage_projects.py tests/e2e/test_projects_page.py -q`

Expected: PASS；页面只有一个“创建项目”主按钮。

- [x] **Step 7: 执行 B1 专项回归**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_project_library.py tests/integration/db tests/integration/use_cases/test_manage_projects.py tests/e2e/test_projects_page.py -q`

Expected: PASS。

- [x] **Step 8: 提交并暂停确认 B1**

```bash
git add assets/incubator_schema src/application/dto/projects.py src/application/ports/incubator.py src/application/use_cases/manage_projects.py src/infrastructure/files/project_scaffolder.py src/ui/pages/projects.py src/application/container.py tests/integration/use_cases/test_manage_projects.py tests/e2e/test_projects_page.py
git commit -m "feat: add atomic project creation and switching"
```

暂停并向 Owner 汇报：实际人日、创建的目录、项目切换效果、已知限制；等待确认后进入 B2。

---

## Batch B2 — 原始材料归档与项目隔离（2.0 人日）

### Task 3: 当前项目组合根、Manifest 和缓存隔离（1.0 人日）

**Files:**
- Create: `src/application/project_context.py`
- Modify: `src/application/container.py`
- Modify: `src/infrastructure/cache/ai_cache.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/files/manifest_store.py`
- Modify: `src/infrastructure/files/query_material_reader.py`
- Modify: `src/infrastructure/files/baseline_card_reader.py`
- Modify: `src/ui/pages/home.py`
- Modify: `src/ui/pages/ingest.py`
- Modify: `src/ui/pages/query.py`
- Modify: `src/ui/pages/lint.py`
- Modify: `src/ui/pages/release.py`
- Modify: `src/ui/pages/trace.py`
- Test: `tests/unit/test_ai_cache.py`
- Test: `tests/unit/test_container_project_context.py`
- Create: `tests/integration/use_cases/test_project_isolation.py`

**Interfaces:**
- Consumes: `.incubator/settings.json` 的 `current_project_id` 与 `ProjectPaths`。
- Produces: `AppContainer.active_project`, `AppContainer.require_project_id()` 和项目作用域服务。

- [x] **Step 1: 写缓存键和容器上下文失败测试**

```python
def test_cache_key_is_project_scoped():
    common = dict(
        task_type="query",
        source_sha256="a" * 64,
        baseline_version="V1",
        prompt_version="P1",
        model_label="M1",
        schema_version="1.0",
        question="当前规则？",
    )
    assert CacheIdentity(project_id="A", **common).cache_key != CacheIdentity(
        project_id="B", **common
    ).cache_key


def test_container_without_active_project_exposes_only_project_management(container):
    assert container.active_project is None
    assert container.manage_projects is not None
    assert container.query is None
    with pytest.raises(RuntimeError, match="active project"):
        container.require_project_id()
```

- [x] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/unit/test_ai_cache.py tests/unit/test_container_project_context.py -q`

Expected: FAIL because `CacheIdentity` has no `project_id` and container has no active context。

- [x] **Step 3: 实现 ProjectContext 和组合根**

```python
@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    paths: ProjectPaths
    db_path: Path


def require_project_id(self) -> str:
    if self.active_project is None:
        raise RuntimeError("active project is required")
    return self.active_project.project_id
```

中央 DB 固定为 `{library_root}/.incubator/product_incubator.db`。Manifest、Wiki 和发布锁从 `active_project.paths` 解析。无当前项目时不构建 Query、Lint、发布和追溯服务。

- [x] **Step 4: 将缓存彻底项目化**

`CacheIdentity` 新增必填 `project_id`；`build_cache_key()` 将 `project_id` 作为第一项。`cache_entries` 新增 `project_id TEXT NOT NULL DEFAULT 'LLD'`，查询和写入同时校验。缓存文件仍以已经包含项目 ID 的哈希命名。

- [x] **Step 5: 将既有页面从配置项目改为活动项目**

机械替换 `container.settings.project_id` 为 `container.require_project_id()`；每个写入命令使用同一个局部变量 `project_id`。页面不得从 URL 或表单接收一个可覆盖活动项目的项目 ID。

- [x] **Step 6: 运行隔离测试和现有 Query/Lint 测试**

Run: `.venv/bin/python -m pytest tests/unit/test_ai_cache.py tests/unit/test_container_project_context.py tests/integration/use_cases/test_project_isolation.py tests/unit/application/test_run_query.py tests/integration/use_cases/test_run_lint.py -q`

Expected: PASS；A、B 相同材料和问题产生不同缓存键。

- [x] **Step 7: 提交本任务**

```bash
git add src/application/project_context.py src/application/container.py src/infrastructure/cache/ai_cache.py src/infrastructure/db/migrations.py src/infrastructure/files/manifest_store.py src/infrastructure/files/query_material_reader.py src/infrastructure/files/baseline_card_reader.py src/ui/pages/home.py src/ui/pages/ingest.py src/ui/pages/query.py src/ui/pages/lint.py src/ui/pages/release.py src/ui/pages/trace.py tests/unit/test_ai_cache.py tests/unit/test_container_project_context.py tests/integration/use_cases/test_project_isolation.py
git commit -m "refactor: scope runtime state to active project"
```

### Task 4: `raw/` 不可变归档、哈希去重和材料页面（1.0 人日）

**Files:**
- Create: `src/application/dto/documents.py`
- Create: `src/application/use_cases/archive_raw_source.py`
- Create: `src/infrastructure/files/source_index_store.py`
- Create: `src/infrastructure/files/project_source_archive.py`
- Create: `src/ui/pages/materials.py`
- Modify: `src/application/container.py`
- Modify: `src/application/ports/incubator.py`
- Test: `tests/integration/files/test_project_source_archive.py`
- Test: `tests/integration/use_cases/test_archive_raw_source.py`
- Test: `tests/e2e/test_materials_page.py`
- Test: `tests/security/test_project_path_isolation.py`

**Interfaces:**
- Consumes: `ProjectPaths.raw_root`、`SqliteSourceRepository`。
- Produces: `ArchiveRawSource.execute()` 和 `ArchivedSourceView`。

- [x] **Step 1: 写外部文件移走、重复归档和符号链接逃逸测试**

```python
def test_archived_copy_survives_original_move(archive_service, tmp_path):
    source = tmp_path / "outside/需求.md"
    source.parent.mkdir()
    source.write_text("# 产品需求\n\n内容", encoding="utf-8")

    result = archive_service.execute(command_for(source))
    source.rename(source.with_suffix(".moved"))

    assert result.archive_path.read_text(encoding="utf-8").startswith("# 产品需求")
    assert result.sha256 == sha256(result.archive_path.read_bytes()).hexdigest()


def test_same_hash_in_same_project_returns_existing_source(archive_service, source_file):
    first = archive_service.execute(command_for(source_file))
    second = archive_service.execute(command_for(source_file))
    assert second.duplicate is True
    assert second.source_id == first.source_id
```

- [x] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/integration/files/test_project_source_archive.py tests/integration/use_cases/test_archive_raw_source.py -q`

Expected: FAIL because project-scoped raw archive does not exist。

- [x] **Step 3: 新增 ProjectSourceArchive，不改动 1.x SourceArchive**

构造函数固定为：

```python
class ProjectSourceArchive:
    def __init__(self, *, paths: ProjectPaths, source_id: str, year: int): ...
```

写入目标固定为 `raw/{year}/{source_id}/{safe_filename}`。写入后重新读取字节并校验 SHA-256；哈希不一致时移动到 `.incubator/quarantine/` 并抛出 `ARCHIVE_HASH_MISMATCH`。现有 `src/infrastructure/files/archive.py::SourceArchive` 和 1.x 测试保持不变，仅供旧版兼容与迁移读取。

- [x] **Step 4: 实现 ArchiveRawSource 和原子来源索引**

`ArchiveRawSourceInput` 包含：`project_id`、`local_path`、材料类型、权威级别、部门、文档日期、显示版本、安全等级、脱敏确认和外调授权。成功顺序：归档 → 哈希复核 → SQLite 来源记录 → `source-index.json` 原子替换。索引失败时将来源状态设为 `index_failed`，不得显示为 Ingest 就绪。

- [x] **Step 5: 实现原始材料页面**

页面展示文件名、来源 ID、SHA-256 前 12 位、完整归档路径、材料类型和 Ingest 状态；只提供“选择文件并归档”主动作。操作系统打开目录不可靠时只提供可复制路径，不在本任务调用 GUI。

- [x] **Step 6: 运行文件、用例、页面和安全测试**

Run: `.venv/bin/python -m pytest tests/integration/files/test_project_source_archive.py tests/integration/use_cases/test_archive_raw_source.py tests/e2e/test_materials_page.py tests/security/test_project_path_isolation.py -q`

Expected: PASS；外部原文件移动后仍可读取归档副本。

- [x] **Step 7: 提交并暂停确认 B2**

```bash
git add src/application/dto/documents.py src/application/use_cases/archive_raw_source.py src/infrastructure/files/source_index_store.py src/infrastructure/files/project_source_archive.py src/application/container.py src/application/ports/incubator.py src/ui/pages/materials.py tests/integration/files/test_project_source_archive.py tests/integration/use_cases/test_archive_raw_source.py tests/e2e/test_materials_page.py tests/security/test_project_path_isolation.py
git commit -m "feat: archive immutable project source files"
```

暂停并向 Owner 展示两个项目的独立 `raw/` 目录、重复文件提示和本地路径；等待确认后进入 B3。

---

## Batch B3 — 首版与增量文档孵化（3.0 人日）

### Task 5: Document Workflow 契约和安全适配器（1.0 人日）

**Files:**
- Create: `src/infrastructure/gateways/document_gateway.py`
- Modify: `src/infrastructure/gateways/schemas.py`
- Modify: `src/infrastructure/gateways/composition.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/application/container.py`
- Modify: `src/domain/models.py`
- Modify: `src/infrastructure/observability/model_call_logger.py`
- Modify: `config/app.yaml`
- Modify: `.env.example`
- Create: `docs/runbook/dify-document-workflow.md`
- Test: `tests/integration/gateways/test_document_gateway.py`
- Test: `tests/integration/gateways/test_composition.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: 新环境变量 `DIFY_DOCUMENT_API_KEY`。
- Produces: `DocumentWorkflowGateway.generate_draft()` 和 `generate_suggestions()`。

- [ ] **Step 1: 写严格输出契约失败测试**

```python
def test_document_gateway_rejects_markdown_without_h1(mock_client):
    mock_client.result = {
        "schema_version": "2.0",
        "task_type": "document_draft",
        "document_markdown": "## 缺少主标题",
        "summary": "生成草稿",
        "missing_sections": [],
        "evidence_gaps": [],
        "source_ids": ["SRC-001"],
    }
    gateway = DocumentWorkflowGateway(mock_client, timeout_seconds=90)

    with pytest.raises(DomainError, match="DOCUMENT_OUTPUT_INVALID"):
        gateway.generate_draft(valid_draft_input())


def test_suggestion_input_contains_only_outlines(mock_client):
    gateway = DocumentWorkflowGateway(mock_client, timeout_seconds=90)
    gateway.generate_suggestions(valid_suggestion_input())
    assert "document_markdown" not in mock_client.last_inputs
    assert mock_client.last_inputs["reference_projects"][0] == {
        "project_id": "B",
        "headings": ["产品概述", "业务流程"],
    }
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/integration/gateways/test_document_gateway.py -q`

Expected: FAIL because Document gateway and schemas do not exist。

- [ ] **Step 3: 定义两个互斥契约**

`DocumentDraftWorkflowOutput` 字段固定为：

```python
schema_version: Literal["2.0"]
task_type: Literal["document_draft"]
document_markdown: Annotated[str, StringConstraints(min_length=20, max_length=200_000)]
summary: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
missing_sections: list[str] = Field(max_length=50)
evidence_gaps: list[str] = Field(max_length=50)
source_ids: list[str] = Field(min_length=1, max_length=100)
section_citations: list[SectionCitationOutput] = Field(min_length=1, max_length=200)
```

其中 `SectionCitationOutput` 固定包含 `heading`、`source_id`、`chunk_id`、`locator`、`excerpt`；`source_id/chunk_id` 必须来自本次输入的来源片段。网关必须拒绝未知来源、未知 chunk 和没有任何章节引用的草稿。

`StructureSuggestionWorkflowOutput` 只允许 `task_type="structure_suggestion"` 和最多 20 条 `{title, reason, reference_project_ids, confidence}`；禁止返回 `document_markdown`。

- [ ] **Step 4: 实现专用 Document Gateway**

Draft 输入包含项目元数据、Schema、当前 Markdown（首版为 `None`）和经过既有安全策略允许的来源片段。Suggestion 输入只包含项目 ID 与 H1/H2/H3 标题数组。两个方法分别使用 Pydantic 输出模型，输出不匹配时 fail closed。

- [ ] **Step 5: 增加配置但保持 1.x 可启动**

新增：

```yaml
timeouts:
  document_seconds: 90
```

`.env.example` 新增 `DIFY_DOCUMENT_API_KEY=`。Key 未配置时旧 Query、Ingest、Lint 继续可用，2.0 文档孵化按钮禁用并显示明确配置提示；2.0 完整验收环境必须配置该 Key。

`ModelCallLog.task_type` 增加 `document_draft` 和 `structure_suggestion`；两类调用继续记录项目、来源 ID、授权、脱敏、出站字符、Workflow run ID、耗时与错误码。结构建议的 `source_ids` 为空数组，日志不得保存标题正文或模型请求体。

- [ ] **Step 6: 写 Dify 导入手册**

手册必须列出两个输入模式、全部字段、输出 JSON 示例、90 秒超时、不得记录 Key、结构建议只接收标题的边界和真实冒烟步骤。

- [ ] **Step 7: 运行网关和配置测试**

Run: `.venv/bin/python -m pytest tests/integration/gateways/test_document_gateway.py tests/integration/gateways/test_composition.py tests/unit/test_config.py -q`

Expected: PASS；旧三 Workflow 配置测试仍通过。

- [ ] **Step 8: 提交本任务**

```bash
git add src/infrastructure/gateways/document_gateway.py src/infrastructure/gateways/schemas.py src/infrastructure/gateways/composition.py src/application/ports/incubator.py src/application/container.py src/domain/models.py src/infrastructure/observability/model_call_logger.py config/app.yaml .env.example docs/runbook/dify-document-workflow.md tests/integration/gateways/test_document_gateway.py tests/integration/gateways/test_composition.py tests/unit/test_config.py
git commit -m "feat: add governed document workflow contract"
```

### Task 6: 草稿持久化、Markdown 校验和孵化页面（2.0 人日）

**Files:**
- Create: `src/infrastructure/files/markdown_sections.py`
- Create: `src/infrastructure/files/document_store.py`
- Create: `src/application/use_cases/incubate_document.py`
- Create: `src/ui/pages/incubate.py`
- Modify: `src/domain/incubator.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `src/application/dto/documents.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/application/container.py`
- Test: `tests/unit/domain/test_markdown_sections.py`
- Test: `tests/integration/use_cases/test_incubate_document.py`
- Test: `tests/e2e/test_incubate_page.py`

**Interfaces:**
- Consumes: `DocumentWorkflowGateway.generate_draft()`、归档来源和当前 Manifest（可不存在）。
- Produces: `DocumentDraftRepository`、`IncubateDocument.execute()` 和可编辑候选文件。

- [ ] **Step 1: 写首版、增量和非法输出失败测试**

```python
def test_initial_incubation_creates_draft_without_current_baseline(env):
    draft = env.incubate.execute(
        IncubateDocumentInput(project_id="NEW", source_ids=["SRC-001"], requested_by="Owner")
    )
    assert draft.parent_version_id is None
    assert draft.status.value == "candidate_draft"
    assert (env.project_root / draft.markdown_path).read_text(encoding="utf-8").startswith("# ")
    assert not (env.project_root / "wiki/current/当前产品方案.md").exists()


def test_incremental_incubation_reads_current_and_never_overwrites_it(env_with_current):
    before = env_with_current.current_path.read_bytes()
    draft = env_with_current.incubate.execute(incremental_command())
    assert draft.parent_version_id == env_with_current.current_version
    assert env_with_current.current_path.read_bytes() == before
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_markdown_sections.py tests/integration/use_cases/test_incubate_document.py -q`

Expected: FAIL because draft models, repository and use case do not exist。

- [ ] **Step 3: 定义草稿表和领域模型**

新增表：

```sql
CREATE TABLE IF NOT EXISTS document_drafts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    version_id TEXT NOT NULL,
    display_version TEXT,
    parent_version_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('candidate_draft','pending_owner','published')),
    markdown_path TEXT NOT NULL,
    markdown_sha256 TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    section_citations_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    missing_sections_json TEXT NOT NULL,
    evidence_gaps_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, version_id)
);
```

版本 ID 由 `VersionIdFactory.next(project_id, now, existing_ids)` 生成 `{PROJECT}-{YYYYMMDD}-{NN}`。

- [ ] **Step 4: 实现 Markdown 结构校验**

`validate_product_markdown()` 必须拒绝：空文档、没有且仅有一个 H1、包含 `{{...}}`／`TODO`／`TBD` 系统占位符、UTF-8 编码失败。`extract_headings()` 只返回 H1/H2/H3 的纯文本标题。

- [ ] **Step 5: 实现 IncubateDocument**

固定顺序：验证项目 → 验证 source IDs 全属于项目 → 读取 Schema → 读取当前 Markdown（可空）→ 执行安全片段选择 → 调用 Document Workflow → 校验 Markdown → 使用临时文件写入 `wiki/drafts/{version}/产品方案.md` → 原子提交 → 写草稿表 → 追加 `wiki/log.md`。

模型失败、输出失败或文件失败均不能产生 `current/当前产品方案.md`。

- [ ] **Step 6: 实现候选编辑与 Diff 页面**

首版展示材料、章节、缺失项和证据缺口，不显示伪 Diff。增量版使用既有 `render_change_diff()` 展示当前全文与候选全文。保存草稿后重算 SHA-256 并把状态更新为 `pending_owner`；只有通过 `validate_product_markdown()` 才能启用发布按钮。

- [ ] **Step 7: 运行孵化用例和页面测试**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_markdown_sections.py tests/integration/use_cases/test_incubate_document.py tests/e2e/test_incubate_page.py -q`

Expected: PASS；首版和增量均不改写当前文档。

- [ ] **Step 8: 执行 B3 专项回归**

Run: `.venv/bin/python -m pytest tests/integration/gateways/test_document_gateway.py tests/integration/use_cases/test_incubate_document.py tests/e2e/test_incubate_page.py tests/integration/use_cases/test_import_source.py -q`

Expected: PASS。

- [ ] **Step 9: 提交并暂停确认 B3**

```bash
git add src/infrastructure/files/markdown_sections.py src/infrastructure/files/document_store.py src/application/use_cases/incubate_document.py src/ui/pages/incubate.py src/domain/incubator.py src/infrastructure/db/migrations.py src/infrastructure/db/repositories.py src/application/dto/documents.py src/application/ports/incubator.py src/application/container.py tests/unit/domain/test_markdown_sections.py tests/integration/use_cases/test_incubate_document.py tests/e2e/test_incubate_page.py
git commit -m "feat: incubate initial and incremental markdown drafts"
```

暂停并让 Owner 检查一个首版候选和一个增量 Diff；汇报 Document Workflow 质量、实际人日和剩余缓冲。

---

## Batch B4 — Owner 批准与原子发布（1.5 人日）

### Task 7: 初始／增量草稿发布、当前镜像和恢复（1.5 人日）

**Files:**
- Create: `src/application/use_cases/publish_document_draft.py`
- Modify: `src/domain/models.py`
- Modify: `src/infrastructure/files/document_store.py`
- Modify: `src/infrastructure/files/markdown_sections.py`
- Modify: `src/infrastructure/files/manifest_store.py`
- Modify: `src/infrastructure/recovery/reconciliation_service.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `src/application/dto/documents.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/incubate.py`
- Test: `tests/integration/use_cases/test_publish_document_draft.py`
- Test: `tests/integration/recovery/test_document_reconciliation.py`
- Test: `tests/e2e/test_document_publish_flow.py`

**Interfaces:**
- Consumes: `DocumentDraft(status='pending_owner')`。
- Produces: `PublishDocumentDraft.execute()`、不可变版本文件、当前镜像和 Manifest。

- [ ] **Step 1: 写首发、增量和故障回滚测试**

```python
def test_initial_publish_creates_first_current_without_parent(env_with_draft):
    baseline = env_with_draft.publish.execute(
        PublishDocumentDraftInput(
            project_id="NEW",
            draft_id=env_with_draft.draft.id,
            owner_name="产品经理",
            display_version="1_0",
        )
    )
    assert baseline.parent_baseline_id is None
    assert env_with_draft.current_path.read_bytes() == env_with_draft.version_path.read_bytes()
    assert env_with_draft.manifest.read_and_validate().current_version == baseline.version


def test_manifest_failure_keeps_previous_current(env_with_current_and_draft, monkeypatch):
    before = env_with_current_and_draft.current_path.read_bytes()
    monkeypatch.setattr(env_with_current_and_draft.manifest, "atomic_replace", raise_os_error)
    with pytest.raises(DomainError, match="RELEASE_FAILED"):
        env_with_current_and_draft.publish.execute(publish_command())
    assert env_with_current_and_draft.current_path.read_bytes() == before
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_publish_document_draft.py -q`

Expected: FAIL because `PublishDocumentDraft` does not exist。

- [ ] **Step 3: 增加显示版本并编译 Markdown 规则卡快照**

为 `Baseline` 和 `BaselineManifest` 增加向后兼容的 `display_version: NonEmptyStr | None = None`；为 `baselines` 表增加可空 `display_version TEXT`，仓库读写同步。1.x Manifest 缺少该字段时仍能解析，界面和导出回退到内部 `version/current_version`。

`compile_sections_to_cards()` 将每个 H2 及其正文编译为一张 `KnowledgeCard`；卡片 ID 使用 `{project_id}-SECTION-{sha256(title)[:12]}`，`product_version` 使用内部版本 ID。`source_refs` 必须由草稿 `section_citations` 编译为既有查询可识别的 `{source_id}:{chunk_id}`，且引用 heading 必须能对应到该 H2。没有 H2、章节没有合法引用或引用不属于本项目时拒绝发布，避免 Query/Lint 得到不可追溯卡片。

- [ ] **Step 4: 实现原子发布文件布局**

每次发布都生成不可变规范文件：

```text
wiki/versions/{version_id}/产品方案.md
wiki/versions/{version_id}/cards.json
```

`wiki/current/当前产品方案.md` 是规范版本文件的派生镜像。Manifest 指向不可变 `wiki/versions/{version_id}/...`，同时保存相同 Markdown 哈希；这样历史 Baseline 路径不会在下次发布时失效。读取和下载前必须核对镜像哈希等于 Manifest 文档哈希，若不一致则阻止写操作并由对账服务从 Manifest 指向的规范版本重建镜像。

- [ ] **Step 5: 实现初始和增量 Manifest**

首发允许 `parent_baseline_id=None`；增量发布要求草稿父版本等于 Manifest 当前版本。文件 staging 校验完成后，按“提交不可变版本目录 → 原子替换 Manifest（权威切换点）→ 原子同步 current 镜像 → 提交中央 DB”执行。Manifest 替换前失败时旧当前版保持有效；Manifest 替换后镜像或数据库失败时标记发布状态不确定并运行对账服务，从 Manifest 指向的规范版本重建镜像和项目当前状态，不得把新 Manifest 静默回滚成旧版本。

- [ ] **Step 6: 实现 Owner 确认对话框**

确认框显示项目、候选版本、显示版本、来源数和“发布后成为当前生效方案”。Owner 姓名取 `.incubator/settings.json`，不能由页面临时输入另一个批准人。

- [ ] **Step 7: 运行发布、恢复和 E2E 测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_publish_document_draft.py tests/integration/recovery/test_document_reconciliation.py tests/e2e/test_document_publish_flow.py -q`

Expected: PASS；注入 Manifest、镜像或 DB 故障时旧当前版保持有效。

- [ ] **Step 8: 运行既有发布回归**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_publish_baseline.py tests/e2e/test_release_flow.py tests/e2e/test_release_failure.py -q`

Expected: PASS，1.x 原子发布语义无回归。

- [ ] **Step 9: 提交并暂停确认 B4**

```bash
git add src/application/use_cases/publish_document_draft.py src/domain/models.py src/infrastructure/files/document_store.py src/infrastructure/files/markdown_sections.py src/infrastructure/files/manifest_store.py src/infrastructure/recovery/reconciliation_service.py src/infrastructure/db/migrations.py src/infrastructure/db/repositories.py src/application/dto/documents.py src/application/container.py src/ui/pages/incubate.py tests/integration/use_cases/test_publish_document_draft.py tests/integration/recovery/test_document_reconciliation.py tests/e2e/test_document_publish_flow.py
git commit -m "feat: publish owner-approved product documents atomically"
```

暂停并展示首发、增量、历史版本和故障回滚证据；Owner 确认后进入 B5。

---

## Batch B5 — 当前产品下载与 AI 完善建议（2.0 人日）

### Task 8: 当前产品、查询、历史和单 Markdown 下载（0.75 人日）

**Files:**
- Create: `src/application/use_cases/export_current_document.py`
- Create: `src/ui/pages/current_product.py`
- Modify: `src/application/dto/documents.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/application/container.py`
- Test: `tests/integration/use_cases/test_export_current_document.py`
- Test: `tests/e2e/test_current_product_page.py`

**Interfaces:**
- Consumes: 当前项目 Manifest 和 `wiki/current/当前产品方案.md`。
- Produces: `ExportedDocument(filename, content, sha256, export_path)`。

- [ ] **Step 1: 写字节一致和无当前版测试**

```python
def test_export_download_bytes_equal_current_markdown(env_with_current):
    exported = env_with_current.export.execute(
        ExportCurrentDocumentInput(project_id="LLD")
    )
    assert exported.filename == "蓝领贷_产品方案_724_1.md"
    assert exported.content == env_with_current.current_path.read_bytes()
    assert exported.export_path.read_bytes() == exported.content


def test_export_is_unavailable_without_current_baseline(env_without_current):
    with pytest.raises(DomainError, match="BASELINE_NOT_FOUND"):
        env_without_current.export.execute(ExportCurrentDocumentInput(project_id="NEW"))
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_export_current_document.py -q`

Expected: FAIL because export use case does not exist。

- [ ] **Step 3: 实现导出服务**

读取当前镜像并与 Manifest 哈希核对；文件名中的 `/\\:*?"<>|` 替换为 `_`。使用临时文件和 `os.replace` 写入 `exports/{filename}`。返回同一字节给 `st.download_button`，不得二次渲染 Markdown。

- [ ] **Step 4: 实现当前产品页面**

顶部展示项目、版本、Owner、时间、父版本；中部显示 Markdown 和既有 Query 表单；底部显示历史版本。无当前版本时禁用查询和下载并引导去“文档孵化”。

- [ ] **Step 5: 运行导出与页面测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_export_current_document.py tests/e2e/test_current_product_page.py -q`

Expected: PASS；下载对象只包含一个 `.md` 文件。

- [ ] **Step 6: 提交本任务**

```bash
git add src/application/use_cases/export_current_document.py src/ui/pages/current_product.py src/application/dto/documents.py src/application/ports/incubator.py src/application/container.py tests/integration/use_cases/test_export_current_document.py tests/e2e/test_current_product_page.py
git commit -m "feat: download the current product markdown"
```

### Task 9: 授权项目结构建议与检查页面（1.25 人日）

**Files:**
- Create: `src/application/use_cases/suggest_document_structure.py`
- Create: `src/ui/pages/checks.py`
- Modify: `src/domain/incubator.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `src/infrastructure/files/markdown_sections.py`
- Modify: `src/application/dto/documents.py`
- Modify: `src/application/container.py`
- Test: `tests/integration/use_cases/test_suggest_document_structure.py`
- Test: `tests/security/test_structure_suggestion_isolation.py`
- Test: `tests/e2e/test_checks_page.py`

**Interfaces:**
- Consumes: 当前项目与本次授权项目的 H1/H2/H3 标题数组。
- Produces: `StructureSuggestion` 列表和采纳后的候选修订任务。

- [ ] **Step 1: 写未授权排除、输入最小化和不改当前版测试**

```python
def test_suggestion_sends_only_explicitly_authorized_project_outlines(env):
    suggestions = env.service.execute(
        SuggestStructureInput(project_id="A", reference_project_ids=["B"], requested_by="Owner")
    )
    assert env.gateway.last_input["reference_projects"] == [
        {"project_id": "B", "headings": ["产品概述", "业务流程", "风险边界"]}
    ]
    assert "C" not in json.dumps(env.gateway.last_input, ensure_ascii=False)
    assert "document_markdown" not in env.gateway.last_input
    assert env.current_a.read_bytes() == env.current_a_before
    assert suggestions[0].reference_project_ids == ["B"]
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_suggest_document_structure.py tests/security/test_structure_suggestion_isolation.py -q`

Expected: FAIL because suggestion use case and table do not exist。

- [ ] **Step 3: 增加建议表和仓库**

```sql
CREATE TABLE IF NOT EXISTS structure_suggestions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    reason TEXT NOT NULL,
    reference_project_ids_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL CHECK (status IN ('open','accepted','ignored')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- [ ] **Step 4: 实现建议服务**

服务必须验证当前和参考项目存在、排除当前项目自身、去重授权清单、读取各项目 Manifest 指向的 Markdown、仅调用 `extract_headings()`。模型输入不得包含段落正文。接受建议时只把该建议状态更新为 `accepted`；`IncubateDocument` 在下一次执行时读取当前项目所有 `accepted` 建议标题作为 `requested_sections`，成功生成新候选后将对应建议保持 accepted 并记录 draft ID。这个状态组合就是“候选修订任务”，不新增另一个任务表，也不能写 `wiki/current/`。

- [ ] **Step 5: 合并检查页面**

页面上半区复用现有 Lint 服务；下半区显示授权项目多选、生成建议、采纳和忽略。发布后自动触发通过 Streamlit session flag 实现；失败只显示“建议生成失败，可稍后重试”，不回滚发布。

- [ ] **Step 6: 运行建议、安全和页面测试**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_suggest_document_structure.py tests/security/test_structure_suggestion_isolation.py tests/e2e/test_checks_page.py -q`

Expected: PASS；未授权项目标题也不得进入模型请求。

- [ ] **Step 7: 执行 B5 专项回归**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_export_current_document.py tests/integration/use_cases/test_suggest_document_structure.py tests/e2e/test_current_product_page.py tests/e2e/test_checks_page.py -q`

Expected: PASS。

- [ ] **Step 8: 提交并暂停确认 B5**

```bash
git add src/application/use_cases/suggest_document_structure.py src/ui/pages/checks.py src/domain/incubator.py src/infrastructure/db/migrations.py src/infrastructure/db/repositories.py src/infrastructure/files/markdown_sections.py src/application/dto/documents.py src/application/container.py tests/integration/use_cases/test_suggest_document_structure.py tests/security/test_structure_suggestion_isolation.py tests/e2e/test_checks_page.py
git commit -m "feat: suggest product document structure safely"
```

暂停并让 Owner 下载一次 Markdown、检查与附件风格一致性、验证未授权项目不参与建议。

---

## Batch B6 — 五入口收敛、LLD 迁移与全链验收（2.5 人日）

### Task 10: 五入口导航、品牌更名和 LLD 可重复迁移（1.0 人日）

**Files:**
- Create: `scripts/migrate_lld_to_v2.py`
- Modify: `src/ui/navigation.py`
- Modify: `src/ui/components/sidebar.py`
- Modify: `streamlit_app.py`
- Modify: `README.md`
- Test: `tests/unit/test_navigation.py`
- Test: `tests/e2e/test_sidebar.py`
- Test: `tests/integration/scripts/test_migrate_lld_to_v2.py`

**Interfaces:**
- Consumes: 1.x `data/source_archive/LLD`、`data/obsidian_vault`、Manifest 和中央 DB。
- Produces: 五路由导航和可 `--dry-run` 的幂等迁移脚本。

- [ ] **Step 1: 写导航和迁移演练失败测试**

```python
def test_v2_navigation_has_exactly_five_routes():
    from src.ui.navigation import get_page_definitions
    assert [(p.title, p.url_path) for p in get_page_definitions()] == [
        ("项目中心", "projects"),
        ("原始材料", "materials"),
        ("文档孵化", "incubate"),
        ("当前产品", "current-product"),
        ("检查与建议", "checks"),
    ]


def test_lld_dry_run_writes_nothing(tmp_path, legacy_fixture):
    before = snapshot_tree(tmp_path)
    result = migrate_lld(legacy_fixture, tmp_path / "library", dry_run=True)
    assert result.status == "DRY_RUN_OK"
    assert snapshot_tree(tmp_path) == before
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest tests/unit/test_navigation.py tests/integration/scripts/test_migrate_lld_to_v2.py -q`

Expected: FAIL because navigation still has six 1.x routes and migration script is absent。

- [ ] **Step 3: 收敛为五个一级入口**

导航严格使用五个页面。既有 Query 作为“当前产品”内部区块，Lint 作为“检查与建议”内部区块，发布作为“文档孵化”内部动作。隐藏旧一级路由，但不删除底层服务和 1.x 页面模块。

- [ ] **Step 4: 完成 2.0 品牌更名**

`streamlit_app.py` 页标题、侧栏 wordmark、README 当前版本说明改为“产品文档孵化器”。历史文档、测试报告、冻结标签、视频说明和 1.x 截图不改名。

- [ ] **Step 5: 实现迁移脚本**

CLI 固定为：

```text
.venv/bin/python scripts/migrate_lld_to_v2.py \
  --source-root . \
  --library-root "$HOME/Documents/产品文档孵化器项目库" \
  --dry-run
```

正式模式先将 LLD 迁移到临时目录：复制当前 Manifest 指向的产品 Markdown/cards.json；复制 LLD 全部来源归档并复核 SHA-256；在中央 DB 新建 LLD Project、当前 Baseline、有效 KnowledgeCard 和 SourceRecord。1.x 的缓存、开放问题、候选变更、演示决定和模型调用日志不迁移，它们继续保留在 1.x 历史库。校验新 Manifest、当前镜像、规范版本、来源哈希和数据库镜像后再提交 `LLD/`。目标已完成且哈希一致时返回 `ALREADY_MIGRATED`；不一致时拒绝覆盖。不得写入 `data/demo_snapshots/`。

- [ ] **Step 6: 运行导航、侧栏和迁移测试**

Run: `.venv/bin/python -m pytest tests/unit/test_navigation.py tests/e2e/test_sidebar.py tests/integration/scripts/test_migrate_lld_to_v2.py -q`

Expected: PASS；dry-run 零写入，正式迁移重复执行幂等。

- [ ] **Step 7: 提交本任务**

```bash
git add scripts/migrate_lld_to_v2.py src/ui/navigation.py src/ui/components/sidebar.py streamlit_app.py README.md tests/unit/test_navigation.py tests/e2e/test_sidebar.py tests/integration/scripts/test_migrate_lld_to_v2.py
git commit -m "feat: migrate LLD into the product document incubator"
```

### Task 11: 双项目全链、安全门禁、文档和最终验收（1.5 人日）

**Files:**
- Create: `tests/e2e/test_incubator_full_success.py`
- Create: `tests/e2e/test_incubator_restart_recovery.py`
- Create: `tests/security/test_incubator_cross_project_isolation.py`
- Create: `scripts/validate_incubator.py`
- Create: `docs/runbook/incubator-operation.md`
- Create: `docs/qa/product-document-incubator-2.0-acceptance.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–10 全部公开接口。
- Produces: A01–A10 可复验的最终证据和操作手册。

- [ ] **Step 1: 写双项目完整成功 E2E**

测试必须真实执行以下顺序，模型侧使用严格契约 MockTransport：

```python
def test_two_projects_complete_isolated_incubation_flows(harness):
    a = harness.create_project("A", "产品A")
    b = harness.create_project("B", "产品B")
    source_a = harness.archive(a, "A需求.md", b"# A需求\n\nA内容")
    source_b = harness.archive(b, "B需求.md", b"# B需求\n\nB内容")
    draft_a = harness.incubate(a, [source_a.id])
    draft_b = harness.incubate(b, [source_b.id])
    baseline_a = harness.publish(a, draft_a.id, display_version="1_0")
    baseline_b = harness.publish(b, draft_b.id, display_version="1_0")
    assert harness.export(a).content != harness.export(b).content
    assert baseline_a.project_id == "A"
    assert baseline_b.project_id == "B"
    assert harness.project_paths(a).manifest_path.read_bytes() != harness.project_paths(b).manifest_path.read_bytes()
```

- [ ] **Step 2: 写重启恢复和破坏性隔离测试**

覆盖：活动项目恢复、草稿保存、Manifest／DB 不一致阻止发布、`../` 路径、项目符号链接逃逸、缓存串用、未授权结构建议、B 发布不改变 A 文件哈希。

- [ ] **Step 3: 运行新 E2E，确认任何失败都来自真实缺口**

Run: `.venv/bin/python -m pytest tests/e2e/test_incubator_full_success.py tests/e2e/test_incubator_restart_recovery.py tests/security/test_incubator_cross_project_isolation.py -q`

Expected: PASS。若失败，回到对应任务修复，不能在测试中降低断言。

- [ ] **Step 4: 实现独立验证脚本**

`validate_incubator.py --library-root PATH` 检查：设置、中央 DB、每项目目录、Manifest、当前镜像、规范版本哈希、来源索引、raw 文件哈希、唯一当前版本和越界路径。成功输出：

```text
INCUBATOR_VALIDATION_OK projects={N} current_projects={M} sources={S}
```

- [ ] **Step 5: 编写操作手册**

手册包含：首次设置、新建项目、归档材料、首版孵化、增量 Diff、Owner 发布、Markdown 下载、授权结构建议、LLD 迁移、备份和异常恢复。命令同时给出 `.venv/bin/python` 形式，不假设全局 `uv` 存在。

- [ ] **Step 6: 运行完整自动验证**

```text
.venv/bin/python -m pytest -q
.venv/bin/coverage run -m pytest
.venv/bin/coverage report --include='src/domain/*,src/application/*'
.venv/bin/ruff check src scripts tests streamlit_app.py
.venv/bin/ruff format --check src scripts tests streamlit_app.py
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

Expected:

- 全量测试 0 failed；
- `src/domain/*,src/application/*` 总覆盖率不低于 90%；
- Ruff、format、compileall、diff check 全部通过。

- [ ] **Step 7: 执行迁移演练与双项目浏览器验收**

1. 对临时项目库运行 LLD `--dry-run`；
2. 正式迁移到临时项目库；
3. 执行 `validate_incubator.py`；
4. 浏览器 1440×1024 依次检查五个页面；
5. 创建第二项目，完成归档、首发、增量、下载和建议；
6. 记录截图和命令结果到验收报告。

- [ ] **Step 8: 对照 A01–A10 填写最终验收报告**

每项记录 `PASS/FAIL`、命令、证据路径和版本 SHA。实际投入按 B1–B6 分项记录；若超过 15 人日，验收必须为 FAIL 并回到 Owner 做范围裁减，不能用“功能完成”覆盖成本门禁。

- [ ] **Step 9: 提交并暂停确认 B6**

```bash
git add tests/e2e/test_incubator_full_success.py tests/e2e/test_incubator_restart_recovery.py tests/security/test_incubator_cross_project_isolation.py scripts/validate_incubator.py docs/runbook/incubator-operation.md docs/qa/product-document-incubator-2.0-acceptance.md README.md
git commit -m "test: verify product document incubator 2.0"
```

暂停并向 Owner 汇报 A01–A10、最终 SHA、实际总人日、剩余风险和是否准许 2.0 首期通过。

---

## Verification Matrix

| 产品规格要求 | 实现任务 | 最终证据 |
| --- | --- | --- |
| 多项目创建、切换、自动建库 | Task 1–2 | A01、`test_manage_projects.py` |
| 中央 DB＋项目文件隔离 | Task 3 | A07、`test_project_isolation.py` |
| `raw/` 复制、不可变、哈希去重 | Task 4 | A02、`test_archive_raw_source.py` |
| 首版候选产品方案 | Task 5–6 | A03、`test_incubate_document.py` |
| 增量候选、Markdown 编辑、Diff | Task 6 | A04 前半、`test_incubate_page.py` |
| Owner 原子发布和恢复 | Task 7 | A04/A09、发布与恢复测试 |
| 当前 Markdown 单文件下载 | Task 8 | A05、字节一致测试 |
| 授权项目结构完善建议 | Task 9 | A06/A07、建议隔离测试 |
| 五个一级入口与 2.0 更名 | Task 10 | 导航和侧栏测试 |
| LLD 迁移且不改历史材料 | Task 10 | A08、迁移幂等测试 |
| 双项目全链、重启和安全 | Task 11 | A07/A09、最终 E2E |
| 15 人日成本门禁 | 每批记录，Task 11 汇总 | A10、最终验收报告 |

## Stop Conditions and Scope Cuts

任何批次触发成本或架构停线条件时，按以下顺序向 Owner提出裁减，不得自行执行：

1. 发布后自动建议改为手动点击；
2. 不生成 `wiki/topics/`；
3. 取消历史版本 Diff，只保留 Markdown 查看；
4. 只显示和复制本地路径，不调用操作系统打开目录；
5. 结构建议只比较 H1/H2；
6. 首期只支持 `.md/.txt`。

四项不可裁减能力发生根本架构冲突时，停止开发并重新评审 2.0 范围，不以临时旁路交付。
