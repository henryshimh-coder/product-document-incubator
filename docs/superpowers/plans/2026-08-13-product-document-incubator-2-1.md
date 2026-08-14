# 产品文档孵化器 2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不超过 7 人日内完成网页单文件归档、材料系列与版本链、固定 8 类材料、两级权威、历史分类调整，以及 L3/L4 零外部调用的本地对照与本地候选发布。

**Architecture:** 在既有 `SourceRecord`、项目级 `raw/`、中央 SQLite、`source-index.json` 和文档草稿发布链路上做兼容扩展。新增材料目录定义和材料写入事务协调器；新归档直接接收浏览器上传的文件名与字节，材料系列使用 `source_records` 的可空扩展字段表达；L3/L4 使用不依赖任何 Gateway 或模型日志器的本地读取服务，并复用现有 `DocumentStore`、草稿编辑、Diff 与 Owner 发布能力。

**Tech Stack:** Python 3.11/3.12、Streamlit 1.60、Pydantic 2、SQLite、Markdown、pypdf、python-docx、filelock、pytest 9、Ruff；不新增运行时依赖。

**Requirement baseline:** `产品文档孵化器_2.1迭代需求文档_v1.0.md`，提交 `ba6d310`。

## Global Constraints

- 目标用户只有 Owner（产品经理），不增加登录、角色、审批流。
- 总投入不得超过 7 人日；任一批次实际投入超过计划 20%，立即停下与 Owner 确认裁减。
- `raw/` 只追加；已归档文件不得修改、覆盖、移动或删除。
- 页面只支持单文件 `.md`、`.txt`、`.pdf`、`.docx`，上限固定 20MB。
- Owner 点击“确认归档”前，`raw/`、SQLite 和 `source-index.json` 均不得产生新增。
- 新材料类型只能是本文固定 8 个编码；旧类型不批量迁移。
- 新权威值只能是 `formal_effective` 或 `discussion_reference`；旧四级值只做兼容展示。
- 材料系列关系只能由 Owner 显式选择建立；不得依据文件名、材料名称或版本字符串自动关联。
- SHA-256 只用于相同字节去重和归档完整性校验，不用于判断逻辑材料关系。
- L3、L4 的读取、对照、关键词定位、本地候选、Diff 和发布全程不得调用外部模型。
- L3、L4 正文、摘录、关键词和人工备注不得进入普通日志、遥测、错误消息、外部缓存或模型调用记录。
- 本地候选记录 `generation_mode=local_manual`，只保存候选 Markdown、材料 ID 和非敏感定位元数据。
- 所有材料读取、系列关联、分类调整与候选创建必须校验 `project_id`。
- 不实现多文件上传、文件夹监听、本地大模型、自动语义比对、历史批量迁移、材料删除和复杂搜索统计。
- 保留 `AuthorityLevel` 的四个历史枚举值，避免破坏既有数据与 1.x/2.0 逻辑。
- 每个任务执行 TDD：先写失败测试、确认失败原因、最小实现、通过测试、再提交。
- 每个 Batch 完成后停止，向 Owner 汇报验证证据和下一批工作，等待确认再继续。
- 每次提交只暂存本任务列出的文件，不得暂存当前工作区已有的其他改动。
- `README.md` 和 `docs/runbook/owner-user-guide.md` 当前已有未提交内容；执行 Task 8 前必须先读取并保留现有内容，只追加 2.1 相关段落，禁止覆盖或丢弃用户改动。

---

## 一、当前代码定位

| 业务板块 | 当前入口与实现 | 当前缺口 | 2.1 落点 |
| --- | --- | --- | --- |
| 原始材料页面 | `src/ui/pages/materials.py` | 手填绝对路径、类型自由文本、四级权威 | 文件上传、确认式表单、8 类、两级权威、系列版本链 |
| 上传组件 | `src/ui/components/file_upload.py` | 固定服务于旧 Ingest，key 与文案不可复用 | 新建材料专用上传组件，不改变旧 Ingest |
| 归档 DTO | `src/application/dto/documents.py:12` | 只接收 `local_path`，无材料身份与系列 | 新建 `dto/materials.py`，旧模块保留兼容导入 |
| 归档用例 | `src/application/use_cases/archive_raw_source.py` | 无固定分类校验、版本链和完整回滚 | 字节归档、服务端治理校验、显式系列关联和失败清理 |
| 原始文件写入 | `src/infrastructure/files/project_source_archive.py` | 只支持 `copy_from(Path)` | 新增 `save(filename, payload)` 和未提交文件清理 |
| 材料模型 | `src/domain/models.py:58` | `SourceRecord` 无材料名称、系列、前序 | 增加三个可空兼容字段，旧记录不回填 |
| 权威枚举 | `src/domain/enums.py:16` | 保存四级历史值 | 枚举不删；新归档只开放两项，显示层映射历史值 |
| 分类口径 | 当前没有唯一目录 | UI、服务、测试可能维护不同值 | 新建 `src/domain/material_catalog.py` |
| SQLite | `migrations.py`、`repositories.py:130` | 无系列字段和草稿生成方式 | 2.1 幂等迁移、部分唯一索引、仓库字段扩展 |
| 来源索引 | `source_index_store.py` | 元数据不足 | schema 2.1，写入名称、系列、前序、版本、权威和安全级别 |
| 历史分类调整 | 当前不存在 | 无同步索引、审计和回滚 | 新建 `ReclassifySource` 和材料写入事务协调器 |
| 外部文档孵化 | `incubate.py` → `IncubateDocument` → Gateway | Fragment 无类型和权威；无 Dify 时无法管理本地草稿 | 补齐元数据；拆出始终可用的草稿工作区 |
| 敏感材料对照 | 当前不存在 | L3/L4 只能归档 | 新建纯本地读取用例和对照组件 |
| 草稿与 Diff | `DocumentStore`、`incubate.py`、`change_diff.py` | 只支持外部 AI 候选 | 增加 `local_manual` 并复用编辑、Diff、发布 |
| 发布 | `PublishDocumentDraft` | 新卡权威硬编码为历史值 | 2.1 新发布卡使用 `formal_effective`，历史卡不改 |
| 组合根 | `src/application/container.py` | 未装配 2.1 服务 | 注入材料查询、调整、本地对照、本地候选和草稿服务 |
| 测试 | `test_archive_raw_source.py`、`test_materials_page.py` 等 | 只覆盖路径归档和基础展示 | 增加 AC-01～AC-31 自动化证据并保留全量回归 |

