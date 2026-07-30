from __future__ import annotations

import json
from pathlib import Path

from src.domain.models import ModelCallLog
from src.infrastructure.db.connection import connect
from src.infrastructure.observability.sensitive_data import reject_sensitive


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ModelCallLogger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def record(self, record: ModelCallLog) -> None:
        validated = ModelCallLog.model_validate(record.model_dump())
        reject_sensitive(validated.model_dump(mode="json"))
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO model_call_logs (
                    id, project_id, task_type, workflow_run_id, correlation_id,
                    source_ids_json, baseline_version, model_label, prompt_version,
                    schema_version, authorized, redacted, outbound_chars,
                    outbound_coverage, result_mode, status, started_at, finished_at,
                    elapsed_ms, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workflow_run_id = excluded.workflow_run_id,
                    correlation_id = excluded.correlation_id,
                    source_ids_json = excluded.source_ids_json,
                    authorized = excluded.authorized,
                    redacted = excluded.redacted,
                    outbound_chars = excluded.outbound_chars,
                    outbound_coverage = excluded.outbound_coverage,
                    result_mode = excluded.result_mode,
                    status = excluded.status,
                    finished_at = excluded.finished_at,
                    elapsed_ms = excluded.elapsed_ms,
                    error_code = excluded.error_code
                """,
                (
                    validated.id,
                    validated.project_id,
                    validated.task_type,
                    validated.workflow_run_id,
                    validated.correlation_id,
                    _json_dumps(validated.source_ids),
                    validated.baseline_version,
                    validated.model_label,
                    validated.prompt_version,
                    validated.schema_version,
                    int(validated.authorized),
                    int(validated.redacted),
                    validated.outbound_chars,
                    validated.outbound_coverage,
                    validated.result_mode.value,
                    validated.status,
                    validated.started_at.isoformat(),
                    (None if validated.finished_at is None else validated.finished_at.isoformat()),
                    validated.elapsed_ms,
                    validated.error_code,
                ),
            )
