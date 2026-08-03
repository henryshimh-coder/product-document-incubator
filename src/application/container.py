from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.application.dto.dashboard import DashboardView, GetDashboardInput
from src.application.dto.ingest import ImportSourceInput
from src.application.dto.query import RunQueryInput
from src.application.use_cases.get_dashboard import GetDashboard
from src.application.use_cases.import_source import ImportSource
from src.application.use_cases.run_query import RunQuery
from src.domain.models import IngestReport, QueryResponse
from src.infrastructure.cache.ai_cache import AiCache
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteChangeRepository,
    SqliteEventRepository,
    SqliteIngestUnitOfWork,
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.archive import SourceArchive
from src.infrastructure.files.extractor import extract_document
from src.infrastructure.files.manifest_integrity import ManifestIntegrityChecker
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader
from src.infrastructure.gateways.composition import DifyGatewaySettings, build_workflow_gateways
from src.infrastructure.observability.event_logger import EventLogger
from src.infrastructure.observability.model_call_logger import ModelCallLogger


class ImportSourceService(Protocol):
    def execute(self, command: ImportSourceInput) -> IngestReport: ...


class DashboardService(Protocol):
    def execute(self, command: GetDashboardInput) -> DashboardView: ...


class QueryService(Protocol):
    def list_historical_versions(self, project_id: str) -> tuple[str, ...]: ...

    def execute(self, command: RunQueryInput) -> QueryResponse: ...


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


@dataclass(frozen=True)
class AppContainer:
    settings: AppSettings
    import_source: ImportSourceService | None = None
    dashboard: DashboardService | None = None
    query: QueryService | None = None


def load_settings(app_path: Path, schema_path: Path) -> AppSettings:
    try:
        app_document = yaml.safe_load(app_path.read_text(encoding="utf-8"))
        schema_document = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        app_data = app_document["app"]
        schema_version = schema_document["schema_version"]
        return AppSettings(**app_data, schema_version=schema_version)
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(f"Invalid application configuration: {error}") from error


def build_container(
    app_path: Path = Path("config/app.yaml"),
    schema_path: Path = Path("config/schema.yaml"),
    *,
    environ: Mapping[str, str] | None = None,
    http_factory=None,
) -> AppContainer:
    settings = load_settings(app_path, schema_path)
    project_root = app_path.resolve().parent.parent
    db_path = project_root / "data/local_state/product_intelligence.db"
    manifest_path = project_root / "data/local_state/current_baseline.json"
    if not manifest_path.is_file():
        return AppContainer(settings=settings)
    migrate(db_path)
    dashboard = GetDashboard(
        manifest=ManifestStore(manifest_path),
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
    runtime = os.environ if environ is None else environ
    required = (
        runtime.get("DIFY_BASE_URL", "").strip(),
        runtime.get("DIFY_INGEST_API_KEY", "").strip(),
        runtime.get("DIFY_QUERY_API_KEY", "").strip(),
        runtime.get("DIFY_LINT_API_KEY", "").strip(),
    )
    if not all(required):
        return AppContainer(settings=settings, dashboard=dashboard)
    gateway_settings = DifyGatewaySettings(
        base_url=required[0],
        ingest_api_key=required[1],
        query_api_key=required[2],
        lint_api_key=required[3],
    )
    gateways = build_workflow_gateways(
        gateway_settings,
        http_factory=http_factory or httpx.Client,
    )
    event_logger = EventLogger(db_path)
    event_logger.reconcile()

    def dictionary(name: str) -> tuple[str, ...]:
        return tuple(term.strip() for term in runtime.get(name, "").split(",") if term.strip())

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
        knowledge=SqliteKnowledgeRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        material_reader=LocalQueryMaterialReader(project_root),
        gateway=gateways.query,
        customer_names=dictionary("REDACTION_CUSTOMER_NAMES"),
        strategy_terms=dictionary("REDACTION_STRATEGY_TERMS"),
        financial_terms=dictionary("REDACTION_FINANCIAL_TERMS"),
        leader_names=dictionary("REDACTION_LEADER_NAMES"),
        unpublished_decisions=dictionary("REDACTION_UNPUBLISHED_DECISIONS"),
        schema_version=settings.schema_version,
    )
    return AppContainer(
        settings=settings,
        import_source=import_service,
        dashboard=dashboard,
        query=query_service,
    )
