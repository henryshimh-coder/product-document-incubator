from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import httpx
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.application.dto.dashboard import DashboardView, GetDashboardInput
from src.application.dto.decision import RecordDecisionInput
from src.application.dto.documents import ArchivedSourceView, ArchiveRawSourceInput
from src.application.dto.ingest import ImportSourceInput
from src.application.dto.lint import ListLintIssuesInput, RunLintInput
from src.application.dto.query import RunQueryInput
from src.application.dto.release import PublishBaselineInput, ReviewChangeRequestInput
from src.application.dto.trace import BuildTraceInput
from src.application.dto.wiki_ingest import (
    ConfirmLocalWikiIngestInput,
    IngestArchivedSourceInput,
    LocalWikiIngestDraftView,
    PrepareLocalWikiIngestInput,
    WikiIngestResultView,
)
from src.application.ports.incubator import (
    CurrentDocumentExporter,
    DocumentDraftPublisher,
    DocumentIncubation,
    DocumentStructureSuggester,
    ProjectManagement,
)
from src.application.project_context import ProjectContext
from src.application.use_cases.archive_raw_source import ArchiveRawSource
from src.application.use_cases.build_trace import BuildTrace
from src.application.use_cases.compare_sensitive_source import CompareSensitiveSource
from src.application.use_cases.confirm_local_wiki_ingest import ConfirmLocalWikiIngest
from src.application.use_cases.create_local_document_draft import CreateLocalDocumentDraft
from src.application.use_cases.export_current_document import ExportCurrentDocument
from src.application.use_cases.get_dashboard import GetDashboard
from src.application.use_cases.import_source import ImportSource
from src.application.use_cases.incubate_document import IncubateDocument
from src.application.use_cases.ingest_archived_source import IngestArchivedSource
from src.application.use_cases.manage_projects import ManageProjects
from src.application.use_cases.prepare_local_wiki_ingest import PrepareLocalWikiIngest
from src.application.use_cases.publish_baseline import PublishBaseline
from src.application.use_cases.publish_document_draft import PublishDocumentDraft
from src.application.use_cases.reclassify_source import ReclassifySource
from src.application.use_cases.record_decision import RecordDecision
from src.application.use_cases.recover_wiki_transaction import RecoverWikiTransaction
from src.application.use_cases.review_change_request import ReviewChangeRequest
from src.application.use_cases.run_lint import (
    DeterministicLintRunner,
    RunLint,
    SafeLintComparisonBuilder,
)
from src.application.use_cases.run_query import RunQuery
from src.application.use_cases.suggest_document_structure import SuggestDocumentStructure
from src.domain.errors import DomainError
from src.domain.models import (
    Baseline,
    ChangeRequest,
    CostImpactInput,
    CostImpactResult,
    DecisionResult,
    IngestReport,
    IssueCard,
    KnowledgeCard,
    LintReport,
    MarketEvidenceGap,
    ModelCallLog,
    QueryResponse,
    RepairResult,
    SourceRecord,
    TraceView,
    ValueMetric,
)
from src.infrastructure.cache.ai_cache import AiCache
from src.infrastructure.db.lint_fact_reader import SqliteLintFactReader
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteChangeRepository,
    SqliteDecisionRepository,
    SqliteDecisionUnitOfWork,
    SqliteDocumentDraftRepository,
    SqliteEventRepository,
    SqliteIngestUnitOfWork,
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteLintUnitOfWork,
    SqliteModelCallLogRepository,
    SqliteProjectRepository,
    SqliteRelationRepository,
    SqliteReleaseUnitOfWork,
    SqliteReviewUnitOfWork,
    SqliteSourceRepository,
    SqliteStructureSuggestionRepository,
    SqliteWikiIngestRunRepository,
)
from src.infrastructure.db.state_lock import acquire_shared, release
from src.infrastructure.files.archive import SourceArchive
from src.infrastructure.files.baseline_card_reader import LocalBaselineCardReader
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.extractor import extract_document
from src.infrastructure.files.manifest_integrity import ManifestIntegrityChecker
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.markdown_store import MarkdownStore
from src.infrastructure.files.project_library import (
    JsonIncubatorSettingsStore,
    ProjectLibraryLocator,
)
from src.infrastructure.files.project_path_resolver import ProjectPathResolver
from src.infrastructure.files.project_scaffolder import ProjectScaffolder
from src.infrastructure.files.project_source_archive import ProjectSourceArchive
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader
from src.infrastructure.files.source_index_store import SourceIndexStore
from src.infrastructure.files.wiki_change_set_store import WikiTransactionCoordinator
from src.infrastructure.files.wiki_context_reader import WikiContextReader
from src.infrastructure.gateways.composition import (
    DifyDocumentGatewaySettings,
    DifyGatewaySettings,
    DifyWikiIngestGatewaySettings,
    WorkflowTimeouts,
    build_document_gateway,
    build_wiki_ingest_gateway,
    build_workflow_gateways,
    default_workflow_timeouts,
)
from src.infrastructure.observability.event_logger import EventLogger
from src.infrastructure.observability.model_call_logger import ModelCallLogger
from src.infrastructure.recovery.reconciliation_service import ReconciliationService
from src.infrastructure.recovery.release_guard import ReleaseGuard


