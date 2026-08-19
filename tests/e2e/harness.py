"""T13 E2E Harness：真实 AppContainer + 确定性 mock 网关的演示流程驱动器。

计划 Step 1 的实现。Harness 只做编排，不绕过任何应用层校验：模型侧由
httpx.MockTransport 提供与联合验收同构的确定性响应，服务端校验、发布闸、
回滚全部为真实应用行为。网关可按任务注入超时（Step 3 实时超时回退用例）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import httpx

from scripts.bootstrap_demo import BASELINE_VERSION, RULE_CARD_CONTENT, RULE_CARD_ID
from scripts.demo_materials import RISK_SENTENCE
from src.application.container import AppContainer, build_container
from src.application.dto.decision import CreateChangeRequestInput, RecordDecisionInput
from src.application.dto.documents import (
    ArchiveRawSourceInput,
    ExportCurrentDocumentInput,
    IncubateDocumentInput,
    PublishDocumentDraftInput,
)
from src.application.dto.ingest import ImportSourceInput
from src.application.dto.lint import RunLintInput
from src.application.dto.projects import CreateProjectInput, RelocateProjectInput
from src.application.dto.query import RunQueryInput
from src.application.dto.release import PublishBaselineInput, ReviewChangeRequestInput
from src.application.dto.wiki_ingest import (
    ConfirmLocalWikiIngestInput,
    IngestArchivedSourceInput,
    PrepareLocalWikiIngestInput,
)
from src.application.use_cases.archive_raw_source import ArchiveRawSource
from src.application.use_cases.confirm_local_wiki_ingest import ConfirmLocalWikiIngest
from src.application.use_cases.export_current_document import ExportCurrentDocument
from src.application.use_cases.incubate_document import IncubateDocument
from src.application.use_cases.ingest_archived_source import IngestArchivedSource
from src.application.use_cases.manage_projects import ManageProjects
from src.application.use_cases.prepare_local_wiki_ingest import PrepareLocalWikiIngest
from src.application.use_cases.publish_document_draft import PublishDocumentDraft
from src.domain.enums import (
    AuthorityLevel,
    ChangeReviewAction,
    DecisionAction,
    ProjectRootStatus,
    SecurityLevel,
)
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import (
    Baseline,
    ChangeRequest,
    DecisionResult,
    IngestReport,
    LintReport,
    Project,
    QueryResponse,
)
from src.infrastructure.db.connection import connect
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteDocumentDraftRepository,
    SqliteModelCallLogRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
    SqliteWikiIngestRunRepository,
)
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.project_library import JsonIncubatorSettingsStore, ProjectPaths
from src.infrastructure.files.project_scaffolder import ProjectScaffolder
from src.infrastructure.files.project_source_archive import ProjectSourceArchive
from src.infrastructure.files.source_index_store import SourceIndexStore
from src.infrastructure.files.wiki_context_reader import WikiContextReader
from src.infrastructure.gateways.dify_client import decode_for_dify_transport
from src.infrastructure.gateways.schemas import WikiIngestWorkflowOutput
from src.infrastructure.observability.model_call_logger import ModelCallLogger
from src.infrastructure.recovery.reconciliation_service import ReconciliationService

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sources"

PUBLISHED_RULE_CONTENT = "目标客群收紧为符合准入要求且通过风险评估的存量客户。"
TARGET_VERSION = "LLD-724_2"

MOCK_ENVIRON = {
    "DIFY_BASE_URL": "https://dify.e2e.local",
    "DIFY_INGEST_API_KEY": "ingest-key",
    "DIFY_QUERY_API_KEY": "query-key",
    "DIFY_LINT_API_KEY": "lint-key",
}


def ingest_command_from_fixture(
    fixture_path: Path,
    *,
    security: SecurityLevel = SecurityLevel.L2_INTERNAL,
    sandbox: bool = False,
) -> ImportSourceInput:
    """从演示夹具构造导入命令（风险材料为正式决定，其余为普通产品材料）。"""
    is_risk = "risk" in fixture_path.name
    return ImportSourceInput(
        project_id="LLD",
        uploaded_name=fixture_path.name,
        uploaded_bytes=fixture_path.read_bytes(),
        source_type="risk_opinion" if is_risk else "meeting_minutes",
        authority_level=(
            AuthorityLevel.FORMAL_DECISION if is_risk else AuthorityLevel.DISCUSSION_REFERENCE
        ),
        source_department="风险" if is_risk else "产品",
        provider=None,
        document_date=date(2026, 8, 4),
        document_version="v1.0",
        applicable_baseline_version=BASELINE_VERSION,
        security_level=security,
        is_redacted_confirmed=True,
        allow_external_model=not sandbox,
        is_sandbox=sandbox,
        preferred_mode="realtime",
    )


def _ingest_result(inputs: dict) -> dict:
    """与联合验收同构：风险材料产出一条冲突候选 + 冲突关系，其余留档。"""
    if inputs["source"]["type"] != "risk_opinion":
        return {
            "schema_version": "1.0",
            "task_id": inputs["task_id"],
            "summary": "留档材料，无需提取候选知识。",
            "items": [],
            "relations": [],
        }
    chunk = next(
        (c for c in inputs["source_chunks"] if RISK_SENTENCE in c["text"]),
        inputs["source_chunks"][0],
    )
    return {
        "schema_version": "1.0",
        "task_id": inputs["task_id"],
        "summary": "识别到一条需会议裁决的风险意见。",
        "items": [
            {
                "item_id": "ITEM-RISK-001",
                "item_type": "professional_opinion",
                "title": "客群限制意见",
                "content": RISK_SENTENCE,
                "target_card_id": RULE_CARD_ID,
                "result_type": "conflict_discussion",
                "status": "conflict",
                "source_citations": [
                    {
                        "source_id": inputs["source"]["id"],
                        "chunk_id": chunk["chunk_id"],
                        "locator": chunk["locator"],
                        "excerpt": chunk["text"][:40],
                    }
                ],
                "confidence": 0.86,
                "uncertainty": "尚未形成正式决定",
            }
        ],
        "relations": [
            {
                "source_id": "ITEM-RISK-001",
                "relation_type": "conflicts_with",
                "target_id": RULE_CARD_ID,
            }
        ],
    }


def _query_result(inputs: dict) -> dict:
    card = next(c for c in inputs["effective_cards"] if c["id"] == RULE_CARD_ID)
    citation = next(
        (
            c
            for c in inputs.get("citations", [])
            if c["id"] in set(card.get("source_citations", []))
        ),
        (inputs.get("citations") or [None])[0],
    )
    return {
        "answer": card["content"],
        "effective_rules": [card["id"]],
        "citations": [citation] if citation else [],
        "candidate_notice": None,
        "conflict_notice": None,
        "baseline_version": inputs["baseline_version"],
        "evidence_sufficiency": "sufficient",
        "result_mode": "realtime",
        "model_call_id": None,
    }


def _lint_result(inputs: dict) -> dict:
    base = next(r for r in inputs["baseline_rules"] if r["id"] == RULE_CARD_ID)
    compare = inputs["comparison_items"][0]
    return {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": "conflict",
                "severity": "pending_decision",
                "title": "客群边界不一致",
                "description": "正式风险意见要求收紧目标客群，需要会议确认执行口径。",
                "evidence": [
                    {
                        "source_id": base["source_id"],
                        "citation_id": base["citation_id"],
                        "excerpt": base["excerpt"],
                        "document_version": base["document_version"],
                        "page_or_section": base["page_or_section"],
                        "side": "current_baseline",
                    },
                    {
                        "source_id": compare["source_id"],
                        "citation_id": compare["citation_id"],
                        "excerpt": compare["excerpt"],
                        "document_version": compare["document_version"],
                        "page_or_section": compare["page_or_section"],
                        "side": "challenging_source",
                    },
                ],
                "impacted_domains": ["产品", "风险"],
                "options": [{"code": "A", "label": "收紧", "impact": "调整产品规则"}],
                "ai_recommendation": "A",
                "ai_confidence": 0.78,
                "uncertainty": "专业意见尚未形成正式决定",
            }
        ],
    }


def mock_http_factory(
    timeout_tasks: frozenset[str] = frozenset(),
    record: list[dict] | None = None,
) -> httpx.Client:
    """确定性 mock 网关；`timeout_tasks` 中的任务一律超时，`record` 收集出站载荷。"""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        # 与真实工作流一致：数组在线路上是 JSON 字符串，先经“解析输入”节点还原。
        inputs = decode_for_dify_transport(json.loads(request.content.decode("utf-8"))["inputs"])
        if "ingest" in auth:
            task = "ingest"
            result_factory = _ingest_result
        elif "query" in auth:
            task = "query"
            result_factory = _query_result
        elif "lint" in auth:
            task = "lint"
            result_factory = _lint_result
        else:  # pragma: no cover - 防御未知任务
            return httpx.Response(400, json={"message": "unknown task key"})
        if record is not None:
            record.append(
                {
                    "task": task,
                    "inputs": inputs,
                    "raw_body": request.content.decode("utf-8"),
                }
            )
        if task in timeout_tasks:
            raise httpx.ConnectTimeout(f"E2E injected timeout for {task}", request=request)
        return httpx.Response(
            200,
            json={
                "workflow_run_id": "WF-E2E-001",
                "data": {"outputs": {"result": result_factory(inputs)}},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


class DemoHarness:
    """计划 Step 1 的演示流程驱动器：真实容器 + 夹具编排。"""

    def __init__(self, container: AppContainer, fixture_dir: Path = FIXTURES_DIR) -> None:
        self.container = container
        self.fixture_dir = fixture_dir

    def import_source(
        self,
        fixture_name: str,
        preferred_mode: Literal["realtime", "cache", "local"] = "realtime",
        *,
        security: SecurityLevel = SecurityLevel.L2_INTERNAL,
        sandbox: bool = False,
    ) -> IngestReport:
        command = ingest_command_from_fixture(
            self.fixture_dir / fixture_name,
            security=security,
            sandbox=sandbox,
        )
        return self.container.import_source.execute(
            command.model_copy(update={"preferred_mode": preferred_mode})
        )

    def query(self, question: str) -> QueryResponse:
        return self.container.query.execute(
            RunQueryInput(
                project_id="LLD",
                question=question,
                scope="effective",
                historical_version=None,
            )
        )

    def run_lint(self, source_id: str | None = None) -> LintReport:
        return self.container.lint.execute(
            RunLintInput(
                project_id="LLD",
                scope="all_current_sources" if source_id is None else "current_plus_source",
                source_id=source_id,
            )
        )

    def record_accept_change(self, issue_id: str, *, evidence_ref: str) -> DecisionResult:
        return self.container.record_decision.execute(
            RecordDecisionInput(
                issue_id=issue_id,
                action=DecisionAction.ACCEPT_CHANGE,
                conclusion="采纳专业意见并形成产品规则调整。",
                confirmed_by="产品经理",
                responsible_party="产品",
                due_at=None,
                verification_condition="发布前完成规则、风险和技术实现一致性复核。",
                idempotency_key=f"E2E-{issue_id}-ACCEPT",
                change_request=CreateChangeRequestInput(
                    target_card_id=RULE_CARD_ID,
                    before_content=RULE_CARD_CONTENT,
                    after_content=PUBLISHED_RULE_CONTENT,
                    rationale="依据正式风险意见和会议结论调整。",
                    evidence_refs=[evidence_ref],
                    impacted_objects=[RULE_CARD_ID],
                    responsible_domain="产品",
                    required_approver_role="产品经理",
                    demo_confirmer="产品经理",
                    target_version=TARGET_VERSION,
                    effective_condition="审批通过且验证完成后发布。",
                ),
            )
        )

    def approve_change(self, change_id: str) -> ChangeRequest:
        return self.container.review_change_request.execute(
            ReviewChangeRequestInput(
                change_request_id=change_id,
                action=ChangeReviewAction.APPROVE,
                reviewed_by="产品经理",
                comment="已检查修改前后、依据、影响对象和目标版本。",
                idempotency_key=f"E2E-{change_id}-APPROVE",
            )
        )

    def publish(self, change_id: str) -> Baseline:
        return self.container.publish_baseline.execute(
            PublishBaselineInput(
                project_id="LLD",
                change_request_id=change_id,
                approved_by="产品经理",
                impact_reviewed=True,
                release_note="完成目标客群边界调整并保留来源与决策记录。",
            )
        )


class _WikiIncubationGateway:
    """Deterministic external boundary used only by the end-to-end acceptance harness."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, inputs: dict, **_: object) -> WikiIngestWorkflowOutput:
        self.calls += 1
        source_id = inputs["source"]["id"]
        return WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown=(
                f"# 来源摘要\n\n已核验归档材料并保留来源定位。 【{source_id}：section: summary】"
            ),
            topic_changes=[
                {
                    "topic_id": "product-principles",
                    "title": "产品原则",
                    "change_type": "create",
                    "markdown": "已核验归档材料的产品原则。",
                    "source_ids": [source_id],
                }
            ],
            conflicts=[],
            evidence_gaps=[],
        )


