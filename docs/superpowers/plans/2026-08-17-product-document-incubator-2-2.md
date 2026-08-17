# Product Document Incubator 2.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留中央 SQLite 的前提下，让项目内容安全分布在本机不同目录，并以标准 Wiki-LLM Ingest 将已归档材料编译为可追溯 Wiki，再从 Wiki 孵化产品文档。

**Architecture:** 采用“中央控制面 + 独立项目内容根”。中央数据库登记项目绝对路径和运行状态，所有文件服务通过统一 `ProjectPathResolver` 获得项目根；L1/L2 外部生成和 L3/L4 本地编辑最终都转换为 `WikiChangeSet`，由带项目锁、暂存、备份和恢复日志的事务协调器提交。

**Tech Stack:** Python 3.11–3.12、Pydantic 2、SQLite、Streamlit 1.60、filelock、httpx/Dify Gateway、pytest 9、Ruff。

## Global Constraints

- 技术规格唯一基线：`docs/superpowers/specs/2026-08-17-product-document-incubator-2-2-architecture-design.md`。
- 产品范围基线：`产品文档孵化器_2.2迭代产品方案_v1.0.md`，文档内版本 v1.1。
- 保留中央数据库 `{control_root}/.incubator/product_incubator.db`；不得创建项目内 SQLite。
- 新项目根严格等于 `{Owner 选择的父目录}/{项目ID}`；目标已存在时禁止覆盖、合并或接管。
- 2.2 只支持本机移动后重新定位，不支持跨电脑直接迁移。
- Raw 永久只读；Ingest 前后字节和 SHA-256 必须不变。
- L3/L4 以及未授权内容的外部 Gateway 调用次数必须为零。
- 外部 Ingest 不得接收完整 index 或含 L3/L4/未授权来源的主题正文，只接收来源级授权过滤后的安全投影。
- Ingest 不得修改 `wiki/current/`、`wiki/versions/`、产品候选或发布 Manifest。
- 旧 `ImportSource`、Query/Lint 比赛演示链路保持兼容，不得改造成 2.2 Wiki Ingest。
- 不新增一级导航；Ingest 操作位于“原始材料”。
- 不新增向量数据库、后台队列、批量 Ingest、在线 Wiki 编辑器或自动三方合并。
- 预计 10.5 人日，封顶 12 人日；超出前必须请求 Owner 裁减非核心能力。
- 当前工作区已有其他用户修改；每次提交只暂存本任务列出的文件。
- 每个 Task 完成并提交后必须停止，向 Owner 汇报“完成内容、验证证据、下一 Task”，等待确认后继续。

---

## 一、文件结构与职责锁定

### 新增文件

| 文件 | 单一职责 |
| --- | --- |
| `src/domain/wiki.py` | Wiki Ingest 状态、页面变更、变更集、运行和结果模型 |
| `src/application/dto/wiki_ingest.py` | 外部 Ingest、本地草稿、确认和恢复命令 DTO |
| `src/application/ports/wiki_ingest.py` | Gateway、事务、上下文和运行仓储端口 |
| `src/application/use_cases/ingest_archived_source.py` | L1/L2 单来源 Wiki Ingest 编排 |
| `src/application/use_cases/prepare_local_wiki_ingest.py` | L3/L4 本地 Markdown 草稿生成 |
| `src/application/use_cases/confirm_local_wiki_ingest.py` | L3/L4 草稿校验与确认 |
| `src/application/use_cases/recover_wiki_transaction.py` | 打开项目时恢复中断事务 |
| `src/infrastructure/files/project_path_resolver.py` | 中央路径登记到安全 `ProjectPaths` 的唯一解析器 |
| `src/infrastructure/files/wiki_store.py` | Wiki 页面读取、来源页路径和确定性 index/log 构建 |
| `src/infrastructure/files/wiki_validator.py` | 路径、Markdown、Frontmatter、引用和链接校验 |
| `src/infrastructure/files/wiki_change_set_store.py` | 事务暂存、备份、替换、回滚和恢复 |
| `src/infrastructure/files/wiki_outbound_context.py` | 按来源授权生成允许外发的 Wiki 安全投影 |
| `src/infrastructure/files/wiki_context_reader.py` | 为产品文档孵化读取已 Ingest Wiki 上下文 |
| `src/infrastructure/gateways/wiki_ingest_gateway.py` | 2.2 Wiki Ingest Gateway 适配器 |
| `tests/unit/domain/test_wiki.py` | 状态、幂等和变更集领域规则 |
| `tests/unit/infrastructure/test_project_path_resolver.py` | 项目路径与身份校验 |
| `tests/unit/infrastructure/test_wiki_validator.py` | Wiki 路径、链接和模板校验 |
| `tests/integration/files/test_wiki_transaction.py` | 提交、回滚、崩溃恢复和并发 |
| `tests/integration/use_cases/test_wiki_ingest.py` | L1/L2 标准流程 |
| `tests/integration/use_cases/test_local_wiki_ingest.py` | L3/L4 本地流程 |
| `tests/security/test_wiki_project_isolation.py` | 跨项目与路径攻击 |
| `tests/security/test_wiki_outbound_projection.py` | 敏感内容零外发 |
| `tests/e2e/test_wiki_incubation_flow.py` | 归档、Ingest、Wiki 和孵化完整流程 |
| `docs/qa/product-document-incubator-2.2-acceptance.md` | AC-01～AC-29 验收证据 |

### 主要修改文件

| 文件 | 修改目的 |
| --- | --- |
| `src/domain/models.py`、`incubator.py`、`enums.py`、`errors.py` | 项目路径、来源 Ingest 字段、页面摘要和稳定错误码 |
| `src/application/dto/projects.py` | 创建父目录与重新定位命令 |
| `src/application/ports/incubator.py` | 项目重新定位和路径状态接口 |
| `src/application/use_cases/manage_projects.py` | 独立目录创建、列表、切换和重新定位 |
| `src/application/use_cases/archive_raw_source.py` | 2.2 新项目归档后进入 `pending_ingest` |
| `src/application/use_cases/incubate_document.py` | 从 Wiki 而非 Raw 构建上下文 |
| `src/infrastructure/db/migrations.py`、`repositories.py` | 2.2 字段、运行表和仓储 |
| `src/infrastructure/files/project_library.py`、`project_scaffolder.py` | 显式项目根和完整 2.2 脚手架 |
| `src/infrastructure/files/source_index_store.py`、`project_audit_log.py` | 2.2 镜像字段和事务化 Ingest 日志 |
| `src/infrastructure/gateways/schemas.py` | Wiki Ingest 输入输出 Schema |
| `src/application/project_context.py`、`container.py` | 中央 DB 与独立项目根服务装配 |
| `src/ui/pages/projects.py`、`materials.py`、`incubate.py` | Owner 页面流程 |
| `assets/incubator_schema/*` | 根入口和 2.2 Ingest 模板 |

### 明确不修改语义

- `src/application/use_cases/import_source.py`
- `src/application/use_cases/publish_document_draft.py`
- `src/application/use_cases/export_current_document.py`
- 当前产品单 Markdown 导出
- 2.1 材料分类、系列和版本链

---

## 二、任务与投入

| Task | 节点 | 人日 |
| ---: | --- | ---: |
| 1 | 中央数据库项目路径字段与仓储 | 0.75 |
| 2 | 统一项目路径解析器 | 0.75 |
| 3 | 2.2 项目脚手架 | 0.75 |
| 4 | 独立目录创建、重新定位与项目页面 | 1.00 |
| 5 | Wiki 领域模型、DTO 和校验器 | 0.75 |
| 6 | Ingest 持久化、状态与 source-index 2.2 | 1.00 |
| 7 | Wiki 多文件事务、回滚与恢复 | 2.00 |
| 8 | 安全投影和 Wiki Ingest Gateway | 0.75 |
| 9 | L1/L2 标准 Ingest 与材料页面 | 1.00 |
| 10 | L3/L4 本地 Ingest 与页面确认 | 0.75 |
| 11 | Wiki 驱动的产品文档孵化 | 0.50 |
| 12 | 端到端、安全回归与验收报告 | 0.50 |
| **合计** |  | **10.50** |

### 规格覆盖自检

| 技术规格范围 | 实施 Task |
| --- | --- |
| 中央控制面、项目路径字段和回填 | 1 |
| 显式项目根、路径隔离和重新定位 | 2、4 |
| README、AGENTS、Wiki/Schema 脚手架 | 3 |
| Wiki 状态、变更集和稳定错误 | 5 |
| source-index、Ingest 运行和数据库迁移 | 6 |
| 项目锁、暂存、备份、回滚和崩溃恢复 | 7 |
| L1/L2 安全投影和外部 Gateway | 8、9 |
| L3/L4 Obsidian 本地编辑和零外发 | 10 |
| Wiki 驱动的产品文档孵化 | 11 |
| UI、项目隔离、兼容、AC-01～AC-29 和全量门禁 | 4、9、10、11、12 |