class ImportSourceService(Protocol):
    def execute(self, command: ImportSourceInput) -> IngestReport: ...


class DashboardService(Protocol):
    def execute(self, command: GetDashboardInput) -> DashboardView: ...


class RawSourceArchiveService(Protocol):
    def execute(self, command: ArchiveRawSourceInput) -> ArchivedSourceView: ...


class SourceReclassificationService(Protocol):
    def execute(self, command): ...


class SensitiveComparisonService(Protocol):
    def execute(self, command): ...


class LocalDraftService(Protocol):
    def execute(self, command): ...


class WikiIngestService(Protocol):
    def execute(self, command: IngestArchivedSourceInput) -> WikiIngestResultView: ...


class PrepareLocalWikiIngestService(Protocol):
    def execute(self, command: PrepareLocalWikiIngestInput) -> LocalWikiIngestDraftView: ...


class ConfirmLocalWikiIngestService(Protocol):
    def execute(self, command: ConfirmLocalWikiIngestInput) -> WikiIngestResultView: ...


class QueryService(Protocol):
    def list_historical_versions(self, project_id: str) -> tuple[str, ...]: ...

    def execute(self, command: RunQueryInput) -> QueryResponse: ...


class LintService(Protocol):
    def execute(self, command: RunLintInput) -> LintReport: ...

    def list_open(self, project_id: str) -> list[IssueCard]: ...

    def list_all(self, project_id: str) -> list[IssueCard]: ...

    def list_issues(self, command: ListLintIssuesInput) -> list[IssueCard]: ...


class DecisionService(Protocol):
    def execute(self, command: RecordDecisionInput) -> DecisionResult: ...


class ReviewService(Protocol):
    def execute(self, command: ReviewChangeRequestInput) -> ChangeRequest: ...


class PublishService(Protocol):
    def execute(self, command: PublishBaselineInput) -> Baseline: ...


class ReleaseCandidateService(Protocol):
    def list_release_candidates(self, project_id: str) -> list[ChangeRequest]: ...


class ReconciliationPort(Protocol):
    def validate_manifest_mirror(self) -> RepairResult: ...

    def rebuild_current_from_manifest(self) -> RepairResult: ...


class TraceService(Protocol):
    def execute(self, command: BuildTraceInput) -> TraceView: ...

    def list_entry_cards(self, project_id: str) -> list[KnowledgeCard]: ...

    def list_model_calls(self, project_id: str, *, limit: int) -> list[ModelCallLog]: ...

    def value_metrics(self, project_id: str) -> list[ValueMetric]: ...

    def market_evidence_gaps(self, project_id: str) -> list[MarketEvidenceGap]: ...

    def list_cost_sources(self, project_id: str) -> list[SourceRecord]: ...

    def calculate_cost_impact(
        self,
        project_id: str,
        command: CostImpactInput,
    ) -> CostImpactResult: ...


class ConfigurationError(ValueError):
    """Raised when application configuration cannot establish a valid contract."""


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    default_query_scope: Literal["effective", "effective_with_notices", "historical"]
    max_upload_mb: int = Field(ge=1, le=20)
    accepted_extensions: tuple[str, ...] = Field(min_length=1)
    demo_mode: bool
    schema_version: str = Field(min_length=1)
    lint_input_contract_version: Literal["2.0"] = "2.0"
    # T15-R01：三个受治理 Workflow 的显式超时（秒）。YAML 加载路径强制
    # 要求 timeouts 节点；直接构造（UI 测试等）使用 60/30/60 默认。
    timeouts: WorkflowTimeouts = Field(default_factory=default_workflow_timeouts)