## 二、目标文件结构

### 2.1 新建文件

| 文件 | 单一职责 |
| --- | --- |
| `src/domain/material_catalog.py` | 固定 8 类、两级新权威、历史中文映射 |
| `src/application/dto/materials.py` | 归档、列表、分类调整、敏感对照和本地候选 DTO |
| `src/application/ports/materials.py` | 材料事务、查询、本地对照和本地候选协议 |
| `src/application/use_cases/list_materials.py` | 材料列表、系列摘要和版本链 |
| `src/application/use_cases/reclassify_source.py` | 历史类型单条调整 |
| `src/application/use_cases/compare_sensitive_source.py` | L3/L4 当前方案／模板本地读取 |
| `src/application/use_cases/create_local_document_draft.py` | 创建 `local_manual` 候选 |
| `src/application/use_cases/manage_document_drafts.py` | 始终可用的草稿列表、读取和保存 |
| `src/domain/services/draft_versions.py` | 外部和本地候选共用的版本 ID |
| `src/infrastructure/db/material_unit_of_work.py` | SQLite、来源索引和日志的一致性协调 |
| `src/infrastructure/files/project_audit_log.py` | `wiki/log.md` 快照、追加和恢复 |
| `src/ui/components/material_upload.py` | 单文件选择、识别和 SHA-256 展示 |
| `src/ui/components/sensitive_comparison.py` | 双栏本地展示与完全匹配关键词高亮 |
| `tests/unit/domain/test_material_catalog.py` | 固定目录和显示映射 |
| `tests/unit/ui/test_sensitive_comparison.py` | HTML 转义和关键词高亮 |
| `tests/integration/use_cases/test_reclassify_source.py` | 分类调整与回滚 |
| `tests/integration/use_cases/test_compare_sensitive_source.py` | 本地读取、完整性和隔离 |
| `tests/integration/use_cases/test_create_local_document_draft.py` | 本地候选和零外部调用 |
| `tests/security/test_sensitive_local_only.py` | 敏感数据零泄漏 |
| `docs/qa/product-document-incubator-2.1-acceptance.md` | AC-01～AC-31 验收报告 |

### 2.2 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `src/domain/enums.py`、`models.py`、`incubator.py` | 归档方式、治理字段和草稿生成方式 |
| `src/application/dto/documents.py` | 兼容转发旧归档 DTO |
| `src/application/ports/incubator.py`、`repositories.py` | 草稿工作区与系列查询接口 |
| `src/application/use_cases/archive_raw_source.py` | 字节归档、治理校验、系列关联、失败清理 |
| `src/application/use_cases/incubate_document.py` | 共享版本工厂，Fragment 增加类型与权威 |
| `src/application/use_cases/publish_document_draft.py` | 本地引用和新发布权威 |
| `src/infrastructure/db/migrations.py`、`repositories.py` | 2.1 迁移和字段持久化 |
| `src/infrastructure/files/project_source_archive.py` | 上传字节保存和未提交清理 |
| `src/infrastructure/files/source_index_store.py` | schema 2.1、原子替换、快照恢复 |
| `src/infrastructure/files/document_store.py` | 模板读取和草稿安全接口 |
| `src/infrastructure/gateways/schemas.py` | Document Fragment 类型和权威 |
| `src/application/container.py` | 装配全部 2.1 服务 |
| `src/ui/pages/materials.py` | 上传、版本链、历史调整、本地对照 |
| `src/ui/pages/incubate.py` | 无 Dify 时仍可管理本地草稿 |
| `tests/integration/db/test_migrations.py`、`test_repositories.py` | 迁移、系列和草稿模式 |
| `tests/integration/use_cases/test_archive_raw_source.py` | 新系列、新版本、隔离、回滚 |
| `tests/e2e/test_materials_page.py`、`test_incubate_page.py` | 2.1 页面流程 |
| `tests/integration/gateways/test_document_gateway.py` | Document Workflow 元数据契约 |
| `tests/integration/use_cases/test_incubate_document.py`、`test_publish_document_draft.py` | 外部与本地候选回归 |
| `tests/security/test_project_path_isolation.py` | 系列、本地对照与候选隔离 |
| `docs/runbook/dify-document-workflow.md` | 类型、权威和参考材料边界 |
| `docs/runbook/owner-user-guide.md`、`README.md` | 2.1 使用说明与入口 |

## 三、稳定接口与数据约定

```python
class MaterialArchiveMode(StrEnum):
    NEW_MATERIAL = "new_material"
    NEW_VERSION = "new_version"


class DocumentGenerationMode(StrEnum):
    EXTERNAL_AI = "external_ai"
    LOCAL_MANUAL = "local_manual"


@dataclass(frozen=True)
class MaterialTypeDefinition:
    code: str
    label: str
    description: str
    examples: tuple[str, ...]
    order: int


MATERIAL_TYPES: tuple[MaterialTypeDefinition, ...]
MATERIAL_TYPES_BY_CODE: dict[str, MaterialTypeDefinition]
NEW_AUTHORITY_LEVELS: tuple[AuthorityLevel, AuthorityLevel]
```

```python
class SourceRecord(DomainModel):
    # 既有字段保持不变
    material_name: NonEmptyStr | None = None
    material_series_id: NonEmptyStr | None = None
    previous_source_id: NonEmptyStr | None = None


class DocumentDraft(DomainModel):
    # 既有字段保持不变
    generation_mode: DocumentGenerationMode = DocumentGenerationMode.EXTERNAL_AI
```

