from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from src.application.dto.wiki_ingest import IngestArchivedSourceInput
from src.application.use_cases.ingest_archived_source import IngestArchivedSource
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import DomainError, ErrorCode, GatewayError
from src.domain.models import Project, SourceRecord
from src.domain.wiki import WikiIngestStatus
from src.infrastructure.db.connection import connect
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteProjectRepository,
    SqliteSourceRepository,
    SqliteWikiIngestRunRepository,
)
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.gateways.schemas import WikiIngestWorkflowOutput
from src.infrastructure.gateways.wiki_ingest_gateway import WikiIngestGateway

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


class RecordingWikiGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.before_return = lambda: None
        self.topic_changes: list[dict[str, Any]] | None = None
        self.conflicts: list[dict[str, Any]] = []
        self.evidence_gaps: list[str] = []

    def fail(self, error: Exception) -> None:
        self.error = error

    def generate(self, inputs, **kwargs):
        self.calls.append({"inputs": deepcopy(inputs), **kwargs})
        if self.error is not None:
            raise self.error
        self.before_return()
        return WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown=("# 来源摘要\n\n该材料明确了已脱敏的产品原则和可追溯证据。"),
            topic_changes=self.topic_changes
            or [
                {
                    "topic_id": "product-principles",
                    "title": "产品原则",
                    "change_type": "create",
                    "markdown": "该产品原则已由归档来源支持。",
                    "source_ids": [inputs["source"]["id"]],
                }
            ],
            conflicts=self.conflicts,
            evidence_gaps=self.evidence_gaps,
        )


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(deepcopy(kwargs))
        return {"workflow_run_id": "WF-WIKI-001", "result": {}}


class PreInvokeRevalidationGateway:
    def __init__(self) -> None:
        self.client = RecordingClient()
        self._gateway = WikiIngestGateway(self.client, timeout_seconds=60)

    def generate(self, inputs, **kwargs):
        tampered = deepcopy(inputs)
        tampered["safe_index_projection"] = "# tampered"
        return self._gateway.generate(tampered, **kwargs)


@dataclass
class IngestFixture:
    paths: ProjectPaths
    db_path: Path
    source_id: str
    gateway: RecordingWikiGateway
    service: IngestArchivedSource
    raw_path: Path

    def page(self, relative_path: str) -> Path:
        return self.paths.project_root / relative_path

    def execute(self, *, requested_by: str = "Owner"):
        return self.service.execute(
            IngestArchivedSourceInput(
                project_id="PROJECT_A",
                source_id=self.source_id,
                requested_by=requested_by,
            )
        )

    def protected_hashes(self) -> dict[str, str]:
        paths = (
            "wiki/current/current.md",
            "wiki/versions/v1.md",
            "wiki/drafts/candidate.md",
            ".incubator/current-baseline.json",
            ".incubator/candidate-manifest.json",
        )
        return {path: hashlib.sha256(self.page(path).read_bytes()).hexdigest() for path in paths}


