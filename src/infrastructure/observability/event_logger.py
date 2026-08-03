from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.domain.models import EventLog
from src.infrastructure.db.connection import connect
from src.infrastructure.observability.sensitive_data import reject_sensitive

EVENT_LOG_PATH = Path("data/local_state/app.log.jsonl")


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AuditDurabilityUncertainError(RuntimeError):
    """SQLite committed, but the canonical JSONL append needs reconciliation."""


@dataclass(frozen=True)
class PreparedEvent:
    event: EventLog
    payload_json: str
    line: str


class EventLogger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.log_path = EVENT_LOG_PATH

    def prepare(self, record: EventLog, *, level: str = "INFO") -> PreparedEvent:
        validated = EventLog.model_validate(record.model_dump())
        normalized_level = level.strip().upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("invalid event log level")
        reject_sensitive(validated.model_dump(mode="json"))
        payload_json = _json_dumps(validated.payload)
        document = {
            "event_id": validated.id,
            "timestamp": validated.created_at.isoformat(),
            "level": normalized_level,
            "event": validated.event_type,
            "project_id": validated.project_id,
            "entity_type": validated.entity_type,
            "entity_id": validated.entity_id,
            "actor": validated.actor,
            "correlation_id": validated.correlation_id,
            "payload": validated.payload,
        }
        return PreparedEvent(
            event=validated,
            payload_json=payload_json,
            line=_json_dumps(document) + "\n",
        )

    @staticmethod
    def insert_prepared(
        connection: sqlite3.Connection,
        prepared: PreparedEvent,
    ) -> None:
        event = prepared.event
        connection.execute(
            """
            INSERT INTO event_logs (
                id, project_id, event_type, entity_type, entity_id,
                actor, correlation_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.project_id,
                event.event_type,
                event.entity_type,
                event.entity_id,
                event.actor,
                event.correlation_id,
                prepared.payload_json,
                event.created_at.isoformat(),
            ),
        )

    def append_prepared(self, prepared: PreparedEvent) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(prepared.line)

    def append_committed(self, prepared: PreparedEvent) -> None:
        try:
            self.append_prepared(prepared)
        except OSError as error:
            raise AuditDurabilityUncertainError(
                f"event {prepared.event.id} committed to SQLite but JSONL append failed"
            ) from error

    def record(self, record: EventLog, *, level: str = "INFO") -> None:
        prepared = self.prepare(record, level=level)
        with connect(self.db_path) as connection:
            self.insert_prepared(connection, prepared)
        self.append_committed(prepared)

    def reconcile(self) -> int:
        existing_ids: set[str] = set()
        rewrite_required = False
        if self.log_path.exists():
            raw_text = self.log_path.read_text(encoding="utf-8")
            if raw_text and not raw_text.endswith("\n"):
                rewrite_required = True
            for line in raw_text.splitlines():
                try:
                    document = json.loads(line)
                except json.JSONDecodeError:
                    rewrite_required = True
                    continue
                if not isinstance(document, dict) or line != _json_dumps(document):
                    rewrite_required = True
                    continue
                event_id = document.get("event_id")
                if not isinstance(event_id, str) or event_id in existing_ids:
                    rewrite_required = True
                    continue
                existing_ids.add(event_id)
        with connect(self.db_path) as connection:
            rows = connection.execute("SELECT * FROM event_logs ORDER BY created_at, id").fetchall()
        sqlite_ids = {row["id"] for row in rows}
        if existing_ids - sqlite_ids:
            rewrite_required = True
        missing_ids = sqlite_ids - existing_ids
        if rewrite_required:
            self._atomic_rewrite(rows)
            return len(missing_ids)
        appended = 0
        for row in rows:
            if row["id"] in existing_ids:
                continue
            self.append_prepared(self.prepare(self._event_from_row(row)))
            appended += 1
        return appended

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventLog:
        return EventLog(
            id=row["id"],
            project_id=row["project_id"],
            event_type=row["event_type"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            actor=row["actor"],
            correlation_id=row["correlation_id"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )

    def _atomic_rewrite(self, rows: list[sqlite3.Row]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.log_path.parent,
                prefix=f".{self.log_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                for row in rows:
                    temporary.write(self.prepare(self._event_from_row(row)).line)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.log_path)
            temporary_path = None
            descriptor = os.open(self.log_path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