```python
class ArchiveRawSourceInput(BaseModel):
    project_id: str
    uploaded_name: str
    uploaded_bytes: bytes
    material_name: str
    archive_mode: MaterialArchiveMode
    target_series_id: str | None = None
    source_type: str
    authority_level: AuthorityLevel
    source_department: str
    document_date: date
    material_version: str
    security_level: SecurityLevel
    is_redacted_confirmed: bool
    allow_external_model: bool


class ReclassifySourceInput(BaseModel):
    project_id: str
    source_id: str
    new_source_type: str
    owner_name: str


class SensitiveComparisonInput(BaseModel):
    project_id: str
    source_id: str


class CreateLocalDraftInput(BaseModel):
    project_id: str
    source_id: str
    requested_by: str
```

```python
class MaterialWriteUnitOfWork(Protocol):
    def persist_archive(
        self, *, source: SourceRecord, series_seed: SourceRecord | None
    ) -> None: ...

    def reclassify(
        self, *, before: SourceRecord, after: SourceRecord, audit_line: str
    ) -> None: ...


class MaterialQuerying(Protocol):
    def list_materials(self, project_id: str) -> list[ArchivedSourceView]: ...
    def list_series(self, project_id: str) -> list[MaterialSeriesView]: ...
    def get_series(self, project_id: str, series_id: str) -> MaterialSeriesView: ...


class SensitiveSourceReviewing(Protocol):
    def execute(self, command: SensitiveComparisonInput) -> SensitiveComparisonView: ...


class LocalDraftCreating(Protocol):
    def execute(self, command: CreateLocalDraftInput) -> IncubationView: ...


class DocumentDraftWorkspace(Protocol):
    def list_drafts(self, project_id: str) -> list[DocumentDraft]: ...
    def read_draft(self, project_id: str, draft_id: str) -> str: ...
    def save_draft(self, project_id: str, draft_id: str, markdown: str) -> DocumentDraft: ...
```

SQLite 只增加可空列或带默认值的列：

```sql
ALTER TABLE source_records ADD COLUMN material_name TEXT;
ALTER TABLE source_records ADD COLUMN material_series_id TEXT;
ALTER TABLE source_records ADD COLUMN previous_source_id TEXT;
ALTER TABLE document_drafts ADD COLUMN generation_mode TEXT NOT NULL DEFAULT 'external_ai';

CREATE INDEX IF NOT EXISTS idx_source_project_series
ON source_records(project_id, material_series_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_series_version
ON source_records(project_id, material_series_id, document_version)
WHERE material_series_id IS NOT NULL;
```

`source-index.json` 以数据库为事实源，schema 版本升级为 `2.1`。每条记录写入 `source_id`、`material_name`、`material_series_id`、`previous_source_id`、`material_version`、`filename`、`archive_path`、`sha256`、`source_type`、`authority_level`、`security_level`、`ingest_status`、`created_at`。旧记录的治理字段保持 `null`；显示名称只在内存中使用 `Path(original_filename).stem`，不得回写。

新材料系列 ID 由注入式 `series_id_factory(project_id)` 生成，生产格式固定为 `MAT-{project_id}-{12位大写十六进制}`；测试注入固定值，不依赖随机结果。

## 四、批次与工时

| Batch | 内容 | 计划投入 | 完成确认点 |
| --- | --- | ---: | --- |
| B1 | 固定目录、领域模型、SQLite 2.1 兼容迁移 | 1.0 人日 | 旧数据库可读，固定目录和两级权威测试通过 |
| B2 | 字节上传、材料系列、版本链、确认式归档与列表 | 2.25 人日 | 归档、系列、权限和页面定向测试通过 |
| B3 | 历史类型单条调整与事务回滚 | 0.75 人日 | 分类调整和三处一致性测试通过 |
| B4 | L3/L4 本地对照、本地候选、编辑、Diff 与发布 | 2.25 人日 | 敏感流程模型调用为零且可发布 |
| B5 | 外部工作流元数据、全量回归、文档和验收报告 | 0.75 人日 | AC-01～AC-31 全部有证据 |
| **合计** |  | **7.0 人日** | 超过前必须请 Owner 确认裁减 |

---

## Batch B1 — 材料治理内核（1.0 人日）

### Task 1: 固定目录、兼容模型与 SQLite 2.1 迁移

**Files:**
- Create: `src/domain/material_catalog.py`
- Modify: `src/domain/enums.py`
- Modify: `src/domain/models.py`
- Modify: `src/domain/incubator.py`
- Modify: `src/infrastructure/db/migrations.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `src/application/ports/repositories.py`
- Test: `tests/unit/domain/test_material_catalog.py`
- Test: `tests/unit/domain/test_models.py`
- Test: `tests/integration/db/test_migrations.py`
- Test: `tests/integration/db/test_repositories.py`

**Interfaces:**
- Produces: 固定材料目录、两级新权威、历史显示映射、扩展 Source 和 Draft。
- Produces: `list_for_series()`、`find_latest_for_series()`、`find_by_series_version()`。

- [ ] **Step 1: 写固定目录和历史映射失败测试**

```python
def test_material_catalog_has_exact_eight_items_in_product_order() -> None:
    from src.domain.material_catalog import MATERIAL_TYPES

    assert [item.code for item in MATERIAL_TYPES] == [
        "product_requirement", "business_rule", "customer_market_material",
        "meeting_minutes", "risk_compliance", "technical_specification",
        "operation_feedback", "other",
    ]
    assert [item.order for item in MATERIAL_TYPES] == list(range(1, 9))


def test_authority_labels_keep_historical_values_visible() -> None:
    assert authority_label(AuthorityLevel.FORMAL_EFFECTIVE) == "正式基线依据"
    assert authority_label(AuthorityLevel.FORMAL_DECISION) == "正式基线依据（历史值）"
    assert authority_label(AuthorityLevel.PROFESSIONAL_OPINION) == "参考材料（历史值）"
    assert authority_label(AuthorityLevel.DISCUSSION_REFERENCE) == "参考材料"
