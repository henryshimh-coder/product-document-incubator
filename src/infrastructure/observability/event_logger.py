from __future__ import annotations

import json
from pathlib import Path

from src.domain.models import EventLog
from src.infrastructure.db.connection import connect
from src.infrastructure.observability.sensitive_data import reject_sensitive

EVENT_LOG_PATH = Path("data/local_state/app.log.jsonl")


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EventLogger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.log_path = EVENT_LOG_PATH

    def record(self, record: EventLog, *, level: str = "INFO") -> None:
        validated = EventLog.model_validate(record.model_dump())
        normalized_level = level.strip().upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("invalid event log level")
        reject_sensitive(validated.model_dump(mode="json"))
        payload_json = _json_dumps(validated.payload)
        document = {
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
        line = _json_dumps(document) + "\n"

        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO event_logs (
                    id, project_id, event_type, entity_type, entity_id,
                    actor, correlation_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validated.id,
                    validated.project_id,
                    validated.event_type,
                    validated.entity_type,
                    validated.entity_id,
                    validated.actor,
                    validated.correlation_id,
                    payload_json,
                    validated.created_at.isoformat(),
                ),
            )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(line)
