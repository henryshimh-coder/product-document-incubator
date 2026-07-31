from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from src.application.dto.ingest import ImportSourceInput
from src.application.use_cases.import_source import ImportSource
from src.domain.enums import AuthorityLevel, KnowledgeStatus, SecurityLevel
from src.domain.errors import AppError, ErrorCode, GatewayError
from src.domain.models import BaselineManifest, KnowledgeCard, Project
from src.infrastructure.cache.ai_cache import AiCache, CacheIdentity
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteIngestUnitOfWork,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.archive import SourceArchive
from src.infrastructure.files.extractor import extract_document
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.gateways.ingest_gateway import IngestGateway
from src.infrastructure.observability.model_call_logger import ModelCallLogger

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


class FakeDifyClient:
    def __init__(self, *, timeout: bool = False) -> None:
        self.timeout = timeout
        self.calls = 0
        self.last_inputs: dict[str, Any] | None = None

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.last_inputs = deepcopy(kwargs["inputs"])
        if self.timeout:
            raise GatewayError.timeout()
        return {
            "workflow_run_id": "WF-INGEST-001",
            "result": _workflow_output(kwargs["inputs"]),
        }


class DuplicateItemClient(FakeDifyClient):
    def run(self, **kwargs: Any) -> dict[str, Any]:
        response = super().run(**kwargs)
        duplicate = deepcopy(response["result"]["items"][0])
        duplicate["title"] = "重复的模型条目"
        response["result"]["items"].append(duplicate)
        return response


def _workflow_output(inputs: dict[str, Any]) -> dict[str, Any]:
    chunk = inputs["source_chunks"][0]
    source = inputs["source"]
    return {
        "schema_version": "1.0",
        "task_id": inputs["task_id"],
        "summary": "识别到一条需会议裁决的风险意见。",
        "items": [
            {
                "item_id": "ITEM-RISK-001",
                "item_type": "professional_opinion",
                "title": "客群限制意见",
                "content": "建议收紧目标客群。",
                "target_card_id": "RULE-001",
                "result_type": "conflict_discussion",
                "status": "conflict",
                "source_citations": [
                    {
                        "source_id": source["id"],
                        "chunk_id": chunk["chunk_id"],
                        "locator": chunk["locator"],
                        "excerpt": chunk["text"][:20],
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
                "target_id": "RULE-001",
            }
        ],
    }


def _manifest(path: Path) -> ManifestStore:
    store = ManifestStore(path)
    manifest = BaselineManifest(
        schema_version="1.0",
        project_id="LLD",
        current_baseline_id="BASE-001",
        current_version="LLD-724_1",
        parent_baseline_id=None,
        full_document_path="baseline/full.md",
        card_snapshot_path="baseline/cards.json",
        full_document_sha256="a" * 64,
        card_snapshot_sha256="b" * 64,
        change_request_id=None,
        approved_by="产品经理",
        published_at=NOW,
    )
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    return store


def _command(content: bytes, **updates: Any) -> ImportSourceInput:
    base = ImportSourceInput(
        project_id="LLD",
        uploaded_name="风险意见.md",
        uploaded_bytes=content,
        source_type="risk_opinion",
        authority_level=AuthorityLevel.PROFESSIONAL_OPINION,
        source_department="风险",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted_confirmed=True,
        allow_external_model=True,
        is_sandbox=False,
        preferred_mode="realtime",
    )
    return base.model_copy(update=updates)


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: FakeDifyClient,
) -> tuple[ImportSource, bytes, Path, ManifestStore]:
    db_path = tmp_path / "state.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="LLD",
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id="BASE-001",
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    SqliteKnowledgeRepository(db_path).upsert_cards(
        [
            KnowledgeCard(
                id="RULE-001",
                project_id="LLD",
                card_type="rule",
                title="目标客群",
                content="当前目标客群为符合准入要求的存量客户。",
                status=KnowledgeStatus.EFFECTIVE,
                product_version="LLD-724_1",
                applicable_scope="一期",
                source_refs=["SRC-BASE"],
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                owner="产品经理",
                confidence=1,
                created_at=NOW,
                updated_at=NOW,
            )
        ]
    )
    archive_root = tmp_path / "archive"
    monkeypatch.setattr(
        "src.domain.services.file_safety.DEFAULT_SOURCE_ARCHIVE_ROOT",
        archive_root,
    )
    monkeypatch.setattr("src.infrastructure.cache.ai_cache.CACHE_ROOT", tmp_path / "cache")
    manifest_store = _manifest(tmp_path / "manifest.json")
    content = (
        "# 风险意见\n风险意见要求收紧目标客群，建议增加准入限制。\n"
        + "这是用于验证最小外调覆盖率的本地材料正文。" * 1400
    ).encode()
    use_case = ImportSource(
        projects=SqliteProjectRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        knowledge=SqliteKnowledgeRepository(db_path),
        unit_of_work=SqliteIngestUnitOfWork(db_path),
        archive_factory=lambda project_id, source_id: SourceArchive(
            project_id=project_id,
            source_id=source_id,
        ),
        extractor=extract_document,
        gateway=IngestGateway(client),
        cache=AiCache(db_path),
        manifest_store=manifest_store,
        model_call_logger=ModelCallLogger(db_path),
        customer_names=[],
        strategy_terms=[],
        financial_terms=[],
        leader_names=[],
        unpublished_decisions=[],
        now=lambda: NOW,
    )
    return use_case, content, db_path, manifest_store


def test_import_source_creates_conflict_without_changing_effective_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, manifest_store = _prepare(tmp_path, monkeypatch, client)
    before = manifest_store.read_and_validate()
    effective_before = SqliteKnowledgeRepository(db_path).list_effective("LLD", "LLD-724_1")

    report = use_case.execute(_command(content))

    assert report.conflict_count == 1
    assert report.result_mode == "realtime"
    assert client.last_inputs is not None
    assert "收紧目标客群" in "".join(chunk["text"] for chunk in client.last_inputs["source_chunks"])
    assert manifest_store.read_and_validate() == before
    assert SqliteKnowledgeRepository(db_path).list_effective("LLD", "LLD-724_1") == effective_before
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_cards WHERE status = 'conflict'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1
        evidence = json.loads(
            connection.execute("SELECT evidence_json FROM issue_cards").fetchone()[0]
        )
        assert {item["side"] for item in evidence} == {
            "current_baseline",
            "challenging_source",
        }
        assert len({item["source_id"] for item in evidence}) == 2
        assert connection.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0] == 1
        model_call = connection.execute(
            "SELECT correlation_id, workflow_run_id, status FROM model_call_logs"
        ).fetchone()
        assert model_call[0]
        assert model_call[1:] == ("WF-INGEST-001", "succeeded")