```

- [ ] **Step 2: 运行测试并确认目录模块尚不存在**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_material_catalog.py -q`

Expected: FAIL with `ModuleNotFoundError: src.domain.material_catalog`。

- [ ] **Step 3: 实现目录与严格校验**

8 项文案逐字采用需求第四章；`MATERIAL_TYPES_BY_CODE` 从 tuple 派生。实现 `require_new_material_type()`，未知值、大小写变体和前后空格一律抛 `MATERIAL_TYPE_INVALID`；实现 `material_type_label()` 和 `authority_label()`。

- [ ] **Step 4: 写迁移、旧行和唯一约束失败测试**

测试两次 `migrate()` 后存在三个 Source 列、`generation_mode` 和版本 `2.1`；旧 Source 三字段为 `None`；旧 Draft 模式为 `external_ai`；同项目同系列同版本第二次插入触发唯一约束。

- [ ] **Step 5: 实现模型、迁移和仓库**

迁移使用 `_add_column_if_missing()`，不扫描旧行。系列链尾查询以“不存在 successor”为标准；查到多个链尾抛 `MATERIAL_SERIES_FORKED`，不得按时间猜测。

- [ ] **Step 6: 运行 B1 测试**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_material_catalog.py tests/unit/domain/test_models.py tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py -q`

Expected: PASS。

- [ ] **Step 7: 提交 B1**

```bash
git add src/domain/material_catalog.py src/domain/enums.py src/domain/models.py src/domain/incubator.py src/infrastructure/db/migrations.py src/infrastructure/db/repositories.py src/application/ports/repositories.py tests/unit/domain/test_material_catalog.py tests/unit/domain/test_models.py tests/integration/db/test_migrations.py tests/integration/db/test_repositories.py
git commit -m "feat: add material governance primitives"
```

**B1 确认点：** 停止开发，展示固定 8 类、两级新权威、旧行空字段和迁移幂等证据；确认后进入 B2。

---

## Batch B2 — 确认式归档与版本链（2.25 人日）

### Task 2: 上传字节归档、系列关系与一致性协调

**Files:**
- Create: `src/application/dto/materials.py`
- Create: `src/application/ports/materials.py`
- Create: `src/infrastructure/db/material_unit_of_work.py`
- Modify: `src/application/dto/documents.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/application/use_cases/archive_raw_source.py`
- Modify: `src/infrastructure/files/project_source_archive.py`
- Modify: `src/infrastructure/files/source_index_store.py`
- Modify: `src/application/container.py`
- Test: `tests/integration/files/test_project_source_archive.py`
- Test: `tests/integration/use_cases/test_archive_raw_source.py`
- Test: `tests/security/test_project_path_isolation.py`

**Interfaces:**
- Consumes: Task 1 的目录、Source 扩展和系列查询。
- Produces: 新 Archive DTO、`MaterialWriteUnitOfWork.persist_archive()`、字节保存和索引快照恢复。

- [ ] **Step 1: 写确认前零写入与服务端校验失败测试**

构造新 DTO 但不执行用例，断言 `raw/`、Source 仓库和索引为空。分别执行未知类型、带前后空格的类型、历史权威、项目未授权但材料请求外部调用、L3/L4 请求外部调用、未脱敏的 L1/L2 请求外部调用、空名称、空版本、非法文件名和 20MB 超限请求；每项均断言三处无新增。

- [ ] **Step 2: 运行测试并确认旧 DTO 不接受上传字节**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_archive_raw_source.py -q`

Expected: FAIL，原因为 DTO 字段或 `ProjectSourceArchive.save` 不存在。

- [ ] **Step 3: 实现字节归档与兼容导入**

`ProjectSourceArchive.save(filename, payload)` 调用 `validate_upload()`，对上传字节计算 SHA-256，使用临时文件、`fsync`、硬链接和复制后哈希校验写入。`copy_from()` 保留为兼容 wrapper。`discard_uncommitted()` 只删除 `duplicate=False` 且位于当前 `raw_root` 的本次文件。

- [ ] **Step 4: 写新系列、新版本和旧材料按需建系列测试**

```python
def test_different_filename_can_join_existing_series(env) -> None:
    first = env.archive.execute(new_material_command("需求说明.md", b"# v1", "v1.0"))
    second = env.archive.execute(
        new_version_command("产品需求终稿.md", b"# v2", "v2.0", first.material_series_id)
    )

    assert second.material_series_id == first.material_series_id
    assert second.previous_source_id == first.source_id
    assert second.material_name == first.material_name
    assert Path(first.archive_path).read_bytes() == b"# v1"
```

补充：相似文件名不自动关联；新版本只能连接链尾；同系列版本重复被拒绝；跨项目系列被拒绝；旧材料按需建系列时除三个治理字段外逐项不变；相同 SHA-256 返回原记录。

- [ ] **Step 5: 实现归档顺序与事务协调**

固定顺序为：从 `ProjectRepository` 读取并校验当前项目 → 校验文件 → 校验类型／权威／安全组合 → SHA-256 去重 → 决定系列 → 写未提交文件 → UoW 持久化 → 返回视图。请求外部调用时必须同时满足项目允许、材料已脱敏且安全等级为 L1/L2，否则服务端拒绝。`NEW_VERSION` 强制继承链尾材料名称。

由于 DTO 默认会执行字符串去空格，`source_type` 必须增加 `mode="before"` 的字段校验器，在 Pydantic 规范化前拒绝 `value != value.strip()`，防止空格变体被静默接受。

UoW 快照索引并 `BEGIN IMMEDIATE`，在同一连接再次校验链尾和版本唯一性，按需更新旧材料三个治理字段，插入新 Source，以事务中的项目全部 Source 原子替换索引，最后提交。失败时 rollback 数据库、恢复索引，并由用例删除未提交归档。

