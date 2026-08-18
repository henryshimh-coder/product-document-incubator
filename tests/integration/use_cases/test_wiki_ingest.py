from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from src.application.dto.wiki_ingest import IngestArchivedSourceInput
from src.application.use_cases.ingest_archived_source import IngestArchivedSource
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import GatewayError
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

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


class RecordingWikiGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def fail(self, error: Exception) -> None:
        self.error = error

    def generate(self, inputs, **kwargs):
        self.calls.append({"inputs": deepcopy(inputs), **kwargs})
        if self.error is not None:
            raise self.error
        return WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown=(
                "# 来源摘要\n\n该材料明确了已脱敏的产品原则和可追溯证据。"
            ),
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

    def execute(self):
        return self.service.execute(
            IngestArchivedSourceInput(
                project_id="PROJECT_A",
                source_id=self.source_id,
                requested_by="Owner",
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
        return {
            path: hashlib.sha256(self.page(path).read_bytes()).hexdigest() for path in paths
        }


def make_ingest_fixture(tmp_path: Path) -> IngestFixture:
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
    raw_text = ("Approved redacted product principle and supporting evidence.\n" * 1200)
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
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=True,
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
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    )
    return IngestFixture(paths, db_path, source_id, gateway, service, raw_path)


@pytest.fixture
def ingest_fixture(tmp_path: Path) -> IngestFixture:
    return make_ingest_fixture(tmp_path)


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
    index_before = ingest_fixture.page(".incubator/source-index.json").read_bytes()
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
    assert ingest_fixture.page(".incubator/source-index.json").read_bytes() == index_before
    source = SqliteSourceRepository(ingest_fixture.db_path).get(ingest_fixture.source_id)
    assert source.ingest_status == "ingest_failed"
    assert source.ingest_error_code == "MODEL_TIMEOUT"
    with connect(ingest_fixture.db_path) as connection:
        failed_run = connection.execute(
            "SELECT status, error_code FROM wiki_ingest_runs WHERE source_id = ?",
            (ingest_fixture.source_id,),
        ).fetchone()
    assert tuple(failed_run) == ("ingest_failed", "MODEL_TIMEOUT")


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