@dataclass(frozen=True)
class AppContainer:
    settings: AppSettings
    manage_projects: ProjectManagement | None = None
    active_project: ProjectContext | None = None
    archive_raw_source: RawSourceArchiveService | None = None
    reclassify_source: SourceReclassificationService | None = None
    compare_sensitive_source: SensitiveComparisonService | None = None
    create_local_document_draft: LocalDraftService | None = None
    wiki_ingest: WikiIngestService | None = None
    prepare_local_wiki_ingest: PrepareLocalWikiIngestService | None = None
    confirm_local_wiki_ingest: ConfirmLocalWikiIngestService | None = None
    incubate_document: DocumentIncubation | None = None
    publish_document_draft: DocumentDraftPublisher | None = None
    export_current_document: CurrentDocumentExporter | None = None
    suggest_document_structure: DocumentStructureSuggester | None = None
    state_lock_fd: int | None = None
    import_source: ImportSourceService | None = None
    dashboard: DashboardService | None = None
    query: QueryService | None = None
    lint: LintService | None = None
    record_decision: DecisionService | None = None
    review_change_request: ReviewService | None = None
    publish_baseline: PublishService | None = None
    release_candidates: ReleaseCandidateService | None = None
    release_guard: ReleaseGuard | None = None
    reconciliation: ReconciliationPort | None = None
    trace: TraceService | None = None

    def require_project_id(self) -> str:
        if self.active_project is None:
            # 仅供尚未迁移的 1.x 嵌入式测试/调用方兼容；实际组合根始终
            # 提供 manage_projects，因此不会绕过 Owner 的项目选择。
            if self.manage_projects is None:
                return self.settings.project_id
            raise RuntimeError("active project is required")
        return self.active_project.project_id

    def close(self) -> None:
        """释放应用运行期持有的状态共享锁（幂等；进程退出时 flock 也会自动释放）。"""
        descriptor = self.state_lock_fd
        if descriptor is None:
            return
        object.__setattr__(self, "state_lock_fd", None)
        release(descriptor)


def _load_settings(app_path: Path, schema_path: Path) -> tuple[AppSettings, bool]:
    try:
        app_document = yaml.safe_load(app_path.read_text(encoding="utf-8"))
        schema_document = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        app_data = app_document["app"]
        # T15-R01：timeouts 节点必须显式存在；缺失、非整数、零、负值或超过
        # 上限都使配置失败，禁止运行时回落隐式默认超时。
        timeouts_data = app_document["timeouts"]
        schema_version = schema_document["schema_version"]
        has_explicit_lint_contract = "lint_input_contract_version" in schema_document
        lint_input_contract_version = schema_document.get(
            "lint_input_contract_version",
            "2.0",
        )
        settings = AppSettings(
            **app_data,
            schema_version=schema_version,
            lint_input_contract_version=lint_input_contract_version,
            timeouts=WorkflowTimeouts(**timeouts_data),
        )
        return settings, has_explicit_lint_contract
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(f"Invalid application configuration: {error}") from error


def load_settings(app_path: Path, schema_path: Path) -> AppSettings:
    settings, _ = _load_settings(app_path, schema_path)
    return settings


def build_container(
    app_path: Path = Path("config/app.yaml"),
    schema_path: Path = Path("config/schema.yaml"),
    *,
    environ: Mapping[str, str] | None = None,
    http_factory=None,
) -> AppContainer:
    settings, has_explicit_lint_contract = _load_settings(app_path, schema_path)
    project_root = app_path.resolve().parent.parent
    project_management = _build_project_management(
        project_root=project_root,
        environ=environ,
    )
    incubator_settings = project_management.settings.load()
    if incubator_settings is not None:
        if incubator_settings.current_project_id is None:
            return AppContainer(settings=settings, manage_projects=project_management)
        try:
            active_project = _build_project_context(
                project_management=project_management,
                project_id=incubator_settings.current_project_id,
            )
        except DomainError:
            return AppContainer(settings=settings, manage_projects=project_management)
        if not active_project.paths.manifest_path.is_file():
            return AppContainer(
                settings=settings,
                manage_projects=project_management,
                active_project=active_project,
                archive_raw_source=_build_raw_source_archive(active_project),
                reclassify_source=_build_reclassify_source(active_project),
                compare_sensitive_source=_build_sensitive_comparison(active_project),
                create_local_document_draft=_build_local_document_draft(active_project),
                wiki_ingest=_build_wiki_ingest(
                    settings=settings,
                    active_project=active_project,
                    environ=environ,
                    http_factory=http_factory,
                ),
                prepare_local_wiki_ingest=_build_prepare_local_wiki_ingest(active_project),
                confirm_local_wiki_ingest=_build_confirm_local_wiki_ingest(active_project),
                incubate_document=_build_document_incubation(
                    settings=settings,
                    active_project=active_project,
                    environ=environ,
                    http_factory=http_factory,
                ),
                publish_document_draft=_build_document_draft_publisher(active_project),
                export_current_document=_build_current_document_exporter(active_project),
                suggest_document_structure=_build_structure_suggestions(
                    settings=settings,
                    active_project=active_project,
                    environ=environ,
                    http_factory=http_factory,
                ),
            )
        lock_fd = acquire_shared(active_project.paths.project_root)
        try:
            return _build_stateful_container(
                settings,
                has_explicit_lint_contract,
                active_project.paths.project_root,
                active_project.db_path,
                active_project.paths.manifest_path,
                environ=environ,
                http_factory=http_factory,
                lock_fd=lock_fd,
                project_management=project_management,
                active_project=active_project,
            )
        except BaseException:
            release(lock_fd)
            raise
    db_path = project_root / "data/local_state/product_intelligence.db"
    manifest_path = project_root / "data/local_state/current_baseline.json"
    if not manifest_path.is_file():
        return AppContainer(settings=settings, manage_projects=project_management)
    # 状态锁必须在读取 Manifest、执行迁移或对账之前获取（评审第三轮 Important）；
    # 构建失败必须释放锁，成功时锁随容器持有直至 close()/进程退出。
    lock_fd = acquire_shared(project_root)
    try:
        return _build_stateful_container(
            settings,
            has_explicit_lint_contract,
            project_root,
            db_path,
            manifest_path,
            environ=environ,
            http_factory=http_factory,
            lock_fd=lock_fd,
            project_management=project_management,
        )
    except BaseException:
        release(lock_fd)
        raise