- [ ] **Step 6: 写索引、数据库和哈希失败回滚测试**

```python
def test_index_failure_removes_archive_and_rolls_back_database(env, monkeypatch) -> None:
    monkeypatch.setattr(env.index, "replace_all", fail_with_oserror)
    with pytest.raises(RuntimeError, match="SOURCE_ARCHIVE_COMMIT_FAILED"):
        env.archive.execute(new_material_command("需求.md", b"# 需求", "v1.0"))
    assert env.sources.list_for_project("PROJECT_A") == []
    assert not [p for p in env.paths.raw_root.rglob("*") if p.is_file()]
    assert not env.index.path.exists()
```

- [ ] **Step 7: 运行 Task 2 测试并提交**

Run: `.venv/bin/python -m pytest tests/integration/files/test_project_source_archive.py tests/integration/use_cases/test_archive_raw_source.py tests/security/test_project_path_isolation.py -q`

Expected: PASS。

```bash
git add src/application/dto/materials.py src/application/dto/documents.py src/application/ports/materials.py src/application/ports/incubator.py src/application/use_cases/archive_raw_source.py src/infrastructure/db/material_unit_of_work.py src/infrastructure/files/project_source_archive.py src/infrastructure/files/source_index_store.py src/application/container.py tests/integration/files/test_project_source_archive.py tests/integration/use_cases/test_archive_raw_source.py tests/security/test_project_path_isolation.py
git commit -m "feat: archive governed material uploads"
```

### Task 3: 材料查询、确认式页面与版本链展示

**Files:**
- Create: `src/application/use_cases/list_materials.py`
- Create: `src/ui/components/material_upload.py`
- Modify: `src/application/ports/materials.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/materials.py`
- Test: `tests/e2e/test_materials_page.py`

**Interfaces:**
- Produces: `ListMaterials.list_materials/list_series/get_series` 和材料上传组件。

- [ ] **Step 1: 写选择文件但未确认时零归档页面测试**

页面设置上传值后，断言展示文件名、大小和 SHA-256 前 12 位；仓库、`raw/` 和索引仍为空。断言不存在 `materials_local_path`；材料名称预填；归档方式无默认；类型只有 8 个中文选项；权威只有 2 项；L3/L4 禁用外部调用。

- [ ] **Step 2: 运行页面测试并确认旧路径表单失败**

Run: `.venv/bin/python -m pytest tests/e2e/test_materials_page.py -q`

Expected: FAIL，页面仍存在路径输入或缺少 `materials_upload`。

- [ ] **Step 3: 实现上传组件与确认表单**

组件只持有 `UploadedFile.name/getvalue()`，不写临时文件。文件变化时才用 `Path(name).stem` 预填名称，避免 rerun 覆盖 Owner 编辑。类型、权威、归档方式均以 `None` 为第一项；选择新版本后才显示系列下拉和版本链；安全授权按需求 9.3 联动。

- [ ] **Step 4: 实现材料查询与兼容列表**

`ListMaterials` 只读当前项目仓库，不解析索引。版本链严格按 `previous_source_id` 排列；断链、循环、分叉返回固定错误。旧材料名称使用文件 stem，旧类型显示“历史类型：{值}”，旧权威按映射显示且不回写。

- [ ] **Step 5: 运行 Task 3 测试并提交**

Run: `.venv/bin/python -m pytest tests/e2e/test_materials_page.py tests/integration/use_cases/test_archive_raw_source.py -q`

Expected: PASS。

```bash
git add src/application/use_cases/list_materials.py src/application/ports/materials.py src/application/container.py src/ui/components/material_upload.py src/ui/pages/materials.py tests/e2e/test_materials_page.py
git commit -m "feat: add confirmed material upload workflow"
```

**B2 确认点：** 启动本地 Streamlit，演示选择但不归档、新材料、不同文件名的新版本、完整版本链、两级权威和 L3 禁用外部调用；停下等待 Owner 确认。

---

## Batch B3 — 历史分类单条调整（0.75 人日）

### Task 4: 分类调整、索引同步、审计与失败回滚

**Files:**
- Create: `src/application/use_cases/reclassify_source.py`
- Create: `src/infrastructure/files/project_audit_log.py`
- Modify: `src/application/ports/materials.py`
- Modify: `src/infrastructure/db/material_unit_of_work.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/materials.py`
- Test: `tests/integration/use_cases/test_reclassify_source.py`
- Test: `tests/e2e/test_materials_page.py`
- Test: `tests/security/test_project_path_isolation.py`

**Interfaces:**
- Produces: `ReclassifySource.execute()` 和 `MaterialWriteUnitOfWork.reclassify()`。

- [ ] **Step 1: 写不可变字段与权限失败测试**

```python
def test_reclassify_changes_only_source_type(env) -> None:
    before = env.sources.get("SRC-LEGACY")
    env.reclassify.execute(reclassify_command(before.id, "risk_compliance"))
    after = env.sources.get(before.id)
    assert after.model_copy(update={"source_type": before.source_type}) == before
    assert Path(after.archive_path).read_bytes() == Path(before.archive_path).read_bytes()
```

标准 8 类材料、未知目标类型和跨项目 Source 均拒绝。

- [ ] **Step 2: 运行测试并确认用例不存在**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_reclassify_source.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现审计适配器与事务**

`ProjectAuditLog` 只允许当前 `wiki/log.md`，提供 `snapshot/append/restore`。审计行只含时间、材料 ID、旧类型、新类型、Owner，不接收正文。UoW 在同一 SQLite 事务内更新类型、原子替换索引、追加日志；任何失败均 rollback 并恢复索引和日志原字节。

- [ ] **Step 4: 写索引和日志失败回滚测试**

分别模拟 `index.replace_all` 和 `audit.append` 抛错；断言 Source、索引、日志与操作前逐字节相同。

- [ ] **Step 5: 增加页面调整入口**

