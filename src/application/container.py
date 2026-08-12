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
from src.application.dto.ingest import ImportSourceInput
from src.application.dto.lint import ListLintIssuesInput, RunLintInput
from src.application.dto.query import RunQueryInput
from src.application.dto.release import PublishBaselineInput, ReviewChangeRequestInput
from src.application.dto.trace import BuildTraceInput
from src.application.ports.incubator import ProjectManagement
from src.application.use_cases.build_trace import BuildTrace
from src.application.use_cases.get_dashboard import GetDashboard
from src.application.use_cases.import_source import ImportSource
from src.application.use_cases.manage_projects import ManageProjects
from src.application.use_cases.publish_baseline import PublishBaseline
from src.application.use_cases.record_decision import RecordDecision
from src.application.use_cases.review_change_request import ReviewChangeRequest
from src.application.use_cases.run_lint import (
    DeterministicLintRunner,
    RunLint,
    SafeLintComparisonBuilder,
)
from src.application.use_cases.run_query import RunQuery
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
)
from src.infrastructure.db.state_lock import acquire_shared, release
from src.infrastructure.files.archive import SourceArchive
from src.infrastructure.files.baseline_card_reader import LocalBaselineCardReader
from src.infrastructure.files.extractor import extract_document
from src.infrastructure.files.manifest_integrity import ManifestIntegrityChecker
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.markdown_store import MarkdownStore
from src.infrastructure.files.project_library import (
    JsonIncubatorSettingsStore,
    ProjectLibraryLocator,
)
from src.infrastructure.files.project_scaffolder import ProjectScaffolder
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader
from src.infrastructure.gateways.composition import (
    DifyGatewaySettings,
    WorkflowTimeouts,
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
        lock_path=(project_root / "data/local_state/locks" / f"{settings.project_id}.release.lock"),
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
    )