def _build_stateful_container(
    settings: AppSettings,
    has_explicit_lint_contract: bool,
    project_root: Path,
    db_path: Path,
    manifest_path: Path,
    *,
    environ: Mapping[str, str] | None,
    http_factory,
    lock_fd: int,
    project_management: ProjectManagement,
    active_project: ProjectContext | None = None,
) -> AppContainer:
    migrate(db_path)
    manifest_store = ManifestStore(manifest_path, project_root=project_root)
    markdown_store = MarkdownStore(project_root)
    reconciliation = ReconciliationService(
        manifest_store=manifest_store,
        db_path=db_path,
        project_root=project_root,
    )
    release_guard = ReleaseGuard()
    if not reconciliation.validate_manifest_mirror().success:
        repaired = reconciliation.rebuild_current_from_manifest()
        if not repaired.success:
            release_guard.block(repaired.error_code or "manifest_sqlite_mismatch")
    event_logger = EventLogger(db_path)
    dashboard = GetDashboard(
        manifest=manifest_store,
        integrity=ManifestIntegrityChecker(
            project_root=project_root,
            db_path=db_path,
            manifest_path=manifest_path,
        ),
        projects=SqliteProjectRepository(db_path),
        issues=SqliteIssueRepository(db_path),
        changes=SqliteChangeRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        events=SqliteEventRepository(db_path),
    )
    decision_service = RecordDecision(
        issues=SqliteIssueRepository(db_path),
        manifest=manifest_store,
        knowledge=SqliteKnowledgeRepository(db_path),
        unit_of_work=SqliteDecisionUnitOfWork(db_path),
        now=lambda: datetime.now(UTC),
    )
    review_service = ReviewChangeRequest(
        changes=SqliteChangeRepository(db_path),
        unit_of_work=SqliteReviewUnitOfWork(db_path, event_logger=event_logger),
        now=lambda: datetime.now(UTC),
    )
    publish_service = PublishBaseline(
        manifest_store=manifest_store,
        markdown_store=markdown_store,
        changes=SqliteChangeRepository(db_path),
        baselines=SqliteBaselineRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        issues=SqliteIssueRepository(db_path),
        integrity=ManifestIntegrityChecker(
            project_root=project_root,
            db_path=db_path,
            manifest_path=manifest_path,
        ),
        material_reader=LocalQueryMaterialReader(project_root),
        release_uow=SqliteReleaseUnitOfWork(db_path, event_logger=event_logger),
        reconciliation=reconciliation,
        guard=release_guard,
        lock_path=(project_root / ".incubator/locks" / "release.lock"),
        now=lambda: datetime.now(UTC),
    )
    if environ is None:
        # 默认组合根从项目根 .env 加载配置（不覆盖已存在的进程环境变量）；
        # 显式 environ 注入的测试/嵌入路径不触碰 .env。
        load_dotenv(project_root / ".env")
    runtime = os.environ if environ is None else environ

    def dictionary(name: str) -> tuple[str, ...]:
        return tuple(term.strip() for term in runtime.get(name, "").split(",") if term.strip())

    local_services = {
        "manage_projects": project_management,
        "archive_raw_source": (
            None if active_project is None else _build_raw_source_archive(active_project)
        ),
        "reclassify_source": (
            None if active_project is None else _build_reclassify_source(active_project)
        ),
        "compare_sensitive_source": (
            None if active_project is None else _build_sensitive_comparison(active_project)
        ),
        "create_local_document_draft": (
            None if active_project is None else _build_local_document_draft(active_project)
        ),
        "wiki_ingest": (
            None
            if active_project is None
            else _build_wiki_ingest(
                settings=settings,
                active_project=active_project,
                environ=environ,
                http_factory=http_factory,
            )
        ),
        "prepare_local_wiki_ingest": (
            None
            if active_project is None
            else _build_prepare_local_wiki_ingest(active_project)
        ),
        "confirm_local_wiki_ingest": (
            None
            if active_project is None
            else _build_confirm_local_wiki_ingest(active_project)
        ),
        "incubate_document": (
            None
            if active_project is None
            else _build_document_incubation(
                settings=settings,
                active_project=active_project,
                environ=environ,
                http_factory=http_factory,
            )
        ),
        "publish_document_draft": (
            None if active_project is None else _build_document_draft_publisher(active_project)
        ),
        "export_current_document": (
            None if active_project is None else _build_current_document_exporter(active_project)
        ),
        "suggest_document_structure": (
            None
            if active_project is None
            else _build_structure_suggestions(
                settings=settings,
                active_project=active_project,
                environ=environ,
                http_factory=http_factory,
            )
        ),
        "dashboard": dashboard,
        "record_decision": decision_service,
        "review_change_request": review_service,
        "publish_baseline": publish_service,
        "release_candidates": SqliteChangeRepository(db_path),
        "release_guard": release_guard,
        "reconciliation": reconciliation,
        "trace": BuildTrace(
            manifest=manifest_store,
            baseline_cards=LocalBaselineCardReader(project_root),
            relations=SqliteRelationRepository(db_path),
            knowledge=SqliteKnowledgeRepository(db_path),
            sources=SqliteSourceRepository(db_path),
            issues=SqliteIssueRepository(db_path),
            decisions=SqliteDecisionRepository(db_path),
            changes=SqliteChangeRepository(db_path),
            baselines=SqliteBaselineRepository(db_path),
            model_calls=SqliteModelCallLogRepository(db_path),
            material_reader=LocalQueryMaterialReader(project_root),
            customer_names=dictionary("REDACTION_CUSTOMER_NAMES"),
            strategy_terms=dictionary("REDACTION_STRATEGY_TERMS"),
            financial_terms=dictionary("REDACTION_FINANCIAL_TERMS"),
            leader_names=dictionary("REDACTION_LEADER_NAMES"),
            unpublished_decisions=dictionary("REDACTION_UNPUBLISHED_DECISIONS"),
        ),
    }
    required = (
        runtime.get("DIFY_BASE_URL", "").strip(),
        runtime.get("DIFY_INGEST_API_KEY", "").strip(),
        runtime.get("DIFY_QUERY_API_KEY", "").strip(),
        runtime.get("DIFY_LINT_API_KEY", "").strip(),
    )
    if not all(required):
        return AppContainer(
            settings=settings,
            state_lock_fd=lock_fd,
            active_project=active_project,
            **local_services,
        )
    if not has_explicit_lint_contract:
        raise ConfigurationError(
            "Live Lint deployment requires explicit "
            "lint_input_contract_version: '2.0' in schema configuration"
        )
    try:
        gateway_settings = DifyGatewaySettings(
            base_url=required[0],
            ingest_api_key=required[1],
            query_api_key=required[2],
            lint_api_key=required[3],
        )
    except ValidationError as error:
        # pydantic 的 ValidationError 文本会内嵌原始输入（含 API Key）；
        # 只透传校验消息本身，切断 Key 进入异常文本与日志的路径。
        message = "; ".join(entry["msg"] for entry in error.errors())
        raise ConfigurationError(f"Invalid Dify gateway configuration: {message}") from None
    gateways = build_workflow_gateways(
        gateway_settings,
        timeouts=settings.timeouts,
        http_factory=http_factory or httpx.Client,
    )
    event_logger.reconcile()

    import_service = ImportSource(
        projects=SqliteProjectRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        knowledge=SqliteKnowledgeRepository(db_path),
        unit_of_work=SqliteIngestUnitOfWork(db_path, event_logger),
        archive_factory=lambda project_id, source_id: SourceArchive(
            project_id=project_id,
            source_id=source_id,
        ),
        extractor=extract_document,
        gateway=gateways.ingest,
        cache=AiCache(db_path),
        manifest_store=ManifestStore(manifest_path),
        model_call_logger=ModelCallLogger(db_path),
        event_logger=event_logger,
        customer_names=dictionary("REDACTION_CUSTOMER_NAMES"),
        strategy_terms=dictionary("REDACTION_STRATEGY_TERMS"),
        financial_terms=dictionary("REDACTION_FINANCIAL_TERMS"),
        leader_names=dictionary("REDACTION_LEADER_NAMES"),
        unpublished_decisions=dictionary("REDACTION_UNPUBLISHED_DECISIONS"),
        schema_version=settings.schema_version,
    )
    query_service = RunQuery(
        manifest=ManifestStore(manifest_path),
        baselines=SqliteBaselineRepository(db_path),
        projects=SqliteProjectRepository(db_path),
        knowledge=SqliteKnowledgeRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        baseline_cards=LocalBaselineCardReader(project_root),
        material_reader=LocalQueryMaterialReader(project_root),
        gateway=gateways.query,
        customer_names=dictionary("REDACTION_CUSTOMER_NAMES"),
        strategy_terms=dictionary("REDACTION_STRATEGY_TERMS"),
        financial_terms=dictionary("REDACTION_FINANCIAL_TERMS"),
        leader_names=dictionary("REDACTION_LEADER_NAMES"),
        unpublished_decisions=dictionary("REDACTION_UNPUBLISHED_DECISIONS"),
        cache=AiCache(db_path),
        schema_version=settings.schema_version,
    )
    lint_store = ManifestStore(manifest_path)
    card_store = MarkdownStore(project_root)
    material_reader = LocalQueryMaterialReader(project_root)
    lint_service = RunLint(
        local_lint=DeterministicLintRunner(
            manifest=lint_store,
            card_store=card_store,
            projects=SqliteProjectRepository(db_path),
            sources=SqliteSourceRepository(db_path),
            fact_reader=SqliteLintFactReader(db_path),
        ),
        comparison_builder=SafeLintComparisonBuilder(
            manifest=lint_store,
            projects=SqliteProjectRepository(db_path),
            knowledge=SqliteKnowledgeRepository(db_path),
            sources=SqliteSourceRepository(db_path),
            card_store=card_store,
            material_reader=material_reader,
            schema_version=settings.schema_version,
            input_contract_version=settings.lint_input_contract_version,
        ),
        gateway=gateways.lint,
        issues=SqliteIssueRepository(db_path),
        unit_of_work=SqliteLintUnitOfWork(db_path),
        customer_names=dictionary("REDACTION_CUSTOMER_NAMES"),
        strategy_terms=dictionary("REDACTION_STRATEGY_TERMS"),
        financial_terms=dictionary("REDACTION_FINANCIAL_TERMS"),
        leader_names=dictionary("REDACTION_LEADER_NAMES"),
        unpublished_decisions=dictionary("REDACTION_UNPUBLISHED_DECISIONS"),
        cache=AiCache(db_path),
    )
    return AppContainer(
        settings=settings,
        state_lock_fd=lock_fd,
        active_project=active_project,
        import_source=import_service,
        query=query_service,
        lint=lint_service,
        **local_services,
    )