自检未发现规格缺口；跨电脑迁移、项目内数据库、在线 Wiki 编辑器、批量 Ingest 和自动三方合并均保持在范围外。

---

### Task 1: 中央数据库项目路径字段与仓储

**Files:**
- Modify: `src/domain/enums.py`
- Modify: `src/domain/models.py`
- Modify: `src/domain/incubator.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `src/application/ports/incubator.py`
- Test: `tests/unit/domain/test_models.py`
- Test: `tests/integration/db/test_migrations.py`
- Test: `tests/integration/db/test_repositories.py`

**Interfaces:**
- Consumes: 现有 `Project`、`ProjectSummary`、`SqliteProjectRepository` 和 `migrate(db_path)`。
- Produces: `ProjectRootStatus`；`Project.project_root_path/root_status/root_last_verified_at`；`SqliteProjectRepository.update_root_location(project_id, project_root, status, verified_at)`。

- [ ] **Step 1: 写入失败测试，锁定领域字段和历史默认值**

```python
def test_project_accepts_registered_root_location(project_factory, tmp_path):
    project = project_factory(
        project_root_path=str(tmp_path / "PROJECT_A"),
        root_status=ProjectRootStatus.AVAILABLE,
        root_last_verified_at=None,
    )
    assert project.project_root_path == str(tmp_path / "PROJECT_A")
    assert project.root_status is ProjectRootStatus.AVAILABLE


def test_legacy_project_defaults_to_unregistered_root(project_factory):
    project = project_factory()
    assert project.project_root_path is None
    assert project.root_status is ProjectRootStatus.UNAVAILABLE
```

- [ ] **Step 2: 运行领域测试并确认失败**

Run: `.venv/bin/pytest tests/unit/domain/test_models.py -q`

Expected: FAIL，提示 `ProjectRootStatus` 或项目路径字段不存在。

- [ ] **Step 3: 增加最小领域类型**

```python
class ProjectRootStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class Project(DomainModel):
    id: NonEmptyStr
    name: NonEmptyStr
    product_line: NonEmptyStr
    stage: NonEmptyStr
    current_baseline_id: NonEmptyStr | None
    allow_external_model: bool
    created_at: datetime
    updated_at: datetime
    project_root_path: NonEmptyStr | None = None
    root_status: ProjectRootStatus = ProjectRootStatus.UNAVAILABLE
    root_last_verified_at: datetime | None = None
```

`ProjectSummary` 增加同名展示字段，`root_last_verified_at` 允许为空。

- [ ] **Step 4: 写入迁移和仓储失败测试**

```python
def test_migrate_adds_2_2_project_location_and_backfills_existing_project(tmp_path):
    db_path = tmp_path / ".incubator/product_incubator.db"
    migrate(db_path)
    insert_legacy_project(db_path, "PROJECT_A")
    (tmp_path / "PROJECT_A/.incubator").mkdir(parents=True)
    write_project_json(tmp_path / "PROJECT_A", project_id="PROJECT_A", schema_version="2.1")
    migrate(db_path)
    project = SqliteProjectRepository(db_path).get("PROJECT_A")
    assert project.project_root_path == str((tmp_path / "PROJECT_A").resolve())
    assert project.root_status is ProjectRootStatus.AVAILABLE


def test_repository_updates_root_location_atomically(project_repository, tmp_path, now):
    project_repository.update_root_location(
        "PROJECT_A", tmp_path / "moved/PROJECT_A", ProjectRootStatus.AVAILABLE, now
    )
    saved = project_repository.get("PROJECT_A")
    assert saved.project_root_path == str((tmp_path / "moved/PROJECT_A").resolve())
    assert saved.root_last_verified_at == now
```

- [ ] **Step 5: 实现幂等迁移和仓储更新**

新增列：

```sql
project_root_path TEXT
root_status TEXT NOT NULL DEFAULT 'unavailable'
root_last_verified_at TEXT
```

在 `migrate()` 中以 `db_path.parent.parent` 作为中央控制根，仅为 NULL 历史记录回填 `{control_root}/{project_id}`；存在且项目 ID 匹配时为 `available`，否则为 `unavailable`。插入 schema migration `2.2`。

仓储方法完整签名为 `update_root_location(project_id: str, project_root: Path, status: ProjectRootStatus, verified_at: datetime | None) -> None`。

- [ ] **Step 6: 运行 Task 1 测试**

Run: `.venv/bin/pytest tests/unit/domain/test_models.py tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py -q`

Expected: PASS，0 failures。

- [ ] **Step 7: 运行格式和静态检查**

Run: `.venv/bin/ruff check src/domain src/infrastructure/db tests/unit/domain/test_models.py tests/integration/db`

Expected: PASS。

- [ ] **Step 8: 仅提交 Task 1 文件**

```bash
git add src/domain/enums.py src/domain/models.py src/domain/incubator.py \
  src/application/ports/incubator.py src/infrastructure/db/migrations.py \
  src/infrastructure/db/repositories.py tests/unit/domain/test_models.py \
  tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py
git commit -m "feat: register independent project roots"
```

**Owner checkpoint:** 停止并报告迁移字段、历史回填结果和测试证据；确认后进入 Task 2。

---

### Task 2: 统一项目路径解析器

**Files:**
- Modify: `src/infrastructure/files/project_library.py`
- Create: `src/infrastructure/files/project_path_resolver.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/domain/errors.py`
- Test: `tests/unit/domain/test_project_library.py`
- Create: `tests/unit/infrastructure/test_project_path_resolver.py`
- Test: `tests/security/test_project_path_isolation.py`

**Interfaces:**
- Consumes: Task 1 的 `SqliteProjectRepository.get/update_root_location` 和 `ProjectRootStatus`。
- Produces: `ProjectPaths.for_registered_root(library_root, project_id, project_root)`；`ProjectPathResolver.resolve/validate_parent/validate_relocation`。

- [ ] **Step 1: 写入显式项目根和隔离失败测试**

```python
def test_paths_accept_registered_root_outside_control_root(tmp_path):
    control = tmp_path / "control"
    project_root = tmp_path / "external/PROJECT_A"
    paths = ProjectPaths.for_registered_root(control, "PROJECT_A", project_root)
    assert paths.library_root == control.resolve()
    assert paths.project_root == project_root.resolve()
    assert paths.raw_root == project_root.resolve() / "raw"


def test_registered_root_rejects_symlink_and_derived_escape(tmp_path):
    target = tmp_path / "real/PROJECT_A"
    target.mkdir(parents=True)
    link = tmp_path / "linked-project"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="project root must not be a symlink"):
        ProjectPaths.for_registered_root(tmp_path / "control", "PROJECT_A", link)
```

- [ ] **Step 2: 运行路径测试并确认失败**

Run: `.venv/bin/pytest tests/unit/domain/test_project_library.py -q`

Expected: FAIL，`for_registered_root` 不存在。

- [ ] **Step 3: 实现显式项目根构造**

```python
@classmethod
def for_registered_root(
    cls, library_root: Path, project_id: str, project_root: Path
) -> "ProjectPaths":
    validate_project_id(project_id)
    lexical_root = project_root.expanduser().absolute()
    if lexical_root.is_symlink():
        raise ValueError("project root must not be a symlink")
    resolved_root = lexical_root.resolve()
    return cls._build(library_root.resolve(), project_id, resolved_root)
```

`_build()` 对 raw/wiki/schema/exports/system/manifest 每个派生路径执行 `is_relative_to(resolved_root)`。

- [ ] **Step 4: 写入 resolver 失败测试**

```python
def test_resolver_marks_missing_root_unavailable(repository, tmp_path, now):
    seed_project(repository, "PROJECT_A", tmp_path / "missing/PROJECT_A")
    resolver = ProjectPathResolver(tmp_path / "control", repository, now=lambda: now)
    with pytest.raises(DomainError, match="PROJECT_ROOT_UNAVAILABLE"):
        resolver.resolve("PROJECT_A")
    assert repository.get("PROJECT_A").root_status is ProjectRootStatus.UNAVAILABLE


def test_validate_relocation_requires_matching_project_json(repository, tmp_path):
    wrong = create_project_tree(tmp_path / "PROJECT_B", project_id="PROJECT_B")
    resolver = ProjectPathResolver(tmp_path / "control", repository)
    with pytest.raises(DomainError, match="PROJECT_ROOT_ID_MISMATCH"):
        resolver.validate_relocation("PROJECT_A", wrong)
