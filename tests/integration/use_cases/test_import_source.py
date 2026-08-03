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
from src.infrastructure.observability.event_logger import EventLogger
from src.infrastructure.observability.model_call_logger import ModelCallLogger

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


class FakeDifyClient:
    def __init__(self, *, timeout: bool = False, on_run=None) -> None:
        self.timeout = timeout
        self.on_run = on_run
        self.calls = 0
        self.last_inputs: dict[str, Any] | None = None

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.last_inputs = deepcopy(kwargs["inputs"])
        if self.timeout:
            raise GatewayError.timeout()
        if self.on_run is not None:
            self.on_run()
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


class InvalidSemanticClient(FakeDifyClient):
    def __init__(self, mutation) -> None:
        super().__init__()
        self.mutation = mutation

    def run(self, **kwargs: Any) -> dict[str, Any]:
        response = super().run(**kwargs)
        self.mutation(response["result"])
        return response


class SameTitleMultiTypeClient(FakeDifyClient):
    def run(self, **kwargs: Any) -> dict[str, Any]:
        response = super().run(**kwargs)
        conflict = response["result"]["items"][0]
        conflict["title"] = "同名审查项"
        candidate = deepcopy(conflict)
        candidate.update(
            {
                "item_id": "ITEM-CANDIDATE-001",
                "content": "建议形成候选变更。",
                "result_type": "candidate",
                "status": "candidate",
                "uncertainty": "待正式批准",
            }
        )
        gap = deepcopy(conflict)
        gap.update(
            {
                "item_id": "ITEM-GAP-001",
                "content": "缺少目标客群统计口径。",
                "target_card_id": None,
                "result_type": "information_gap",
                "status": "ai_inferred",
                "uncertainty": "需补充材料",
            }
        )
        response["result"]["items"] = [conflict, candidate, gap]
        response["result"]["relations"] = [
            response["result"]["relations"][0],
            {
                "source_id": "ITEM-CANDIDATE-001",
                "relation_type": "proposes_change_to",
                "target_id": "RULE-001",
            },
        ]
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
    event_logger = EventLogger(db_path)
    event_logger.log_path = tmp_path / "app.log.jsonl"
    use_case = ImportSource(
        projects=SqliteProjectRepository(db_path),
        sources=SqliteSourceRepository(db_path),
        knowledge=SqliteKnowledgeRepository(db_path),
        unit_of_work=SqliteIngestUnitOfWork(db_path, event_logger),
        archive_factory=lambda project_id, source_id: SourceArchive(
            project_id=project_id,
            source_id=source_id,
        ),
        extractor=extract_document,
        gateway=IngestGateway(client),
        cache=AiCache(db_path),
        manifest_store=manifest_store,
        model_call_logger=ModelCallLogger(db_path),
        event_logger=event_logger,
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
    jsonl_document = json.loads((tmp_path / "app.log.jsonl").read_text(encoding="utf-8"))
    assert jsonl_document["event_id"].startswith("EVENT-INGEST-")
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
    assert duplicate.result_items == first.result_items
    assert duplicate.source_hash8 == first.source_hash8
    assert duplicate.cache_generated_at == first.cache_generated_at
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


def test_completed_duplicate_revalidates_manifest_and_command_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, manifest_store = _prepare(tmp_path, monkeypatch, client)
    use_case.execute(_command(content))

    with pytest.raises(AppError) as metadata_mismatch:
        use_case.execute(_command(content, source_department="合规"))
    assert metadata_mismatch.value.code == "SOURCE_METADATA_MISMATCH"

    manifest = manifest_store.read_and_validate()
    manifest_store.atomic_replace(manifest.model_copy(update={"current_version": "LLD-724_2"}))
    with pytest.raises(AppError) as baseline_mismatch:
        use_case.execute(_command(content))
    assert baseline_mismatch.value.code == ErrorCode.BASELINE_INTEGRITY_FAILED
    assert client.calls == 1
    assert SqliteSourceRepository(db_path).list_for_project("LLD")[0].ingest_status == "completed"


def test_duplicate_restores_exact_same_title_candidate_conflict_and_gap_display_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SameTitleMultiTypeClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)

    first = use_case.execute(_command(content))
    duplicate = use_case.execute(_command(content, preferred_mode="cache"))

    assert first.candidate_count == 1
    assert first.conflict_count == 1
    assert [item.status for item in first.result_items] == [
        "conflict",
        "candidate",
        "ai_inferred",
    ]
    assert duplicate.duplicate is True
    assert duplicate.result_items == first.result_items
    assert client.calls == 1
    with sqlite3.connect(db_path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM event_logs "
                "WHERE entity_id = ? AND event_type = 'source_ingest_completed'",
                (first.source_id,),
            ).fetchone()[0]
        )
    assert payload["result_items"] == [item.model_dump(mode="json") for item in first.result_items]


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
    duplicate = use_case.execute(command)
    assert duplicate.duplicate is True
    assert duplicate.result_items
    assert duplicate.source_hash8 == digest[:8]
    assert duplicate.cache_generated_at == report.cache_generated_at


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
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT event_type FROM event_logs WHERE entity_id = ?", (source.id,)
            ).fetchone()[0]
            == "source_ingest_security_blocked"
        )