def _build_project_management(
    *,
    project_root: Path,
    environ: Mapping[str, str] | None,
) -> ManageProjects:
    runtime = os.environ if environ is None else environ
    locator = ProjectLibraryLocator(
        pointer_path=project_root / "data/local_state/incubator-root.json",
        environ=runtime,
    )
    library_root = locator.resolve()
    settings_store = JsonIncubatorSettingsStore(library_root)
    database_path = library_root / ".incubator/product_incubator.db"
    if settings_store.load() is not None:
        migrate(database_path)
    schema_source = Path(__file__).resolve().parents[2] / "assets/incubator_schema"

    def now() -> datetime:
        return datetime.now(UTC)

    return ManageProjects(
        library_root=library_root,
        projects=SqliteProjectRepository(database_path),
        scaffolder=ProjectScaffolder(
            library_root=library_root,
            schema_source=schema_source,
            now=now,
        ),
        settings=settings_store,
        now=now,
        locator=locator,
        schema_source=schema_source,
        path_resolver=ProjectPathResolver(
            library_root,
            SqliteProjectRepository(database_path),
            now=now,
        ),
    )


def _build_project_context(
    *, project_management: ProjectManagement, project_id: str
) -> ProjectContext:
    paths = project_management.path_resolver.resolve(project_id)
    context = ProjectContext(
        project_id=project_id,
        paths=paths,
        db_path=project_management.library_root / ".incubator/product_incubator.db",
    )
    RecoverWikiTransaction(
        project_id=project_id,
        coordinator=WikiTransactionCoordinator(
            paths=paths,
            db_path=context.db_path,
            validator=None,
        ),
    )
    return context