只给历史类型显示“调整分类”。保存前展示“不修改归档文件、路径或内容指纹”；成功和失败文案逐字使用需求 9.6。

- [ ] **Step 6: 运行 B3 测试并提交**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_reclassify_source.py tests/e2e/test_materials_page.py tests/security/test_project_path_isolation.py -q`

Expected: PASS。

```bash
git add src/application/use_cases/reclassify_source.py src/infrastructure/files/project_audit_log.py src/application/ports/materials.py src/infrastructure/db/material_unit_of_work.py src/application/container.py src/ui/pages/materials.py tests/integration/use_cases/test_reclassify_source.py tests/e2e/test_materials_page.py tests/security/test_project_path_isolation.py
git commit -m "feat: reclassify legacy materials atomically"
```

**B3 确认点：** 展示调整前后数据库、索引和日志，以及模拟失败后三者原样恢复；停下等待 Owner 确认。

---

## Batch B4 — L3/L4 本地对照与本地候选（2.25 人日）

### Task 5: 敏感材料纯本地读取、双栏对照和关键词定位

**Files:**
- Create: `src/application/use_cases/compare_sensitive_source.py`
- Create: `src/ui/components/sensitive_comparison.py`
- Modify: `src/application/dto/materials.py`
- Modify: `src/application/ports/materials.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/materials.py`
- Test: `tests/integration/use_cases/test_compare_sensitive_source.py`
- Test: `tests/unit/ui/test_sensitive_comparison.py`
- Test: `tests/security/test_sensitive_local_only.py`
- Test: `tests/security/test_project_path_isolation.py`

**Interfaces:**
- Produces: `CompareSensitiveSource.execute()`、`SensitiveComparisonView` 和 `highlight_exact()`。
- 禁止依赖: Gateway、ModelCallLogger、AI Cache 和 HTTP Client。

- [ ] **Step 1: 写 L3/L4 本地读取与拒绝测试**

```python
def test_l3_comparison_reads_current_and_archive_locally(env) -> None:
    result = env.compare.execute(SensitiveComparisonInput(
        project_id="PROJECT_A", source_id="SRC-L3"
    ))
    assert result.left_label == "当前生效方案"
    assert result.left_markdown.startswith("# 当前产品方案")
    assert "敏感规则" in result.sensitive_text
```

增加：无当前方案使用替换项目名后的模板；L1/L2、跨项目、路径逃逸和哈希不一致均拒绝；异常文本不含正文。

- [ ] **Step 2: 运行测试并确认用例不存在**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_compare_sensitive_source.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现纯本地读取**

顺序：读 Source → 校验项目 → 校验 L3/L4 → 校验路径位于当前 `raw_root` → 读字节和哈希 → `extract_document_bytes()` → 读当前方案或模板 → 返回内存视图。异常只返回固定错误码，不写任何日志或缓存。

- [ ] **Step 4: 写并实现关键词高亮安全测试**

```python
def test_highlight_exact_escapes_html() -> None:
    html = highlight_exact("<script>规则A与规则AB</script>", "规则A")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert html.count("<mark") == 2
```

先 HTML escape，再对完全匹配关键词加 `<mark>`；空关键词只返回转义文本。

- [ ] **Step 5: 实现页面入口和 Session 清理**

L3/L4 版本行显示“与当前方案对照”。组件使用双栏独立滚动区域；顶部固定显示“仅本地处理，未调用外部模型”。关键词只放 Session State；关闭、切换 Source 或项目时清理。页面不得出现 AI 总结、建议或外部调用按钮。

- [ ] **Step 6: 写零模型调用和零泄漏测试**

操作前后 `model_call_logs`、cache 行数相同；扫描项目日志、事件日志、模型日志、异常和捕获日志，均不含敏感句与关键词。

- [ ] **Step 7: 运行并提交 Task 5**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_compare_sensitive_source.py tests/unit/ui/test_sensitive_comparison.py tests/security/test_sensitive_local_only.py tests/security/test_project_path_isolation.py -q`

Expected: PASS。

```bash
git add src/application/use_cases/compare_sensitive_source.py src/ui/components/sensitive_comparison.py src/application/dto/materials.py src/application/ports/materials.py src/application/container.py src/ui/pages/materials.py tests/integration/use_cases/test_compare_sensitive_source.py tests/unit/ui/test_sensitive_comparison.py tests/security/test_sensitive_local_only.py tests/security/test_project_path_isolation.py
git commit -m "feat: compare sensitive materials locally"
```

### Task 6: 本地候选、草稿工作区与 Owner 发布

**Files:**
- Create: `src/domain/services/draft_versions.py`
- Create: `src/application/use_cases/manage_document_drafts.py`
- Create: `src/application/use_cases/create_local_document_draft.py`
- Modify: `src/domain/incubator.py`
- Modify: `src/application/dto/materials.py`
- Modify: `src/application/ports/incubator.py`
- Modify: `src/application/ports/materials.py`
- Modify: `src/application/use_cases/incubate_document.py`
- Modify: `src/application/use_cases/publish_document_draft.py`
- Modify: `src/infrastructure/db/repositories.py`
- Modify: `src/infrastructure/files/document_store.py`
- Modify: `src/application/container.py`
- Modify: `src/ui/pages/materials.py`
- Modify: `src/ui/pages/incubate.py`
- Test: `tests/integration/use_cases/test_create_local_document_draft.py`
- Test: `tests/integration/use_cases/test_incubate_document.py`
- Test: `tests/integration/use_cases/test_publish_document_draft.py`
- Test: `tests/e2e/test_incubate_page.py`
- Test: `tests/security/test_sensitive_local_only.py`

**Interfaces:**
- Produces: `CreateLocalDocumentDraft.execute()`、`ManageDocumentDrafts` 和 `LOCAL_MANUAL` 草稿。
- 保持: 外部 `IncubateDocument.execute()` 行为与返回类型。

- [ ] **Step 1: 写当前方案和模板两种候选失败测试**

