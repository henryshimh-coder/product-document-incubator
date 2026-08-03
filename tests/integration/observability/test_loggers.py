from __future__ import annotations

import importlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.domain.models import EventLog, ModelCallLog
from src.infrastructure.db.migrations import migrate

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def _prepare_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, name, product_line, stage, current_baseline_id,
                allow_external_model, created_at, updated_at
            ) VALUES ('LLD', '推荐官链客计划', '零售信贷', '方案评审', NULL, 1, ?, ?)
            """,
            (NOW.isoformat(), NOW.isoformat()),
        )
    return db_path


def _model_log(status: str, *, finished: bool) -> ModelCallLog:
    return ModelCallLog(
        id="CALL-001",
        project_id="LLD",
        task_type="query",
        workflow_run_id="WF-001" if finished else None,
        correlation_id="CORR-001",
        source_ids=["SRC-002", "SRC-001"],
        baseline_version="LLD-724_1",
        model_label="dify-query",
        prompt_version="query-v1",
        schema_version="1.0",
        authorized=True,
        redacted=True,
        outbound_chars=120,
        outbound_coverage=0.2,
        result_mode="realtime",
        status=status,
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=12) if finished else None,
        elapsed_ms=12 if finished else None,
        error_code=None,
    )


def test_model_call_logger_upserts_validated_lifecycle_with_correlation(tmp_path: Path):
    """Catches lifecycle completion creating duplicates or losing audit identifiers."""
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.model_call_logger")
    logger = module.ModelCallLogger(db_path)

    logger.record(_model_log("started", finished=False))
    logger.record(_model_log("succeeded", finished=True))

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT workflow_run_id, correlation_id, source_ids_json, status,
                   elapsed_ms, error_code
            FROM model_call_logs WHERE id = 'CALL-001'
            """
        ).fetchone()
        count = connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0]
    assert row == (
        "WF-001",
        "CORR-001",
        '["SRC-002","SRC-001"]',
        "succeeded",
        12,
        None,
    )
    assert count == 1


def test_model_call_log_rejects_naive_or_inconsistent_lifecycle_timestamps():
    """Catches non-UTC or incomplete completed calls entering the audit database."""
    with pytest.raises(ValueError, match="UTC"):
        _model_log("started", finished=False).model_copy(
            update={"started_at": datetime(2026, 7, 29, 10, 0)}
        ).model_validate(
            {
                **_model_log("started", finished=False).model_dump(),
                "started_at": datetime(2026, 7, 29, 10, 0),
            }
        )

    with pytest.raises(ValueError, match="finished_at"):
        ModelCallLog.model_validate(
            {
                **_model_log("succeeded", finished=True).model_dump(),
                "finished_at": None,
            }
        )