def _build_raw_source_archive(active_project: ProjectContext) -> ArchiveRawSource:
    return ArchiveRawSource(
        paths=active_project.paths,
        projects=SqliteProjectRepository(active_project.db_path),
        sources=SqliteSourceRepository(active_project.db_path),
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=active_project.paths,
            source_id=source_id,
            year=year,
        ),
        index=SourceIndexStore(active_project.paths),
    )


def _build_reclassify_source(active_project: ProjectContext) -> ReclassifySource:
    return ReclassifySource(
        paths=active_project.paths,
        sources=SqliteSourceRepository(active_project.db_path),
        index=SourceIndexStore(active_project.paths),
    )


def _build_sensitive_comparison(active_project: ProjectContext) -> CompareSensitiveSource:
    return CompareSensitiveSource(
        paths=active_project.paths,
        sources=SqliteSourceRepository(active_project.db_path),
        store=DocumentStore(active_project.paths),
    )


def _build_local_document_draft(active_project: ProjectContext) -> CreateLocalDocumentDraft:
    return CreateLocalDocumentDraft(
        paths=active_project.paths,
        projects=SqliteProjectRepository(active_project.db_path),
        sources=SqliteSourceRepository(active_project.db_path),
        drafts=SqliteDocumentDraftRepository(active_project.db_path),
        store=DocumentStore(active_project.paths),
    )


