from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.domain.models import EventLog
from src.infrastructure.db.connection import connect

EVENT_LOG_PATH = Path("data/local_state/app.log.jsonl")
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "prompt",
    "full_text",
    "raw_text",
    "customer_identity",
    "client_identity",
    "local_key",
    "access_token",
)
SENSITIVE_VALUE = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
SENSITIVE_EXACT_KEYS = frozenset(
    {"content", "text", "excerpt", "question", "answer", "raw_content"}
)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reject_sensitive(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if normalized_key in SENSITIVE_EXACT_KEYS or any(
                part in normalized_key for part in SENSITIVE_KEY_PARTS
            ):
                raise ValueError("sensitive event payload key is prohibited")
            _reject_sensitive(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _reject_sensitive(nested)
        return
    if isinstance(value, str) and SENSITIVE_VALUE.search(value):
        raise ValueError("sensitive event payload value is prohibited")


class EventLogger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.log_path = EVENT_LOG_PATH

    def record(self, record: EventLog, *, level: str = "INFO") -> None:
        validated = EventLog.model_validate(record.model_dump())
        normalized_level = level.strip().upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("invalid event log level")
        _reject_sensitive(validated.payload)
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