def test_manifest_change_after_model_call_closes_audit_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation = None
    client = FakeDifyClient(on_run=lambda: mutation())
    use_case, content, db_path, manifest_store = _prepare(tmp_path, monkeypatch, client)
    current = manifest_store.read_and_validate()

    def change_manifest() -> None:
        manifest_store.atomic_replace(current.model_copy(update={"current_version": "LLD-724_2"}))

    mutation = change_manifest
    with pytest.raises(AppError) as failed:
        use_case.execute(_command(content))

    assert failed.value.code == ErrorCode.BASELINE_INTEGRITY_FAILED
    source = SqliteSourceRepository(db_path).list_for_project("LLD")[0]
    assert source.ingest_status == "validation_failed"
    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT status FROM model_call_logs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()[0]
            == "failed"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda output: output["items"][0].update({"target_card_id": None}),
        lambda output: output["items"][0].update({"status": "candidate"}),
        lambda output: output["relations"][0].update({"relation_type": "supports"}),
        lambda output: output["relations"].append(
            {
                "source_id": "ITEM-RISK-001",
                "relation_type": "supports",
                "target_id": "RULE-001",
            }
        ),
    ],
)
def test_semantically_inconsistent_model_output_is_rejected_and_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
) -> None:
    client = InvalidSemanticClient(mutation)
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)

    with pytest.raises(AppError) as invalid:
        use_case.execute(_command(content))

    assert invalid.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    source = SqliteSourceRepository(db_path).list_for_project("LLD")[0]
    assert source.ingest_status == "validation_failed"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT status FROM model_call_logs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()[0]
            == "failed"
        )


def test_short_document_reports_coverage_budget_without_model_audit_and_allows_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, _, db_path, _ = _prepare(tmp_path, monkeypatch, client)
    content = "# 风险\n建议收紧客群。".encode()

    with pytest.raises(AppError) as blocked:
        use_case.execute(_command(content))
    assert blocked.value.code == "OUTBOUND_COVERAGE_EXCEEDED"
    assert client.calls == 0
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0] == 0

    with pytest.raises(AppError) as no_cache:
        use_case.execute(_command(content, preferred_mode="cache"))
    assert no_cache.value.code == ErrorCode.CACHE_NOT_FOUND

    report = use_case.execute(_command(content, preferred_mode="local"))
    assert report.result_mode == "local_only"
    assert report.created_card_ids == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0] == 0


def test_local_check_is_traceable_but_does_not_occupy_completed_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)
    command = _command(content)

    local_report = use_case.execute(command.model_copy(update={"preferred_mode": "local"}))

    assert local_report.result_mode == "local_only"
    assert local_report.created_card_ids == []
    assert client.calls == 0
    source = SqliteSourceRepository(db_path).get(local_report.source_id)
    assert source.ingest_status == "local_checked"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM knowledge_cards").fetchone()[0] == 1
        event_types = {
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM event_logs WHERE entity_id = ?", (source.id,)
            ).fetchall()
        }
    assert event_types == {"source_ingest_local_checked"}

    realtime_report = use_case.execute(command)

    assert realtime_report.duplicate is False
    assert realtime_report.result_mode == "realtime"
    assert client.calls == 1
    assert SqliteSourceRepository(db_path).get(source.id).ingest_status == "completed"
    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM event_logs WHERE entity_id = ? "
                "AND event_type = 'source_ingest_completed'",
                (source.id,),
            ).fetchone()[0]
            == 1
        )


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

    with pytest.raises(AppError) as failed:
        use_case.execute(_command(content))
    assert failed.value.code == "INGEST_PERSISTENCE_FAILED"

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
                "SELECT status FROM model_call_logs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()[0]
            == "succeeded"
        )
        assert (
            connection.execute(
                "SELECT ingest_status FROM source_records WHERE id = ?", (source_id,)
            ).fetchone()[0]
            == "persistence_failed"
        )


def test_cache_write_failure_does_not_turn_committed_realtime_ingest_into_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)
    monkeypatch.setattr(
        use_case.cache,
        "put",
        lambda identity, result: (_ for _ in ()).throw(OSError("cache disk full")),
    )

    report = use_case.execute(_command(content))

    assert report.result_mode == "realtime"
    assert report.conflict_count == 1
    source = SqliteSourceRepository(db_path).get(report.source_id)
    assert source.ingest_status == "completed"
    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM event_logs WHERE entity_id = ?", (report.source_id,)
            ).fetchone()[0]
            == 1
        )


def test_jsonl_append_failure_returns_committed_success_and_reconciles_from_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDifyClient()
    use_case, content, db_path, _ = _prepare(tmp_path, monkeypatch, client)
    logger = use_case.event_logger
    original_append = logger.append_prepared
    monkeypatch.setattr(
        logger,
        "append_prepared",
        lambda prepared: (_ for _ in ()).throw(OSError("audit disk full")),
    )

    report = use_case.execute(_command(content))

    assert report.audit_reconciliation_pending is True
    assert SqliteSourceRepository(db_path).get(report.source_id).ingest_status == "completed"
    assert not logger.log_path.exists()
    with sqlite3.connect(db_path) as connection:
        event_id = connection.execute(
            "SELECT id FROM event_logs WHERE entity_id = ? "
            "AND event_type = 'source_ingest_completed'",
            (report.source_id,),
        ).fetchone()[0]

    monkeypatch.setattr(logger, "append_prepared", original_append)
    assert logger.reconcile() == 1
    document = json.loads(logger.log_path.read_text(encoding="utf-8"))
    assert document["event_id"] == event_id