class _WikiDocumentGateway:
    """Strict deterministic document workflow response bound to projected Wiki pages."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_draft(self, inputs: dict) -> dict:
        self.calls += 1
        page = inputs["wiki_pages"][0]
        return {
            "workflow_run_id": f"WF-{inputs['project_id']}",
            "result": {
                "document_markdown": (
                    f"# {inputs['project_name']} 产品方案\n\n"
                    "## 产品概述\n\n基于已 Ingest 的 Wiki 证据生成候选。"
                ),
                "summary": "基于项目 Wiki 的候选产品文档。",
                "missing_sections": [],
                "evidence_gaps": [],
                "source_ids": [page["source_id"]],
                "section_citations": [
                    {
                        "heading": "产品概述",
                        "source_id": page["source_id"],
                        "chunk_id": page["chunk_id"],
                        "locator": page["locator"],
                        "excerpt": page["excerpt"],
                    }
                ],
            },
        }


class WikiIncubatorHarness:
    """T12 uses the real 2.2 services and central repositories end to end."""

    def __init__(self, tmp_path: Path) -> None:
        self.library_root = tmp_path / "library"
        self.db_path = self.library_root / ".incubator" / "product_incubator.db"
        migrate(self.db_path)
        self.projects = SqliteProjectRepository(self.db_path)

        def now() -> datetime:
            return datetime.now(UTC)

        self.manager = ManageProjects(
            library_root=self.library_root,
            projects=self.projects,
            scaffolder=ProjectScaffolder(
                library_root=self.library_root,
                schema_source=Path("assets/incubator_schema").resolve(),
                now=now,
            ),
            settings=JsonIncubatorSettingsStore(self.library_root),
            now=now,
        )
        self.manager.initialize("Owner", self.library_root)
        self.wiki_gateway = _WikiIncubationGateway()
        self.document_gateway = _WikiDocumentGateway()

    @property
    def gateway_calls(self) -> int:
        return self.wiki_gateway.calls + self.document_gateway.calls

    def create_project(self, project_id: str, parent_root: Path) -> ProjectPaths:
        self.manager.create(
            CreateProjectInput(
                project_id=project_id,
                name=project_id,
                description=f"{project_id} 产品文档",
                allow_external_model=True,
                parent_root=parent_root,
            )
        )
        return ProjectPaths.for_registered_root(
            self.library_root, project_id, parent_root / project_id
        )

    def archive_l2(self, paths: ProjectPaths, filename: str, payload: bytes):
        return self._archive(paths, filename, payload, SecurityLevel.L2_INTERNAL)

    def archive_l4(self, paths: ProjectPaths, filename: str, payload: bytes):
        return self._archive(paths, filename, payload, SecurityLevel.L4_RESTRICTED)

    def _archive(self, paths: ProjectPaths, filename: str, payload: bytes, security: SecurityLevel):
        return ArchiveRawSource(
            paths=paths,
            projects=self.projects,
            sources=SqliteSourceRepository(self.db_path),
            archive_factory=lambda source_id, year: ProjectSourceArchive(
                paths=paths, source_id=source_id, year=year
            ),
            index=SourceIndexStore(paths),
        ).execute(
            ArchiveRawSourceInput(
                project_id=paths.project_id,
                uploaded_name=filename,
                uploaded_bytes=payload,
                source_type="product_requirement",
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                source_department="产品部",
                document_date=date.today(),
                material_version="1.0",
                security_level=security,
                is_redacted_confirmed=security != SecurityLevel.L4_RESTRICTED,
                allow_external_model=security == SecurityLevel.L2_INTERNAL,
            )
        )

    def ingest(self, paths: ProjectPaths, source_id: str):
        return IngestArchivedSource(
            paths=paths,
            db_path=self.db_path,
            sources=SqliteSourceRepository(self.db_path),
            runs=SqliteWikiIngestRunRepository(self.db_path),
            gateway=self.wiki_gateway,
            customer_names=(),
            strategy_terms=(),
            financial_terms=(),
            leader_names=(),
            unpublished_decisions=(),
        ).execute(
            IngestArchivedSourceInput(
                project_id=paths.project_id, source_id=source_id, requested_by="Owner"
            )
        )

    def prepare_local(self, paths: ProjectPaths, source_id: str) -> Path:
        return (
            PrepareLocalWikiIngest(paths=paths, sources=SqliteSourceRepository(self.db_path))
            .execute(
                PrepareLocalWikiIngestInput(
                    project_id=paths.project_id, source_id=source_id, requested_by="Owner"
                )
            )
            .draft_root
        )

    def write_valid_local_source_page(self, draft_root: Path, source_id: str) -> None:
        """The one permitted simulation: an Owner's local Markdown edit."""
        source = draft_root / "source.md"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "## 来源摘要\n\n\n## 来源定位",
                "## 来源摘要\n\nOwner 已在本地核验限制级材料。\n\n## 来源定位",
            ),
            encoding="utf-8",
        )
        topics = draft_root / "topics"
        topics.mkdir(exist_ok=True)
        (topics / "restricted-principles.md").write_text(
            "---\npage_type: topic\ntopic_id: restricted-principles\n"
            f"project_id: {source_id.split('-')[1]}\n---\n"
            "# 主题：限制级原则\n\n## 当前综合结论\n\n"
            f"- Owner 已完成本地核验。 【{source_id}：Owner local review】\n\n"
            "## 支持来源\n\n"
            f"- 【{source_id}：Owner local review】\n\n## 冲突来源\n\n- 无\n\n"
            "## 待确认项\n\n- 无\n",
            encoding="utf-8",
        )

    def confirm_local(self, paths: ProjectPaths, source_id: str):
        return ConfirmLocalWikiIngest(
            paths=paths,
            db_path=self.db_path,
            sources=SqliteSourceRepository(self.db_path),
            runs=SqliteWikiIngestRunRepository(self.db_path),
        ).execute(
            ConfirmLocalWikiIngestInput(
                project_id=paths.project_id, source_id=source_id, requested_by="Owner"
            )
        )

    def incubate(self, paths: ProjectPaths, source_ids: list[str]):
        service = IncubateDocument(
            paths=paths,
            projects=self.projects,
            sources=SqliteSourceRepository(self.db_path),
            drafts=SqliteDocumentDraftRepository(self.db_path),
            store=DocumentStore(paths),
            gateway=self.document_gateway,
            wiki_context=WikiContextReader(
                paths=paths, sources=SqliteSourceRepository(self.db_path)
            ),
            model_call_logger=ModelCallLogger(self.db_path),
        )
        result = service.execute(
            IncubateDocumentInput(
                project_id=paths.project_id, source_ids=source_ids, requested_by="Owner"
            )
        )
        return service.save_draft(paths.project_id, result.draft.id, result.markdown)

    def publish(self, paths: ProjectPaths, draft_id: str):
        manifest = ManifestStore(paths.manifest_path, project_root=paths.project_root)
        return PublishDocumentDraft(
            paths=paths,
            projects=self.projects,
            sources=SqliteSourceRepository(self.db_path),
            drafts=SqliteDocumentDraftRepository(self.db_path),
            store=DocumentStore(paths),
            manifest=manifest,
            reconciliation=ReconciliationService(
                manifest_store=manifest, db_path=self.db_path, project_root=paths.project_root
            ),
        ).execute(
            PublishDocumentDraftInput(
                project_id=paths.project_id,
                draft_id=draft_id,
                owner_name="Owner",
                display_version="1.0",
            )
        )

    def current_markdown(self, paths: ProjectPaths) -> str | None:
        path = paths.wiki_root / "current" / "当前产品方案.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def export(self, paths: ProjectPaths):
        return ExportCurrentDocument(
            paths=paths,
            projects=self.projects,
            manifest=ManifestStore(paths.manifest_path, project_root=paths.project_root),
        ).execute(ExportCurrentDocumentInput(project_id=paths.project_id))

    def project_records(self, project_id: str) -> dict[str, object]:
        project = self.projects.get(project_id).model_dump(mode="json")
        project.pop("root_status")
        project.pop("root_last_verified_at")
        with connect(self.db_path) as connection:
            ingest_runs = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM wiki_ingest_runs WHERE project_id = ? ORDER BY id",
                    (project_id,),
                ).fetchall()
            ]
        return {
            "project": project,
            "sources": [
                source.model_dump(mode="json")
                for source in SqliteSourceRepository(self.db_path).list_for_project(project_id)
            ],
            "drafts": [
                draft.model_dump(mode="json")
                for draft in SqliteDocumentDraftRepository(self.db_path).list_for_project(
                    project_id
                )
            ],
            "ingest_runs": ingest_runs,
            "model_calls": [
                call.model_dump(mode="json")
                for call in SqliteModelCallLogRepository(self.db_path).list_for_project(
                    project_id, limit=1000
                )
            ],
        }

    def start_legacy_project(self, project_id: str, parent_root: Path) -> ProjectPaths:
        """Register a representative pre-2.2 project without backfilling its content tree."""
        project_root = parent_root / project_id
        for directory in ("raw", "wiki", "schema", "exports", ".incubator"):
            (project_root / directory).mkdir(parents=True, exist_ok=False)
        (project_root / "wiki" / "index.md").write_text("# Legacy Wiki\n", encoding="utf-8")
        (project_root / "wiki" / "log.md").write_text("# Legacy Log\n", encoding="utf-8")
        (project_root / ".incubator" / "project.json").write_text(
            json.dumps({"project_id": project_id, "schema_version": "2.1"}) + "\n",
            encoding="utf-8",
        )
        timestamp = datetime.now(UTC)
        self.projects.add(
            Project(
                id=project_id,
                name="Legacy project",
                product_line="Legacy product line",
                stage="active",
                current_baseline_id=None,
                allow_external_model=False,
                created_at=timestamp,
                updated_at=timestamp,
                project_root_path=str(project_root),
                root_status=ProjectRootStatus.AVAILABLE,
            )
        )
        self.manager.switch(project_id)
        return ProjectPaths.for_registered_root(self.library_root, project_id, project_root)

    def restart_container(self) -> AppContainer:
        return build_container(environ={"INCUBATOR_LIBRARY_ROOT": str(self.library_root)})

    def relocate(self, project_id: str, project_root: Path) -> ProjectPaths:
        selected = self.manager.relocate(
            RelocateProjectInput(project_id=project_id, project_root=project_root)
        )
        return ProjectPaths.for_registered_root(
            self.library_root, project_id, selected.project_root
        )

    def open_project(self, project_id: str) -> ProjectPaths:
        try:
            selected = self.manager.switch(project_id)
        except FileNotFoundError as error:
            raise DomainError(ErrorCode.PROJECT_ROOT_UNAVAILABLE) from error
        return ProjectPaths.for_registered_root(
            self.library_root, project_id, selected.project_root
        )

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def tree_hashes(paths: ProjectPaths) -> dict[str, str]:
        return {
            path.relative_to(paths.project_root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(paths.project_root.rglob("*"))
            if path.is_file()
        }