```

- [ ] **Step 5: 实现 resolver 三个接口**

`ProjectPathResolver` 的固定签名为：

```text
resolve(project_id: str) -> ProjectPaths
validate_parent(parent_root: Path, project_id: str) -> Path
validate_relocation(project_id: str, project_root: Path) -> ProjectPaths
```

`validate_parent` 要求父目录存在、是目录且可写；目标 `{parent}/{project_id}` 必须不存在。`validate_relocation` 校验 `.incubator/project.json` 的 project ID 和必需目录，但不写数据库。

同时在 `ErrorCode`/`ERROR_CATALOG` 增加 `PROJECT_ROOT_UNAVAILABLE`、`PROJECT_ROOT_ID_MISMATCH`、`PROJECT_ROOT_NOT_WRITABLE` 和 `PROJECT_ROOT_ALREADY_EXISTS`，resolver 抛出 `DomainError`，页面只显示安全用户文案。

- [ ] **Step 6: 运行路径与安全测试**

Run: `.venv/bin/pytest tests/unit/domain/test_project_library.py tests/unit/infrastructure/test_project_path_resolver.py tests/security/test_project_path_isolation.py -q`

Expected: PASS。

- [ ] **Step 7: 提交 Task 2**

```bash
git add src/infrastructure/files/project_library.py src/domain/errors.py \
  src/infrastructure/files/project_path_resolver.py src/application/ports/incubator.py \
  tests/unit/domain/test_project_library.py \
  tests/unit/infrastructure/test_project_path_resolver.py \
  tests/security/test_project_path_isolation.py
git commit -m "feat: resolve registered project paths safely"
```

**Owner checkpoint:** 停止并演示一个项目位于中央控制根之外仍能解析，以及符号链接/错误项目 ID 被拒绝。

---

### Task 3: 2.2 项目脚手架

**Files:**
- Modify: `src/infrastructure/files/project_scaffolder.py`
- Create: `assets/incubator_schema/root-README.md`
- Create: `assets/incubator_schema/root-AGENTS.md`
- Create: `assets/incubator_schema/ingest-contract.md`
- Create: `assets/incubator_schema/source-page-template.md`
- Create: `assets/incubator_schema/topic-page-template.md`
- Modify: `assets/incubator_schema/AGENTS.md`
- Test: `tests/integration/use_cases/test_manage_projects.py`
- Test: `tests/integration/scripts/test_validate_incubator.py`

**Interfaces:**
- Consumes: Task 2 的 `ProjectPaths.for_registered_root`。
- Produces: `ProjectScaffolder.prepare(command, parent_root)`；完整 2.2 项目目录；Schema 2.2 `project.json`。

- [ ] **Step 1: 写入完整脚手架失败测试**

```python
def test_scaffolder_builds_complete_2_2_wiki_llm_tree(tmp_path, schema_assets, now):
    parent = tmp_path / "projects"
    parent.mkdir()
    scaffolder = ProjectScaffolder(
        library_root=tmp_path / "control", schema_source=schema_assets, now=lambda: now
    )
    prepared = scaffolder.prepare(project_command("PROJECT_A"), parent_root=parent)
    required = {
        "README.md", "AGENTS.md", "wiki/sources", "wiki/drafts/local-ingest",
        "schema/ingest-contract.md", "schema/source-page-template.md",
        "schema/topic-page-template.md", ".incubator/transactions", ".incubator/locks",
    }
    assert all((prepared.temp_root / item).exists() for item in required)
    assert json.loads((prepared.temp_root / ".incubator/project.json").read_text())["schema_version"] == "2.2"
```

- [ ] **Step 2: 运行测试并确认缺少入口和模板**

Run: `.venv/bin/pytest tests/integration/use_cases/test_manage_projects.py -q`

Expected: FAIL，缺少 2.2 必需路径或 `prepare(command, parent_root=parent)` 参数。

- [ ] **Step 3: 编写可信模板内容**

根 README 必须包含：项目身份、目录职责、`归档 → Ingest → 孵化 → 发布`、Wiki 链接、安全边界和 Obsidian 提示。

根 AGENTS 必须包含：先读 Ingest Contract、Raw 只读、单来源、引用、冲突、项目隔离、L3/L4 零外发和禁止写 current/versions。

`ingest-contract.md` 必须使用与产品方案一致的输入、输出、状态、幂等和失败定义。

- [ ] **Step 4: 修改 scaffolder 以父目录为提交边界**

```python
ROOT_TEMPLATE_MAP = {
    "root-README.md": "README.md",
    "root-AGENTS.md": "AGENTS.md",
}

SCHEMA_FILENAMES = (
    "AGENTS.md",
    "ingest-contract.md",
    "source-page-template.md",
    "topic-page-template.md",
    "product-document-template.md",
    "field-conventions.md",
)
```

```python
def prepare(self, command: CreateProjectInput, *, parent_root: Path) -> PreparedProject:
    parent = parent_root.expanduser().resolve()
    target = parent / command.project_id
    paths = ProjectPaths.for_registered_root(self.library_root, command.project_id, target)
    temp_root = parent / f".{command.project_id}.tmp-{uuid4().hex}"
    self._build_tree(temp_root, command)
    return PreparedProject(command.project_id, temp_root, paths)
```

`abort()` 只能删除同一父目录、名称匹配本次 UUID 的临时目录。`commit()` 继续使用 no-replace 原子改名。

- [ ] **Step 5: 运行脚手架和验证脚本测试**

Run: `.venv/bin/pytest tests/integration/use_cases/test_manage_projects.py tests/integration/scripts/test_validate_incubator.py -q`

Expected: PASS。

- [ ] **Step 6: 提交 Task 3**

```bash
git add src/infrastructure/files/project_scaffolder.py assets/incubator_schema \
  tests/integration/use_cases/test_manage_projects.py \
  tests/integration/scripts/test_validate_incubator.py
git commit -m "feat: scaffold wiki llm projects"
```

**Owner checkpoint:** 停止并展示新项目目录树、README、AGENTS 和 Ingest Contract；确认后进入 Task 4。

---

### Task 4: 独立目录创建、重新定位与项目页面

**Files:**
- Modify: `src/application/dto/projects.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/application/use_cases/manage_projects.py`
- Modify: `src/application/project_context.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/projects.py`
- Test: `tests/integration/use_cases/test_manage_projects.py`
- Test: `tests/unit/test_container_project_context.py`
- Test: `tests/e2e/test_projects_page.py`

**Interfaces:**
- Consumes: Task 1 的项目路径仓储、Task 2 的 resolver、Task 3 的 scaffolder。
- Produces: `ManageProjects.create(CreateProjectInput.parent_root)`、`ManageProjects.relocate(RelocateProjectInput)`；容器按登记路径构造 `ProjectContext`。

- [ ] **Step 1: 写入项目管理失败测试**

```python
def test_create_registers_owner_selected_root(manager, tmp_path):
    parent = tmp_path / "customer-projects"
    parent.mkdir()
    created = manager.create(project_input("PROJECT_A", parent_root=parent))
    assert Path(created.project_root_path) == (parent / "PROJECT_A").resolve()
    assert (parent / "PROJECT_A/README.md").is_file()


def test_relocate_updates_only_registry(manager, tmp_path):
    original = create_managed_project(manager, "PROJECT_A", tmp_path / "one")
    moved = tmp_path / "two/PROJECT_A"
    moved.parent.mkdir()
    original.rename(moved)
    manager.relocate(RelocateProjectInput(project_id="PROJECT_A", project_root=moved))
    assert manager.projects.get("PROJECT_A").project_root_path == str(moved.resolve())
    assert (moved / "wiki/index.md").is_file()
```

- [ ] **Step 2: 运行管理测试并确认失败**

Run: `.venv/bin/pytest tests/integration/use_cases/test_manage_projects.py -q`

Expected: FAIL，DTO、`relocate()` 或独立父目录尚不存在。

- [ ] **Step 3: 实现 create/switch/list/relocate**

```python
class CreateProjectInput(ProjectDto):
    project_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    initial_display_version: str | None = Field(default=None, max_length=50)
    allow_external_model: bool = False
    parent_root: Path | None = None
```

`ProjectManagement` 增加固定接口 `relocate(command: RelocateProjectInput) -> ProjectSelection`。

兼容规则：调用方未传 `parent_root` 时使用中央控制根。创建数据库失败后，将本次新目录同盘改名为 `.{project_id}.quarantine-{uuid}`。`list()` 和 `switch()` 必须调用 resolver，不再拼接路径。

- [ ] **Step 4: 修改组合根**

`_build_project_context()` 使用：

```python
paths = project_management.path_resolver.resolve(project_id)
return ProjectContext(
    project_id=project_id,
    paths=paths,
    db_path=project_management.library_root / ".incubator/product_incubator.db",
)
```

路径不可用时容器只装配项目中心，不自动创建缺失目录。

- [ ] **Step 5: 写入项目页面 E2E 测试**

```python
def test_owner_creates_projects_in_two_independent_parents(app_page, tmp_path):
    create_from_page(app_page, "PROJECT_A", tmp_path / "one")
    create_from_page(app_page, "PROJECT_B", tmp_path / "two")
    assert (tmp_path / "one/PROJECT_A/README.md").is_file()
    assert (tmp_path / "two/PROJECT_B/README.md").is_file()