```python
def test_local_draft_copies_current_without_model_call(env) -> None:
    before_calls = env.model_call_count()
    result = env.local_drafts.execute(local_command())
    assert result.markdown == env.store.read_current()
    assert result.draft.generation_mode.value == "local_manual"
    assert result.draft.source_ids == ["SRC-L3"]
    assert all(item.excerpt is None for item in result.draft.section_citations)
    assert env.model_call_count() == before_calls
```

无当前方案时断言模板 `{产品名称}` 已替换为 `Project.name`；L1/L2 和跨项目拒绝；文件或数据库失败不留草稿半成品。

- [ ] **Step 2: 运行测试并确认本地候选用例不存在**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_create_local_document_draft.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 抽取版本工厂和始终可用的草稿工作区**

把 `VersionIdFactory` 移到 `draft_versions.py`。`ManageDocumentDrafts` 负责项目归属、草稿路径防逃逸、读取、Markdown 校验、原子保存、哈希更新和状态改为 `PENDING_OWNER`。`incubate.py` 不再因 Dify 未配置提前 return：只禁用 AI 生成区，本地／历史草稿仍可编辑、Diff 和发布。

- [ ] **Step 4: 实现本地候选**

有当前方案时复制当前 Markdown 并记录 Manifest parent；无当前方案时读取模板并替换项目名。每个 H2 建立只含 heading、source ID、`LOCAL-MANUAL`、`owner-local-review`、`excerpt=None` 的引用。写草稿文件后插入 `generation_mode=LOCAL_MANUAL`；数据库失败删除本次草稿目录。服务构造函数不得接受 Gateway 或 Logger。

- [ ] **Step 5: 调整发布校验**

`DocumentSectionCitation.excerpt` 改为可空，但由 `DocumentDraft` 模型校验器保证只有 `LOCAL_MANUAL` 可为空；外部候选仍要求非空摘录。发布继续验证每个 H2、Source 项目、Source ID、草稿哈希、Markdown、父版本和 Manifest。

发布本地候选前，必须再次验证每个关联 Source 的归档路径位于当前项目 `raw_root`、文件存在、size 与记录一致、SHA-256 与记录一致；任一失败抛 `PUBLISH_SOURCE_INTEGRITY_FAILED`，不得只依赖创建候选时的校验。增加“创建候选后篡改 L3 归档，发布被拒绝且当前方案不变”的回归测试。新发布知识卡权威使用 `AuthorityLevel.FORMAL_EFFECTIVE`。

- [ ] **Step 6: 写无 Dify 编辑发布测试**

页面在未配置 Dify 时仍展示本地草稿编辑器；保存后进入 `PENDING_OWNER`，显示 Diff，并可发布到 `wiki/current/当前产品方案.md`。集成测试断言全程模型调用数不变。

- [ ] **Step 7: 运行并提交 B4**

Run: `.venv/bin/python -m pytest tests/integration/use_cases/test_create_local_document_draft.py tests/integration/use_cases/test_incubate_document.py tests/integration/use_cases/test_publish_document_draft.py tests/e2e/test_incubate_page.py tests/security/test_sensitive_local_only.py -q`

Expected: PASS。

```bash
git add src/domain/services/draft_versions.py src/application/use_cases/manage_document_drafts.py src/application/use_cases/create_local_document_draft.py src/domain/incubator.py src/application/dto/materials.py src/application/ports/incubator.py src/application/ports/materials.py src/application/use_cases/incubate_document.py src/application/use_cases/publish_document_draft.py src/infrastructure/db/repositories.py src/infrastructure/files/document_store.py src/application/container.py src/ui/pages/materials.py src/ui/pages/incubate.py tests/integration/use_cases/test_create_local_document_draft.py tests/integration/use_cases/test_incubate_document.py tests/integration/use_cases/test_publish_document_draft.py tests/e2e/test_incubate_page.py tests/security/test_sensitive_local_only.py
git commit -m "feat: create and publish local manual drafts"
```

**B4 确认点：** 使用 L3 夹具演示“本地对照 → 关键词 → 候选 → 编辑 → Diff → 发布”，展示模型调用表和外部请求记录前后均为零；停下等待 Owner 确认。

---

## Batch B5 — 工作流元数据、全量回归与交付（0.75 人日）

### Task 7: 外部候选携带固定类型与两级权威

**Files:**
- Modify: `src/infrastructure/gateways/schemas.py`
- Modify: `src/application/use_cases/incubate_document.py`
- Modify: `tests/integration/gateways/test_document_gateway.py`
- Modify: `tests/integration/use_cases/test_incubate_document.py`
- Modify: `docs/runbook/dify-document-workflow.md`

**Interfaces:**
- Produces: `DocumentSourceFragmentInput.source_type/authority_level`。
- 保持: L3/L4 不进入外部工作流，输出与引用逐字校验不变。

- [ ] **Step 1: 写 Fragment 元数据失败测试**

```python
def test_document_fragments_include_type_and_authority(env) -> None:
    env.service.execute(env.command())
    fragment = env.gateway.inputs["source_fragments"][0]
    assert fragment["source_type"] == "product_requirement"
    assert fragment["authority_level"] == "formal_effective"
```

- [ ] **Step 2: 运行测试并确认字段缺失**

Run: `.venv/bin/python -m pytest tests/integration/gateways/test_document_gateway.py tests/integration/use_cases/test_incubate_document.py -q`

Expected: FAIL，Fragment 缺少治理字段。

- [ ] **Step 3: 扩展契约和手册**

`DocumentSourceFragmentInput` 增加 `source_type: NonEmptyStr` 与 `authority_level: AuthorityLevel`。用例从已校验 Source 填入。手册明确 `formal_effective` 可作确定性依据；`discussion_reference` 只能支持建议或待确认内容；最终生效仍须 Owner 发布。

- [ ] **Step 4: 运行并提交 Task 7**

Run: `.venv/bin/python -m pytest tests/integration/gateways/test_document_gateway.py tests/integration/use_cases/test_incubate_document.py -q`