def test_event_logger_writes_queryable_sqlite_index_and_safe_utf8_jsonl(
    tmp_path: Path,
    monkeypatch,
):
    """Catches event correlation existing only in payload or Unicode being escaped."""
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    event = EventLog(
        id="EVENT-001",
        project_id="LLD",
        event_type="source_ingest_completed",
        entity_type="source",
        entity_id="SRC-001",
        actor="产品经理",
        correlation_id="CORR-001",
        payload={"duration_ms": 12, "status": "succeeded"},
        created_at=NOW,
    )

    logger.record(event, level="INFO")

    log_path = tmp_path / "data" / "local_state" / "app.log.jsonl"
    raw_line = log_path.read_text(encoding="utf-8")
    document = json.loads(raw_line)
    assert "产品经理" in raw_line
    assert document == {
        "actor": "产品经理",
        "correlation_id": "CORR-001",
        "event_id": "EVENT-001",
        "entity_id": "SRC-001",
        "entity_type": "source",
        "event": "source_ingest_completed",
        "level": "INFO",
        "payload": {"duration_ms": 12, "status": "succeeded"},
        "project_id": "LLD",
        "timestamp": "2026-07-29T10:00:00+00:00",
    }
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT correlation_id, level, payload_json
            FROM event_logs WHERE project_id = 'LLD' AND correlation_id = 'CORR-001'
            """
        ).fetchone()
    assert row == ("CORR-001", "INFO", '{"duration_ms":12,"status":"succeeded"}')


def test_event_log_production_path_cannot_be_overridden(tmp_path: Path):
    """Catches callers redirecting audit events away from the fixed local log."""
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")

    with pytest.raises(TypeError):
        module.EventLogger(db_path, log_path=tmp_path / "other.jsonl")


@pytest.mark.parametrize(
    ("field", "secret_value"),
    [
        ("actor", "Bearer EVENT-TOP-SECRET"),
        ("entity_id", "app-1234567890abcdef1234567890abcdef"),
        ("event_type", "sk-abcdefghijklmnopqrstuvwxyz123456"),
    ],
)
def test_event_logger_rejects_sensitive_top_level_fields_before_any_write(
    tmp_path: Path,
    monkeypatch,
    field: str,
    secret_value: str,
):
    """Catches top-level event metadata bypassing the hard audit secret boundary."""
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    data = {
        "id": "EVENT-SECRET",
        "project_id": "LLD",
        "event_type": "safe_event",
        "entity_type": "source",
        "entity_id": "SRC-001",
        "actor": "system",
        "correlation_id": "CORR-SECRET",
        "payload": {"status": "failed"},
        "created_at": NOW,
    }
    data[field] = secret_value
    event = EventLog.model_validate(data)

    with pytest.raises(ValueError, match="sensitive"):
        logger.record(event)

    assert not (tmp_path / "data" / "local_state" / "app.log.jsonl").exists()
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "dify-secret"},
        {"nested": {"full_prompt": "全部外调提示"}},
        {"dify_api_key": "plain-token"},
        {"nested": {"customer_secret": "plain-value"}},
        {"request_prompt": "完整请求提示"},
        {"content": "未脱敏完整原文"},
        {"status": "Bearer secret-value"},
    ],
)
def test_event_logger_rejects_sensitive_keys_and_values_before_any_write(
    tmp_path: Path,
    monkeypatch,
    payload,
):
    """Catches credentials or complete prompts entering either audit sink."""
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    event = EventLog(
        id="EVENT-SECRET",
        project_id="LLD",
        event_type="unsafe_event",
        entity_type="source",
        entity_id="SRC-001",
        actor="system",
        correlation_id="CORR-SECRET",
        payload=payload,
        created_at=NOW,
    )

    with pytest.raises(ValueError, match="sensitive"):
        logger.record(event)

    assert not (tmp_path / "data" / "local_state" / "app.log.jsonl").exists()
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0] == 0


def test_model_call_logger_allows_safe_prompt_version_metadata(tmp_path: Path):
    """Catches substring matching from blocking the explicitly safe prompt version field."""
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.model_call_logger")
    logger = module.ModelCallLogger(db_path)

    logger.record(_model_log("started", finished=False))

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT prompt_version FROM model_call_logs WHERE id = 'CALL-001'"
        ).fetchone()
    assert row == ("query-v1",)


def test_event_logger_reconciles_committed_sqlite_event_after_jsonl_append_failure(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    event = EventLog(
        id="EVENT-RECOVER",
        project_id="LLD",
        event_type="source_ingest_completed",
        entity_type="source",
        entity_id="SRC-RECOVER",
        actor="system",
        correlation_id="CORR-RECOVER",
        payload={"status": "succeeded"},
        created_at=NOW,
    )
    original_append = logger.append_prepared
    monkeypatch.setattr(
        logger,
        "append_prepared",
        lambda prepared: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(module.AuditDurabilityUncertainError):
        logger.record(event)

    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM event_logs WHERE id = 'EVENT-RECOVER'"
            ).fetchone()[0]
            == 1
        )
    monkeypatch.setattr(logger, "append_prepared", original_append)
    assert logger.reconcile() == 1
    document = json.loads((tmp_path / "data/local_state/app.log.jsonl").read_text(encoding="utf-8"))
    assert document["event_id"] == "EVENT-RECOVER"


def test_event_logger_rewrites_partial_tail_to_canonical_unique_sqlite_events(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    first = EventLog(
        id="EVENT-FIRST",
        project_id="LLD",
        event_type="source_ingest_completed",
        entity_type="source",
        entity_id="SRC-FIRST",
        actor="system",
        correlation_id="CORR-FIRST",
        payload={"status": "succeeded"},
        created_at=NOW,
    )
    second = first.model_copy(
        update={
            "id": "EVENT-SECOND",
            "entity_id": "SRC-SECOND",
            "correlation_id": "CORR-SECOND",
            "created_at": NOW + timedelta(seconds=1),
        }
    )
    logger.record(first)
    original_append = logger.append_prepared

    def append_partial_then_fail(prepared):
        with logger.log_path.open("a", encoding="utf-8") as stream:
            stream.write(prepared.line[:23])
        raise OSError("disk full after partial write")

    monkeypatch.setattr(logger, "append_prepared", append_partial_then_fail)
    with pytest.raises(module.AuditDurabilityUncertainError):
        logger.record(second)
    monkeypatch.setattr(logger, "append_prepared", original_append)

    assert logger.reconcile() == 1
    raw_lines = logger.log_path.read_text(encoding="utf-8").splitlines()
    documents = [json.loads(line) for line in raw_lines]
    assert raw_lines == [
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for document in documents
    ]
    event_ids = [document["event_id"] for document in documents]
    with sqlite3.connect(db_path) as connection:
        sqlite_ids = {row[0] for row in connection.execute("SELECT id FROM event_logs").fetchall()}
    assert event_ids == ["EVENT-FIRST", "EVENT-SECOND"]
    assert len(event_ids) == len(set(event_ids))
    assert set(event_ids) == sqlite_ids


def test_event_logger_rewrites_invalid_utf8_tail_from_sqlite(
    tmp_path: Path,
    monkeypatch,
):
    """Catches a truncated multibyte tail blocking startup reconciliation."""
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    event = EventLog(
        id="EVENT-UTF8",
        project_id="LLD",
        event_type="source_ingest_failed",
        entity_type="source",
        entity_id="SRC-UTF8",
        actor="system",
        correlation_id="CORR-UTF8",
        payload={"status": "failed"},
        created_at=NOW,
    )
    prepared = logger.prepare(event)
    with sqlite3.connect(db_path) as connection:
        logger.insert_prepared(connection, prepared)
    logger.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.log_path.write_bytes(b'{"payload":"\xe4')

    assert logger.reconcile() == 1

    raw_line = logger.log_path.read_text(encoding="utf-8")
    assert raw_line.endswith("\n")
    assert json.loads(raw_line)["event_id"] == "EVENT-UTF8"


def test_event_logger_preserves_normalized_levels_when_rewriting_partial_tail(
    tmp_path: Path,
    monkeypatch,
):
    """Catches SQLite reconciliation downgrading WARNING/ERROR events to INFO."""
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    warning = EventLog(
        id="EVENT-WARNING",
        project_id="LLD",
        event_type="source_ingest_security_blocked",
        entity_type="source",
        entity_id="SRC-WARNING",
        actor="system",
        correlation_id="CORR-WARNING",
        payload={"status": "blocked"},
        created_at=NOW,
    )
    error = warning.model_copy(
        update={
            "id": "EVENT-ERROR",
            "event_type": "source_ingest_failed",
            "entity_id": "SRC-ERROR",
            "correlation_id": "CORR-ERROR",
            "payload": {"status": "failed"},
            "created_at": NOW + timedelta(seconds=1),
        }
    )
    logger.record(warning, level=" warning ")
    original_append = logger.append_prepared

    def append_invalid_utf8_then_fail(prepared):
        with logger.log_path.open("ab") as stream:
            stream.write(b'{"payload":"\xe4')
        raise OSError("disk full after partial multibyte write")

    monkeypatch.setattr(logger, "append_prepared", append_invalid_utf8_then_fail)
    with pytest.raises(module.AuditDurabilityUncertainError):
        logger.record(error, level="error")
    monkeypatch.setattr(logger, "append_prepared", original_append)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id, level FROM event_logs ORDER BY created_at, id"
        ).fetchall()
    assert rows == [("EVENT-WARNING", "WARNING"), ("EVENT-ERROR", "ERROR")]

    assert logger.reconcile() == 1
    documents = [
        json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(document["event_id"], document["level"]) for document in documents] == [
        ("EVENT-WARNING", "WARNING"),
        ("EVENT-ERROR", "ERROR"),
    ]


def test_event_logger_atomic_rewrite_failure_preserves_partial_original(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.event_logger")
    logger = module.EventLogger(db_path)
    event = EventLog(
        id="EVENT-ATOMIC",
        project_id="LLD",
        event_type="source_ingest_completed",
        entity_type="source",
        entity_id="SRC-ATOMIC",
        actor="system",
        correlation_id="CORR-ATOMIC",
        payload={"status": "succeeded"},
        created_at=NOW,
    )
    prepared = logger.prepare(event)
    with sqlite3.connect(db_path) as connection:
        logger.insert_prepared(connection, prepared)
    logger.log_path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"payload":"\xe4'
    logger.log_path.write_bytes(original)
    monkeypatch.setattr(
        os,
        "replace",
        lambda source, target: (_ for _ in ()).throw(OSError("replace denied")),
    )

    with pytest.raises(OSError, match="replace denied"):
        logger.reconcile()

    assert logger.log_path.read_bytes() == original
    assert list(logger.log_path.parent.glob(".app.log.jsonl.*.tmp")) == []


@pytest.mark.parametrize(
    ("field", "secret_value"),
    [
        ("source_ids", ["Bearer MODEL-CALL-SECRET"]),
        ("model_label", "app-1234567890abcdef1234567890abcdef"),
        ("error_code", "sk-abcdefghijklmnopqrstuvwxyz123456"),
    ],
)
def test_model_call_logger_rejects_sensitive_record_fields_before_sqlite_write(
    tmp_path: Path,
    field: str,
    secret_value,
):
    """Catches model-call metadata bypassing the hard audit secret boundary."""
    db_path = _prepare_db(tmp_path)
    module = importlib.import_module("src.infrastructure.observability.model_call_logger")
    logger = module.ModelCallLogger(db_path)
    if field == "error_code":
        record = _model_log("succeeded", finished=True).model_copy(
            update={"status": "failed", field: secret_value}
        )
    else:
        record = _model_log("started", finished=False).model_copy(update={field: secret_value})

    with pytest.raises(ValueError, match="sensitive"):
        logger.record(record)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0] == 0