def test_completed_duplicate_returns_original_ids_without_rewriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)
    first = use_case.execute(_command(content))

    duplicate = use_case.execute(_command(content))

    assert duplicate.duplicate is True
    assert duplicate.created_card_ids == first.created_card_ids
    assert duplicate.created_relation_ids == first.created_relation_ids
    assert duplicate.created_issue_ids == first.created_issue_ids
    assert client.calls == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_cards WHERE status <> 'effective'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 1


def test_timeout_keeps_archive_and_recovers_same_source_from_exact_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient(timeout=True)
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)
    command = _command(content)
    digest = hashlib.sha256(content).hexdigest()
    source_id = f"SRC-{digest[:16].upper()}"
    task_id = f"INGEST-{digest[:16].upper()}"
    cached_inputs = {
        "schema_version": "1.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": task_id,
        "language": "zh-CN",
        "source": {
            "id": source_id,
            "type": "risk_opinion",
            "authority_level": "professional_opinion",
            "document_version": "v1.0",
            "document_date": "2026-07-29",
            "applicable_scope": "风险",
        },
        "baseline_rules": [],
        "source_chunks": [
            {
                "chunk_id": f"{source_id}-0001",
                "locator": "heading:风险意见; line:1",
                "text": "# 风险意见",
            }
        ],
    }
    # Cache payload validation is strict; the use case must only key by the exact identity.
    use_case.cache.put(
        CacheIdentity(
            task_type="ingest",
            source_sha256=digest,
            baseline_version="LLD-724_1",
            prompt_version="ingest-v1",
            model_label="dify-ingest",
            schema_version="1.0",
        ),
        _workflow_output(cached_inputs),
    )

    with pytest.raises(AppError) as timeout:
        use_case.execute(command)
    assert timeout.value.code == ErrorCode.MODEL_TIMEOUT
    failed = SqliteSourceRepository(db_path).get(source_id)
    assert failed.ingest_status == "realtime_failed"
    assert Path(failed.archive_path).exists()

    report = use_case.execute(command.model_copy(update={"preferred_mode": "cache"}))

    assert report.source_id == source_id
    assert report.result_mode == "cache"
    assert SqliteSourceRepository(db_path).get(source_id).ingest_status == "completed"
    assert client.calls == 1
    assert len(SqliteSourceRepository(db_path).list_for_project("LLD")) == 1


def test_security_policy_rejects_realtime_l3_before_gateway_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)

    with pytest.raises(AppError) as denied:
        use_case.execute(
            _command(
                content,
                security_level=SecurityLevel.L3_CONFIDENTIAL,
                allow_external_model=True,
            )
        )

    assert denied.value.code == ErrorCode.EXTERNAL_CALL_DENIED
    assert client.calls == 0
    source = SqliteSourceRepository(db_path).list_for_project("LLD")[0]
    assert source.allow_external_model is False


def test_duplicate_model_item_ids_are_rejected_before_any_result_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DuplicateItemClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)

    with pytest.raises(AppError) as invalid:
        use_case.execute(_command(content))

    assert invalid.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_cards WHERE status <> 'effective'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 0


def test_final_write_rolls_back_cards_relations_issues_and_status_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)
    digest = hashlib.sha256(content).hexdigest()
    source_id = f"SRC-{digest[:16].upper()}"
    event_id = f"EVENT-INGEST-{digest[:16].upper()}"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO event_logs (
                id, project_id, event_type, entity_type, entity_id, actor,
                correlation_id, payload_json, created_at
            ) VALUES (?, 'LLD', 'seed', 'source', ?, 'system', 'seed', '{}', ?)
            """,
            (event_id, source_id, NOW.isoformat()),
        )

    with pytest.raises(sqlite3.IntegrityError):
        use_case.execute(_command(content))

    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_cards WHERE status <> 'effective'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT ingest_status FROM source_records WHERE id = ?", (source_id,)
            ).fetchone()[0]
            != "completed"
        )