def test_unavailable_project_offers_relocation_instead_of_open(app_page):
    assert app_page.button(key="project_relocate_PROJECT_A")
    assert not app_page.button(key="project_open_PROJECT_A")
```

- [ ] **Step 6: 实现项目页面最小交互**

创建表单增加父目录输入、默认恢复和最终路径预览。项目卡显示实际路径；unavailable 时提供重新定位表单并将新根传给 `manager.relocate()`。

- [ ] **Step 7: 运行 Task 4 测试**

Run: `.venv/bin/pytest tests/integration/use_cases/test_manage_projects.py tests/unit/test_container_project_context.py tests/e2e/test_projects_page.py -q`

Expected: PASS。

- [ ] **Step 8: 提交 Task 4**

```bash
git add src/application/dto/projects.py src/application/ports/incubator.py \
  src/application/use_cases/manage_projects.py src/application/project_context.py \
  src/application/container.py src/ui/pages/projects.py \
  tests/integration/use_cases/test_manage_projects.py \
  tests/unit/test_container_project_context.py tests/e2e/test_projects_page.py
git commit -m "feat: manage projects across local directories"
```

**Owner checkpoint:** 停止并现场创建两个不同父目录项目，再移动其中一个并重新定位。

---

### Task 5: Wiki 领域模型、DTO 和校验器

**Files:**
- Create: `src/domain/wiki.py`
- Create: `src/application/dto/wiki_ingest.py`
- Create: `src/application/ports/wiki_ingest.py`
- Create: `src/infrastructure/files/wiki_validator.py`
- Modify: `src/domain/errors.py`
- Create: `tests/unit/domain/test_wiki.py`
- Create: `tests/unit/infrastructure/test_wiki_validator.py`

**Interfaces:**
- Consumes: 现有 `DomainModel`、`Sha256Str`、`SecurityLevel` 和项目路径边界。
- Produces: `WikiIngestStatus`、`WikiPageChange`、`WikiChangeSet`、`WikiIngestRun`、四个命令 DTO、`WikiValidator.validate_change_set()`。

- [ ] **Step 1: 写入领域失败测试**

```python
def test_change_set_requires_one_source_page_index_log_and_safe_targets(change_set_factory):
    change_set = change_set_factory(
        paths=[
            "wiki/sources/SRC-A-material.md",
            "wiki/index.md",
            "wiki/log.md",
            ".incubator/source-index.json",
        ]
    )
    change_set.validate_contract()


@pytest.mark.parametrize("forbidden", [
    "raw/2026/SRC-A/a.md", "wiki/current/当前产品方案.md",
    "wiki/versions/1.0.md", ".incubator/current-baseline.json", "../escape.md",
])
def test_change_set_rejects_forbidden_target(change_set_factory, forbidden):
    with pytest.raises(ValueError, match="WIKI_CHANGESET_TARGET_FORBIDDEN"):
        change_set_factory(paths=[forbidden]).validate_contract()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/pytest tests/unit/domain/test_wiki.py -q`

Expected: FAIL，`src.domain.wiki` 不存在。

- [ ] **Step 3: 实现领域模型和命令 DTO**

```python
class WikiIngestStatus(StrEnum):
    PENDING = "pending_ingest"
    PROCESSING = "ingesting"
    INGESTED = "ingested"
    FAILED = "ingest_failed"
    REINGEST_RECOMMENDED = "reingest_recommended"
    LOCAL_REVIEW_REQUIRED = "local_review_required"


class WikiPageChange(DomainModel):
    relative_path: NonEmptyStr
    operation: Literal["create", "replace"]
    before_sha256: Sha256Str | None
    markdown: NonEmptyStr
    after_sha256: Sha256Str


class WikiChangeSet(DomainModel):
    transaction_id: NonEmptyStr
    project_id: NonEmptyStr
    source_id: NonEmptyStr
    idempotency_key: Sha256Str
    schema_version: Literal["2.2"]
    generation_mode: DocumentGenerationMode
    page_changes: list[WikiPageChange]
    source_page_path: NonEmptyStr
    topic_page_paths: list[NonEmptyStr]
    conflict_count: int = Field(ge=0)
    evidence_gap_count: int = Field(ge=0)
    result_digest: Sha256Str