def _build_wiki_ingest(
    *,
    settings: AppSettings,
    active_project: ProjectContext,
    environ: Mapping[str, str] | None,
    http_factory,
) -> IngestArchivedSource | None:
    """Compose the optional 2.2 Wiki path only with its dedicated credential."""
    if active_project.wiki_schema_version != "2.2":
        return None
    if environ is None:
        load_dotenv(active_project.paths.project_root / ".env")
    runtime = os.environ if environ is None else environ
    base_url = runtime.get("DIFY_BASE_URL", "").strip()
    wiki_key = runtime.get("DIFY_WIKI_INGEST_API_KEY", "").strip()
    if not base_url or not wiki_key:
        return None
    try:
        wiki_settings = DifyWikiIngestGatewaySettings(
            base_url=base_url,
            wiki_ingest_api_key=wiki_key,
        )
    except ValidationError as error:
        message = "; ".join(entry["msg"] for entry in error.errors())
        raise ConfigurationError(
            f"Invalid Dify Wiki Ingest gateway configuration: {message}"
        ) from None

    def dictionary(name: str) -> tuple[str, ...]:
        return tuple(term.strip() for term in runtime.get(name, "").split(",") if term.strip())

    return IngestArchivedSource(
        paths=active_project.paths,
        db_path=active_project.db_path,
        sources=SqliteSourceRepository(active_project.db_path),
        runs=SqliteWikiIngestRunRepository(active_project.db_path),
        gateway=build_wiki_ingest_gateway(
            wiki_settings,
            timeouts=settings.timeouts,
            http_factory=http_factory or httpx.Client,
        ),
        customer_names=dictionary("REDACTION_CUSTOMER_NAMES"),
        strategy_terms=dictionary("REDACTION_STRATEGY_TERMS"),
        financial_terms=dictionary("REDACTION_FINANCIAL_TERMS"),
        leader_names=dictionary("REDACTION_LEADER_NAMES"),
        unpublished_decisions=dictionary("REDACTION_UNPUBLISHED_DECISIONS"),
    )


def _build_prepare_local_wiki_ingest(
    active_project: ProjectContext,
) -> PrepareLocalWikiIngest | None:
    if active_project.wiki_schema_version != "2.2":
        return None
    return PrepareLocalWikiIngest(
        paths=active_project.paths,
        sources=SqliteSourceRepository(active_project.db_path),
    )


def _build_confirm_local_wiki_ingest(
    active_project: ProjectContext,
) -> ConfirmLocalWikiIngest | None:
    if active_project.wiki_schema_version != "2.2":
        return None
    return ConfirmLocalWikiIngest(
        paths=active_project.paths,
        db_path=active_project.db_path,
        sources=SqliteSourceRepository(active_project.db_path),
        runs=SqliteWikiIngestRunRepository(active_project.db_path),
    )