Expected: PASS。

```bash
git add src/infrastructure/gateways/schemas.py src/application/use_cases/incubate_document.py tests/integration/gateways/test_document_gateway.py tests/integration/use_cases/test_incubate_document.py docs/runbook/dify-document-workflow.md
git commit -m "feat: send material governance metadata to document workflow"
```

### Task 8: Owner 文档、AC 验收和全量质量门禁

**Files:**
- Modify: `README.md`
- Modify: `docs/runbook/owner-user-guide.md`
- Create: `docs/qa/product-document-incubator-2.1-acceptance.md`
- Modify: `tests/e2e/test_incubator_full_success.py`
- Modify: `tests/e2e/test_materials_page.py`
- Modify: `tests/e2e/test_incubate_page.py`
- Modify: `tests/security/test_sensitive_local_only.py`

**Interfaces:**
- Produces: AC-01～AC-31 的测试、命令、结果和证据路径。

- [ ] **Step 1: 增加 2.1 主成功流程**

固定流程：创建项目 → 页面上传 L2 标准材料 → 外部候选 → 发布 → 下载；再上传 L3 新版本 → 本地对照 → 本地候选 → 发布。断言两个项目的 `raw/`、草稿、当前方案、索引和日志完全隔离。

- [ ] **Step 2: 运行 2.1 定向测试**

Run: `.venv/bin/python -m pytest tests/unit/domain/test_material_catalog.py tests/integration/use_cases/test_archive_raw_source.py tests/integration/use_cases/test_reclassify_source.py tests/integration/use_cases/test_compare_sensitive_source.py tests/integration/use_cases/test_create_local_document_draft.py tests/e2e/test_materials_page.py tests/e2e/test_incubate_page.py tests/security/test_sensitive_local_only.py tests/security/test_project_path_isolation.py -q`

Expected: PASS，零失败。

- [ ] **Step 3: 更新 Owner 说明和验收报告**

说明书只保留：选择文件、材料名称、新材料／新版本、8 类、两级权威、安全级别、确认归档、历史分类调整、L3/L4 本地对照、本地候选、Diff 和发布。验收报告每项写验收编号、测试 ID、执行命令、结果、证据路径和结论；无证据只能标记不通过。

- [ ] **Step 4: 运行全量质量门禁**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q src tests scripts
.venv/bin/coverage run -m pytest -q
.venv/bin/coverage report --include='src/domain/*,src/application/*'
git diff --check
```

Expected: pytest 零失败；Ruff 零错误；format 无改动；compileall 退出码 0；领域／应用覆盖率不低于当前实测基线 94%；diff 无空白错误。

- [ ] **Step 5: 执行 Streamlit 人工验收**

在 1440×1024 视口执行 B2、B3、B4 三条流程。截图不得含设备绝对路径、敏感内容泄漏或 L3/L4 外部调用按钮。刷新后版本链、历史类型、本地草稿和当前方案仍可恢复。

- [ ] **Step 6: 提交 B5**

```bash
git add README.md docs/runbook/owner-user-guide.md docs/qa/product-document-incubator-2.1-acceptance.md tests/e2e/test_incubator_full_success.py tests/e2e/test_materials_page.py tests/e2e/test_incubate_page.py tests/security/test_sensitive_local_only.py
git commit -m "docs: deliver product document incubator 2.1"
```

**B5 最终确认点：** 提交验收报告、全量测试、页面截图和最终 SHA；只有 AC-01～AC-31 全部通过才准许标记 2.1 完成。

---

## 五、需求覆盖矩阵

| 验收范围 | 实施任务 | 自动化证据 |
| --- | --- | --- |
| AC-01～AC-04 页面选取与安全 | Task 2、3 | Archive 与 Materials 页面测试 |
| AC-05～AC-10 名称和版本链 | Task 1～3 | 迁移、仓库、归档、页面测试 |
| AC-11～AC-13 固定分类和编码 | Task 1～3、7 | 目录、归档、Gateway 测试 |
| AC-14～AC-16 两级权威 | Task 1、3、6、7 | 映射、页面、发布、Gateway 测试 |
| AC-17～AC-20 L3/L4 本地处理 | Task 5、6 | 对照、本地候选、零泄漏测试 |
| AC-21～AC-22 原文件和完整性 | Task 2 | Archive 和回滚测试 |
| AC-23～AC-25 中文与历史兼容 | Task 1、3 | 目录和列表测试 |
| AC-26～AC-28 分类调整与回滚 | Task 4 | Reclassify 测试 |
| AC-29 市场证据口径 | Task 1、2、7 | 新归档拒绝旧值，历史兼容测试继续通过 |
| AC-30 项目隔离 | Task 2、4、5、6、8 | Path isolation 和主流程 |
| AC-31 既有流程回归 | Task 8 | 全量 pytest、Ruff、compileall、coverage |

## 六、裁减顺序与不可裁减项

如预计超过 7 人日，必须先停止并请 Owner 确认。推荐依次裁减：文件名相似度提示、版本链复杂视觉样式、对照页独立滚动样式、分类调整内联样式。裁减后仍须保留文本版本链、双栏对照和单条分类调整。

不可裁减：确认前零写入、固定 8 类、两级新权威、显式系列关联、旧数据兼容、SHA-256 完整性、项目隔离、L3/L4 零外部调用、本地候选可编辑发布、失败回滚和全量回归。

## 七、执行交接

执行前使用 `superpowers:using-git-worktrees` 检查隔离工作区。执行方式二选一：

1. **Subagent-Driven（推荐）**：每个 Task 使用独立实现 agent，每个 Batch 完成后做需求符合性和代码质量 review。
2. **Inline Execution**：当前会话使用 `superpowers:executing-plans` 按 Batch 执行，每个 Batch 后停止等待 Owner 确认。

不得跳过 B1～B5 的确认点，也不得在定向测试失败时进入下一 Batch。