def make_ingest_fixture(
    tmp_path: Path,
    *,
    raw_text: str | None = None,
    security_level: SecurityLevel = SecurityLevel.L2_INTERNAL,
    is_redacted: bool = True,
    allow_external_model: bool = True,
    customer_names: tuple[str, ...] = (),
    strategy_terms: tuple[str, ...] = (),
) -> IngestFixture:
    library = tmp_path / "library"
    paths = ProjectPaths.for_project(library, "PROJECT_A")
    paths.project_root.mkdir(parents=True)
    paths.system_root.mkdir(parents=True)
    paths.schema_root.mkdir(parents=True)
    paths.wiki_root.mkdir(parents=True)
    (paths.wiki_root / "sources").mkdir()
    (paths.wiki_root / "topics").mkdir()
    (paths.system_root / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "2.2",
                "wiki_schema_version": "2.2",
                "project_id": "PROJECT_A",
                "allow_external_model": True,
            }
        ),
        encoding="utf-8",
    )
    (paths.schema_root / "ingest-contract.md").write_text(
        "Generate a traceable Wiki proposal from locally verified and redacted evidence.",
        encoding="utf-8",
    )
    (paths.wiki_root / "index.md").write_text("# Wiki\n", encoding="utf-8")
    (paths.wiki_root / "log.md").write_text("# Log\n", encoding="utf-8")

    db_path = tmp_path / "control" / "product-incubator.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="PROJECT_A",
            name="Project A",
            product_line="Test",
            stage="incubating",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
            project_root_path=str(paths.project_root),
        )
    )
    source_id = "SRC-PROJECT-A-001"
    raw_text = raw_text or "Approved redacted product principle and supporting evidence.\n" * 1200
    raw_path = paths.raw_root / "2026" / source_id / "principles.md"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(raw_text, encoding="utf-8")
    raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    source = SourceRecord(
        id=source_id,
        project_id="PROJECT_A",
        original_filename="principles.md",
        archive_path=raw_path.relative_to(paths.project_root).as_posix(),
        sha256=raw_sha,
        mime_type="text/plain",
        size_bytes=raw_path.stat().st_size,
        source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="Product",
        provider=None,
        document_date=date(2026, 8, 17),
        document_version="1.0",
        applicable_baseline_version="BASE-1",
        security_level=security_level,
        is_redacted=is_redacted,
        allow_external_model=allow_external_model,
        is_sandbox=False,
        ingest_status="pending_ingest",
        created_at=NOW,
        material_name="Product principles",
        material_series_id="MAT-PROJECT-A-001",
    )
    SqliteSourceRepository(db_path).add(source)
    (paths.system_root / "source-index.json").write_text(
        json.dumps(
            {
                "schema_version": "2.2",
                "project_id": "PROJECT_A",
                "sources": [
                    {
                        "source_id": source.id,
                        "material_name": source.material_name,
                        "material_series_id": source.material_series_id,
                        "previous_source_id": None,
                        "material_version": source.document_version,
                        "filename": source.original_filename,
                        "archive_path": source.archive_path,
                        "sha256": source.sha256,
                        "source_type": source.source_type,
                        "authority_level": source.authority_level.value,
                        "security_level": source.security_level.value,
                        "ingest_status": source.ingest_status,
                        "ingest_schema_version": None,
                        "ingested_at": None,
                        "source_page_path": None,
                        "topic_page_paths": [],
                        "ingest_result_digest": None,
                        "ingest_error_code": None,
                        "generation_mode": None,
                        "created_at": source.created_at.isoformat(),
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    protected = {
        "wiki/current/current.md": "Published current\n",
        "wiki/versions/v1.md": "Published version\n",
        "wiki/drafts/candidate.md": "Owner candidate\n",
        ".incubator/current-baseline.json": '{"version":"v1"}\n',
        ".incubator/candidate-manifest.json": '{"candidate":"draft"}\n',
    }
    for relative_path, content in protected.items():
        target = paths.project_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    gateway = RecordingWikiGateway()
    service = IngestArchivedSource(
        paths=paths,
        db_path=db_path,
        sources=SqliteSourceRepository(db_path),
        runs=SqliteWikiIngestRunRepository(db_path),
        gateway=gateway,
        customer_names=customer_names,
        strategy_terms=strategy_terms,
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    )
    return IngestFixture(paths, db_path, source_id, gateway, service, raw_path)


@pytest.fixture
def ingest_fixture(tmp_path: Path) -> IngestFixture:
    return make_ingest_fixture(tmp_path)


def test_central_permission_revocation_blocks_external_wiki_call(ingest_fixture) -> None:
    """Central SQLite permission wins over permissive project-local metadata."""
    with connect(ingest_fixture.db_path) as connection:
        connection.execute(
            "UPDATE projects SET allow_external_model = 0 WHERE id = ?",
            ("PROJECT_A",),
        )

    with pytest.raises(DomainError, match="WIKI_EXTERNAL_CALL_DENIED"):
        ingest_fixture.execute()

    assert ingest_fixture.gateway.calls == []


def test_ingest_archived_l2_source_updates_complete_wiki(ingest_fixture) -> None:
    """Catches a successful run omitting any governed Wiki projection or DB result."""
    before_protected = ingest_fixture.protected_hashes()
    before_raw = hashlib.sha256(ingest_fixture.raw_path.read_bytes()).hexdigest()

    result = ingest_fixture.execute()

    assert result.status is WikiIngestStatus.INGESTED
    assert result.source_page_path.startswith("wiki/sources/")
    assert ingest_fixture.page(result.source_page_path).is_file()
    assert ingest_fixture.source_id in ingest_fixture.page("wiki/index.md").read_text()
    assert ingest_fixture.source_id in ingest_fixture.page("wiki/log.md").read_text()
    assert result.topic_page_paths
    assert all(ingest_fixture.page(path).is_file() for path in result.topic_page_paths)
    assert len(ingest_fixture.gateway.calls) == 1
    persisted = SqliteSourceRepository(ingest_fixture.db_path).get(ingest_fixture.source_id)
    assert persisted.ingest_status == "ingested"
    assert persisted.source_page_path == result.source_page_path
    assert ingest_fixture.protected_hashes() == before_protected
    assert hashlib.sha256(ingest_fixture.raw_path.read_bytes()).hexdigest() == before_raw
    with connect(ingest_fixture.db_path) as connection:
        audit = connection.execute(
            "SELECT task_type, source_ids_json, outbound_chars, outbound_coverage, "
            "status, error_code "
            "FROM model_call_logs WHERE task_type = 'wiki_ingest'"
        ).fetchone()
    assert audit is not None
    assert audit["source_ids_json"] == json.dumps([ingest_fixture.source_id], separators=(",", ":"))
    assert audit["outbound_chars"] > 0
    source_line = "Approved redacted product principle and supporting evidence."
    source_chars = (len(source_line) * 1200) + 1199
    assert audit["outbound_coverage"] == pytest.approx((3 * len(source_line)) / source_chars)
    assert audit["status"] == "succeeded"
    assert audit["error_code"] is None


def test_wiki_ingest_reports_coverage_error_before_gateway(tmp_path: Path) -> None:
    """Catches genuine source-chunk coverage excess being mislabeled or invoked."""
    fixture = make_ingest_fixture(tmp_path, raw_text="A" * 1000)

    with pytest.raises(DomainError) as caught:
        fixture.execute(requested_by="Owner")

    assert caught.value.code == ErrorCode.OUTBOUND_COVERAGE_EXCEEDED.value
    assert fixture.gateway.calls == []


def test_redaction_expansion_persists_truthful_over_one_coverage_audit(
    tmp_path: Path,
) -> None:
    """Catches truthful over-one coverage being dropped by audit validation."""
    raw_text = "a@b.co"
    redacted_text = "[已脱敏:email]"
    fixture = make_ingest_fixture(tmp_path, raw_text=raw_text)

    with pytest.raises(DomainError) as caught:
        fixture.execute(requested_by="Owner")

    expected_coverage = len(redacted_text) / len(raw_text)
    assert expected_coverage > 1
    assert caught.value.code == ErrorCode.OUTBOUND_COVERAGE_EXCEEDED.value
    assert fixture.gateway.calls == []
    with connect(fixture.db_path) as connection:
        audit = connection.execute(
            "SELECT outbound_coverage, result_mode, status, error_code "
            "FROM model_call_logs WHERE task_type = 'wiki_ingest'"
        ).fetchone()
    assert audit is not None
    assert audit["outbound_coverage"] == pytest.approx(expected_coverage)
    assert tuple(audit)[1:] == (
        "local_only",
        "failed",
        ErrorCode.OUTBOUND_COVERAGE_EXCEEDED.value,
    )


def test_successful_duplicate_returns_without_gateway_or_wiki_change(ingest_fixture) -> None:
    """Catches idempotent success invoking the model or appending Wiki content twice."""
    first = ingest_fixture.execute()
    wiki_hashes = {
        path: hashlib.sha256(ingest_fixture.page(path).read_bytes()).hexdigest()
        for path in (
            first.source_page_path,
            *first.topic_page_paths,
            "wiki/index.md",
            "wiki/log.md",
            ".incubator/source-index.json",
        )
    }

    duplicate = ingest_fixture.execute()

    assert duplicate.duplicate is True
    assert duplicate.status is WikiIngestStatus.INGESTED
    assert len(ingest_fixture.gateway.calls) == 1
    assert {
        path: hashlib.sha256(ingest_fixture.page(path).read_bytes()).hexdigest()
        for path in wiki_hashes
    } == wiki_hashes


def test_gateway_failure_records_safe_error_and_preserves_wiki(ingest_fixture) -> None:
    """Catches a failed model call partially changing Wiki or leaking its raw detail."""
    wiki_before = {
        str(path.relative_to(ingest_fixture.paths.project_root)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in ingest_fixture.paths.wiki_root.rglob("*")
        if path.is_file()
    }
    ingest_fixture.gateway.fail(GatewayError.timeout())

    with pytest.raises(GatewayError, match="MODEL_TIMEOUT"):
        ingest_fixture.execute()

    wiki_after = {
        str(path.relative_to(ingest_fixture.paths.project_root)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in ingest_fixture.paths.wiki_root.rglob("*")
        if path.is_file()
    }
    assert wiki_after == wiki_before
    source_index = json.loads(
        ingest_fixture.page(".incubator/source-index.json").read_text(encoding="utf-8")
    )
    mirrored = next(
        item for item in source_index["sources"] if item["source_id"] == ingest_fixture.source_id
    )
    assert mirrored["ingest_status"] == "ingest_failed"
    assert mirrored["ingest_error_code"] == "MODEL_TIMEOUT"
    source = SqliteSourceRepository(ingest_fixture.db_path).get(ingest_fixture.source_id)
    assert source.ingest_status == "ingest_failed"
    assert source.ingest_error_code == "MODEL_TIMEOUT"
    with connect(ingest_fixture.db_path) as connection:
        failed_run = connection.execute(
            "SELECT status, error_code FROM wiki_ingest_runs WHERE source_id = ?",
            (ingest_fixture.source_id,),
        ).fetchone()
    assert tuple(failed_run) == ("ingest_failed", "MODEL_TIMEOUT")


def test_structural_topic_symlink_fails_closed_before_gateway(ingest_fixture) -> None:
    topic_root = ingest_fixture.paths.wiki_root / "topics"
    current_root = ingest_fixture.paths.wiki_root / "current"
    if topic_root.exists():
        topic_root.rmdir()
    current_root.mkdir(parents=True, exist_ok=True)
    (current_root / "leak.md").write_text(
        "---\npage_type: topic\ntopic_id: leak\nproject_id: PROJECT_A\n---\n- leak",
        encoding="utf-8",
    )
    topic_root.symlink_to("current")

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        ingest_fixture.execute()

    assert ingest_fixture.gateway.calls == []


def test_lock_symlink_fails_closed_before_lock_write_or_gateway(ingest_fixture) -> None:
    lock_root = ingest_fixture.paths.system_root / "locks"
    protected = ingest_fixture.paths.wiki_root / "current"
    protected.mkdir(parents=True, exist_ok=True)
    if lock_root.exists():
        lock_root.rmdir()
    lock_root.symlink_to(protected, target_is_directory=True)
    before = ingest_fixture.protected_hashes()

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        ingest_fixture.execute()

    assert ingest_fixture.gateway.calls == []
    assert ingest_fixture.protected_hashes() == before
    assert not any(protected.glob("*.lock"))


@pytest.mark.parametrize("field", ["source_page_markdown", "topic_markdown"])
def test_model_output_with_injected_citation_body_fails_before_wiki_commit(
    ingest_fixture,
    field: str,
) -> None:
    project_b_root = ingest_fixture.paths.library_root / "PROJECT_B"
    project_b_root.mkdir(parents=True, exist_ok=True)
    SqliteProjectRepository(ingest_fixture.db_path).add(
        Project(
            id="PROJECT_B",
            name="Project B",
            product_line="Test",
            stage="incubating",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
            project_root_path=str(project_b_root),
        )
    )
    raw_path = project_b_root / "raw" / "2026" / "SRC-PROJECT-B-001" / "secret.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("PROJECT-B-SECRET", encoding="utf-8")
    SqliteSourceRepository(ingest_fixture.db_path).add(
        SourceRecord(
            id="SRC-PROJECT-B-001",
            project_id="PROJECT_B",
            original_filename="secret.md",
            archive_path=str(raw_path),
            sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            mime_type="text/plain",
            size_bytes=raw_path.stat().st_size,
            source_type="product_requirement",
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            source_department="Product",
            provider=None,
            document_date=date(2026, 8, 17),
            document_version="1.0",
            applicable_baseline_version="BASE-1",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted=True,
            allow_external_model=True,
            is_sandbox=False,
            ingest_status="ingested",
            created_at=NOW,
            material_name="Project B secret",
            material_series_id="MAT-B-001",
        )
    )
    if field == "source_page_markdown":
        ingest_fixture.gateway.generate = lambda inputs, **kwargs: WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown=("# 来源摘要\n\n伪造来源【SRC-PROJECT-B-001：section】"),
            topic_changes=[
                {
                    "topic_id": "product-principles",
                    "title": "产品原则",
                    "change_type": "create",
                    "markdown": "该产品原则已由归档来源支持。",
                    "source_ids": [inputs["source"]["id"]],
                }
            ],
            conflicts=[],
            evidence_gaps=[],
        )
    else:
        ingest_fixture.gateway.generate = lambda inputs, **kwargs: WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown="# 来源摘要\n\n该材料明确了已脱敏的产品原则。",
            topic_changes=[
                {
                    "topic_id": "product-principles",
                    "title": "产品原则",
                    "change_type": "create",
                    "markdown": "伪造注入【SRC-PROJECT-B-001：section】",
                    "source_ids": [inputs["source"]["id"]],
                }
            ],
            conflicts=[],
            evidence_gaps=[],
        )

    wiki_before = ingest_fixture.page("wiki/index.md").read_text(encoding="utf-8")
    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        ingest_fixture.execute()

    assert ingest_fixture.page("wiki/index.md").read_text(encoding="utf-8") == wiki_before
    assert not any(ingest_fixture.paths.wiki_root.joinpath("sources").glob("*.md"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("topic_id", "product-principles【SRC-L3：section】"),
        ("topic_id", "product-principles【SRC-PROJECT-B-001：section】"),
        ("title", "产品原则【SRC-L3：section】"),
        ("title", "产品原则【SRC-PROJECT-B-001：section】"),
        ("title", "产品原则【SRC-L3：section"),
        ("title", "abc123:section"),
        ("title", "12345：section"),
        ("title", "A:section"),
        ("title", "_LEGACY:section"),
        ("title", "产品原则 src-project-b-001:section"),
        ("title", "产品原则 Src-Project-B-001：section"),
        ("title", "产品原则 SRC-PROJECT-B-001:"),
        ("title", "产品原则 SRC-PROJECT-B-001："),
    ],
    ids=[
        "topic-id-l3",
        "topic-id-cross-project",
        "title-l3",
        "title-cross-project",
        "title-malformed",
        "title-lowercase-compact",
        "title-digits-only",
        "title-single-letter",
        "title-leading-underscore",
        "title-lowercase-source-id",
        "title-mixedcase-source-id",
        "title-dangling-ascii-colon",
        "title-dangling-fullwidth-colon",
    ],
)
def test_topic_metadata_with_citation_tokens_fails_before_transaction_write(
    ingest_fixture,
    field: str,
    value: str,
) -> None:
    wiki_before = ingest_fixture.page("wiki/index.md").read_text(encoding="utf-8")
    ingest_fixture.gateway.topic_changes = [
        {
            "topic_id": "product-principles",
            "title": "产品原则",
            "change_type": "create",
            "markdown": "该产品原则已由归档来源支持。",
            "source_ids": [ingest_fixture.source_id],
            field: value,
        }
    ]

    with pytest.raises(DomainError, match="TOPIC_METADATA_INVALID"):
        ingest_fixture.execute()

    transactions_root = ingest_fixture.paths.system_root / "transactions"
    assert ingest_fixture.page("wiki/index.md").read_text(encoding="utf-8") == wiki_before
    assert not any(ingest_fixture.paths.wiki_root.joinpath("sources").glob("*.md"))
    assert not transactions_root.exists() or not any(transactions_root.glob("*/journal.json"))


@pytest.mark.parametrize(
    ("field", "text", "extra_source"),
    [
        ("conflict_summary", "跨项目注入【SRC-PROJECT-B-001：section】", None),
        ("evidence_gap", "未知来源【SRC-UNKNOWN-001：section】", None),
        ("evidence_gap", "坏引用【SRC-PROJECT-A-001：section", None),
        (
            "conflict_summary",
            "高敏来源【SRC-L3-001：section】",
            SourceRecord(
                id="SRC-L3-001",
                project_id="PROJECT_A",
                original_filename="l3.md",
                archive_path="raw/2026/SRC-L3-001/l3.md",
                sha256="c" * 64,
                mime_type="text/plain",
                size_bytes=7,
                source_type="product_requirement",
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                source_department="Product",
                provider=None,
                document_date=date(2026, 8, 17),
                document_version="1.0",
                applicable_baseline_version="BASE-1",
                security_level=SecurityLevel.L3_CONFIDENTIAL,
                is_redacted=True,
                allow_external_model=False,
                is_sandbox=False,
                ingest_status="ingested",
                created_at=NOW,
                material_name="L3 secret",
                material_series_id="MAT-L3-001",
            ),
        ),
    ],
    ids=["conflict-cross-project", "gap-unknown", "gap-malformed", "conflict-l3"],
)
def test_model_output_with_injected_conflict_or_gap_citation_fails_before_wiki_commit(
    ingest_fixture,
    field: str,
    text: str,
    extra_source: SourceRecord | None,
) -> None:
    project_b_root = ingest_fixture.paths.library_root / "PROJECT_B"
    project_b_root.mkdir(parents=True, exist_ok=True)
    SqliteProjectRepository(ingest_fixture.db_path).add(
        Project(
            id="PROJECT_B",
            name="Project B",
            product_line="Test",
            stage="incubating",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
            project_root_path=str(project_b_root),
        )
    )
    raw_path = project_b_root / "raw" / "2026" / "SRC-PROJECT-B-001" / "secret.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("PROJECT-B-SECRET", encoding="utf-8")
    SqliteSourceRepository(ingest_fixture.db_path).add(
        SourceRecord(
            id="SRC-PROJECT-B-001",
            project_id="PROJECT_B",
            original_filename="secret.md",
            archive_path=str(raw_path),
            sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            mime_type="text/plain",
            size_bytes=raw_path.stat().st_size,
            source_type="product_requirement",
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            source_department="Product",
            provider=None,
            document_date=date(2026, 8, 17),
            document_version="1.0",
            applicable_baseline_version="BASE-1",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted=True,
            allow_external_model=True,
            is_sandbox=False,
            ingest_status="ingested",
            created_at=NOW,
            material_name="Project B secret",
            material_series_id="MAT-B-001",
        )
    )
    if extra_source is not None:
        SqliteSourceRepository(ingest_fixture.db_path).add(extra_source)

    if field == "conflict_summary":
        source_ids = [ingest_fixture.source_id]
        if extra_source is not None:
            source_ids.append(extra_source.id)
        ingest_fixture.gateway.generate = lambda inputs, **kwargs: WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown="# 来源摘要\n\n该材料明确了已脱敏的产品原则。",
            topic_changes=[
                {
                    "topic_id": "product-principles",
                    "title": "产品原则",
                    "change_type": "create",
                    "markdown": "该产品原则已由归档来源支持。",
                    "source_ids": [inputs["source"]["id"]],
                }
            ],
            conflicts=[{"summary": text, "source_ids": source_ids}],
            evidence_gaps=[],
        )
    else:
        ingest_fixture.gateway.generate = lambda inputs, **kwargs: WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown="# 来源摘要\n\n该材料明确了已脱敏的产品原则。",
            topic_changes=[
                {
                    "topic_id": "product-principles",
                    "title": "产品原则",
                    "change_type": "create",
                    "markdown": "该产品原则已由归档来源支持。",
                    "source_ids": [inputs["source"]["id"]],
                }
            ],
            conflicts=[],
            evidence_gaps=[text],
        )

    wiki_before = ingest_fixture.page("wiki/index.md").read_text(encoding="utf-8")
    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        ingest_fixture.execute()

    assert ingest_fixture.page("wiki/index.md").read_text(encoding="utf-8") == wiki_before
    assert not any(ingest_fixture.paths.wiki_root.joinpath("sources").glob("*.md"))


def test_permission_revocation_records_truthful_content_free_audit_without_gateway(
    ingest_fixture,
) -> None:
    with connect(ingest_fixture.db_path) as connection:
        connection.execute(
            "UPDATE projects SET allow_external_model = 0 WHERE id = ?",
            ("PROJECT_A",),
        )

    with pytest.raises(DomainError, match="WIKI_EXTERNAL_CALL_DENIED"):
        ingest_fixture.execute()

    assert ingest_fixture.gateway.calls == []
    with connect(ingest_fixture.db_path) as connection:
        audit = connection.execute(
            """
            SELECT authorized, redacted, result_mode, status, outbound_chars, error_code
            FROM model_call_logs
            WHERE task_type = 'wiki_ingest'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
    assert audit is not None
    assert tuple(audit) == (0, 0, "local_only", "failed", 0, "WIKI_EXTERNAL_CALL_DENIED")


def test_safety_proof_failure_records_truthful_audit_and_skips_gateway(
    ingest_fixture,
    monkeypatch,
) -> None:
    def fail_proof(*args, **kwargs):
        raise GatewayError.outbound_safety_proof_invalid()

    monkeypatch.setattr(
        "src.application.use_cases.ingest_archived_source.create_outbound_safety_proof",
        fail_proof,
    )

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        ingest_fixture.execute()

    assert ingest_fixture.gateway.calls == []
    with connect(ingest_fixture.db_path) as connection:
        audit = connection.execute(
            """
            SELECT authorized, redacted, result_mode, status, outbound_chars, error_code
            FROM model_call_logs
            WHERE task_type = 'wiki_ingest'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
    assert audit is not None
    assert audit["authorized"] == 0
    assert audit["redacted"] == 0
    assert audit["result_mode"] == "local_only"
    assert audit["status"] == "failed"
    assert audit["outbound_chars"] > 0
    assert audit["error_code"] == "REDACTION_REQUIRED"


def test_gateway_preinvoke_revalidation_failure_records_local_only_audit(
    ingest_fixture,
) -> None:
    gateway = PreInvokeRevalidationGateway()
    ingest_fixture.service.gateway = gateway

    with pytest.raises(GatewayError, match="OUTBOUND_SAFETY_PROOF_INVALID"):
        ingest_fixture.execute()

    assert gateway.client.calls == []
    with connect(ingest_fixture.db_path) as connection:
        audit = connection.execute(
            """
            SELECT authorized, redacted, result_mode, status, outbound_chars, error_code
            FROM model_call_logs
            WHERE task_type = 'wiki_ingest'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
    assert audit is not None
    assert audit["authorized"] == 1
    assert audit["redacted"] == 1
    assert audit["result_mode"] == "local_only"
    assert audit["status"] == "failed"
    assert audit["outbound_chars"] > 0
    assert audit["error_code"] == "REDACTION_REQUIRED"


def test_audit_logger_failure_does_not_mask_original_error(ingest_fixture) -> None:
    ingest_fixture.gateway.fail(GatewayError.timeout())

    def fail_audit(*_args, **_kwargs):
        raise OSError("audit disk full")

    ingest_fixture.service.model_call_logger.record = fail_audit

    with pytest.raises(GatewayError, match="MODEL_TIMEOUT"):
        ingest_fixture.execute()


def test_failed_run_retries_with_same_idempotency_without_duplicate_success(
    ingest_fixture,
) -> None:
    """Catches retry inserting a second unique idempotency row or skipping the Gateway."""
    ingest_fixture.gateway.fail(GatewayError.timeout())
    with pytest.raises(GatewayError):
        ingest_fixture.execute()
    ingest_fixture.gateway.error = None

    result = ingest_fixture.execute()

    assert result.status is WikiIngestStatus.INGESTED
    assert result.duplicate is False
    assert len(ingest_fixture.gateway.calls) == 2
    with connect(ingest_fixture.db_path) as connection:
        rows = connection.execute(
            "SELECT status FROM wiki_ingest_runs WHERE source_id = ?",
            (ingest_fixture.source_id,),
        ).fetchall()
    assert [row["status"] for row in rows] == ["ingested"]


def test_outbound_payload_uses_task8_local_authorization(ingest_fixture) -> None:
    """Catches the use case bypassing Task 8 proof/authorization or sending file paths."""
    ingest_fixture.execute()

    call = ingest_fixture.gateway.calls[0]
    serialized = json.dumps(call["inputs"], ensure_ascii=False)
    assert repr(call["safety_proof"]) == "OutboundSafetyProof(<opaque>)"
    assert repr(call["wiki_authorization"]) == "WikiOutboundAuthorization(<opaque>)"
    assert "raw_path" not in serialized
    assert "wiki/current" not in serialized
    assert "wiki/topics/" not in serialized
    assert ingest_fixture.source_id in serialized


def test_project_lock_blocks_distinct_source_before_second_gateway_call(
    ingest_fixture,
) -> None:
    """Catches two sources invoking the model against the same stale project snapshot."""
    sources = SqliteSourceRepository(ingest_fixture.db_path)
    original = sources.get(ingest_fixture.source_id)
    second_id = "SRC-PROJECT-A-002"
    second_raw = ingest_fixture.paths.raw_root / "2026" / second_id / "second.md"
    second_raw.parent.mkdir(parents=True)
    second_raw.write_text(
        "Second approved redacted source statement.\n" * 1200,
        encoding="utf-8",
    )
    second = original.model_copy(
        update={
            "id": second_id,
            "original_filename": "second.md",
            "archive_path": second_raw.relative_to(ingest_fixture.paths.project_root).as_posix(),
            "sha256": hashlib.sha256(second_raw.read_bytes()).hexdigest(),
            "size_bytes": second_raw.stat().st_size,
            "material_name": "Second principles",
            "material_series_id": "MAT-PROJECT-A-002",
        }
    )
    sources.add(second)
    from src.infrastructure.files.source_index_store import SourceIndexStore

    SourceIndexStore(ingest_fixture.paths).upsert(second)
    entered_gateway = threading.Event()
    release_gateway = threading.Event()

    def wait_in_gateway() -> None:
        entered_gateway.set()
        assert release_gateway.wait(timeout=5)

    ingest_fixture.gateway.before_return = wait_in_gateway
    first_errors: list[Exception] = []

    def run_first() -> None:
        try:
            ingest_fixture.execute()
        except Exception as error:  # pragma: no cover - asserted below
            first_errors.append(error)

    first = threading.Thread(target=run_first)
    first.start()
    assert entered_gateway.wait(timeout=5)
    try:
        source_index = json.loads(
            ingest_fixture.page(".incubator/source-index.json").read_text(encoding="utf-8")
        )
        first_mirror = next(
            item
            for item in source_index["sources"]
            if item["source_id"] == ingest_fixture.source_id
        )
        assert first_mirror["ingest_status"] == "ingesting"
        with pytest.raises(DomainError, match="WIKI_INGEST_ALREADY_RUNNING"):
            ingest_fixture.service.execute(
                IngestArchivedSourceInput(
                    project_id="PROJECT_A",
                    source_id=second_id,
                    requested_by="Owner",
                )
            )
        assert len(ingest_fixture.gateway.calls) == 1
        assert sources.get(second_id).ingest_status == "pending_ingest"
    finally:
        release_gateway.set()
        first.join(timeout=5)
    assert not first.is_alive()
    assert first_errors == []


def test_existing_topic_update_preserves_prior_evidence_and_appends_conflicts(
    ingest_fixture,
) -> None:
    """Catches an update replacing accepted conclusions or trusted old locators."""
    sources = SqliteSourceRepository(ingest_fixture.db_path)
    incoming = sources.get(ingest_fixture.source_id)
    old_source = incoming.model_copy(
        update={
            "id": "SRC-OLD-001",
            "sha256": "b" * 64,
            "archive_path": "raw/2025/SRC-OLD-001/old.md",
            "size_bytes": 42,
            "document_version": "0.9",
            "material_name": "Prior decision",
            "material_series_id": "MAT-OLD-001",
            "ingest_status": "ingested",
        }
    )
    sources.add(old_source)
    topic_path = ingest_fixture.page("wiki/topics/product-principles.md")
    existing = """---
page_type: topic
topic_id: product-principles
project_id: PROJECT_A
updated_at: '2026-08-01T00:00:00+00:00'
---
# 主题：产品原则

## 已接受结论

- 保留的旧结论 【SRC-OLD-001：line:77】

## 历史冲突

- 保留的旧冲突 【SRC-OLD-001：line:88】
"""
    topic_path.write_text(existing, encoding="utf-8")
    ingest_fixture.gateway.topic_changes = [
        {
            "topic_id": "product-principles",
            "title": "产品原则",
            "change_type": "update",
            "markdown": "新来源补充结论。",
            "source_ids": [incoming.id, old_source.id],
        }
    ]
    ingest_fixture.gateway.conflicts = [
        {
            "summary": "新旧原则需要 Owner 并行审阅。",
            "source_ids": [incoming.id, old_source.id],
        }
    ]
    ingest_fixture.gateway.evidence_gaps = ["缺少上线日期证据。"]

    result = ingest_fixture.execute()

    assert result.topic_page_paths == ["wiki/topics/product-principles.md"]
    updated = topic_path.read_text(encoding="utf-8")
    assert "保留的旧结论 【SRC-OLD-001：line:77】" in updated
    assert "保留的旧冲突 【SRC-OLD-001：line:88】" in updated
    assert "新来源补充结论。" in updated
    assert "新旧原则需要 Owner 并行审阅。" in updated
    assert "缺少上线日期证据。" in updated
    assert "SRC-OLD-001：line:1" not in updated