def _build_document_incubation(
    *,
    settings: AppSettings,
    active_project: ProjectContext,
    environ: Mapping[str, str] | None,
    http_factory,
) -> IncubateDocument:
    """Compose the optional 2.0 drafting path without enabling legacy workflows."""
    if environ is None:
        load_dotenv(active_project.paths.project_root / ".env")
    runtime = os.environ if environ is None else environ
    base_url = runtime.get("DIFY_BASE_URL", "").strip()
    document_key = runtime.get("DIFY_DOCUMENT_API_KEY", "").strip()
    gateway = None
    if base_url and document_key:
        try:
            document_settings = DifyDocumentGatewaySettings(
                base_url=base_url,
                document_api_key=document_key,
            )
        except ValidationError as error:
            message = "; ".join(entry["msg"] for entry in error.errors())
            raise ConfigurationError(
                f"Invalid Dify document gateway configuration: {message}"
            ) from None
        gateway = build_document_gateway(
            document_settings,
            timeouts=settings.timeouts,
            http_factory=http_factory or httpx.Client,
        )

    def dictionary(name: str) -> tuple[str, ...]:
        return tuple(term.strip() for term in runtime.get(name, "").split(",") if term.strip())

    return IncubateDocument(
        paths=active_project.paths,
        projects=SqliteProjectRepository(active_project.db_path),
        sources=SqliteSourceRepository(active_project.db_path),
        drafts=SqliteDocumentDraftRepository(active_project.db_path),
        store=DocumentStore(active_project.paths),
        gateway=gateway,
        wiki_context=WikiContextReader(
            paths=active_project.paths,
            sources=SqliteSourceRepository(active_project.db_path),
        ),
        model_call_logger=ModelCallLogger(active_project.db_path),
        local_draft_creator=_build_local_document_draft(active_project),
        accepted_suggestions=SqliteStructureSuggestionRepository(active_project.db_path),
        customer_names=dictionary("REDACTION_CUSTOMER_NAMES"),
        strategy_terms=dictionary("REDACTION_STRATEGY_TERMS"),
        financial_terms=dictionary("REDACTION_FINANCIAL_TERMS"),
        leader_names=dictionary("REDACTION_LEADER_NAMES"),
        unpublished_decisions=dictionary("REDACTION_UNPUBLISHED_DECISIONS"),
    )


def _build_document_draft_publisher(active_project: ProjectContext) -> PublishDocumentDraft:
    manifest = ManifestStore(
        active_project.paths.manifest_path,
        project_root=active_project.paths.project_root,
    )
    return PublishDocumentDraft(
        paths=active_project.paths,
        projects=SqliteProjectRepository(active_project.db_path),
        sources=SqliteSourceRepository(active_project.db_path),
        drafts=SqliteDocumentDraftRepository(active_project.db_path),
        store=DocumentStore(active_project.paths),
        manifest=manifest,
        reconciliation=ReconciliationService(
            manifest_store=manifest,
            db_path=active_project.db_path,
            project_root=active_project.paths.project_root,
        ),
    )


def _build_current_document_exporter(active_project: ProjectContext) -> ExportCurrentDocument:
    return ExportCurrentDocument(
        paths=active_project.paths,
        projects=SqliteProjectRepository(active_project.db_path),
        manifest=ManifestStore(
            active_project.paths.manifest_path,
            project_root=active_project.paths.project_root,
        ),
    )


def _build_structure_suggestions(
    *,
    settings: AppSettings,
    active_project: ProjectContext,
    environ: Mapping[str, str] | None,
    http_factory,
) -> SuggestDocumentStructure | None:
    if environ is None:
        load_dotenv(active_project.paths.project_root / ".env")
    runtime = os.environ if environ is None else environ
    base_url = runtime.get("DIFY_BASE_URL", "").strip()
    document_key = runtime.get("DIFY_DOCUMENT_API_KEY", "").strip()
    if not base_url or not document_key:
        return None
    try:
        document_settings = DifyDocumentGatewaySettings(
            base_url=base_url,
            document_api_key=document_key,
        )
    except ValidationError as error:
        message = "; ".join(entry["msg"] for entry in error.errors())
        raise ConfigurationError(
            f"Invalid Dify document gateway configuration: {message}"
        ) from None
    return SuggestDocumentStructure(
        paths=active_project.paths,
        projects=SqliteProjectRepository(active_project.db_path),
        suggestions=SqliteStructureSuggestionRepository(active_project.db_path),
        gateway=build_document_gateway(
            document_settings,
            timeouts=settings.timeouts,
            http_factory=http_factory or httpx.Client,
        ),
    )