class WikiIngestRun(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    source_id: NonEmptyStr
    transaction_id: NonEmptyStr
    idempotency_key: Sha256Str
    schema_version: Literal["2.2"]
    generation_mode: DocumentGenerationMode
    status: NonEmptyStr
    source_page_path: NonEmptyStr | None = None
    topic_page_paths: list[NonEmptyStr] = Field(default_factory=list)
    result_digest: Sha256Str | None = None
    error_code: NonEmptyStr | None = None
    started_at: datetime
    finished_at: datetime | None = None


class WikiTransactionResult(DomainModel):
    transaction_id: NonEmptyStr
    idempotency_key: Sha256Str
    status: Literal["committed", "rolled_back", "recovery_required"]
```

DTO 定义固定为：

```python
class IngestArchivedSourceInput(BaseModel):
    project_id: str
    source_id: str
    requested_by: str


class PrepareLocalWikiIngestInput(BaseModel):
    project_id: str
    source_id: str
    requested_by: str


class ConfirmLocalWikiIngestInput(BaseModel):
    project_id: str
    source_id: str
    requested_by: str


class RecoverWikiTransactionInput(BaseModel):
    project_id: str


class WikiIngestResultView(BaseModel):
    source_id: str
    status: WikiIngestStatus
    source_page_path: str | None
    topic_page_paths: list[str]
    conflict_count: int
    evidence_gap_count: int
    duplicate: bool = False


class LocalWikiIngestDraftView(BaseModel):
    source_id: str
    status: Literal[WikiIngestStatus.LOCAL_REVIEW_REQUIRED]
    draft_root: Path
```

- [ ] **Step 4: 写入 Markdown/路径校验测试**

```python
def test_validator_rejects_cross_project_source_id(paths, change_set_factory):
    change = change_set_factory(markdown="source_id: SRC-PROJECT-B-001")
    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        WikiValidator(paths).validate_change_set(change)


def test_validator_rejects_broken_obsidian_link(paths, change_set_factory):
    change = change_set_factory(markdown="[[wiki/topics/missing]]")
    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        WikiValidator(paths).validate_change_set(change)
```

- [ ] **Step 5: 实现最小校验器**

校验器执行：路径白名单、项目边界、source ID、Frontmatter 必填、Obsidian 链接目标、create/replace before SHA 和 Markdown 非空。模型输出不能决定目标路径。

在 `ErrorCode`/`ERROR_CATALOG` 增加：`WIKI_SCHEMA_MISSING`、`WIKI_SOURCE_INTEGRITY_FAILED`、`WIKI_EXTERNAL_CALL_DENIED`、`WIKI_CHANGESET_INVALID`、`WIKI_CONCURRENT_MODIFICATION`、`WIKI_TRANSACTION_FAILED`、`WIKI_RECOVERY_REQUIRED` 和 `WIKI_INGEST_ALREADY_RUNNING`。

- [ ] **Step 6: 运行 Task 5 测试与 Ruff**

Run: `.venv/bin/pytest tests/unit/domain/test_wiki.py tests/unit/infrastructure/test_wiki_validator.py -q`

Run: `.venv/bin/ruff check src/domain/wiki.py src/domain/errors.py src/application/dto/wiki_ingest.py src/application/ports/wiki_ingest.py src/infrastructure/files/wiki_validator.py tests/unit/domain/test_wiki.py tests/unit/infrastructure/test_wiki_validator.py`

Expected: 两条命令均 PASS。

- [ ] **Step 7: 提交 Task 5**

```bash
git add src/domain/wiki.py src/domain/errors.py src/application/dto/wiki_ingest.py \
  src/application/ports/wiki_ingest.py src/infrastructure/files/wiki_validator.py \
  tests/unit/domain/test_wiki.py tests/unit/infrastructure/test_wiki_validator.py
git commit -m "feat: define governed wiki change sets"
```

**Owner checkpoint:** 停止并汇报允许写入与禁止写入范围，以及变更集必须包含的文件。

---

### Task 6: Ingest 持久化、状态与 source-index 2.2

**Files:**
- Modify: `src/domain/models.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `src/application/ports/wiki_ingest.py`
- Modify: `src/infrastructure/files/source_index_store.py`
- Modify: `src/application/use_cases/archive_raw_source.py`
- Modify: `src/application/project_context.py`
- Test: `tests/integration/db/test_migrations.py`
- Test: `tests/integration/db/test_repositories.py`
- Test: `tests/integration/files/test_project_source_archive.py`
- Test: `tests/integration/use_cases/test_archive_raw_source.py`

**Interfaces:**
- Consumes: Task 5 的 `WikiIngestRun`、`WikiIngestStatus`。
- Produces: SourceRecord 2.2 字段、`SqliteWikiIngestRunRepository`、source-index 2.2、2.2 归档状态。

- [ ] **Step 1: 写入迁移和运行仓储失败测试**

```python
def test_migrate_adds_wiki_ingest_fields_and_runs_table(db_path):
    migrate(db_path)
    source_columns = table_columns(db_path, "source_records")
    assert {"ingest_schema_version", "ingested_at", "source_page_path",
            "topic_page_paths_json", "ingest_result_digest", "ingest_error_code",
            "generation_mode"} <= source_columns
    assert "wiki_ingest_runs" in table_names(db_path)


def test_run_repository_enforces_idempotency(run_repository, wiki_run):
    run_repository.add(wiki_run)
    with pytest.raises(sqlite3.IntegrityError):
        run_repository.add(wiki_run.model_copy(update={"id": "RUN-2"}))
```

- [ ] **Step 2: 运行数据库测试并确认失败**

Run: `.venv/bin/pytest tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py -q`

Expected: FAIL，字段和 `wiki_ingest_runs` 不存在。

- [ ] **Step 3: 实现 2.2 增量迁移和仓储**

按技术规格第十一章增加 source 列和 `wiki_ingest_runs` 表。仓储固定接口：

```text
add(run: WikiIngestRun) -> None
get_by_transaction(transaction_id: str) -> WikiIngestRun | None
get_succeeded_by_idempotency(key: str) -> WikiIngestRun | None
update(run: WikiIngestRun) -> None
list_interrupted(project_id: str, older_than: datetime) -> list[WikiIngestRun]
```

- [ ] **Step 4: 写入 source-index 2.2 镜像测试**

```python
def test_source_index_mirrors_wiki_ingest_result(index_store, ingested_source):
    index_store.upsert(ingested_source)
    item = read_source_index(index_store.path)["sources"][0]
    assert item["ingest_status"] == "ingested"
    assert item["source_page_path"].startswith("wiki/sources/")
    assert item["topic_page_paths"] == ["wiki/topics/pricing.md"]
    assert item["generation_mode"] == "external_ai"
```

- [ ] **Step 5: 实现 SourceRecord 字段和 source-index 2.2**

在 `SourceRecord` 末尾增加：

```python
ingest_schema_version: NonEmptyStr | None = None
ingested_at: datetime | None = None
source_page_path: NonEmptyStr | None = None
topic_page_paths: list[NonEmptyStr] = Field(default_factory=list)
ingest_result_digest: Sha256Str | None = None
ingest_error_code: NonEmptyStr | None = None
generation_mode: DocumentGenerationMode | None = None
```

旧构造调用继续通过。仓储在 `topic_page_paths_json` 和列表间转换。`SourceIndexStore` 的 schema version 升为 `2.2`，同时继续读取 2.1 文件。

- [ ] **Step 6: 让 2.2 项目归档进入 pending_ingest**

`ProjectContext` 增加 `wiki_schema_version`，从 `.incubator/project.json` 读取。`ArchiveRawSource` 接受该版本：

```python
ingest_status = "pending_ingest" if self.wiki_schema_version == "2.2" else "archived"
```

历史项目仍为 `archived`，不得批量更新。

- [ ] **Step 7: 运行 Task 6 测试**

Run: `.venv/bin/pytest tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py tests/integration/files/test_project_source_archive.py tests/integration/use_cases/test_archive_raw_source.py -q`

Expected: PASS。

- [ ] **Step 8: 提交 Task 6**

```bash
git add src/domain/models.py src/application/ports/wiki_ingest.py \
  src/infrastructure/db/migrations.py src/infrastructure/db/repositories.py \
  src/infrastructure/files/source_index_store.py \
  src/application/use_cases/archive_raw_source.py src/application/project_context.py \
  tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py \
  tests/integration/files/test_project_source_archive.py \
  tests/integration/use_cases/test_archive_raw_source.py
git commit -m "feat: persist wiki ingest lifecycle"
```

**Owner checkpoint:** 停止并展示新材料 `pending_ingest`、历史材料仍兼容，以及 source-index 2.2 字段。

---

### Task 7: Wiki 多文件事务、回滚与恢复

**Files:**
- Create: `src/infrastructure/files/wiki_store.py`
- Create: `src/infrastructure/files/wiki_change_set_store.py`
- Create: `src/application/use_cases/recover_wiki_transaction.py`
- Modify: `src/infrastructure/files/project_audit_log.py`
- Modify: `src/application/ports/wiki_ingest.py`
- Create: `tests/integration/files/test_wiki_transaction.py`
- Test: `tests/e2e/test_incubator_restart_recovery.py`

**Interfaces:**
- Consumes: Task 5 的 `WikiChangeSet/WikiValidator`、Task 6 的 Source 和 Run 仓储。
- Produces: `WikiTransactionCoordinator.commit(change_set) -> WikiTransactionResult`、`WikiTransactionCoordinator.recover() -> WikiTransactionResult`、确定性 index/log/source-index 写入。

- [ ] **Step 1: 写入成功提交失败测试**

```python
def test_commit_replaces_all_targets_and_database_once(transaction_fixture):
    before = transaction_fixture.snapshot()
    result = transaction_fixture.coordinator.commit(transaction_fixture.change_set)
    assert result.status == "committed"
    assert transaction_fixture.source().ingest_status == "ingested"
    assert transaction_fixture.page("wiki/sources/SRC-A-material.md").is_file()
    assert transaction_fixture.page("wiki/log.md").read_text().count(result.idempotency_key) == 1
    assert transaction_fixture.raw_sha256() == before.raw_sha256
```

- [ ] **Step 2: 运行测试并确认事务协调器不存在**

Run: `.venv/bin/pytest tests/integration/files/test_wiki_transaction.py::test_commit_replaces_all_targets_and_database_once -q`

Expected: FAIL，模块或协调器不存在。

- [ ] **Step 3: 实现 staging、journal 和成功提交**

事务目录固定为 `.incubator/transactions/{transaction_id}`，包含 `journal.json`、`staged/`、`backup/`、`result.json`。journal 状态使用：

```python
BUILDING = "building"
PREPARED = "prepared"
FILES_COMMITTED = "files_committed"
DATABASE_COMMITTED = "database_committed"
COMMITTED = "committed"
ROLLING_BACK = "rolling_back"
ROLLED_BACK = "rolled_back"
RECOVERY_REQUIRED = "recovery_required"
```

每次状态写入用临时文件加 `os.replace`；目标替换前保存 before/after SHA-256 和 backup。

- [ ] **Step 4: 对每个失败点写参数化回滚测试**

```python
@pytest.mark.parametrize("failure_stage", [
    "after_prepare", "after_first_file", "after_files", "before_database_commit",
])
def test_failure_restores_files_and_leaves_no_success_run(transaction_fixture, failure_stage):
    before = transaction_fixture.snapshot()
    transaction_fixture.fail_at(failure_stage)
    with pytest.raises(RuntimeError, match="WIKI_TRANSACTION_FAILED"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)
    assert transaction_fixture.snapshot().wiki_hashes == before.wiki_hashes
    assert transaction_fixture.source().ingest_status == "ingest_failed"
    assert transaction_fixture.succeeded_run() is None
```

- [ ] **Step 5: 实现失败回滚**

文件提交失败时按 backup 恢复；SQLite 更新在一个 connection transaction 中完成。回滚失败时写 `recovery_required`，抛出 `WIKI_RECOVERY_REQUIRED`，禁止下一次提交。

- [ ] **Step 6: 写入崩溃恢复矩阵测试**

```python
@pytest.mark.parametrize(
    ("journal_state", "db_succeeded", "expected"),
    [
        ("prepared", False, "rolled_back"),
        ("files_committed", False, "rolled_back"),
        ("files_committed", True, "committed"),
        ("database_committed", True, "committed"),
        ("database_committed", False, "recovery_required"),
    ],
)
def test_recovery_matrix(transaction_fixture, journal_state, db_succeeded, expected):
    transaction_fixture.seed_interrupted(journal_state, db_succeeded=db_succeeded)
    result = transaction_fixture.coordinator.recover()
    assert result.status == expected
```

- [ ] **Step 7: 实现恢复、锁和中断运行处理**

使用 `.incubator/locks/wiki-ingest.lock`。启动或进入项目时：先恢复 journal，再把无活动锁、无可恢复提交且超过阈值的 `ingesting` 标记为 `ingest_failed/WIKI_INGEST_INTERRUPTED`。

- [ ] **Step 8: 验证并发和 Owner 外部修改**

```python
def test_before_hash_change_aborts_without_overwriting_owner_edit(transaction_fixture):
    transaction_fixture.owner_edits("wiki/topics/pricing.md", "Owner new text")
    with pytest.raises(DomainError, match="WIKI_CONCURRENT_MODIFICATION"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)
    assert transaction_fixture.page("wiki/topics/pricing.md").read_text() == "Owner new text"
```

Run: `.venv/bin/pytest tests/integration/files/test_wiki_transaction.py tests/e2e/test_incubator_restart_recovery.py -q`

Expected: PASS。

- [ ] **Step 9: 提交 Task 7**

```bash
git add src/infrastructure/files/wiki_store.py \
  src/infrastructure/files/wiki_change_set_store.py \
  src/application/use_cases/recover_wiki_transaction.py \
  src/infrastructure/files/project_audit_log.py src/application/ports/wiki_ingest.py \
  tests/integration/files/test_wiki_transaction.py \
  tests/e2e/test_incubator_restart_recovery.py
git commit -m "feat: commit wiki changes recoverably"
```

**Owner checkpoint:** 停止并汇报所有注入失败点的回滚证据，以及一次模拟崩溃后的自动恢复结果。

---

### Task 8: 安全投影和 Wiki Ingest Gateway

**Files:**
- Create: `src/infrastructure/files/wiki_outbound_context.py`
- Create: `src/infrastructure/gateways/wiki_ingest_gateway.py`
- Modify: `src/infrastructure/gateways/schemas.py`
- Modify: `src/infrastructure/gateways/composition.py`
- Modify: `src/application/ports/wiki_ingest.py`
- Create: `tests/security/test_wiki_outbound_projection.py`
- Test: `tests/integration/gateways/test_workflow_schemas.py`
- Create: `tests/integration/gateways/test_wiki_ingest_gateway.py`

**Interfaces:**
- Consumes: Task 5 的 Gateway 端口和 Wiki 模型；现有 Dify `WorkflowGateway`、安全证明和超时机制。
- Produces: `WikiOutboundContextBuilder.build(project_id, related_topic_paths) -> WikiOutboundProjection`、`WikiIngestGateway.generate(inputs, safety_proof) -> WikiIngestWorkflowOutput`、2.2 输入输出 Schema。

- [ ] **Step 1: 写入敏感投影失败测试**

```python
def test_projection_excludes_topic_with_any_l3_source(project_wiki, source_repository):
    project_wiki.write_topic("pricing", citations=["SRC-L1", "SRC-L3"], body="SECRET-PRICE")
    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=["wiki/topics/pricing.md"]
    )
    serialized = projection.model_dump_json()
    assert "SECRET-PRICE" not in serialized
    assert "pricing" not in serialized
    assert projection.local_sensitive_comparison_required is True


def test_projection_includes_only_authorized_l1_l2_claims(project_wiki, source_repository):
    project_wiki.write_topic("channels", citations=["SRC-L1"], body="Safe channel")
    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=["wiki/topics/channels.md"]
    )
    assert projection.safe_related_topics[0].markdown == "Safe channel"
```

- [ ] **Step 2: 运行安全测试并确认失败**

Run: `.venv/bin/pytest tests/security/test_wiki_outbound_projection.py -q`

Expected: FAIL，安全投影模块不存在。

- [ ] **Step 3: 实现来源级安全投影**

每个被投影的 Wiki 陈述必须能够解析到 source IDs；所有来源均满足 L1/L2、已脱敏、材料允许外部调用且项目允许外部调用时才可进入投影。否则整页排除，只返回本地对照布尔值和计数，不返回标题或正文。

```python
class SafeWikiTopicInput(BaseModel):
    title: str
    markdown: str
    source_ids: list[str]


class WikiOutboundProjection(BaseModel):
    safe_index_projection: str
    safe_related_topics: list[SafeWikiTopicInput]
    local_sensitive_comparison_required: bool
    excluded_topic_count: int = Field(ge=0)
```

- [ ] **Step 4: 写入 Gateway Schema 和契约测试**

```python
def test_wiki_gateway_rejects_task_and_schema_mismatch(fake_client, safety_proof):
    gateway = WikiIngestGateway(fake_client, timeout_seconds=60)
    fake_client.output = valid_output(task_id="OTHER", schema_version="2.2")
    with pytest.raises(OutputValidationError, match="TASK_ID_MISMATCH"):
        gateway.generate(valid_input(task_id="TASK-1"), safety_proof=safety_proof)
```

输入字段固定包含 `safe_index_projection` 和 `safe_related_topics`，不得包含原始 `current_index`、完整 topic 文件或路径由模型指定的字段。

- [ ] **Step 5: 实现 Gateway 并复用现有安全调用基础设施**

`WikiIngestGateway` 使用现有 `validate_input`、`invoke`、显式 timeout 和 `OutputValidationError`。输出只包含来源页 Markdown、结构化主题变更、冲突和证据缺口。

- [ ] **Step 6: 运行 Task 8 测试**

Run: `.venv/bin/pytest tests/security/test_wiki_outbound_projection.py tests/integration/gateways/test_workflow_schemas.py tests/integration/gateways/test_wiki_ingest_gateway.py -q`

Expected: PASS。

- [ ] **Step 7: 提交 Task 8**

```bash
git add src/infrastructure/files/wiki_outbound_context.py \
  src/infrastructure/gateways/wiki_ingest_gateway.py \
  src/infrastructure/gateways/schemas.py src/infrastructure/gateways/composition.py \
  src/application/ports/wiki_ingest.py \
  tests/security/test_wiki_outbound_projection.py \
  tests/integration/gateways/test_workflow_schemas.py \
  tests/integration/gateways/test_wiki_ingest_gateway.py
git commit -m "feat: generate safe wiki ingest proposals"
```

**Owner checkpoint:** 停止并展示含 L3/L4 的主题未进入外发载荷，以及本地敏感对照提示仍被保留。

---

### Task 9: L1/L2 标准 Ingest 与材料页面

**Files:**
- Create: `src/application/use_cases/ingest_archived_source.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/materials.py`
- Create: `tests/integration/use_cases/test_wiki_ingest.py`
- Modify: `tests/e2e/test_materials_page.py`
- Create: `tests/security/test_wiki_project_isolation.py`

**Interfaces:**
- Consumes: Task 6 的来源/运行仓储、Task 7 的事务协调器、Task 8 的安全投影和 Gateway。
- Produces: `IngestArchivedSource.execute(command) -> WikiIngestResultView`；材料卡“开始 Ingest/查看 Wiki/重试”。

- [ ] **Step 1: 写入成功流程失败测试**

```python
def test_ingest_archived_l2_source_updates_complete_wiki(ingest_fixture):
    result = ingest_fixture.service.execute(
        IngestArchivedSourceInput(
            project_id="PROJECT_A", source_id="SRC-PROJECT-A-001", requested_by="Owner"
        )
    )
    assert result.status is WikiIngestStatus.INGESTED
    assert result.source_page_path.startswith("wiki/sources/")
    assert ingest_fixture.page(result.source_page_path).is_file()
    assert "SRC-PROJECT-A-001" in ingest_fixture.page("wiki/index.md").read_text()
    assert ingest_fixture.gateway.calls == 1
```

- [ ] **Step 2: 运行用例测试并确认失败**

Run: `.venv/bin/pytest tests/integration/use_cases/test_wiki_ingest.py -q`

Expected: FAIL，`IngestArchivedSource` 不存在。

- [ ] **Step 3: 实现预检、幂等和状态编排**

执行顺序固定：resolve → project/source ID → Raw 路径/SHA → 状态/幂等 → 授权 → extract/redact → safe projection → Gateway → local deterministic compilation → validator → transaction commit。

模型调用前把 run/source 设为 `ingesting`；Gateway 或验证失败时设为 `ingest_failed`，Wiki 文件保持不变。相同成功幂等键直接返回 `duplicate=True`，Gateway 调用次数不增加。

- [ ] **Step 4: 写入安全与边界测试**

```python
def test_ingest_rejects_source_from_other_project(ingest_fixture):
    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        ingest_fixture.service.execute(
            IngestArchivedSourceInput(
                project_id="PROJECT_A", source_id="SRC-PROJECT-B-001", requested_by="Owner"
            )
        )
    assert ingest_fixture.project_b_hashes_unchanged()


def test_ingest_failure_never_changes_current_or_manifest(ingest_fixture):
    before = ingest_fixture.release_hashes()
    ingest_fixture.gateway.fail("MODEL_TIMEOUT")
    with pytest.raises(AppError):
        ingest_fixture.ingest_l2()
    assert ingest_fixture.release_hashes() == before
```

- [ ] **Step 5: 装配服务并修改材料页面**

`AppContainer` 增加可空 `wiki_ingest` 服务。材料页面根据状态显示：

```text
pending_ingest → 开始 Ingest
ingesting → 处理中（禁用重复提交）
ingested → 查看 Wiki 结果
ingest_failed → 查看安全错误码 / 重新 Ingest
reingest_recommended → 明确重新 Ingest
```

页面不直接写 source-index。

- [ ] **Step 6: 写入页面 E2E 测试**

```python
def test_material_page_runs_ingest_after_owner_click(app_page):
    button = app_page.button(key="material_ingest_SRC-PROJECT-A-001")
    assert button
    button.click().run()
    assert app_page.success
    assert "已 Ingest" in app_page.markdown[0].value
```

- [ ] **Step 7: 运行 Task 9 测试**

Run: `.venv/bin/pytest tests/integration/use_cases/test_wiki_ingest.py tests/e2e/test_materials_page.py tests/security/test_wiki_project_isolation.py -q`

Expected: PASS。

- [ ] **Step 8: 提交 Task 9**

```bash
git add src/application/use_cases/ingest_archived_source.py \
  src/application/container.py src/ui/pages/materials.py \
  tests/integration/use_cases/test_wiki_ingest.py \
  tests/e2e/test_materials_page.py tests/security/test_wiki_project_isolation.py
git commit -m "feat: ingest archived sources into wiki"
```

**Owner checkpoint:** 停止并实时演示一份 L1/L2 材料从 pending 到 ingested，以及来源页、主题页、index、log 同步更新。

---

### Task 10: L3/L4 本地 Ingest 与页面确认

**Files:**
- Create: `src/application/use_cases/prepare_local_wiki_ingest.py`
- Create: `src/application/use_cases/confirm_local_wiki_ingest.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/materials.py`
- Create: `tests/integration/use_cases/test_local_wiki_ingest.py`
- Modify: `tests/e2e/test_materials_page.py`
- Modify: `tests/security/test_wiki_outbound_projection.py`

**Interfaces:**
- Consumes: Task 5 的本地 DTO/validator，Task 7 的事务协调器。
- Produces: `PrepareLocalWikiIngest.execute(command) -> LocalWikiIngestDraftView`、`ConfirmLocalWikiIngest.execute(command) -> WikiIngestResultView`；Obsidian 草稿目录和页面确认入口。

- [ ] **Step 1: 写入本地草稿失败测试**

```python
def test_prepare_l4_creates_local_template_without_gateway(local_ingest_fixture):
    result = local_ingest_fixture.prepare.execute(
        PrepareLocalWikiIngestInput(
            project_id="PROJECT_A", source_id="SRC-PROJECT-A-L4", requested_by="Owner"
        )
    )
    assert result.status is WikiIngestStatus.LOCAL_REVIEW_REQUIRED
    assert (result.draft_root / "README.md").is_file()
    assert (result.draft_root / "source.md").is_file()
    assert local_ingest_fixture.gateway.calls == 0
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/pytest tests/integration/use_cases/test_local_wiki_ingest.py -q`

Expected: FAIL，本地用例不存在。

- [ ] **Step 3: 实现 PrepareLocalWikiIngest**

草稿固定写入 `wiki/drafts/local-ingest/{source_id}/`。README 写明 Owner 操作和必填章节；source.md 从可信模板生成，只预填元数据、Raw 相对路径和 SHA，不自动复制敏感正文。只允许 L3/L4 进入此用例。

- [ ] **Step 4: 写入确认成功与失败保留测试**

```python
def test_confirm_local_draft_commits_wiki_and_removes_draft(local_ingest_fixture):
    draft_root = local_ingest_fixture.prepare_and_fill_valid_draft()
    result = local_ingest_fixture.confirm.execute(
        ConfirmLocalWikiIngestInput(
            project_id="PROJECT_A", source_id="SRC-PROJECT-A-L4", requested_by="Owner"
        )
    )
    assert result.status is WikiIngestStatus.INGESTED
    assert not draft_root.exists()
    assert local_ingest_fixture.gateway.calls == 0


def test_invalid_local_draft_is_preserved_for_owner_correction(local_ingest_fixture):
    draft_root = local_ingest_fixture.prepare_invalid_draft()
    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        local_ingest_fixture.confirm_source()
    assert draft_root.is_dir()
    assert local_ingest_fixture.gateway.calls == 0
```

- [ ] **Step 5: 实现本地确认并共用事务协调器**

确认用例读取 source.md 和 topics 草稿，校验后生成 `generation_mode=local_manual` 的 `WikiChangeSet`。成功提交后删除草稿；`result.json` 只保留路径、SHA 和计数，不保存正文。

- [ ] **Step 6: 修改材料页面并强化零外发测试**

L3/L4 显示“创建本地 Ingest 草稿”“复制草稿路径”“校验并确认本地 Ingest”。测试注入会在任何 Gateway 调用时直接失败的 spy。

- [ ] **Step 7: 运行 Task 10 测试**

Run: `.venv/bin/pytest tests/integration/use_cases/test_local_wiki_ingest.py tests/e2e/test_materials_page.py tests/security/test_wiki_outbound_projection.py -q`

Expected: PASS，L3/L4 Gateway 调用数为 0。

- [ ] **Step 8: 提交 Task 10**

```bash
git add src/application/use_cases/prepare_local_wiki_ingest.py \
  src/application/use_cases/confirm_local_wiki_ingest.py \
  src/application/container.py src/ui/pages/materials.py \
  tests/integration/use_cases/test_local_wiki_ingest.py \
  tests/e2e/test_materials_page.py tests/security/test_wiki_outbound_projection.py
git commit -m "feat: confirm sensitive wiki ingest locally"
```

**Owner checkpoint:** 停止并用 Obsidian 打开一份 L3/L4 草稿，编辑后回到页面确认，同时展示零外部调用证据。

---

### Task 11: Wiki 驱动的产品文档孵化

**Files:**
- Create: `src/infrastructure/files/wiki_context_reader.py`
- Modify: `src/application/ports/wiki_ingest.py`
- Modify: `src/application/use_cases/incubate_document.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/incubate.py`
- Modify: `tests/integration/use_cases/test_incubate_document.py`
- Modify: `tests/e2e/test_incubate_page.py`

**Interfaces:**
- Consumes: 已 `ingested` 来源、来源页/主题页和现有 `IncubateDocument` 草稿/发布逻辑。
- Produces: `WikiContextReader.list_ingested_sources/read_context`；候选生成输入不再直接读取 Raw。

- [ ] **Step 1: 写入上下文选择失败测试**

```python
def test_incubation_lists_only_ingested_sources(incubation_fixture):
    incubation_fixture.seed_source("SRC-PENDING", status="pending_ingest")
    incubation_fixture.seed_source("SRC-READY", status="ingested", with_wiki=True)
    assert incubation_fixture.service.list_sources("PROJECT_A") == [
        {"id": "SRC-READY", "label": "Ready · SRC-READY", "wiki_page_count": "2"}
    ]


def test_incubation_uses_wiki_and_never_extracts_raw(incubation_fixture):
    incubation_fixture.raw_extractor.fail_if_called()
    result = incubation_fixture.generate_from("SRC-READY")
    assert result.draft.source_ids == ["SRC-READY"]
    assert incubation_fixture.gateway.last_input["wiki_pages"]
```

- [ ] **Step 2: 运行测试并确认当前实现仍读取 Raw**

Run: `.venv/bin/pytest tests/integration/use_cases/test_incubate_document.py -q`

Expected: FAIL，pending 来源仍出现或 Raw extractor 被调用。

- [ ] **Step 3: 实现 WikiContextReader**

读取来源页和关联主题页，校验项目边界、页面存在、source ID 和 Raw SHA 引用。返回：

```python
class WikiIncubationContext(DomainModel):
    source_ids: list[str]
    pages: list[WikiContextPage]
    conflicts: list[str]
    evidence_gaps: list[str]
```

L3/L4 已本地确认的 Wiki 不允许进入外部文档 Gateway；继续使用 2.1 本地候选能力。

- [ ] **Step 4: 修改 IncubateDocument 和页面**

删除候选生成路径上的 Raw 抽取调用，Gateway 输入使用 `wiki_pages`。保留版本 ID、草稿存储、Diff、保存、Owner 审核和发布。页面显示页面数量、冲突和证据缺口。

- [ ] **Step 5: 运行孵化测试**

Run: `.venv/bin/pytest tests/integration/use_cases/test_incubate_document.py tests/e2e/test_incubate_page.py tests/integration/use_cases/test_publish_document_draft.py tests/integration/use_cases/test_export_current_document.py -q`

Expected: PASS，发布和 Markdown 导出回归不变。

- [ ] **Step 6: 提交 Task 11**

```bash
git add src/infrastructure/files/wiki_context_reader.py \
  src/application/ports/wiki_ingest.py src/application/use_cases/incubate_document.py \
  src/application/container.py src/ui/pages/incubate.py \
  tests/integration/use_cases/test_incubate_document.py tests/e2e/test_incubate_page.py
git commit -m "feat: incubate product documents from wiki"
```

**Owner checkpoint:** 停止并演示 pending 材料不可选、ingested 材料可选，以及候选引用 Wiki 页面而非直接读取 Raw。

---

### Task 12: 端到端、安全回归与验收报告

**Files:**
- Create: `tests/e2e/test_wiki_incubation_flow.py`
- Modify: `tests/e2e/harness.py`
- Modify: `tests/security/test_wiki_project_isolation.py`
- Modify: `tests/security/test_wiki_outbound_projection.py`
- Create: `docs/qa/product-document-incubator-2.2-acceptance.md`
- Modify: `docs/runbook/owner-user-guide.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1～11 的完整功能。
- Produces: 四条主 E2E、AC-01～AC-29 证据矩阵、Owner 使用说明和最终回归证据。

- [ ] **Step 1: 写入四条端到端场景**

```python
def test_l2_archive_ingest_wiki_incubate_publish(wiki_harness, tmp_path):
    project = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    source = wiki_harness.archive_l2(project, "requirements.md", b"# Requirements\nSafe")
    raw_before = wiki_harness.sha256(source.archive_path)
    ingest = wiki_harness.ingest(source)
    assert ingest.status is WikiIngestStatus.INGESTED
    draft = wiki_harness.incubate(project, [source.id])
    assert source.id in draft.source_ids
    assert wiki_harness.current_markdown(project) is None
    wiki_harness.publish(project, draft)
    assert wiki_harness.current_markdown(project)
    assert wiki_harness.sha256(source.archive_path) == raw_before


def test_l4_archive_local_edit_confirm_without_gateway(wiki_harness, tmp_path):
    project = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    source = wiki_harness.archive_l4(project, "strategy.md", b"# Restricted")
    draft_root = wiki_harness.prepare_local(source)
    wiki_harness.write_valid_local_source_page(draft_root, source)
    result = wiki_harness.confirm_local(source)
    assert result.status is WikiIngestStatus.INGESTED
    assert wiki_harness.gateway_calls == 0


def test_two_projects_in_different_roots_never_cross_read(wiki_harness, tmp_path):
    project_a = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    project_b = wiki_harness.create_project("PROJECT_B", tmp_path / "two")
    source_a = wiki_harness.archive_l2(project_a, "a.md", b"# A")
    project_b_before = wiki_harness.tree_hashes(project_b)
    wiki_harness.ingest(source_a)
    assert wiki_harness.tree_hashes(project_b) == project_b_before


def test_move_relocate_then_continue_ingest(wiki_harness, tmp_path):
    project = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    moved = tmp_path / "two/PROJECT_A"
    moved.parent.mkdir()
    project.root.rename(moved)
    with pytest.raises(DomainError, match="PROJECT_ROOT_UNAVAILABLE"):
        wiki_harness.open_project(project.id)
    project = wiki_harness.relocate(project.id, moved)
    source = wiki_harness.archive_l2(project, "after.md", b"# After")
    assert wiki_harness.ingest(source).status is WikiIngestStatus.INGESTED
```

在 `tests/e2e/harness.py` 增加 `WikiIncubatorHarness`，固定提供 `create_project`、`archive_l2`、`archive_l4`、`prepare_local`、`write_valid_local_source_page`、`confirm_local`、`ingest`、`incubate`、`publish`、`relocate`、`open_project`、`sha256` 和 `tree_hashes`。这些方法必须组合真实用例和仓储；只有 `write_valid_local_source_page` 可以模拟 Owner 编辑文件。

- [ ] **Step 2: 运行新 E2E 并修复仅属于 2.2 的集成缺口**

Run: `.venv/bin/pytest tests/e2e/test_wiki_incubation_flow.py tests/security/test_wiki_project_isolation.py tests/security/test_wiki_outbound_projection.py -q`

Expected: PASS。若失败，只修改失败证明涉及的 2.2 文件，不进行无关重构。

- [ ] **Step 3: 编写 AC-01～AC-29 验收矩阵**

报告每行包含：AC 编号、自动化测试节点、人工证据、结果、Commit SHA。不得只写“已通过”，必须给出命令或证据文件路径。

- [ ] **Step 4: 更新 Owner 使用说明**

只增加：独立父目录、路径不可用/重新定位、pending_ingest、L1/L2 Ingest、L3/L4 Obsidian 草稿、本地确认、Wiki 驱动孵化。保持小白可读，不加入内部类名。

- [ ] **Step 5: 运行全量质量门禁**

Run: `.venv/bin/pytest -q`

Expected: 全部 PASS，0 failed。

Run: `.venv/bin/coverage run -m pytest -q && .venv/bin/coverage report --include='src/domain/*,src/application/*' --fail-under=94`

Expected: 测试全部 PASS；领域层和 application 层合并覆盖率不低于 94%。

Run: `.venv/bin/ruff check .`

Expected: PASS。

Run: `.venv/bin/ruff format --check .`

Expected: PASS。

Run: `.venv/bin/python -m compileall -q src tests scripts`

Expected: PASS。

Run: `git diff --check && git status --short --branch`

Expected: 无空白错误；只显示本计划预期修改和执行前已存在的用户文件。

- [ ] **Step 6: 提交验收材料**

```bash
git add tests/e2e/test_wiki_incubation_flow.py \
  tests/e2e/harness.py \
  tests/security/test_wiki_project_isolation.py \
  tests/security/test_wiki_outbound_projection.py \
  docs/qa/product-document-incubator-2.2-acceptance.md \
  docs/runbook/owner-user-guide.md README.md
git commit -m "test: accept product document incubator 2.2"
```

如果 `README.md` 或 `docs/runbook/owner-user-guide.md` 在执行开始前已有未提交修改，先比较归属；无法安全拆分时不得暂存，改为在验收报告中记录待 Owner 合并的文档补丁。

**Owner checkpoint:** 停止并提交最终验收报告、测试数量、覆盖率、最终 SHA 和仍未纳入提交的用户工作区文件；由 Owner 决定是否合并或发布。

---

## 三、执行顺序和停止规则

```text
Task 1 数据位置
→ Task 2 路径解析
→ Task 3 脚手架
→ Task 4 项目管理/页面
→ Task 5 Wiki 契约
→ Task 6 状态持久化
→ Task 7 文件事务/恢复
→ Task 8 安全 Gateway
→ Task 9 L1/L2 Ingest
→ Task 10 L3/L4 本地 Ingest
→ Task 11 Wiki 文档孵化
→ Task 12 完整验收
```

不得跳过 Task 7 直接接入 Gateway；否则会形成“模型生成成功但 Wiki 只写一半”的不可验收状态。

不得在 Task 9 前修改产品文档孵化读取逻辑；否则系统会出现没有稳定 Wiki 可供孵化的中间版本。

每个 Task 的 Owner checkpoint 都是强制停止点，不因测试通过而自动继续。

## 四、最终完成定义

2.2 只有同时满足以下条件才算完成：

1. 新项目可分别创建在本机不同父目录；
2. 项目移动后能通过身份校验重新定位；
3. 新项目具有完整 README、AGENTS、sources 和 Ingest Contract；
4. 归档后状态为 pending_ingest；
5. L1/L2 可形成完整 Wiki 事务且外发上下文经过安全投影；
6. L3/L4 可在 Obsidian 编辑并本地确认，Gateway 调用数为零；
7. 事务失败或进程中断可回滚或确定性恢复；
8. 同一幂等键不会产生重复页面、段落或成功日志；
9. 产品文档孵化默认只读取已 Ingest Wiki；
10. Ingest 不修改 Raw、当前产品、历史版本和发布 Manifest；
11. A 项目无法读取或修改 B 项目；
12. AC-01～AC-29 均有证据；
13. 全量测试、覆盖率、Ruff、格式和 compileall 门禁通过；
14. 实际投入未超过 12 人日，或超出前已经获得 Owner 明确裁减确认。
